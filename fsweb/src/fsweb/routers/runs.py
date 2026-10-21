"""Runs (check / render / diff / prepare), progress via SSE, artefacts and findings."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import anyio
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsweb import audit, schemas
from fsweb.authz.deps import Principal, authorize, check_if_match, etag, get_db, get_state, require_principal
from fsweb.db import RLSContext, utcnow
from fsweb.errors import bad_request, conflict, forbidden, not_found
from fsweb.jobs.queue import TERMINAL
from fsweb.models import Artefact, Engagement, File, FileVersion, FindingRow, RenderWorker, Run, RunEvent
from fsweb.routers.engagements import load_engagement
from fsweb.services import workflow
from fsweb.services.common import create_run, get_engagement

router = APIRouter(tags=["runs"])

FINDING_STATUSES = {"Open", "Accepted", "Not an issue", "Resolved"}


def _version_in(db: Session, e: Engagement, version_id: str) -> FileVersion:
    v = db.get(FileVersion, version_id)
    if v is None or v.engagement_id != e.id:
        raise not_found("version")
    return v


def render_queue(db: Session) -> str:
    """Windows render workers take renders only while their heartbeat is fresh (< 2 min)."""
    from datetime import timedelta

    fresh = db.execute(select(func.count()).select_from(RenderWorker).where(RenderWorker.last_heartbeat_at > utcnow() - timedelta(minutes=2))).scalar_one()
    return "render-win" if fresh else "default"


def start_render(request: Request, db: Session, p: Principal, e: Engagement, *, mode: str, version_id: str | None, engine: str,
                 idempotency_key: str | None) -> Run:
    if mode == "final":
        authorize(request, db, p, "release.client", engagement=e)
        failed = [g.as_dict() for g in workflow.final_pdf_gates(db, e) if not g.ok]
        if failed:
            raise conflict("gate_failed", "a FINAL PDF needs zero High findings and passing signatory checks", gates=failed)
    else:
        authorize(request, db, p, "render.draft", engagement=e)
    v = _version_in(db, e, version_id) if version_id else workflow.latest_workbook_version(db, e)
    if v is None:
        raise conflict("no_workbook", "upload a workbook first")
    if mode == "final":
        latest = workflow.latest_workbook_version(db, e)
        if latest is None or v.id != latest.id:
            raise conflict("not_latest", "a FINAL PDF must be rendered from the latest workbook version")
    run, _ = create_run(db, e, "render", created_by=p.user_id, input_versions=[v], params={"mode": mode, "engine": engine},
                        idempotency_key=idempotency_key, queue=render_queue(db), correlation_id=p.correlation_id, actor=p.actor())
    return run


@router.post("/engagements/{engagement_id}/runs", response_model=schemas.RunOut, status_code=202)
def create_run_ep(engagement_id: str, body: schemas.RunIn, request: Request, response: Response, p: Principal = Depends(require_principal),
                  db: Session = Depends(get_db)) -> Run:
    e = load_engagement(request, db, p, engagement_id)
    key = body.idempotency_key or request.headers.get("Idempotency-Key")
    if body.kind == "render":
        run = start_render(request, db, p, e, mode=str(body.params.get("mode", "draft")), version_id=(body.input_version_ids or [None])[0],
                           engine=str(body.params.get("engine", "auto")), idempotency_key=key)
        db.commit()
        return run
    if body.kind == "check":
        authorize(request, db, p, "run.check", engagement=e)
        versions = [_version_in(db, e, vid) for vid in body.input_version_ids] or [v for v in [workflow.latest_workbook_version(db, e)] if v]
        if not versions:
            raise conflict("no_workbook", "upload a workbook first")
        params = dict(body.params)
        if params.get("pdf_version_id"):
            _version_in(db, e, str(params["pdf_version_id"]))
    elif body.kind == "pdf_check":  # PDF-04: a printed PDF without its workbook
        authorize(request, db, p, "run.check", engagement=e)
        versions = [_version_in(db, e, vid) for vid in body.input_version_ids]
        if not versions:
            pdf_file = db.execute(select(File).where(File.engagement_id == e.id, File.kind == "printed_pdf").order_by(File.created_at.desc())).scalars().first()
            latest_pdf = workflow.latest_version(db, pdf_file.id) if pdf_file else None
            if latest_pdf is None:
                raise conflict("no_pdf", "upload the printed PDF first")
            versions = [latest_pdf]
        if any(v.content_type != "application/pdf" for v in versions):
            raise bad_request("not_pdf", "PDF-only checks need a PDF version")
        params = dict(body.params)
    elif body.kind == "diff":
        authorize(request, db, p, "file.view", engagement=e)
        if len(body.input_version_ids) != 2:
            raise bad_request("two_versions", "a diff needs exactly two versions")
        versions = [_version_in(db, e, vid) for vid in body.input_version_ids]
        params = dict(body.params)
    else:  # prepare (Module P): trial balance + mapping -> statements workbook
        authorize(request, db, p, "file.upload", engagement=e)
        versions = [_version_in(db, e, vid) for vid in body.input_version_ids]
        if not versions:
            tb = db.execute(select(File).where(File.engagement_id == e.id, File.kind == "tb")).scalars().first()
            latest = workflow.latest_version(db, tb.id) if tb else None
            if latest is None:
                raise conflict("no_tb", "upload a trial balance first")
            versions = [latest]
        params = dict(body.params)
    run, created = create_run(db, e, body.kind, created_by=p.user_id, input_versions=versions, params=params, idempotency_key=key,
                              correlation_id=p.correlation_id, actor=p.actor())
    db.commit()
    if not created:
        response.status_code = 200
    return run


@router.get("/engagements/{engagement_id}/runs", response_model=list[schemas.RunOut])
def list_runs(engagement_id: str, request: Request, kind: str | None = None, limit: int = 50, p: Principal = Depends(require_principal),
              db: Session = Depends(get_db)) -> list[Run]:
    e = load_engagement(request, db, p, engagement_id, "finding.view")
    stmt = select(Run).where(Run.engagement_id == e.id).order_by(Run.created_at.desc()).limit(max(1, min(limit, 500)))
    if kind:
        stmt = stmt.where(Run.kind == kind)
    return list(db.execute(stmt).scalars())


def load_run(request: Request, db: Session, p: Principal, run_id: str, permission: str = "finding.view") -> tuple[Run, Engagement]:
    run = db.get(Run, run_id)
    if run is None or run.tenant_id != p.tenant_id:
        raise not_found("run")
    e = get_engagement(db, run.engagement_id)
    if not p.is_firm_admin and not p.is_member(e.client_id):
        raise not_found("run")
    authorize(request, db, p, permission, engagement=e)
    return run, e


@router.get("/runs/{run_id}", response_model=schemas.RunOut)
def get_run(run_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Run:
    run, _ = load_run(request, db, p, run_id)
    return run


@router.get("/runs/{run_id}/result", response_model=schemas.RunResultOut)
def run_result(run_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.RunResultOut:
    run, _ = load_run(request, db, p, run_id)
    if p.is_client_user:
        raise forbidden("run details are internal to the firm")
    r: dict[str, Any] = run.result_json or {}
    big = {"key_figures", "statements", "rpt_recon", "narrative", "xref_map", "text_proposals", "sheet_states"}
    return schemas.RunResultOut(run_id=run.id, key_figures=r.get("key_figures", []), statements=r.get("statements", {}),
                                rpt_recon=r.get("rpt_recon", []), narrative=r.get("narrative", []), xref_map=r.get("xref_map", []),
                                text_proposals=r.get("text_proposals", []), summary={k: v for k, v in r.items() if k not in big})


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> StreamingResponse:
    """Server-Sent Events: progress until the run finishes (04 §5 step 4)."""
    run, _ = load_run(request, db, p, run_id)
    st = get_state(request)
    ctx = RLSContext(p.tenant_id, p.user_id, "firm" if p.is_firm_admin else "user")
    start = int(request.headers.get("Last-Event-ID") or 0)
    db.close()

    def poll(last_id: int) -> tuple[list[dict[str, Any]], str]:
        with st.db.scope(ctx) as s:
            evs = s.execute(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.id > last_id).order_by(RunEvent.id)).scalars().all()
            status = s.execute(select(Run.status).where(Run.id == run_id)).scalar_one()
            return [{"id": e.id, "kind": e.kind, "message": e.message, "pct": e.pct, "ts": e.ts.isoformat()} for e in evs], status

    async def stream() -> AsyncIterator[bytes]:
        last = start
        idle = 0
        while True:
            events, status = await anyio.to_thread.run_sync(poll, last)
            for ev in events:
                last = ev["id"]
                yield f"id: {ev['id']}\nevent: {ev['kind']}\ndata: {json.dumps(ev)}\n\n".encode()
            if status in TERMINAL:
                yield f"event: end\ndata: {json.dumps({'status': status})}\n\n".encode()
                return
            if await request.is_disconnected():
                return
            idle += 1
            if idle % 30 == 0:
                yield b": keep-alive\n\n"
            await asyncio.sleep(0.5)

    _ = run
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/runs/{run_id}/artefacts", response_model=list[schemas.ArtefactOut])
def run_artefacts(run_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[Artefact]:
    run, _ = load_run(request, db, p, run_id)
    if p.is_client_user:
        raise forbidden("run artefacts are internal to the firm")
    return list(db.execute(select(Artefact).where(Artefact.run_id == run.id).order_by(Artefact.created_at)).scalars())


@router.get("/artefacts/{artefact_id}/download", responses={200: {"content": {"application/octet-stream": {}}}})
def download_artefact(artefact_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Response:
    a = db.get(Artefact, artefact_id)
    if a is None or a.tenant_id != p.tenant_id:
        raise not_found("artefact")
    e = get_engagement(db, a.engagement_id)
    if p.is_client_user or (not p.is_firm_admin and not p.is_member(e.client_id)):
        raise not_found("artefact")
    authorize(request, db, p, "file.download", engagement=e)
    if a.kind in {"payload", "response"} and not p.all_roles & {"partner", "manager", "staff", "firm_admin"}:
        raise forbidden("AI payloads are visible to the engagement team only")
    data = get_state(request).storage.get_bytes(a.object_key)
    audit.append(db, p.actor(), "file.downloaded", client_id=e.client_id, engagement_id=e.id, object_type="artefact", object_id=a.id,
                 details={"kind": a.kind, "sha256": a.sha256})
    db.commit()
    return Response(content=data, media_type=a.content_type, headers={"Content-Disposition": f'attachment; filename="{a.name}"', "Cache-Control": "no-store"})


# ------------------------------------------------------------------ findings


@router.get("/runs/{run_id}/findings", response_model=list[schemas.FindingOut])
def list_findings(run_id: str, request: Request, severity: str | None = None, rule: str | None = None, sheet: str | None = None,
                  status: str | None = None, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[FindingRow]:
    run, _ = load_run(request, db, p, run_id)
    stmt = select(FindingRow).where(FindingRow.run_id == run.id).order_by(FindingRow.fid)
    if p.is_client_user:  # View findings: "(shared items only)"
        stmt = stmt.where(FindingRow.shared_with_client.is_(True))
    if severity:
        stmt = stmt.where(FindingRow.severity == severity)
    if rule:
        stmt = stmt.where(FindingRow.rule_id.like(f"{rule}%"))
    if sheet:
        stmt = stmt.where(FindingRow.sheet == sheet)
    if status:
        stmt = stmt.where(FindingRow.status == status)
    return list(db.execute(stmt).scalars())


@router.patch("/findings/{finding_id}", response_model=schemas.FindingOut)
def patch_finding(finding_id: str, body: schemas.FindingPatchIn, request: Request, response: Response, p: Principal = Depends(require_principal),
                  db: Session = Depends(get_db)) -> FindingRow:
    f = db.get(FindingRow, finding_id)
    if f is None or f.tenant_id != p.tenant_id:
        raise not_found("finding")
    e = get_engagement(db, f.engagement_id)
    if not p.is_firm_admin and not p.is_member(e.client_id):
        raise not_found("finding")
    authorize(request, db, p, "finding.update", engagement=e)
    if body.shared_with_client is not None:
        authorize(request, db, p, "release.client", engagement=e)
    check_if_match(request, f.version)
    if body.status == "Not an issue" and not (body.status_reason or "").strip():
        raise bad_request("reason_required", "marking a finding 'Not an issue' needs a reason (rule-quality analytics)")
    before = {"status": f.status, "assigned_to": f.assigned_to, "shared": f.shared_with_client}
    if body.status is not None and body.status != f.status:
        f.status = body.status
        f.status_reason = body.status_reason
        f.status_changed_at = utcnow()
    if body.assigned_to is not None:
        f.assigned_to = body.assigned_to or None
    if body.comment is not None:
        f.comment = body.comment
    if body.shared_with_client is not None:
        f.shared_with_client = body.shared_with_client
    audit.append(db, p.actor(), "finding.updated", client_id=e.client_id, engagement_id=e.id, object_type="finding", object_id=f.id, before=before,
                 details={"status": f.status, "reason": f.status_reason, "assigned_to": f.assigned_to, "shared": f.shared_with_client})
    db.commit()
    response.headers["ETag"] = etag(f.version)
    return f


@router.get("/analytics/rule-quality", response_model=list[dict[str, Any]])
def rule_quality(request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """E-RULE-05: false-positive rate per rule; rules above 10 % are flagged for tuning."""
    authorize(request, db, p, "rules.view")
    stmt = select(FindingRow.rule_id, FindingRow.status, func.count()).where(FindingRow.tenant_id == p.tenant_id)
    if not p.is_firm_admin:
        stmt = stmt.where(FindingRow.client_id.in_(p.client_ids))
    stats: dict[str, dict[str, int]] = {}
    for rule_id, status, n in db.execute(stmt.group_by(FindingRow.rule_id, FindingRow.status)).all():
        stats.setdefault(rule_id, {})[status] = int(n)
    out = []
    for rule_id, by in sorted(stats.items()):
        total = sum(by.values())
        fp = by.get("Not an issue", 0)
        rate = fp / total if total else 0.0
        out.append({"rule_id": rule_id, "findings": total, "not_an_issue": fp, "false_positive_rate": round(rate, 4),
                    "resolved": by.get("Resolved", 0), "flag_for_tuning": rate > 0.10})
    return out


_ = FINDING_STATUSES
