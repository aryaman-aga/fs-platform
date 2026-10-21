"""Internal protocol for Windows render workers (fsrender-win, E-ARCH-03).

Authenticated with ``X-Worker-Token``; reachable only on the internal network
(the reverse proxy does not publish ``/api/v1/internal``). A worker claims a job
from the ``render-win`` queue, downloads the workbook, renders with Excel and
uploads the PDF; the platform then runs the PDF-vs-workbook verification.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi import File as FormFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from fsweb import metrics, schemas
from fsweb.authz.deps import get_db, get_state
from fsweb.db import SYSTEM, RLSContext, set_rls, utcnow
from fsweb.errors import ApiError, conflict, not_found, unauthorized
from fsweb.jobs.handlers import store_render
from fsweb.jobs.queue import claim, finish
from fsweb.models import Engagement, FileVersion, Job, RenderWorker, Run
from fsweb.security.crypto import consteq
from fsweb.services import engagements as eng_svc

router = APIRouter(tags=["internal"], include_in_schema=True)


def require_worker(request: Request, db: Session = Depends(get_db)) -> Session:
    st = get_state(request)
    token = request.headers.get("X-Worker-Token", "")
    if not st.settings.worker_token or not token or not consteq(token, st.settings.worker_token):
        raise unauthorized("render worker token required", "worker_unauthenticated")
    set_rls(db, SYSTEM)
    return db


def _heartbeat(db: Session, worker_id: str, excel_build: str | None) -> None:
    now = utcnow()
    w = db.get(RenderWorker, worker_id)
    if w is None:
        db.add(RenderWorker(id=worker_id, excel_build=excel_build, last_heartbeat_at=now))
    else:
        w.last_heartbeat_at = now
        w.excel_build = excel_build or w.excel_build
    metrics.render_heartbeat(worker_id, now)


@router.post("/internal/render/heartbeat", response_model=schemas.OkOut)
def heartbeat(body: schemas.HeartbeatIn, db: Session = Depends(require_worker)) -> schemas.OkOut:
    _heartbeat(db, body.worker_id, body.excel_build)
    db.commit()
    return schemas.OkOut(message="ok")


@router.post("/internal/render/claim", response_model=schemas.RenderClaimOut, responses={204: {"description": "no job"}})
def claim_job(body: schemas.RenderClaimIn, request: Request, db: Session = Depends(require_worker)) -> Any:
    st = get_state(request)
    _heartbeat(db, body.worker_id, body.excel_build)
    db.commit()
    job = claim(st.db, ["render-win"], body.worker_id)
    if job is None:
        return Response(status_code=204)
    run = db.get(Run, job.run_id)
    assert run is not None
    run.status = "running"
    run.started_at = utcnow()
    eng = db.get(Engagement, run.engagement_id)
    assert eng is not None
    cfg = eng_svc.config_for(db, eng)
    db.commit()
    params = run.params_json or {}
    return schemas.RenderClaimOut(job_id=job.id, run_id=run.id, mode=str(params.get("mode", "draft")), sheets=list(cfg.print_sheets.explicit),
                                  options={"engine": "excel", "watermark": params.get("mode", "draft") != "final", "run_id": run.id,
                                           "timeout_s": 300, "pdfa": params.get("mode") == "final"})


def _job(db: Session, job_id: str) -> tuple[Job, Run]:
    job = db.get(Job, job_id)
    if job is None or job.queue != "render-win":
        raise not_found("job")
    if job.status != "claimed":
        raise conflict("not_claimed", f"job is {job.status}")
    run = db.get(Run, job.run_id)
    assert run is not None
    return job, run


@router.get("/internal/render/{job_id}/input", responses={200: {"content": {"application/octet-stream": {}}}})
def job_input(job_id: str, request: Request, db: Session = Depends(require_worker)) -> Response:
    job, run = _job(db, job_id)
    v = db.get(FileVersion, run.input_version_ids[0])
    assert v is not None
    job.heartbeat_at = utcnow()
    db.commit()
    return Response(get_state(request).storage.get_bytes(v.object_key), media_type=v.content_type,
                    headers={"Content-Disposition": f'attachment; filename="{v.original_name}"', "X-Content-SHA256": v.sha256})


@router.post("/internal/render/{job_id}/complete", response_model=schemas.RunOut)
async def job_complete(job_id: str, request: Request, pdf: Annotated[UploadFile, FormFile()], meta: Annotated[str, Form()] = "{}",
                       db: Session = Depends(require_worker)) -> Run:
    st = get_state(request)
    job, run = _job(db, job_id)
    data = await pdf.read()
    if not data.startswith(b"%PDF-"):
        raise ApiError(415, "bad_magic", "the upload is not a PDF")
    try:
        info: dict[str, Any] = json.loads(meta or "{}")
    except ValueError:
        info = {}
    eng = db.get(Engagement, run.engagement_id)
    base = db.get(FileVersion, run.input_version_ids[0])
    assert eng is not None
    assert base is not None
    set_rls(db, RLSContext(run.tenant_id, run.created_by, "system"))
    with tempfile.TemporaryDirectory(dir=st.settings.work_path()) as td:
        wb = st.storage.download_to(base.object_key, Path(td) / ("workbook" + Path(base.original_name).suffix))
        p = Path(td) / "statements.pdf"
        p.write_bytes(data)
        recon = st.engine.reconcile_pdf(wb, p, eng_svc.config_for(db, eng))
    mode = str((run.params_json or {}).get("mode", "draft"))
    result = store_render(None, db, run, eng, base, data, mode, str(info.get("engine", "excel")), str(info.get("engine_version", "")),
                          recon.model_dump(mode="json"), recon.pages, state=st)
    result["excel_build"] = info.get("excel_build")
    run.status = "succeeded"
    run.finished_at = utcnow()
    run.result_json = result
    db.commit()
    finish(st.db, job.id, ok=True)
    metrics.observe_run(run.kind, run.started_at, run.finished_at)
    return run


@router.post("/internal/render/{job_id}/fail", response_model=schemas.OkOut)
def job_fail(job_id: str, body: schemas.RenderFailIn, request: Request, db: Session = Depends(require_worker)) -> schemas.OkOut:
    st = get_state(request)
    job, run = _job(db, job_id)
    final = job.attempts >= job.max_attempts
    run.status = "failed" if final else "queued"
    run.error_code = "render_failed"
    run.error_message = body.error[:2000]
    if final:
        run.finished_at = utcnow()
    db.commit()
    if final:
        metrics.observe_run(run.kind, run.started_at, run.finished_at)
    status = finish(st.db, job.id, ok=False, error=body.error, retryable=True)
    return schemas.OkOut(message=f"job {status}")
