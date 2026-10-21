"""Admin console: access reviews, DPDP requests, CERT-In incidents, backups, ITGC
releases, usage / billing, evidence pack and system health."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsweb import __version__, audit, schemas
from fsweb.authz.deps import Principal, authorize, deny, get_db, get_state, require_principal, require_stepup
from fsweb.db import RLSContext, utcnow
from fsweb.errors import bad_request, conflict, forbidden, not_found
from fsweb.jobs.queue import queue_depth
from fsweb.models import (
    AccessReview,
    AuditHead,
    BackupLog,
    BillingUsage,
    Client,
    DsrRequest,
    Incident,
    Release,
    RenderWorker,
    Run,
    User,
)
from fsweb.services import compliance

router = APIRouter(tags=["compliance & operations"])


# ------------------------------------------------------------------ access reviews


@router.post("/admin/access-reviews", response_model=schemas.OkOut, status_code=201)
def start_review(body: schemas.AccessReviewCampaignIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.OkOut:
    authorize(request, db, p, "admin.compliance")
    n = compliance.start_access_review(db, p.tenant_id, body.period, p.actor())
    db.commit()
    return schemas.OkOut(message=f"{n} assignment(s) to certify" if n else "campaign already exists")


@router.get("/admin/access-reviews", response_model=list[schemas.AccessReviewOut])
def list_reviews(request: Request, period: str | None = None, mine: bool = False, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[AccessReview]:
    stmt = select(AccessReview).where(AccessReview.tenant_id == p.tenant_id).order_by(AccessReview.due_at)
    if mine or not p.is_firm_admin:
        stmt = stmt.where(AccessReview.reviewer_id == p.user_id)
    else:
        authorize(request, db, p, "admin.compliance")
    if period:
        stmt = stmt.where(AccessReview.period == period)
    return list(db.execute(stmt).scalars())


def _still_reviews(p: Principal, item: AccessReview) -> bool:
    """A named reviewer decides only while still holding a reviewing role: Partner or Manager on the client (or a
    Firm Admin standing in), Firm Admin for firm-wide roles. An elevation that has lapsed does not count."""
    if item.client_id:
        return bool(p.roles_for(item.client_id) & {"partner", "manager", "firm_admin"})
    return p.is_firm_admin


@router.post("/admin/access-reviews/{item_id}/decide", response_model=schemas.AccessReviewOut)
def decide(item_id: str, body: schemas.AccessDecisionIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> AccessReview:
    item = db.get(AccessReview, item_id)
    if item is None or item.tenant_id != p.tenant_id:
        raise not_found("access review item")
    if item.reviewer_id != p.user_id or not _still_reviews(p, item):
        authorize(request, db, p, "admin.compliance")
    if item.user_id == p.user_id:
        raise forbidden("you cannot certify your own access", "segregation_of_duties")
    if item.decision != "pending":
        raise conflict("decided", "item already decided")
    compliance.decide_access(db, item, body.decision, p.actor())
    db.commit()
    return item


@router.post("/admin/access-reviews/auto-revoke", response_model=schemas.OkOut)
def run_auto_revoke(request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.OkOut:
    authorize(request, db, p, "admin.compliance")
    n = compliance.auto_revoke(db, p.tenant_id, p.actor())
    db.commit()
    return schemas.OkOut(message=f"{n} unanswered assignment(s) revoked")


@router.get("/admin/access-reviews/report", response_model=dict[str, Any])
def review_report(request: Request, period: str | None = None, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> dict[str, Any]:
    authorize(request, db, p, "admin.compliance")
    return compliance.access_report(db, p.tenant_id, period)


# ------------------------------------------------------------------ DPDP requests


@router.get("/admin/dsr", response_model=list[schemas.DsrOut])
def list_dsr(request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[DsrRequest]:
    authorize(request, db, p, "admin.compliance")
    return list(db.execute(select(DsrRequest).where(DsrRequest.tenant_id == p.tenant_id).order_by(DsrRequest.due_at)).scalars())


@router.post("/admin/dsr", response_model=schemas.DsrOut, status_code=201)
def create_dsr(body: schemas.DsrIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> DsrRequest:
    st = get_state(request)
    authorize(request, db, p, "admin.compliance")
    u = db.execute(select(User).where(User.email == body.principal_ref.lower())).scalar_one_or_none()
    d = DsrRequest(tenant_id=p.tenant_id, principal_ref=body.principal_ref, principal_user_id=u.id if u else None, kind=body.kind, details=body.details,
                   due_at=utcnow() + timedelta(days=st.settings.dsr_due_days), created_by=p.user_id)
    db.add(d)
    db.flush()
    audit.append(db, p.actor(), "dsr.received", object_type="dsr", object_id=d.id, details={"kind": body.kind})
    db.commit()
    return d


@router.post("/admin/dsr/{dsr_id}/execute", response_model=schemas.DsrOut)
def execute_dsr(dsr_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> DsrRequest:
    """Access → export of the principal's personal data; erasure → pseudonymise subject to legal retention."""
    st = get_state(request)
    authorize(request, db, p, "admin.compliance")
    reason = require_stepup(request, p)
    d = db.get(DsrRequest, dsr_id)
    if d is None or d.tenant_id != p.tenant_id:
        raise not_found("request")
    if d.status in {"completed", "rejected", "partially_completed"}:
        raise conflict("closed", "request already closed")
    u = db.get(User, d.principal_user_id) if d.principal_user_id else None
    if d.kind == "access":
        if u is None:
            d.resolution = "no personal data held for this principal"
        else:
            key = f"t/{p.tenant_id}/dsr/{d.id}.json"
            st.storage.put_bytes(key, json.dumps(compliance.dsr_export(db, u), indent=2).encode(), "application/json")
            d.resolution = f"export prepared: {key}"
        d.status = "completed"
    elif d.kind == "erasure":
        if u is None:
            d.resolution = "no personal data held for this principal"
            d.status = "completed"
        else:
            d.resolution = compliance.dsr_erase(db, u, st.settings.retention_years)
            d.status = "partially_completed"
    else:
        raise bad_request("manual", "correction and grievance requests are completed manually with PATCH")
    d.handled_by = p.user_id
    d.completed_at = utcnow()
    audit.append(db, p.actor(), "dsr.executed", object_type="dsr", object_id=d.id, details={"kind": d.kind, "status": d.status, "reason": reason})
    db.commit()
    return d


@router.patch("/admin/dsr/{dsr_id}", response_model=schemas.DsrOut)
def patch_dsr(dsr_id: str, body: schemas.DsrPatchIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> DsrRequest:
    authorize(request, db, p, "admin.compliance")
    d = db.get(DsrRequest, dsr_id)
    if d is None or d.tenant_id != p.tenant_id:
        raise not_found("request")
    d.status = body.status
    d.resolution = body.resolution or d.resolution
    d.handled_by = p.user_id
    if body.status in {"completed", "rejected", "partially_completed"}:
        d.completed_at = utcnow()
    audit.append(db, p.actor(), "dsr.updated", object_type="dsr", object_id=d.id, details={"status": body.status})
    db.commit()
    return d


# ------------------------------------------------------------------ incidents (CERT-In)


def incident_out(i: Incident) -> schemas.IncidentOut:
    out = schemas.IncidentOut.model_validate(i)
    out.overdue = bool(i.certin_reportable and i.reported_at is None and i.report_due_at and i.report_due_at < utcnow())
    return out


@router.get("/admin/incidents", response_model=list[schemas.IncidentOut])
def list_incidents(request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[schemas.IncidentOut]:
    authorize(request, db, p, "ops.manage")
    return [incident_out(i) for i in db.execute(select(Incident).where(Incident.tenant_id == p.tenant_id).order_by(Incident.detected_at.desc())).scalars()]


@router.post("/admin/incidents", response_model=schemas.IncidentOut, status_code=201)
def create_incident(body: schemas.IncidentIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.IncidentOut:
    authorize(request, db, p, "ops.manage")
    detected = body.detected_at or utcnow()
    i = Incident(tenant_id=p.tenant_id, severity=body.severity, title=body.title, description=body.description, detected_at=detected,
                 certin_reportable=body.certin_reportable, report_due_at=compliance.incident_due(detected, body.certin_reportable), created_by=p.user_id)
    db.add(i)
    db.flush()
    if body.certin_reportable:
        from fsweb.services.common import notify, users_with_role

        for u in users_with_role(db, p.tenant_id, "firm_admin") + users_with_role(db, p.tenant_id, "system_admin"):
            notify(db, p.tenant_id, u.email, "CERT-In reportable incident: report within 6 hours",
                   f"Incident '{body.title}' must be reported to CERT-In by {i.report_due_at:%d-%b-%Y %H:%M} UTC.", "incident")
    audit.append(db, p.actor(), "incident.recorded", object_type="incident", object_id=i.id, details={"severity": body.severity, "certin": body.certin_reportable})
    db.commit()
    return incident_out(i)


@router.patch("/admin/incidents/{incident_id}", response_model=schemas.IncidentOut)
def patch_incident(incident_id: str, body: schemas.IncidentPatchIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.IncidentOut:
    authorize(request, db, p, "ops.manage")
    i = db.get(Incident, incident_id)
    if i is None or i.tenant_id != p.tenant_id:
        raise not_found("incident")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(i, k, v)
    audit.append(db, p.actor(), "incident.updated", object_type="incident", object_id=i.id, details=body.model_dump(exclude_none=True, mode="json"))
    db.commit()
    return incident_out(i)


# ------------------------------------------------------------------ backups (C13)


@router.get("/admin/backups", response_model=list[schemas.BackupLogOut])
def list_backups(request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[BackupLog]:
    if not p.is_firm_admin:
        authorize(request, db, p, "backup.manage")
    return list(db.execute(select(BackupLog).where(BackupLog.tenant_id == p.tenant_id).order_by(BackupLog.started.desc()).limit(500)).scalars())


@router.post("/admin/backups", response_model=schemas.BackupLogOut, status_code=201)
def record_backup(body: schemas.BackupLogIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> BackupLog:
    value = authorize(request, db, p, "backup.manage")
    if value != "allow":
        raise forbidden("only the System Admin records backup operations")
    b = BackupLog(tenant_id=p.tenant_id, kind=body.kind, status=body.status, size=body.size, location=body.location, verified=body.verified,
                  started=body.started or utcnow(), finished=body.finished, details_json=body.details, created_by=p.user_id)
    if body.kind == "restore_request":
        b.status = "pending_approval"
    db.add(b)
    db.flush()
    audit.append(db, p.actor(), "backup.recorded", object_type="backup", object_id=b.id, details={"kind": body.kind, "status": b.status})
    db.commit()
    return b


@router.post("/admin/backups/{backup_id}/approve-restore", response_model=schemas.BackupLogOut)
def approve_restore(backup_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> BackupLog:
    """A Firm Admin approves the restore of client data (05 §3 'approve restore of client data')."""
    value = authorize(request, db, p, "backup.manage")
    if value != "approve_restore":
        raise forbidden("restores of client data are approved by a Firm Admin")
    reason = require_stepup(request, p)
    b = db.get(BackupLog, backup_id)
    if b is None or b.tenant_id != p.tenant_id or b.kind != "restore_request":
        raise not_found("restore request")
    if b.status != "pending_approval":
        raise conflict("not_pending", "restore request is not pending")
    if b.created_by == p.user_id:
        raise forbidden("segregation of duties", "segregation_of_duties")
    b.status = "ok"
    b.details_json = {**(b.details_json or {}), "approved_by": p.user_id, "approved_at": utcnow().isoformat(), "reason": reason}
    audit.append(db, p.actor(), "backup.restore_approved", object_type="backup", object_id=b.id, details={"reason": reason})
    db.commit()
    return b


# ------------------------------------------------------------------ ITGC releases


@router.get("/admin/releases", response_model=list[schemas.ReleaseRecordOut])
def list_releases(request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[Release]:
    authorize(request, db, p, "ops.manage")
    return list(db.execute(select(Release).where(Release.tenant_id == p.tenant_id).order_by(Release.created_at.desc())).scalars())


@router.post("/admin/releases", response_model=schemas.ReleaseRecordOut, status_code=201)
def create_release(body: schemas.ReleaseRecordIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Release:
    authorize(request, db, p, "ops.manage")
    if body.rule_change and not body.regression_evidence:
        raise bad_request("regression_required", "rule / template changes need golden-regression evidence (E-ITGC-02)")
    r = Release(tenant_id=p.tenant_id, created_by=p.user_id, **body.model_dump())
    db.add(r)
    db.flush()
    audit.append(db, p.actor(), "release.recorded", object_type="release", object_id=r.id, details={"version": body.version, "ticket": body.ticket_ref})
    db.commit()
    return r


@router.post("/admin/releases/{release_id}/approve", response_model=schemas.ReleaseRecordOut)
def approve_release(release_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Release:
    """E-ITGC-01: approver ≠ developer."""
    authorize(request, db, p, "ops.manage")
    r = db.get(Release, release_id)
    if r is None or r.tenant_id != p.tenant_id:
        raise not_found("release")
    if r.created_by == p.user_id:
        raise deny(request, db, p, "ops.manage", "change management: the approver must not be the developer", code="segregation_of_duties")
    r.approver_id = p.user_id
    r.approved_at = utcnow()
    audit.append(db, p.actor(), "release.approved", object_type="release", object_id=r.id)
    db.commit()
    return r


@router.post("/admin/releases/{release_id}/deployed", response_model=schemas.ReleaseRecordOut)
def mark_deployed(release_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Release:
    authorize(request, db, p, "ops.manage")
    r = db.get(Release, release_id)
    if r is None or r.tenant_id != p.tenant_id:
        raise not_found("release")
    if not r.approved_at and not r.emergency:
        raise conflict("not_approved", "approve the release before deploying (emergency changes are reviewed within 48 hours)")
    r.deployed_at = utcnow()
    audit.append(db, p.actor(), "release.deployed", object_type="release", object_id=r.id, details={"emergency": r.emergency})
    db.commit()
    return r


# ------------------------------------------------------------------ usage, evidence, health


@router.get("/admin/usage", response_model=schemas.UsageOut)
def usage(request: Request, month: str | None = None, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.UsageOut:
    """Per-client monthly usage (D7 billing basis, E-BIZ-01)."""
    authorize(request, db, p, "admin.compliance")
    month = month or utcnow().strftime("%Y-%m")
    names = {c.id: c.name for c in db.execute(select(Client).where(Client.tenant_id == p.tenant_id)).scalars()}
    rows = [{"client_id": u.client_id, "client": names.get(u.client_id, u.client_id), "engagements": u.engagements, "runs": u.runs, "renders": u.renders,
             "ai_calls": u.ai_calls} for u in db.execute(select(BillingUsage).where(BillingUsage.tenant_id == p.tenant_id, BillingUsage.month == month)).scalars()]
    return schemas.UsageOut(month=month, rows=rows)


@router.get("/admin/evidence-pack", responses={200: {"content": {"application/zip": {}}}})
def evidence_pack(request: Request, quarter: str, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Response:
    authorize(request, db, p, "admin.compliance")
    if len(quarter) != 6 or quarter[4] != "Q":
        raise bad_request("invalid_quarter", "use e.g. 2026Q3")
    chain = audit.verify_chain(db, p.tenant_id)
    data = compliance.build_evidence_pack(db, p.tenant_id, quarter, chain)
    audit.append(db, p.actor(), "evidence_pack.exported", details={"quarter": quarter, "chain_ok": chain.ok})
    db.commit()
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="evidence-pack-{quarter}.zip"'})


@router.get("/admin/health", response_model=schemas.HealthOut)
def health(request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.HealthOut:
    st = get_state(request)
    authorize(request, db, p, "ops.manage")
    db.commit()  # release the request transaction before reading system-wide figures (RLS hides other clients' jobs)
    with st.db.scope(RLSContext(p.tenant_id, p.user_id, "system")) as s:
        workers = [{"id": w.id, "excel_build": w.excel_build, "last_heartbeat_at": w.last_heartbeat_at.isoformat(),
                    "fresh": w.last_heartbeat_at > utcnow() - timedelta(minutes=2)} for w in s.execute(select(RenderWorker)).scalars()]
        depth = queue_depth(s)
        running = int(s.execute(select(func.count()).select_from(Run).where(Run.tenant_id == p.tenant_id, Run.status == "running")).scalar_one())
    last_backup = db.execute(select(BackupLog).where(BackupLog.tenant_id == p.tenant_id, BackupLog.kind.in_(["pg_full", "pg_diff", "objects"]))
                             .order_by(BackupLog.started.desc())).scalars().first()
    last_drill = db.execute(select(BackupLog).where(BackupLog.tenant_id == p.tenant_id, BackupLog.kind == "restore_drill")
                            .order_by(BackupLog.started.desc())).scalars().first()
    head = db.get(AuditHead, p.tenant_id)
    return schemas.HealthOut(
        status="ok", version=__version__, engine=st.engine.name, engine_version=st.engine.engine_version(), database=True, storage=st.storage.health(),
        queue_depth={**depth, "running": running}, render_workers=workers,
        last_backup=None if last_backup is None else {"kind": last_backup.kind, "started": last_backup.started.isoformat(), "status": last_backup.status},
        last_restore_drill=None if last_drill is None else {"started": last_drill.started.isoformat(), "status": last_drill.status, "verified": last_drill.verified},
        audit_head=None if head is None else {"seq": head.seq, "hash": head.event_hash, "updated_at": head.updated_at.isoformat()},
    )
