"""Local audit trail for CLI / offline runs (02 §3.6), SQLite (stdlib).

Tables: runs, findings, plans, edits, approvals, ai_calls and an append-only
``events`` table whose rows form a SHA-256 hash chain::

    event_hash = sha256(prev_hash || canonical_json(event))

Events also anchor the *content* tables: the event written for a check run
carries the digest of its findings, a plan event the plan hash, an apply
event the digest of the edits. ``verify_chain()`` recomputes both, so editing
any audited row (or deleting an event) is detected.

Application logs written by this module carry IDs and hashes only; cell
contents are logged only when verbose debug logging is switched on.

Encryption at rest: when ``key`` is given (or ``FSCHECK_AUDIT_KEY`` is set) the
store opens the database through SQLCipher (``sqlcipher3`` module); without it
the firm should rely on disk encryption.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import logging
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fscore.models import ApplyResult, CheckResult, Finding, FixPlan

log = logging.getLogger("fscore.audit")

GENESIS = "0" * 64
SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
  actor TEXT, file_name TEXT, file_sha256 TEXT, config_hash TEXT, engine_version TEXT,
  rule_set_version TEXT, rule_set_hash TEXT, status TEXT NOT NULL DEFAULT 'running',
  params_json TEXT, summary_json TEXT, result_json TEXT
);
CREATE TABLE IF NOT EXISTS findings (
  run_id TEXT NOT NULL, finding_id TEXT NOT NULL, fingerprint TEXT, rule_id TEXT NOT NULL,
  severity TEXT NOT NULL, certainty TEXT, sheet TEXT, cells TEXT, difference TEXT, fix_kind TEXT,
  status TEXT NOT NULL DEFAULT 'Open', detail_json TEXT NOT NULL,
  PRIMARY KEY (run_id, finding_id)
);
CREATE TABLE IF NOT EXISTS plans (
  id TEXT PRIMARY KEY, run_id TEXT, plan_sha256 TEXT NOT NULL, source_sha256 TEXT, policy TEXT,
  created_at TEXT NOT NULL, edit_count INTEGER NOT NULL, plan_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edits (
  plan_id TEXT NOT NULL, edit_id TEXT NOT NULL, sheet TEXT, cell TEXT, kind TEXT, grp TEXT,
  old_formula TEXT, old_value TEXT, new_formula TEXT, new_value TEXT, reason TEXT,
  applied INTEGER NOT NULL DEFAULT 0, out_sha256 TEXT,
  PRIMARY KEY (plan_id, edit_id)
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, approver TEXT NOT NULL, plan_sha256 TEXT NOT NULL,
  item_ids TEXT NOT NULL, ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_calls (
  id TEXT PRIMARY KEY, run_id TEXT, ts TEXT NOT NULL, provider TEXT, model TEXT, region TEXT,
  payload_sha256 TEXT NOT NULL, payload TEXT, retain TEXT NOT NULL, requested_by TEXT,
  approved_by TEXT, decision TEXT NOT NULL, stop_reason TEXT, response_sha256 TEXT, response TEXT
);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT, action TEXT NOT NULL,
  object_type TEXT, object_id TEXT, details_json TEXT NOT NULL, prev_hash TEXT NOT NULL, event_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_findings_run ON findings(run_id);
CREATE INDEX IF NOT EXISTS ix_events_object ON events(object_type, object_id);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_text(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


def event_digest(prev_hash: str, event: dict[str, Any]) -> str:
    return sha256_text(prev_hash + canonical_json(event))


def default_path() -> Path:
    env = os.environ.get("FSCHECK_AUDIT_DB")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".fscheck" / "audit.db"


def default_actor() -> str:
    env = os.environ.get("FSCHECK_USER")
    if env:
        return env
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


@dataclass
class ChainVerification:
    ok: bool
    events: int
    head: str
    broken_at: int | None = None
    reason: str = ""


def _findings_digest(rows: list[tuple[str, str]]) -> str:
    """Digest over (finding_id, detail_json) pairs in finding-id order."""
    h = hashlib.sha256()
    for fid, detail in sorted(rows):
        h.update(fid.encode("utf-8"))
        h.update(b"\x00")
        h.update(detail.encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest()


def _edits_digest(rows: list[tuple[Any, ...]]) -> str:
    return sha256_text(canonical_json(sorted([list(r) for r in rows])))


class AuditStore:
    """Append-only audit store. Use as a context manager or call ``close()``."""

    def __init__(self, path: str | Path | None = None, *, key: str | None = None, actor: str | None = None, verbose: bool = False) -> None:
        self.path = Path(path).expanduser() if path else default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.actor = actor or default_actor()
        self.verbose = verbose
        key = key or os.environ.get("FSCHECK_AUDIT_KEY")
        self.conn = self._connect(key)
        self.conn.executescript(_SCHEMA)
        self.conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))

    def _connect(self, key: str | None) -> sqlite3.Connection:
        if key:
            try:
                import sqlcipher3
            except ImportError as exc:
                raise RuntimeError("an audit-store key was given but SQLCipher (sqlcipher3) is not installed") from exc
            conn = sqlcipher3.connect(str(self.path), isolation_level=None)
            conn.execute("PRAGMA key = '" + key.replace("'", "''") + "'")
        else:
            # autocommit mode: transactions are opened explicitly by _tx()
            conn = sqlite3.connect(str(self.path), isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn  # type: ignore[no-any-return]

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> AuditStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Serialised write transaction (the hash chain needs a single writer)."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    # ------------------------------------------------------------------ events
    def _append(self, conn: sqlite3.Connection, action: str, *, object_type: str = "", object_id: str = "", details: dict[str, Any] | None = None, actor: str | None = None) -> str:
        row = conn.execute("SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row[0] if row else GENESIS
        ts = _now()
        # normalise through JSON so the hash equals what verify_chain() recomputes from the stored row
        norm = json.loads(canonical_json(details or {}))
        event = {"ts": ts, "actor": actor or self.actor, "action": action, "object_type": object_type, "object_id": object_id, "details": norm}
        h = event_digest(prev, event)
        conn.execute(
            "INSERT INTO events(ts, actor, action, object_type, object_id, details_json, prev_hash, event_hash) VALUES (?,?,?,?,?,?,?,?)",
            (ts, event["actor"], action, object_type, object_id, canonical_json(event["details"]), prev, h),
        )
        log.info("audit event %s %s:%s %s", action, object_type, object_id, h[:16])
        return h

    def append_event(self, action: str, *, object_type: str = "", object_id: str = "", details: dict[str, Any] | None = None, actor: str | None = None) -> str:
        """Record an event (details must hold IDs, hashes and counts — not cell contents)."""
        with self._tx() as conn:
            return self._append(conn, action, object_type=object_type, object_id=object_id, details=details, actor=actor)

    # ------------------------------------------------------------------ runs
    def start_run(self, kind: str, *, file_name: str | None = None, file_sha256: str | None = None, config_hash: str | None = None,
                  params: dict[str, Any] | None = None) -> str:
        run_id = "R-" + uuid.uuid4().hex[:12]
        from fscore import ENGINE_VERSION
        from fscore.registry import rule_set_hash, rule_set_version

        with self._tx() as conn:
            conn.execute(
                "INSERT INTO runs(id, kind, started_at, actor, file_name, file_sha256, config_hash, engine_version, rule_set_version, rule_set_hash, params_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, kind, _now(), self.actor, file_name, file_sha256, config_hash, ENGINE_VERSION, rule_set_version(), rule_set_hash(), canonical_json(params or {})),
            )
            self._append(conn, "run.started", object_type="run", object_id=run_id,
                         details={"kind": kind, "file_sha256": file_sha256, "config_hash": config_hash})
        return run_id

    def finish_run(self, run_id: str, *, status: str, summary: dict[str, Any] | None = None) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE runs SET finished_at=?, status=?, summary_json=? WHERE id=?", (_now(), status, canonical_json(summary or {}), run_id))
            self._append(conn, "run.finished", object_type="run", object_id=run_id, details={"status": status, "summary": summary or {}})

    def record_check(self, result: CheckResult, *, kind: str = "check", run_id: str | None = None, params: dict[str, Any] | None = None) -> str:
        """Store a check result: run row, findings, and a chained event with the findings digest."""
        run = result.run
        if run_id is None:
            run_id = self.start_run(kind, file_name=run.file_name, file_sha256=run.file_sha256, config_hash=run.config_hash, params=params)
        rows = []
        for f in result.findings:
            detail = f.model_dump_json()
            rows.append((run_id, f.id or f.fingerprint, f.fingerprint, f.rule_id, f.severity.value, f.certainty.value, f.sheet,
                         ",".join(f.cells), None if f.difference is None else str(f.difference), f.fix_kind.value, "Open", detail))
        digest = _findings_digest([(r[1], r[11]) for r in rows])
        counts = result.counts()
        with self._tx() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO findings(run_id, finding_id, fingerprint, rule_id, severity, certainty, sheet, cells, difference, fix_kind, status, detail_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            conn.execute(
                "UPDATE runs SET finished_at=?, status=?, summary_json=?, result_json=?, engine_version=?, rule_set_version=?, rule_set_hash=? WHERE id=?",
                (_now(), "completed", canonical_json({"counts": counts, "by_rule": result.by_rule()}), result.model_dump_json(),
                 run.engine_version, run.rule_set_version, run.rule_set_hash, run_id),
            )
            self._append(conn, "check.recorded", object_type="run", object_id=run_id,
                         details={"findings": len(rows), "findings_digest": digest, "counts": counts, "file_sha256": run.file_sha256})
        if self.verbose:
            for f in result.findings:
                log.debug("finding %s %s %s!%s printed=%s expected=%s", f.id, f.rule_id, f.sheet, ",".join(f.cells), f.printed, f.expected)
        return run_id

    def record_plan(self, plan: FixPlan, *, run_id: str | None = None) -> str:
        plan_id = plan.id or ("P-" + uuid.uuid4().hex[:12])
        edits = [
            (plan_id, e.id, e.sheet, e.cell, e.kind.value, e.group.value, e.old_formula, e.old_value, e.new_formula, e.new_value, e.reason)
            for e in plan.edits
        ]
        with self._tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO plans(id, run_id, plan_sha256, source_sha256, policy, created_at, edit_count, plan_json) VALUES (?,?,?,?,?,?,?,?)",
                (plan_id, run_id, plan.plan_sha256, plan.source_sha256, plan.policy, _now(), len(plan.edits), plan.model_dump_json()),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO edits(plan_id, edit_id, sheet, cell, kind, grp, old_formula, old_value, new_formula, new_value, reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                edits,
            )
            self._append(conn, "plan.recorded", object_type="plan", object_id=plan_id,
                         details={"run_id": run_id, "plan_sha256": plan.plan_sha256, "edits": len(edits), "edits_digest": _edits_digest([e[1:] for e in edits])})
        return plan_id

    def record_approval(self, plan_id: str, approver: str, plan_sha256: str, item_ids: list[str]) -> str:
        """Maker-checker approval bound to the plan hash (control C2)."""
        row = self.conn.execute("SELECT plan_sha256 FROM plans WHERE id=?", (plan_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown plan {plan_id}")
        if row[0] != plan_sha256:
            raise ValueError("approval refused: the plan changed after it was shown to the approver")
        appr_id = "A-" + uuid.uuid4().hex[:12]
        with self._tx() as conn:
            conn.execute("INSERT INTO approvals(id, plan_id, approver, plan_sha256, item_ids, ts) VALUES (?,?,?,?,?,?)",
                         (appr_id, plan_id, approver, plan_sha256, canonical_json(sorted(item_ids)), _now()))
            self._append(conn, "plan.approved", object_type="plan", object_id=plan_id, actor=approver,
                         details={"plan_sha256": plan_sha256, "items": sorted(item_ids)})
        return appr_id

    def record_apply(self, plan_id: str, applied: ApplyResult, *, run_id: str | None = None) -> None:
        ids = set(applied.applied_edit_ids)
        with self._tx() as conn:
            for eid in ids:
                conn.execute("UPDATE edits SET applied=1, out_sha256=? WHERE plan_id=? AND edit_id=?", (applied.sha256, plan_id, eid))
            rows = conn.execute(
                "SELECT edit_id, sheet, cell, kind, grp, old_formula, old_value, new_formula, new_value, reason, applied, out_sha256 FROM edits WHERE plan_id=?",
                (plan_id,),
            ).fetchall()
            self._append(conn, "plan.applied", object_type="plan", object_id=plan_id,
                         details={"run_id": run_id, "ok": applied.ok, "verification_ok": applied.verification.ok, "out_sha256": applied.sha256,
                                  "applied": sorted(ids), "skipped": sorted(applied.skipped_edit_ids), "edits_state_digest": _edits_digest(rows)})

    def record_ai_call(
        self,
        *,
        payload: str | bytes | dict[str, Any] | list[Any],
        provider: str,
        model: str | None,
        region: str | None,
        decision: str,
        retain: str = "hash_only",
        run_id: str | None = None,
        requested_by: str | None = None,
        approved_by: str | None = None,
        stop_reason: str | None = None,
        response: str | dict[str, Any] | None = None,
    ) -> str:
        """Record an AI payload decision/call. The payload text is kept only when ``retain == 'full'``."""
        text = payload.decode("utf-8") if isinstance(payload, bytes) else (payload if isinstance(payload, str) else canonical_json(payload))
        p_hash = sha256_text(text)
        r_text = None if response is None else (response if isinstance(response, str) else canonical_json(response))
        r_hash = None if r_text is None else sha256_text(r_text)
        call_id = "AI-" + uuid.uuid4().hex[:12]
        full = retain == "full"
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO ai_calls(id, run_id, ts, provider, model, region, payload_sha256, payload, retain, requested_by, approved_by, decision, stop_reason, response_sha256, response) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (call_id, run_id, _now(), provider, model, region, p_hash, text if full else None, retain, requested_by or self.actor,
                 approved_by, decision, stop_reason, r_hash, r_text if full else None),
            )
            self._append(conn, "ai.call", object_type="ai_call", object_id=call_id,
                         details={"provider": provider, "model": model, "region": region, "payload_sha256": p_hash,
                                  "decision": decision, "stop_reason": stop_reason, "response_sha256": r_hash, "approved_by": approved_by})
        return call_id

    # ------------------------------------------------------------------ queries
    def runs(self, limit: int = 20, kind: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT id, kind, started_at, finished_at, actor, file_name, file_sha256, status, summary_json FROM runs"
        args: tuple[Any, ...] = ()
        if kind:
            q += " WHERE kind=?"
            args = (kind,)
        q += " ORDER BY started_at DESC LIMIT ?"
        cols = ["id", "kind", "started_at", "finished_at", "actor", "file_name", "file_sha256", "status", "summary"]
        out = []
        for r in self.conn.execute(q, (*args, limit)).fetchall():
            d = dict(zip(cols, r, strict=True))
            d["summary"] = json.loads(d["summary"]) if d["summary"] else {}
            out.append(d)
        return out

    def findings(self, run_id: str) -> list[Finding]:
        rows = self.conn.execute("SELECT detail_json FROM findings WHERE run_id=? ORDER BY finding_id", (run_id,)).fetchall()
        return [Finding.model_validate_json(r[0]) for r in rows]

    def check_result(self, run_id: str) -> CheckResult | None:
        row = self.conn.execute("SELECT result_json FROM runs WHERE id=?", (run_id,)).fetchone()
        return None if row is None or row[0] is None else CheckResult.model_validate_json(row[0])

    def latest_check_run(self) -> str | None:
        row = self.conn.execute("SELECT id FROM runs WHERE result_json IS NOT NULL ORDER BY started_at DESC LIMIT 1").fetchone()
        return None if row is None else str(row[0])

    def events(self, object_id: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT seq, ts, actor, action, object_type, object_id, details_json, event_hash FROM events"
        args: tuple[Any, ...] = ()
        if object_id:
            q += " WHERE object_id=?"
            args = (object_id,)
        cols = ["seq", "ts", "actor", "action", "object_type", "object_id", "details", "event_hash"]
        out = []
        for r in self.conn.execute(q + " ORDER BY seq", args).fetchall():
            d = dict(zip(cols, r, strict=True))
            d["details"] = json.loads(d["details"])
            out.append(d)
        return out

    def head(self) -> str:
        row = self.conn.execute("SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        return row[0] if row else GENESIS

    # ------------------------------------------------------------------ verification
    def verify_chain(self) -> ChainVerification:
        """Recompute the hash chain and the content anchors (findings, plans, edits)."""
        rows = self.conn.execute(
            "SELECT seq, ts, actor, action, object_type, object_id, details_json, prev_hash, event_hash FROM events ORDER BY seq"
        ).fetchall()
        prev = GENESIS
        last_seq = 0
        for seq, ts, actor, action, otype, oid, details_json, prev_hash, ev_hash in rows:
            if seq != last_seq + 1:
                return ChainVerification(False, len(rows), prev, seq, f"event sequence gap before #{seq} (deleted event)")
            last_seq = seq
            if prev_hash != prev:
                return ChainVerification(False, len(rows), prev, seq, f"event #{seq} does not link to the previous event")
            try:
                details = json.loads(details_json)
            except json.JSONDecodeError:
                return ChainVerification(False, len(rows), prev, seq, f"event #{seq} details are not valid JSON")
            event = {"ts": ts, "actor": actor, "action": action, "object_type": otype, "object_id": oid, "details": details}
            if event_digest(prev, event) != ev_hash:
                return ChainVerification(False, len(rows), prev, seq, f"event #{seq} was modified")
            problem = self._check_anchor(action, oid, details)
            if problem:
                return ChainVerification(False, len(rows), prev, seq, problem)
            prev = ev_hash
        return ChainVerification(True, len(rows), prev)

    def _check_anchor(self, action: str, object_id: str, details: dict[str, Any]) -> str | None:
        if action == "check.recorded":
            frows = self.conn.execute("SELECT finding_id, detail_json FROM findings WHERE run_id=?", (object_id,)).fetchall()
            if len(frows) != details.get("findings") or _findings_digest([(r[0], r[1]) for r in frows]) != details.get("findings_digest"):
                return f"findings of run {object_id} were altered after they were recorded"
        elif action == "plan.recorded":
            prow = self.conn.execute("SELECT plan_sha256 FROM plans WHERE id=?", (object_id,)).fetchone()
            if prow is None or prow[0] != details.get("plan_sha256"):
                return f"plan {object_id} was altered after it was recorded"
            erows = self.conn.execute(
                "SELECT edit_id, sheet, cell, kind, grp, old_formula, old_value, new_formula, new_value, reason FROM edits WHERE plan_id=?", (object_id,)
            ).fetchall()
            if _edits_digest(erows) != details.get("edits_digest"):
                return f"edits of plan {object_id} were altered after they were recorded"
        return None
