"""Review notes like an audit file (06 §3): Partner and Reviewer clear, the preparer responds."""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from fsweb import audit, schemas
from fsweb.authz.deps import Principal, authorize, check_if_match, get_db, require_principal
from fsweb.db import utcnow
from fsweb.errors import conflict, forbidden, not_found
from fsweb.models import ReviewNote, ReviewNoteMsg, User
from fsweb.routers.engagements import load_engagement
from fsweb.services.common import get_engagement, notify

router = APIRouter(tags=["review-notes"])


def note_out(db: Session, n: ReviewNote) -> schemas.ReviewNoteOut:
    out = schemas.ReviewNoteOut.model_validate(n)
    out.messages = [schemas.ReviewNoteMsgOut.model_validate(m) for m in db.execute(
        select(ReviewNoteMsg).where(ReviewNoteMsg.review_note_id == n.id).order_by(ReviewNoteMsg.created_at)).scalars()]
    return out


@router.get("/engagements/{engagement_id}/review-notes", response_model=list[schemas.ReviewNoteOut])
def list_notes(engagement_id: str, request: Request, status: str | None = None, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[schemas.ReviewNoteOut]:
    e = load_engagement(request, db, p, engagement_id)
    stmt = select(ReviewNote).where(ReviewNote.engagement_id == e.id).order_by(ReviewNote.created_at)
    if p.is_client_user:
        stmt = stmt.where(ReviewNote.audience == "client")
    if status:
        stmt = stmt.where(ReviewNote.status == status)
    return [note_out(db, n) for n in db.execute(stmt).scalars()]


@router.post("/engagements/{engagement_id}/review-notes", response_model=schemas.ReviewNoteOut, status_code=201)
def raise_note(engagement_id: str, body: schemas.ReviewNoteIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.ReviewNoteOut:
    e = load_engagement(request, db, p, engagement_id, "review.raise")
    audience = "client" if body.target_type == "client_query" else "team"
    if audience == "client":
        authorize(request, db, p, "release.client", engagement=e)
    if body.assigned_to:
        u = db.get(User, body.assigned_to)
        if u is None or u.tenant_id != p.tenant_id:
            raise not_found("assignee")
        if audience == "team" and not u.is_firm_user:
            raise conflict("invalid_assignee", "team review notes are assigned to firm users")
    n = ReviewNote(tenant_id=p.tenant_id, client_id=e.client_id, engagement_id=e.id, target_type=body.target_type, target_ref=body.target_ref,
                   text=body.text, audience=audience, raised_by=p.user_id, assigned_to=body.assigned_to, created_by=p.user_id)
    db.add(n)
    db.flush()
    if body.assigned_to:
        u = db.get(User, body.assigned_to)
        if u is not None:
            notify(db, p.tenant_id, u.email, "A review note was assigned to you", "Open FS-Platform to respond.", "review_note")
    audit.append(db, p.actor(), "review_note.raised", client_id=e.client_id, engagement_id=e.id, object_type="review_note", object_id=n.id,
                 details={"target": body.target_type, "audience": audience})
    db.commit()
    return note_out(db, n)


def _note(request: Request, db: Session, p: Principal, note_id: str) -> tuple[ReviewNote, object]:
    n = db.get(ReviewNote, note_id)
    if n is None or n.tenant_id != p.tenant_id:
        raise not_found("review note")
    e = get_engagement(db, n.engagement_id)
    if not p.is_firm_admin and not p.is_member(e.client_id):
        raise not_found("review note")
    if p.is_client_user and n.audience != "client":
        raise not_found("review note")
    return n, e


@router.get("/review-notes/{note_id}", response_model=schemas.ReviewNoteOut)
def get_note(note_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.ReviewNoteOut:
    n, e = _note(request, db, p, note_id)
    authorize(request, db, p, "engagement.view", engagement=e)  # type: ignore[arg-type]
    return note_out(db, n)


@router.post("/review-notes/{note_id}/messages", response_model=schemas.ReviewNoteOut, status_code=201)
def respond(note_id: str, body: schemas.ReviewNoteMsgIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.ReviewNoteOut:
    n, e = _note(request, db, p, note_id)
    authorize(request, db, p, "portal.respond" if p.is_client_user else "review.respond", engagement=e)  # type: ignore[arg-type]
    if n.status == "Cleared":
        raise conflict("cleared", "the note is cleared; raise a new one")
    db.add(ReviewNoteMsg(tenant_id=p.tenant_id, client_id=n.client_id, review_note_id=n.id, user_id=p.user_id, text=body.text, created_by=p.user_id))
    if p.user_id != n.raised_by:
        n.status = "Responded"
    audit.append(db, p.actor(), "review_note.responded", client_id=n.client_id, engagement_id=n.engagement_id, object_type="review_note", object_id=n.id)
    db.commit()
    return note_out(db, n)


@router.post("/review-notes/{note_id}/clear", response_model=schemas.ReviewNoteOut)
def clear(note_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.ReviewNoteOut:
    n, e = _note(request, db, p, note_id)
    if n.audience == "client":
        authorize(request, db, p, "release.client", engagement=e)  # type: ignore[arg-type]
    else:
        authorize(request, db, p, "review.clear", engagement=e)  # type: ignore[arg-type]
    check_if_match(request, n.version)
    if n.status == "Cleared":
        raise conflict("cleared", "already cleared")
    if p.is_client_user:
        raise forbidden("client users cannot clear notes")
    n.status = "Cleared"
    n.cleared_by = p.user_id
    n.cleared_at = utcnow()
    audit.append(db, p.actor(), "review_note.cleared", client_id=n.client_id, engagement_id=n.engagement_id, object_type="review_note", object_id=n.id)
    db.commit()
    return note_out(db, n)


@router.get("/engagements/{engagement_id}/review-notes/export", responses={200: {"content": {"text/csv": {}}}})
def export_notes(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Response:
    """Export with clearance evidence for the engagement archive (06 §3)."""
    e = load_engagement(request, db, p, engagement_id, "finding.view")
    users = {u.id: u.email for u in db.execute(select(User).where(User.tenant_id == p.tenant_id)).scalars()}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["note_id", "target_type", "target_ref", "status", "audience", "raised_by", "raised_at", "assigned_to", "cleared_by", "cleared_at", "text", "thread"])
    for n in db.execute(select(ReviewNote).where(ReviewNote.engagement_id == e.id).order_by(ReviewNote.created_at)).scalars():
        msgs = db.execute(select(ReviewNoteMsg).where(ReviewNoteMsg.review_note_id == n.id).order_by(ReviewNoteMsg.created_at)).scalars()
        thread = " | ".join(f"{users.get(m.user_id, m.user_id)} @ {m.created_at:%d-%b-%Y %H:%M}: {m.text}" for m in msgs)
        w.writerow([n.id, n.target_type, n.target_ref or "", n.status, n.audience, users.get(n.raised_by, n.raised_by), f"{n.created_at:%d-%b-%Y %H:%M}",
                    users.get(n.assigned_to or "", ""), users.get(n.cleared_by or "", ""), f"{n.cleared_at:%d-%b-%Y %H:%M}" if n.cleared_at else "", n.text, thread])
    audit.append(db, p.actor(), "review_note.exported", client_id=e.client_id, engagement_id=e.id)
    db.commit()
    return Response(content=buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="review-notes.csv"'})
