"""Append-only, tamper-evident audit trail (06 §4).

``event_hash = SHA256(prev_event_hash || canonical(event))``. Appends are
serialised per tenant by row-locking ``audit_head`` (PostgreSQL) or by SQLite's
single-writer transactions, and they commit in the same transaction as the state
change they describe. Denials and failed logins are written in their own
transaction so they persist even though the request fails.

PostgreSQL additionally blocks UPDATE/DELETE/TRUNCATE on ``audit_event`` with a
trigger and the runtime role holds only INSERT/SELECT on it.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsweb.db import utcnow
from fsweb.models import AuditEvent, AuditHead

GENESIS = "0" * 64

# Event classes logged (06 §4). Used by tests and the evidence pack.
EVENT_CLASSES = {
    "auth": ["auth.login", "auth.login_failed", "auth.logout", "auth.mfa_verified", "auth.mfa_failed", "auth.mfa_enrolled", "auth.locked"],
    "denial": ["authz.denied"],
    "files": ["file.uploaded", "file.downloaded"],
    "runs": ["run.created", "run.succeeded", "run.failed"],
    "fixes": ["fixplan.proposed", "fixplan.approved", "fixplan.applied", "edit.applied"],
    "ai": ["ai.payload_approved", "ai.call_sent", "ai.call_completed"],
    "render": ["render.completed", "engagement.released", "engagement.locked", "engagement.unlocked"],
    "users": ["user.invited", "user.disabled", "assignment.created", "role.permissions_changed"],
    "backup": ["backup.recorded", "backup.restore_drill"],
}


def _sanitise(v: Any) -> Any:
    """Make details JSON-stable across backends (no floats/Decimals, str keys)."""
    if isinstance(v, dict):
        return {str(k): _sanitise(x) for k, x in v.items()}
    if isinstance(v, list | tuple | set):
        return [_sanitise(x) for x in v]
    if isinstance(v, bool) or v is None or isinstance(v, int | str):
        return v
    if isinstance(v, float | Decimal):
        return str(v)
    if isinstance(v, datetime):
        return v.astimezone(UTC).isoformat()
    return str(v)


def _ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def payload_of(e: AuditEvent) -> dict[str, Any]:
    return {
        "tenant_id": e.tenant_id,
        "seq": e.seq,
        "ts": _ts(e.ts),
        "user_id": e.user_id,
        "role_at_time": e.role_at_time,
        "ip": e.ip,
        "user_agent": e.user_agent,
        "client_id": e.client_id,
        "engagement_id": e.engagement_id,
        "object_type": e.object_type,
        "object_id": e.object_id,
        "action": e.action,
        "outcome": e.outcome,
        "before_hash": e.before_hash,
        "after_hash": e.after_hash,
        "details": e.details or {},
        "correlation_id": e.correlation_id,
    }


def canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(prev: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256((prev + canonical(payload)).encode("utf-8")).hexdigest()


def sha256_json(obj: Any) -> str:
    return hashlib.sha256(canonical(_sanitise(obj)).encode("utf-8")).hexdigest()


@dataclass
class Actor:
    """Who is acting (copied onto every event)."""

    tenant_id: str
    user_id: str | None = None
    roles: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    correlation_id: str | None = None


def ensure_head(session: Session, tenant_id: str) -> None:
    if session.get(AuditHead, tenant_id) is None:
        session.add(AuditHead(tenant_id=tenant_id, seq=0, event_hash=GENESIS))
        session.flush()


def append(
    session: Session,
    actor: Actor,
    action: str,
    *,
    client_id: str | None = None,
    engagement_id: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    before: Any = None,
    after: Any = None,
    details: dict[str, Any] | None = None,
    outcome: str = "ok",
) -> AuditEvent:
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        head = session.execute(
            select(AuditHead).where(AuditHead.tenant_id == actor.tenant_id).with_for_update()
        ).scalar_one_or_none()
    else:
        head = session.get(AuditHead, actor.tenant_id)
    if head is None:
        head = AuditHead(tenant_id=actor.tenant_id, seq=0, event_hash=GENESIS)
        session.add(head)
        session.flush()
    ev = AuditEvent(
        tenant_id=actor.tenant_id,
        seq=head.seq + 1,
        ts=utcnow(),
        user_id=actor.user_id,
        role_at_time=actor.roles,
        ip=actor.ip,
        user_agent=(actor.user_agent or "")[:300] or None,
        client_id=client_id,
        engagement_id=engagement_id,
        object_type=object_type,
        object_id=object_id,
        action=action,
        outcome=outcome,
        before_hash=None if before is None else (before if isinstance(before, str) and len(before) == 64 else sha256_json(before)),
        after_hash=None if after is None else (after if isinstance(after, str) and len(after) == 64 else sha256_json(after)),
        details=_sanitise(details or {}),
        correlation_id=actor.correlation_id,
        prev_event_hash=head.event_hash,
        created_by=actor.user_id,
    )
    # Microsecond precision survives every backend, so the hash is reproducible.
    ev.ts = ev.ts.replace(microsecond=ev.ts.microsecond)
    ev.event_hash = compute_hash(ev.prev_event_hash, payload_of(ev))
    head.seq = ev.seq
    head.event_hash = ev.event_hash
    head.updated_at = utcnow()
    session.add(ev)
    session.flush()
    return ev


@dataclass
class ChainReport:
    ok: bool
    tenant_id: str
    events: int = 0
    head_seq: int = 0
    head_hash: str = GENESIS
    first_bad_seq: int | None = None
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tenant_id": self.tenant_id,
            "events": self.events,
            "head_seq": self.head_seq,
            "head_hash": self.head_hash,
            "first_bad_seq": self.first_bad_seq,
            "problems": self.problems[:50],
        }


def verify_chain(session: Session, tenant_id: str) -> ChainReport:
    rep = ChainReport(ok=True, tenant_id=tenant_id)
    prev = GENESIS
    expected_seq = 1
    stmt = select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.seq).execution_options(yield_per=1000)
    for ev in session.execute(stmt).scalars():
        rep.events += 1
        bad = []
        if ev.seq != expected_seq:
            bad.append(f"seq {ev.seq}: expected {expected_seq} (gap or reorder)")
        if ev.prev_event_hash != prev:
            bad.append(f"seq {ev.seq}: prev_event_hash does not link to the previous event")
        recomputed = compute_hash(ev.prev_event_hash, payload_of(ev))
        if recomputed != ev.event_hash:
            bad.append(f"seq {ev.seq}: content does not match event_hash (tampered)")
        if bad:
            rep.ok = False
            rep.problems.extend(bad)
            if rep.first_bad_seq is None:
                rep.first_bad_seq = ev.seq
        prev = ev.event_hash
        expected_seq = ev.seq + 1
        rep.head_seq = ev.seq
        rep.head_hash = ev.event_hash
    head = session.get(AuditHead, tenant_id)
    if head is not None and (head.seq != rep.head_seq or head.event_hash != rep.head_hash):
        rep.ok = False
        rep.problems.append("audit_head does not match the last event (events deleted or head altered)")
    return rep


def count_events(session: Session, tenant_id: str, action: str | None = None) -> int:
    stmt = select(func.count()).select_from(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    return int(session.execute(stmt).scalar_one())


def write_head_file(directory: str | Path, report: ChainReport, day: str | None = None) -> Path:
    """Write-once file with the day's head hash (06 §4). Refuses to overwrite."""
    d = Path(directory) / report.tenant_id
    d.mkdir(parents=True, exist_ok=True)
    day = day or datetime.now(UTC).strftime("%Y-%m-%d")
    path = d / f"{day}.json"
    body = json.dumps({"day": day, **report.as_dict(), "written_at": _ts(utcnow())}, indent=2, sort_keys=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o444)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:  # identical bytes on every platform
        fh.write(body + "\n")
    return path
