"""Audit trail viewers (06 §4): engagement timeline, firm-wide search, verification, CSV export."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from fsweb import audit, schemas
from fsweb.authz.deps import Principal, authorize, get_db, permission_value, require_principal
from fsweb.errors import forbidden
from fsweb.models import AuditEvent
from fsweb.routers.engagements import load_engagement

router = APIRouter(tags=["audit"])


def _scope(db: Session, p: Principal, request: Request) -> Select[tuple[AuditEvent]]:
    stmt = select(AuditEvent).where(AuditEvent.tenant_id == p.tenant_id)
    if p.is_firm_admin:
        authorize(request, db, p, "audit.view")
        return stmt
    clients = [c for c in p.client_ids if permission_value(db, p, "audit.view", c) != "deny"]
    if not clients:
        authorize(request, db, p, "audit.view")  # raises and records the denial
        raise forbidden()
    # Partner / Manager: own clients (05 §3); plus their own sign-in events.
    return stmt.where(or_(AuditEvent.client_id.in_(clients), AuditEvent.user_id == p.user_id))


def _filters(stmt: Select[tuple[AuditEvent]], engagement_id: str | None, user_id: str | None, action: str | None,
             since: datetime | None, until: datetime | None) -> Select[tuple[AuditEvent]]:
    if engagement_id:
        stmt = stmt.where(AuditEvent.engagement_id == engagement_id)
    if user_id:
        stmt = stmt.where(AuditEvent.user_id == user_id)
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action}%"))
    if since:
        stmt = stmt.where(AuditEvent.ts >= since)
    if until:
        stmt = stmt.where(AuditEvent.ts <= until)
    return stmt


@router.get("/audit", response_model=schemas.Page)
def search(request: Request, engagement_id: str | None = None, user_id: str | None = None, action: str | None = None,
           since: datetime | None = None, until: datetime | None = None, limit: int = 100, offset: int = 0,
           p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.Page:
    stmt = _filters(_scope(db, p, request), engagement_id, user_id, action, since, until)
    total = int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())
    rows = db.execute(stmt.order_by(AuditEvent.seq.desc()).limit(max(1, min(limit, 1000))).offset(max(0, offset))).scalars()
    return schemas.Page(total=total, items=[schemas.AuditEventOut.model_validate(r).model_dump(mode="json") for r in rows])


@router.get("/audit/verify", response_model=schemas.ChainOut)
def verify(request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.ChainOut:
    if not p.is_firm_admin:
        authorize(request, db, p, "users.manage")
    rep = audit.verify_chain(db, p.tenant_id)
    audit.append(db, p.actor(), "audit.chain_verified", details={"ok": rep.ok, "events": rep.events, "head_seq": rep.head_seq})
    db.commit()
    return schemas.ChainOut(**rep.as_dict())


@router.get("/audit/export.csv", responses={200: {"content": {"text/csv": {}}}})
def export_csv(request: Request, engagement_id: str | None = None, user_id: str | None = None, action: str | None = None,
               since: datetime | None = None, until: datetime | None = None, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Response:
    stmt = _filters(_scope(db, p, request), engagement_id, user_id, action, since, until).order_by(AuditEvent.seq)
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["seq", "ts", "user_id", "role_at_time", "ip", "client_id", "engagement_id", "object_type", "object_id", "action", "outcome",
            "before_hash", "after_hash", "correlation_id", "prev_event_hash", "event_hash"]
    w.writerow(cols)
    for ev in db.execute(stmt.limit(200_000)).scalars():
        w.writerow([getattr(ev, c).isoformat() if c == "ts" else (getattr(ev, c) or "") for c in cols])
    audit.append(db, p.actor(), "audit.exported", details={"engagement_id": engagement_id, "action": action})
    db.commit()
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="audit-trail.csv"'})


@router.get("/engagements/{engagement_id}/timeline", response_model=list[schemas.AuditEventOut])
def timeline(engagement_id: str, request: Request, limit: int = 500, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[AuditEvent]:
    """Per-engagement timeline for the team (06 §4)."""
    e = load_engagement(request, db, p, engagement_id, "finding.view")
    return list(db.execute(select(AuditEvent).where(AuditEvent.engagement_id == e.id).order_by(AuditEvent.seq.desc()).limit(max(1, min(limit, 5000)))).scalars())
