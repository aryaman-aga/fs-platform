"""Job handlers: each run kind calls the engine through ``fsweb.engine`` in a
per-job sandbox directory that is wiped afterwards (04 §6)."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fscore.config import AIConfig, EngagementConfig
from fscore.models import CheckResult, FixPlan, TextEdit
from fsweb import audit, metrics
from fsweb.db import RLSContext, utcnow
from fsweb.engine import EngineUnavailable
from fsweb.jobs.queue import add_event, finish
from fsweb.models import (
    AIRequest,
    Artefact,
    BillingUsage,
    EditRow,
    Engagement,
    FileVersion,
    FindingRow,
    FixPlanRow,
    Job,
    NarrativeEdit,
    Run,
    RunEvent,
    Tenant,
)
from fsweb.models import (
    File as FileModel,
)
from fsweb.security.crypto import hmac_hex
from fsweb.services import engagements as eng_svc
from fsweb.services import rules as rules_svc
from fsweb.services import workflow
from fsweb.services.common import ensure_file, store_artefact, store_version
from fsweb.state import AppState

log = logging.getLogger("fsweb.worker")


class JobFailed(RuntimeError):
    """A deterministic failure: do not retry."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass
class JobContext:
    state: AppState
    job: Job
    run: Run
    sandbox: Path
    _last: tuple[str, float] = ("", -1.0)
    outputs: dict[str, Any] = field(default_factory=dict)
    session: Session | None = None

    def progress(self, stage: str, pct: float) -> None:
        pct = max(0.0, min(1.0, float(pct)))
        last_stage, last_pct = self._last
        if not (stage != last_stage or pct - last_pct >= 0.05 or pct >= 1.0):
            return
        self._last = (stage, pct)
        s = self.session
        if self.state.db.is_postgres or s is None:
            add_event(self.state.db, self.run, stage, pct)  # own transaction: visible to SSE at once
            return
        # SQLite has a single writer: record the event through the job's own session, and
        # commit it only when nothing else is pending (handlers commit before engine calls).
        s.add(RunEvent(tenant_id=self.run.tenant_id, client_id=self.run.client_id, run_id=self.run.id, kind="progress", message=stage[:300], pct=pct))
        pending = [o for o in s.new if not isinstance(o, RunEvent)] or s.dirty or s.deleted
        if not pending:
            s.commit()

    def actor(self) -> audit.Actor:
        return audit.Actor(self.run.tenant_id, self.run.created_by, "system:worker", None, f"fsweb-worker/{self.state.component}",
                           self.run.correlation_id)

    def rls(self) -> RLSContext:
        return RLSContext(self.run.tenant_id, self.run.created_by, "system")

    def fetch(self, db: Session, version_id: str, name: str | None = None) -> Path:
        v = db.get(FileVersion, version_id)
        if v is None:
            raise JobFailed("input_missing", f"input version {version_id} not found")
        dest = self.sandbox / (name or f"{v.id}{Path(v.original_name).suffix}")
        self.state.storage.download_to(v.object_key, dest)
        if hashlib.sha256(dest.read_bytes()).hexdigest() != v.sha256:
            raise JobFailed("integrity", f"stored object for version {v.id} does not match its SHA-256")
        return dest


def _bump_usage(db: Session, run: Run, *, renders: int = 0, ai_calls: int = 0) -> None:
    month = utcnow().strftime("%Y-%m")
    row = db.execute(select(BillingUsage).where(BillingUsage.client_id == run.client_id, BillingUsage.month == month)).scalar_one_or_none()
    if row is None:
        row = BillingUsage(tenant_id=run.tenant_id, client_id=run.client_id, month=month, engagements=0, runs=0, renders=0, ai_calls=0)
        db.add(row)
        db.flush()
    row.runs += 1
    row.renders += renders
    row.ai_calls += ai_calls
    row.engagements = int(db.execute(select(func.count(func.distinct(Run.engagement_id))).where(
        Run.client_id == run.client_id, Run.created_at >= utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0))).scalar_one())


def _config(db: Session, eng: Engagement, ctx: JobContext | None = None) -> EngagementConfig:
    """The engagement configuration; with a job context, the signatory master entered in the platform is
    written to the job sandbox and handed to the engine (control C8)."""
    cfg = eng_svc.config_for(db, eng)
    if ctx is not None:
        master = eng_svc.signatory_master(db, ctx.state.cipher, eng)
        if master:
            path = ctx.sandbox / "signatories.yaml"
            path.write_text(yaml.safe_dump({"signatories": master}, allow_unicode=True, sort_keys=False), encoding="utf-8")
            cfg = cfg.model_copy(update={"signatories": str(path)})
    return cfg


# =================================================================== handlers


def handle_check(ctx: JobContext, db: Session, run: Run, eng: Engagement) -> dict[str, Any]:
    st = ctx.state
    cfg = _config(db, eng, ctx)
    wb = ctx.fetch(db, run.input_version_ids[0], "workbook" + Path(db.get(FileVersion, run.input_version_ids[0]).original_name).suffix)  # type: ignore[union-attr]
    pdf = None
    pdf_vid = (run.params_json or {}).get("pdf_version_id")
    if pdf_vid:
        pdf = ctx.fetch(db, str(pdf_vid), "printed.pdf")
    prev = db.execute(select(Run).where(Run.engagement_id == run.engagement_id, Run.kind == "check", Run.status == "succeeded", Run.id != run.id)
                      .order_by(Run.created_at.desc())).scalars().first()
    prev_states = (prev.result_json or {}).get("sheet_states") if prev else None
    db.commit()  # no transaction is held while the engine runs
    ctx.progress("checking", 0.05)
    result = st.engine.check_workbook(wb, cfg, pdf_path=pdf, progress=ctx.progress, previous_sheet_states=prev_states or None)
    ctx.progress("storing findings", 0.9)
    _store_findings(db, run, result)
    _carry_forward_statuses(db, run)
    run.engine_version = result.run.engine_version
    run.rule_set_version = result.run.rule_set_version
    run.rule_set_hash = result.run.rule_set_hash
    run.config_hash = result.run.config_hash
    rules_svc.record_rule_versions(db, run, st.engine.list_rules(), result.run.rule_set_version, result.run.rule_set_hash)
    store_artefact(db, st.storage, run, "result", "check_result.json", result.model_dump_json().encode("utf-8"), "application/json")
    reg = ctx.sandbox / "exceptions_register.xlsx"
    html = ctx.sandbox / "report.html"
    try:
        st.engine.write_register(result, reg)
        store_artefact(db, st.storage, run, "register", "Exceptions register.xlsx", reg.read_bytes())
        st.engine.write_html_report(result, html)
        store_artefact(db, st.storage, run, "html", "FS-Check report.html", html.read_bytes(), "text/html")
    except EngineUnavailable as exc:
        log.warning("report writers unavailable: %s", exc)
    workflow.advance(db, eng, {"DATA_RECEIVED"}, "CHECKED", ctx.actor(), "check run completed")
    return _result_summary(result)


def _store_findings(db: Session, run: Run, result: CheckResult) -> None:
    for f in result.findings:
        db.add(FindingRow(
            tenant_id=run.tenant_id, client_id=run.client_id, engagement_id=run.engagement_id, run_id=run.id, fid=f.id or "",
            fingerprint=f.fingerprint or f.compute_fingerprint(), rule_id=f.rule_id, rule_version=f.rule_version, family=f.family,
            severity=f.severity.value, certainty=f.certainty.value, category=f.category, sheet=f.sheet, cells=list(f.cells),
            caption=f.caption, printed=f.printed, expected=f.expected, diff=None if f.difference is None else str(f.difference),
            period=f.period, evidence_json=[e.model_dump(mode="json") for e in f.evidence], explanation=f.explanation,
            suggested_fix=f.suggested_fix, fix_kind=f.fix_kind.value, created_by=run.created_by,
        ))


def _result_summary(result: CheckResult) -> dict[str, Any]:
    counts = result.counts()
    return {
        "counts": counts,
        "high": counts.get("High", 0),
        "by_rule": result.by_rule(),
        "key_figures": [k.model_dump(mode="json") for k in result.key_figures],
        "statements": result.statements,
        "rpt_recon": result.rpt_recon,
        "narrative": result.narrative,
        "xref_map": [x.model_dump(mode="json") for x in result.xref_map][:500],
        "text_proposals": [t.model_dump(mode="json") for t in getattr(result, "text_proposals", [])],
        "sheet_states": getattr(result, "sheet_states", {}) or {},
        "selftest": result.run.selftest.model_dump(mode="json"),
        "warnings": result.run.warnings,
        "pdf": None if result.pdf is None else {"misses": len(result.pdf.misses), "stale": result.pdf.stale, "pages": result.pdf.pages},
        "file_sha256": result.run.file_sha256,
    }


def handle_pdf_check(ctx: JobContext, db: Session, run: Run, eng: Engagement) -> dict[str, Any]:
    """PDF-04 PDF-only mode: findings only, lower certainty, never workbook fixes."""
    st = ctx.state
    pdf = ctx.fetch(db, run.input_version_ids[0], "statements.pdf")
    cfg = _config(db, eng, ctx)
    db.commit()
    ctx.progress("checking PDF", 0.1)
    result = st.engine.check_pdf_only(pdf, cfg)
    _store_findings(db, run, result)
    run.engine_version = result.run.engine_version
    run.rule_set_version = result.run.rule_set_version
    run.rule_set_hash = result.run.rule_set_hash
    run.config_hash = result.run.config_hash
    store_artefact(db, st.storage, run, "result", "pdf_check_result.json", result.model_dump_json().encode("utf-8"), "application/json")
    return {**_result_summary(result), "mode": "pdf_only"}


def _carry_forward_statuses(db: Session, run: Run) -> None:
    """Keep reviewer decisions (Not an issue / Accepted) for the same finding across runs."""
    prev = db.execute(select(Run).where(Run.engagement_id == run.engagement_id, Run.kind == "check", Run.status == "succeeded", Run.id != run.id)
                      .order_by(Run.created_at.desc())).scalars().first()
    if prev is None:
        return
    db.flush()
    decided = {f.fingerprint: f for f in db.execute(select(FindingRow).where(FindingRow.run_id == prev.id, FindingRow.status.in_(["Not an issue", "Accepted"]))).scalars()}
    if not decided:
        return
    for f in db.execute(select(FindingRow).where(FindingRow.run_id == run.id)).scalars():
        old = decided.get(f.fingerprint)
        if old is not None:
            f.status = old.status
            f.status_reason = (old.status_reason or "") + " (carried forward)"
            f.shared_with_client = old.shared_with_client


def _check_result_for(db: Session, st: AppState, check_run_id: str) -> CheckResult | None:
    art = db.execute(select(Artefact).where(Artefact.run_id == check_run_id, Artefact.kind == "result")).scalars().first()
    if art is None:
        return None
    return CheckResult.model_validate_json(st.storage.get_bytes(art.object_key))


def handle_plan(ctx: JobContext, db: Session, run: Run, eng: Engagement) -> dict[str, Any]:
    st = ctx.state
    fp = db.get(FixPlanRow, (run.params_json or {}).get("fix_plan_id"))
    if fp is None:
        raise JobFailed("plan_missing", "fix plan row not found")
    cfg = _config(db, eng, ctx)
    wb = ctx.fetch(db, fp.base_version_id, "workbook.xlsx")
    cr = _check_result_for(db, st, fp.run_id)
    db.commit()
    plan = st.engine.propose_fixes(wb, cfg, check_result=cr, progress=ctx.progress)
    fp.plan_json = plan.model_dump(mode="json")
    fp.plan_sha256 = plan.plan_sha256
    fp.has_balancing = any(e.group.value == "balancing" for e in plan.edits)
    fp.status = "proposed"
    for e in plan.edits:
        db.add(EditRow(tenant_id=run.tenant_id, client_id=run.client_id, fix_plan_id=fp.id, eid=e.id, sheet=e.sheet, cell=e.cell, kind=e.kind.value,
                       grp=e.group.value, fix_kind=e.fix_kind.value, old_value=e.old_value, new_value=e.new_value, old_formula=e.old_formula,
                       new_formula=e.new_formula, reason=e.reason, created_by=run.created_by))
    store_artefact(db, st.storage, run, "plan", "fix_plan.json", plan.model_dump_json(indent=2).encode(), "application/json")
    audit.append(db, ctx.actor(), "fixplan.proposed", client_id=eng.client_id, engagement_id=eng.id, object_type="fix_plan", object_id=fp.id,
                 after=fp.plan_sha256, details={"edits": len(plan.edits), "balancing": fp.has_balancing, "converged": plan.converged})
    workflow.advance(db, eng, {"CHECKED"}, "FIX_PLANNED", ctx.actor(), "fix plan proposed")
    return {"fix_plan_id": fp.id, "plan_sha256": fp.plan_sha256, "edits": len(plan.edits), "balancing": [b.model_dump(mode="json") for b in plan.balancing],
            "converged": plan.converged, "unresolved": plan.unresolved}


def handle_fix(ctx: JobContext, db: Session, run: Run, eng: Engagement) -> dict[str, Any]:
    st = ctx.state
    fp = db.get(FixPlanRow, (run.params_json or {}).get("fix_plan_id"))
    if fp is None:
        raise JobFailed("plan_missing", "fix plan row not found")
    plan = FixPlan.model_validate(fp.plan_json)
    if fp.status not in {"approved", "applying"} or fp.approved_plan_sha256 != plan.plan_sha256:
        raise JobFailed("plan_not_approved", "the plan changed after approval or is not approved (control C2)")
    if fp.has_balancing and not fp.balancing_approved_by:
        raise JobFailed("balancing_not_approved", "balancing adjustments need approval")
    for b in plan.balancing:  # control C3, re-checked at apply time
        if b.status == "applied" and abs(b.adjustment) > _threshold(db, eng):
            raise JobFailed("balancing_too_large", f"balancing adjustment {b.adjustment} exceeds the rounding threshold (C3)")
    cfg = _config(db, eng, ctx)
    base = db.get(FileVersion, fp.base_version_id)
    assert base is not None
    wb = ctx.fetch(db, base.id, "source" + Path(base.original_name).suffix)
    out = ctx.sandbox / ("corrected" + Path(base.original_name).suffix)
    approved = set(fp.approved_edit_ids or [])
    db.commit()
    res = st.engine.apply_fixes(wb, plan, out, approved_edit_ids=approved, config=cfg)
    store_artefact(db, st.storage, run, "verification", "verification.json", res.model_dump_json(indent=2).encode(), "application/json")
    if not res.ok or not res.verification.ok or res.verification.remaining_cast_plug:
        fp.status = "failed"
        raise JobFailed("verification_failed", res.message or "post-write verification failed (control C5)",
                        {"verification": res.verification.model_dump(mode="json")})
    f = db.get(FileModel, base.file_id)
    assert f is not None
    nv = store_version(db, st.storage, eng, f, out.read_bytes(), original_name=base.original_name.replace(".xlsx", " - casting corrected.xlsx") if base.original_name.endswith(".xlsx") else base.original_name,
                       created_by=fp.approved_by, parent_version_id=base.id, produced_by_run_id=run.id,
                       change_summary={"source": "fix", "fix_plan_id": fp.id, "plan_sha256": fp.plan_sha256, "edits_applied": len(res.applied_edit_ids),
                                       "printed_moves": len(res.printed_moves)})
    applied = set(res.applied_edit_ids)
    for er in db.execute(select(EditRow).where(EditRow.fix_plan_id == fp.id)).scalars():
        if er.eid in applied:
            er.applied = True
            er.approved = er.approved or er.fix_kind == "auto" or er.eid in approved
            er.applied_version_id = nv.id
    fp.status = "applied"
    run.output_version_id = nv.id
    audit.append(db, ctx.actor(), "fixplan.applied", client_id=eng.client_id, engagement_id=eng.id, object_type="fix_plan", object_id=fp.id,
                 before=base.sha256, after=nv.sha256, details={"edits": sorted(applied), "new_version": nv.version_no})
    workflow.advance(db, eng, {"FIX_APPROVED"}, "FIXED_VERIFIED", ctx.actor(), "corrected copy written and verified")
    return {"output_version_id": nv.id, "applied": sorted(applied), "skipped": res.skipped_edit_ids,
            "printed_moves": [m.model_dump(mode="json") for m in res.printed_moves][:500], "verification": res.verification.model_dump(mode="json")}


def _threshold(db: Session, eng: Engagement) -> Any:
    return _config(db, eng).policy.balancing_threshold


def handle_narrative(ctx: JobContext, db: Session, run: Run, eng: Engagement) -> dict[str, Any]:
    st = ctx.state
    ids = list((run.params_json or {}).get("edit_ids", []))
    edits = [db.get(NarrativeEdit, i) for i in ids]
    if not edits or any(e is None or e.status != "approved" for e in edits):
        raise JobFailed("not_approved", "narrative edits must be approved before they are applied")
    base = workflow.latest_workbook_version(db, eng)
    if base is None:
        raise JobFailed("no_workbook", "no workbook")
    wb = ctx.fetch(db, base.id, "source" + Path(base.original_name).suffix)
    out = ctx.sandbox / ("narrative" + Path(base.original_name).suffix)
    text_edits = [TextEdit(sheet=e.sheet, cell=e.cell, new_text=e.new_text, reason=e.reason) for e in edits if e is not None]
    cfg = _config(db, eng, ctx)
    db.commit()
    res = st.engine.apply_text_edits(wb, text_edits, out, cfg)
    if not res.ok:
        raise JobFailed("narrative_failed", res.message or "typed-text edit failed")
    f = db.get(FileModel, base.file_id)
    assert f is not None
    nv = store_version(db, st.storage, eng, f, out.read_bytes(), original_name=base.original_name, created_by=run.created_by, parent_version_id=base.id,
                       produced_by_run_id=run.id, change_summary={"source": "narrative", "edits": ids})
    for e in edits:
        assert e is not None
        e.status = "applied"
        e.applied_run_id = run.id
    run.output_version_id = nv.id
    audit.append(db, ctx.actor(), "narrative.applied", client_id=eng.client_id, engagement_id=eng.id, object_type="file_version", object_id=nv.id,
                 before=base.sha256, after=nv.sha256, details={"edits": ids})
    eng_svc.data_changed(db, eng, ctx.actor(), nv, reason="narrative text edited")
    return {"output_version_id": nv.id, "edits": ids}


FINDING_CLOSED = ("Not an issue", "Resolved")  # the statuses workflow.final_pdf_gates does not count as open


def final_check_result(st: AppState, db: Session, eng: Engagement, base: FileVersion) -> CheckResult:
    """The check run a FINAL PDF stands on (02 §10A): the latest check of this workbook version, without the
    findings people closed in the platform, so the engine's own final gate (control C7) agrees with the gate the
    request passed (``workflow.final_pdf_gates``)."""
    check = workflow.latest_run(db, eng, "check", version_id=base.id)
    result = _check_result_for(db, st, check.id) if check is not None else None
    if check is None or result is None:
        raise JobFailed("not_checked", "run the checks on this workbook version before rendering a FINAL PDF")
    closed = set(db.execute(select(FindingRow.fid).where(FindingRow.run_id == check.id, FindingRow.status.in_(FINDING_CLOSED))).scalars())
    return result.model_copy(update={"findings": [f for f in result.findings if (f.id or "") not in closed]})


def handle_render(ctx: JobContext, db: Session, run: Run, eng: Engagement) -> dict[str, Any]:
    st = ctx.state
    params = run.params_json or {}
    mode = str(params.get("mode", "draft"))
    base = db.get(FileVersion, run.input_version_ids[0])
    assert base is not None
    wb = ctx.fetch(db, base.id, "workbook" + Path(base.original_name).suffix)
    out = ctx.sandbox / "statements.pdf"
    cfg = _config(db, eng, ctx)
    check_result = final_check_result(st, db, eng, base) if mode == "final" else None
    db.commit()
    res = st.engine.render_pdf(wb, out, mode=mode, config=cfg, run_id=run.id, check_result=check_result,
                               engine=str(params.get("engine", "auto")))
    if not res.ok or not res.pdf_path:
        raise JobFailed("render_failed", res.message or "render failed")
    return store_render(ctx, db, run, eng, base, Path(res.pdf_path).read_bytes(), mode, res.engine, res.engine_version,
                        res.verification.model_dump(mode="json") if res.verification else None, res.pages)


def store_render(ctx: JobContext | None, db: Session, run: Run, eng: Engagement, base: FileVersion, pdf: bytes, mode: str, engine: str,
                 engine_version: str, verification: dict[str, Any] | None, pages: int, state: AppState | None = None) -> dict[str, Any]:
    st = state or (ctx.state if ctx else None)
    assert st is not None
    f = ensure_file(db, eng, "rendered_pdf", f"{mode.title()} statements PDF", run.created_by)
    stem = Path(base.original_name).stem
    nv = store_version(db, st.storage, eng, f, pdf, original_name=f"{stem}_{mode}_v{base.version_no}_{utcnow().strftime('%Y%m%d-%H%M')}.pdf",
                       created_by=run.created_by, parent_version_id=None, produced_by_run_id=run.id,
                       change_summary={"source": "render", "mode": mode, "workbook_version_id": base.id, "engine": engine})
    run.output_version_id = nv.id
    misses = len((verification or {}).get("misses", [])) if verification is not None else -1
    actor = audit.Actor(run.tenant_id, run.created_by, "system:render", None, "fsweb-render", run.correlation_id)
    audit.append(db, actor, "render.completed", client_id=eng.client_id, engagement_id=eng.id, object_type="file_version", object_id=nv.id,
                 after=nv.sha256, details={"mode": mode, "engine": engine, "engine_version": engine_version, "pages": pages, "misses": misses})
    _bump_usage(db, run, renders=1)
    return {"mode": mode, "engine": engine, "engine_version": engine_version, "pages": pages, "pdf_version_id": nv.id, "pdf_sha256": nv.sha256,
            "misses": misses, "verification": verification}


def handle_diff(ctx: JobContext, db: Session, run: Run, eng: Engagement) -> dict[str, Any]:
    st = ctx.state
    a = ctx.fetch(db, run.input_version_ids[0], "a.xlsx")
    b = ctx.fetch(db, run.input_version_ids[1], "b.xlsx")
    cfg = _config(db, eng, ctx)
    db.commit()
    d = st.engine.diff_workbooks(a, b, cfg)
    store_artefact(db, st.storage, run, "diff", "diff.json", d.model_dump_json(indent=2).encode(), "application/json")
    return {"changes": len(d.changes), "printed_moves": len(d.printed_moves), "hidden_changes": len(d.hidden_changes)}


def handle_prepare(ctx: JobContext, db: Session, run: Run, eng: Engagement) -> dict[str, Any]:
    """Module P through the engine facade (``fscore.api.prepare_statements`` when available)."""
    st = ctx.state
    import fscore.api as api

    prep: Callable[..., Any] | None = getattr(api, "prepare_statements", None)
    if prep is None:
        raise JobFailed("engine_unavailable", "Module P (prepare_statements) is not available in this engine build")
    tb = ctx.fetch(db, run.input_version_ids[0], "trial_balance" + Path(db.get(FileVersion, run.input_version_ids[0]).original_name).suffix)  # type: ignore[union-attr]
    out = ctx.sandbox / "prepared.xlsx"
    params = dict(run.params_json or {})
    cfg = _config(db, eng, ctx)
    db.commit()
    result = prep(tb, out, config=cfg, **{k: v for k, v in params.items() if k not in {"auto", "reason"}})
    if not out.exists():
        raise JobFailed("prepare_failed", "preparation produced no workbook")
    f = workflow.workbook_file(db, eng) or ensure_file(db, eng, "workbook", "Statements workbook", run.created_by)
    latest = workflow.latest_version(db, f.id)
    nv = store_version(db, st.storage, eng, f, out.read_bytes(), original_name="Prepared statements.xlsx", created_by=run.created_by,
                       parent_version_id=latest.id if latest else None, produced_by_run_id=run.id, change_summary={"source": "prepare"})
    run.output_version_id = nv.id
    workflow.advance(db, eng, {"DRAFT_SETUP"}, "DATA_RECEIVED", ctx.actor(), "statements prepared")
    eng_svc.data_changed(db, eng, ctx.actor(), nv, reason="statements re-prepared")
    summary = result.model_dump(mode="json") if hasattr(result, "model_dump") else {}
    return {"output_version_id": nv.id, "prepare": summary}


def handle_ai(ctx: JobContext, db: Session, run: Run, eng: Engagement) -> dict[str, Any]:
    """Only the ai-gateway component may call out (C9, 05 §4)."""
    st = ctx.state
    if st.component != "ai-gateway":
        raise JobFailed("egress_denied", "AI calls run only in the ai-gateway component")
    req = db.get(AIRequest, (run.params_json or {}).get("ai_request_id"))
    if req is None:
        raise JobFailed("ai_request_missing", "AI request not found")
    raw = st.storage.get_bytes(req.payload_object_key)
    sha = hashlib.sha256(raw).hexdigest()
    expected_token = hmac_hex(st.settings.secret_key, "ai-approval", req.id, req.payload_sha256)
    token_ok = req.approval_token_hash == hashlib.sha256(expected_token.encode()).hexdigest()
    if sha != req.payload_sha256 or not token_ok or req.status not in {"approved", "queued"}:
        req.status = "rejected"
        req.error = "payload hash does not match the approval" if sha != req.payload_sha256 else "approval token invalid"
        audit.append(db, ctx.actor(), "ai.call_refused", client_id=eng.client_id, engagement_id=eng.id, object_type="ai_request", object_id=req.id,
                     outcome="denied", details={"approved_sha256": req.payload_sha256, "actual_sha256": sha})
        db.commit()  # the refusal is recorded even though the job fails
        raise JobFailed("approval_mismatch", req.error)
    if not eng.ai_enabled:
        req.status = "rejected"
        req.error = "AI is disabled for this engagement"
        db.commit()
        raise JobFailed("ai_disabled", req.error)
    tenant = db.get(Tenant, eng.tenant_id)
    budget = float((tenant.settings_json or {}).get("ai_monthly_budget_usd", st.settings.ai_monthly_budget_usd)) if tenant else st.settings.ai_monthly_budget_usd
    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = float(db.execute(select(func.coalesce(func.sum(AIRequest.cost_usd), 0.0)).where(AIRequest.tenant_id == eng.tenant_id, AIRequest.completed_at >= month_start)).scalar_one())
    if spent >= budget:
        req.status = "rejected"
        req.error = "monthly AI budget exhausted (E-AI-04)"
        db.commit()
        raise JobFailed("budget", req.error)
    cfg = AIConfig.model_validate({"provider": req.provider, "model": req.model, "region": req.region})
    payload = json.loads(raw)
    req.status = "sent"
    req.sent_at = utcnow()
    audit.append(db, ctx.actor(), "ai.call_sent", client_id=eng.client_id, engagement_id=eng.id, object_type="ai_request", object_id=req.id,
                 details={"provider": req.provider, "model": req.model, "region": req.region, "prompt_version": req.prompt_version, "payload_sha256": sha})
    db.commit()  # the 'sent' record survives even if the provider call fails
    t0 = time.monotonic()
    mapping = None
    mkey = req.payload_object_key.replace("/payload-", "/mapping-")
    if mkey != req.payload_object_key and st.storage.exists(mkey):
        mapping = json.loads(st.storage.get_bytes(mkey))  # token map / preview: stays inside the firm, never sent
    resp = st.engine.ai_send(payload, cfg, provider=req.provider, mapping=mapping, requester=req.requested_by, approver=req.approved_by)
    elapsed = time.monotonic() - t0
    body = json.dumps(resp, sort_keys=True, default=str).encode()
    a = store_artefact(db, st.storage, run, "response", "ai_response.json", body, "application/json")
    usage = resp.get("usage") or {}
    req.response_object_key = a.object_key
    req.stop_reason = str(resp.get("stop_reason") or "")[:30] or None
    req.input_tokens = int(usage.get("input_tokens", 0))
    req.output_tokens = int(usage.get("output_tokens", 0))
    req.cost_usd = round(req.input_tokens * 5e-6 + req.output_tokens * 25e-6, 6)
    req.status = "completed" if req.stop_reason not in {"refusal"} else "refused"
    req.completed_at = utcnow()
    audit.append(db, ctx.actor(), "ai.call_completed", client_id=eng.client_id, engagement_id=eng.id, object_type="ai_request", object_id=req.id,
                 details={"stop_reason": req.stop_reason, "input_tokens": req.input_tokens, "output_tokens": req.output_tokens, "seconds": round(elapsed, 2)})
    _bump_usage(db, run, ai_calls=1)
    return {"ai_request_id": req.id, "stop_reason": req.stop_reason, "status": req.status}


HANDLERS: dict[str, Callable[[JobContext, Session, Run, Engagement], dict[str, Any]]] = {
    "check": handle_check,
    "plan": handle_plan,
    "fix": handle_fix,
    "narrative": handle_narrative,
    "render": handle_render,
    "diff": handle_diff,
    "prepare": handle_prepare,
    "ai_review": handle_ai,
    "pdf_check": handle_pdf_check,
}


def process(state: AppState, job: Job) -> str:
    """Run one claimed job. Returns the final job status."""
    sandbox = Path(tempfile.mkdtemp(prefix=f"job-{job.id[:8]}-", dir=state.settings.work_path()))
    run_id = job.run_id
    try:
        with state.db.scope(RLSContext(job.tenant_id, None, "system")) as s:
            run = s.get(Run, run_id)
            assert run is not None
            run.status = "running"
            run.started_at = run.started_at or utcnow()
            run.engine_version = state.engine.engine_version()
            rsv, rsh = state.engine.rule_set()
            run.rule_set_version, run.rule_set_hash = rsv, rsh or None
            s.flush()
            s.expunge(run)
        add_event(state.db, run, "started", 0.0, kind="status")
        ctx = JobContext(state, job, run, sandbox)
        handler = HANDLERS.get(run.kind)
        if handler is None:
            raise JobFailed("unknown_kind", f"no handler for run kind {run.kind}")
        with state.db.scope(ctx.rls()) as s:
            r = s.get(Run, run_id)
            eng = s.get(Engagement, r.engagement_id) if r else None
            assert r is not None
            assert eng is not None
            ctx.session = s
            result = handler(ctx, s, r, eng)
            r.status = "succeeded"
            r.finished_at = utcnow()
            r.result_json = result
            r.error_code = None
            r.error_message = None
            if r.kind not in {"render", "ai_review"}:
                _bump_usage(s, r)
            audit.append(s, ctx.actor(), "run.succeeded", client_id=r.client_id, engagement_id=r.engagement_id, object_type="run", object_id=r.id,
                         details={"kind": r.kind})
            metrics.observe_run(r.kind, r.started_at, r.finished_at)
        add_event(state.db, run, "succeeded", 1.0, kind="status")
        return finish(state.db, job.id, ok=True)
    except JobFailed as exc:
        _fail(state, job, run_id, exc.code, str(exc), exc.details, final=True)
        return finish(state.db, job.id, ok=False, error=f"{exc.code}: {exc}", retryable=False)
    except EngineUnavailable as exc:
        _fail(state, job, run_id, "engine_unavailable", str(exc), {}, final=True)
        return finish(state.db, job.id, ok=False, error=f"engine_unavailable: {exc}", retryable=False)
    except Exception as exc:
        log.exception("job %s failed", job.id)
        final = job.attempts >= job.max_attempts
        _fail(state, job, run_id, "engine_error", f"{type(exc).__name__}: {exc}"[:500], {}, final=final)
        return finish(state.db, job.id, ok=False, error=f"{type(exc).__name__}: {exc}", retryable=True)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def _fail(state: AppState, job: Job, run_id: str, code: str, message: str, details: dict[str, Any], *, final: bool) -> None:
    with state.db.scope(RLSContext(job.tenant_id, None, "system")) as s:
        r = s.get(Run, run_id)
        if r is None:
            return
        r.status = "failed" if final else "queued"
        r.error_code = code
        r.error_message = message[:2000]
        if final:
            r.finished_at = utcnow()
            r.result_json = {**(r.result_json or {}), "error": {"code": code, "message": message[:500], **details}}
            if r.kind == "fix":
                fp = s.get(FixPlanRow, (r.params_json or {}).get("fix_plan_id"))
                if fp is not None and fp.status in {"applying", "approved"}:
                    fp.status = "failed"
            if r.kind == "plan":
                fp = s.get(FixPlanRow, (r.params_json or {}).get("fix_plan_id"))
                if fp is not None and fp.status == "planning":
                    fp.status = "failed"
                    fp.rejection_reason = message[:500]
            metrics.observe_run(r.kind, r.started_at, r.finished_at)
            actor = audit.Actor(r.tenant_id, r.created_by, "system:worker", None, "fsweb-worker", r.correlation_id)
            audit.append(s, actor, "run.failed", client_id=r.client_id, engagement_id=r.engagement_id, object_type="run", object_id=r.id,
                         outcome="failed", details={"kind": r.kind, "code": code})
        s.flush()
        s.expunge(r)
    add_event(state.db, r, f"failed: {code}" if final else f"retrying: {code}", 1.0 if final else 0.0, kind="status")
