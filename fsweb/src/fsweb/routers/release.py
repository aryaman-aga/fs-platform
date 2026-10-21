"""PDF render (draft / final), release to the client with a change report (C12),
and the client portal (08 §3 screen 5)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsweb import audit, schemas
from fsweb.authz.deps import Principal, authorize, get_db, get_state, require_principal
from fsweb.db import utcnow
from fsweb.errors import conflict, forbidden, not_found
from fsweb.models import (
    DraftRelease,
    Engagement,
    FileVersion,
    FindingRow,
    MgmtItem,
    ReviewNote,
    ReviewNoteMsg,
    Run,
    User,
)
from fsweb.routers.engagements import engagement_out, load_engagement
from fsweb.routers.files import deliver
from fsweb.routers.runs import start_render
from fsweb.services import engagements as eng_svc
from fsweb.services import workflow
from fsweb.services.common import get_engagement, notify, users_with_role

router = APIRouter(tags=["render & release"])


@router.post("/engagements/{engagement_id}/render", response_model=schemas.RunOut, status_code=202)
def render(engagement_id: str, body: schemas.RenderIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Run:
    e = load_engagement(request, db, p, engagement_id)
    run = start_render(request, db, p, e, mode=body.mode, version_id=body.version_id, engine=body.engine,
                       idempotency_key=body.idempotency_key or request.headers.get("Idempotency-Key"))
    db.commit()
    return run


@router.post("/engagements/{engagement_id}/release", response_model=schemas.ReleaseOut, status_code=201)
def release(engagement_id: str, body: schemas.ReleaseIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> DraftRelease:
    st = get_state(request)
    e = load_engagement(request, db, p, engagement_id, "release.client")
    if e.status not in {"REVIEW", "DRAFT_RELEASED_TO_CLIENT"}:
        raise conflict("invalid_state", f"drafts are released from REVIEW (engagement is {e.status})")
    run = db.get(Run, body.render_run_id)
    if run is None or run.engagement_id != e.id or run.kind != "render" or run.status != "succeeded" or not run.output_version_id:
        raise conflict("invalid_render", "choose a successful render of this engagement")
    latest = workflow.latest_workbook_version(db, e)
    if latest is None or latest.id not in (run.input_version_ids or []):
        raise conflict("stale_render", "the render is not from the latest workbook version; render again")
    if int((run.result_json or {}).get("misses", -1)) != 0:
        raise conflict("pdf_mismatch", "the PDF does not match the workbook (PDF-01 misses); it cannot be released (C7)")
    prev = db.execute(select(DraftRelease).where(DraftRelease.engagement_id == e.id).order_by(DraftRelease.released_at.desc())).scalars().first()
    diff: dict[str, object] = {"first_release": prev is None}
    if prev is not None and prev.workbook_version_id != latest.id:
        va, vb = db.get(FileVersion, prev.workbook_version_id), latest
        assert va is not None
        with tempfile.TemporaryDirectory(dir=st.settings.work_path()) as td:
            pa = st.storage.download_to(va.object_key, Path(td) / f"a{Path(va.original_name).suffix}")
            pb = st.storage.download_to(vb.object_key, Path(td) / f"b{Path(vb.original_name).suffix}")
            d = st.engine.diff_workbooks(pa, pb, eng_svc.config_for(db, e))
        diff = {"first_release": False, "from_version_id": va.id, "to_version_id": vb.id, **d.model_dump(mode="json")}
    rel = DraftRelease(tenant_id=p.tenant_id, client_id=e.client_id, engagement_id=e.id, render_run_id=run.id, pdf_version_id=run.output_version_id,
                       workbook_version_id=latest.id, released_by=p.user_id, diff_json=diff, notes=body.notes, created_by=p.user_id)
    db.add(rel)
    db.flush()
    if e.status == "REVIEW":
        workflow.set_status(db, e, "DRAFT_RELEASED_TO_CLIENT", p.actor(), reason="draft released to client")
    for role in ("client_viewer", "client_uploader"):
        for u in users_with_role(db, p.tenant_id, role, e.client_id):
            notify(db, p.tenant_id, u.email, "A draft is available for your review", "Sign in to FS-Platform to view the released draft.", "release")
    audit.append(db, p.actor(), "engagement.released", client_id=e.client_id, engagement_id=e.id, object_type="draft_release", object_id=rel.id,
                 details={"pdf_version_id": rel.pdf_version_id, "workbook_version_id": latest.id, "printed_moves": len(diff.get("printed_moves", []) or [])})  # type: ignore[arg-type]
    db.commit()
    return rel


@router.get("/engagements/{engagement_id}/releases", response_model=list[schemas.ReleaseOut])
def list_releases(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[DraftRelease]:
    e = load_engagement(request, db, p, engagement_id)
    return list(db.execute(select(DraftRelease).where(DraftRelease.engagement_id == e.id).order_by(DraftRelease.released_at.desc())).scalars())


# ================================================================== client portal


def _portal(request: Request, db: Session, p: Principal, engagement_id: str, permission: str = "portal.view") -> Engagement:
    if not p.is_client_user:
        raise forbidden("the portal is for client users", "portal_only")
    e = get_engagement(db, engagement_id)
    if e.tenant_id != p.tenant_id or not p.is_member(e.client_id):
        raise not_found("engagement")
    authorize(request, db, p, permission, engagement=e)
    return e


@router.get("/portal/engagements", response_model=list[schemas.EngagementOut])
def portal_engagements(request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[schemas.EngagementOut]:
    if not p.is_client_user:
        raise forbidden("the portal is for client users", "portal_only")
    rows = db.execute(select(Engagement).where(Engagement.client_id.in_(p.client_ids)).order_by(Engagement.period_end.desc())).scalars()
    return [engagement_out(db, e) for e in rows]


@router.get("/portal/engagements/{engagement_id}/releases", response_model=list[schemas.ReleaseOut])
def portal_releases(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[DraftRelease]:
    e = _portal(request, db, p, engagement_id)
    return list(db.execute(select(DraftRelease).where(DraftRelease.engagement_id == e.id).order_by(DraftRelease.released_at.desc())).scalars())


@router.get("/portal/releases/{release_id}/download", responses={200: {"content": {"application/pdf": {}}}})
def portal_download(release_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Response:
    rel = db.get(DraftRelease, release_id)
    if rel is None or rel.tenant_id != p.tenant_id:
        raise not_found("release")
    e = _portal(request, db, p, rel.engagement_id, "file.download")
    v = db.get(FileVersion, rel.pdf_version_id)
    assert v is not None
    audit.append(db, p.actor(), "file.downloaded", client_id=e.client_id, engagement_id=e.id, object_type="draft_release", object_id=rel.id,
                 details={"sha256": v.sha256, "portal": True})
    db.commit()
    return deliver(request, p, v, e)


@router.get("/portal/engagements/{engagement_id}/findings", response_model=list[schemas.FindingOut])
def portal_findings(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[FindingRow]:
    e = _portal(request, db, p, engagement_id, "finding.view")
    wb = workflow.latest_workbook_version(db, e)
    check = workflow.latest_run(db, e, "check", version_id=wb.id) if wb else None
    if check is None:
        return []
    return list(db.execute(select(FindingRow).where(FindingRow.run_id == check.id, FindingRow.shared_with_client.is_(True)).order_by(FindingRow.fid)).scalars())


@router.get("/portal/engagements/{engagement_id}/queries", response_model=list[schemas.ReviewNoteOut])
def portal_queries(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[schemas.ReviewNoteOut]:
    from fsweb.routers.reviewnotes import note_out

    e = _portal(request, db, p, engagement_id)
    rows = db.execute(select(ReviewNote).where(ReviewNote.engagement_id == e.id, ReviewNote.audience == "client").order_by(ReviewNote.created_at)).scalars()
    return [note_out(db, n) for n in rows]


@router.post("/portal/queries/{note_id}/respond", response_model=schemas.ReviewNoteOut, status_code=201)
def portal_respond(note_id: str, body: schemas.ReviewNoteMsgIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.ReviewNoteOut:
    from fsweb.routers.reviewnotes import note_out

    n = db.get(ReviewNote, note_id)
    if n is None or n.tenant_id != p.tenant_id or n.audience != "client":
        raise not_found("query")
    _portal(request, db, p, n.engagement_id, "portal.respond")
    if n.status == "Cleared":
        raise conflict("cleared", "the query is closed")
    db.add(ReviewNoteMsg(tenant_id=p.tenant_id, client_id=n.client_id, review_note_id=n.id, user_id=p.user_id, text=body.text, created_by=p.user_id))
    n.status = "Responded"
    audit.append(db, p.actor(), "client_query.responded", client_id=n.client_id, engagement_id=n.engagement_id, object_type="review_note", object_id=n.id)
    db.commit()
    return note_out(db, n)


@router.get("/portal/engagements/{engagement_id}/mgmt-items", response_model=list[schemas.MgmtItemOut])
def portal_mgmt_items(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[MgmtItem]:
    e = _portal(request, db, p, engagement_id)
    return list(db.execute(select(MgmtItem).where(MgmtItem.engagement_id == e.id).order_by(MgmtItem.created_at)).scalars())


@router.post("/portal/mgmt-items/{item_id}/confirm", response_model=schemas.MgmtItemOut)
def portal_confirm(item_id: str, body: schemas.MgmtConfirmIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> MgmtItem:
    m = db.get(MgmtItem, item_id)
    if m is None or m.tenant_id != p.tenant_id:
        raise not_found("item")
    e = _portal(request, db, p, m.engagement_id, "portal.confirm")
    if e.status != "DRAFT_RELEASED_TO_CLIENT":
        raise conflict("invalid_state", "representations are confirmed after a draft is released")
    m.status = body.decision
    m.comment = body.comment
    m.confirmed_by = p.user_id
    m.confirmed_at = utcnow()
    audit.append(db, p.actor(), "mgmt_item.decided", client_id=e.client_id, engagement_id=e.id, object_type="mgmt_item", object_id=m.id,
                 details={"decision": body.decision})
    db.flush()
    pending = int(db.execute(select(func.count()).select_from(MgmtItem).where(MgmtItem.engagement_id == e.id, MgmtItem.status != "confirmed")).scalar_one())
    if pending == 0:
        workflow.set_status(db, e, "MGMT_CONFIRMED", p.actor(), reason="all management representations confirmed by the client")
        if e.partner_id:
            partner = db.get(User, e.partner_id)
            if partner:
                notify(db, p.tenant_id, partner.email, "Client confirmed management representations", "The engagement is ready for final review.", "workflow")
    db.commit()
    return m
