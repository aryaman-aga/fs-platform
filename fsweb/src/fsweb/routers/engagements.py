"""Engagements: lifecycle (06 §1), lock / dual unlock, configuration, disclosure
checklist (E-PREP-02) and management-representation items."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsweb import audit, schemas
from fsweb.authz.deps import (
    Principal,
    authorize,
    check_if_match,
    etag,
    get_db,
    get_state,
    permission_value,
    require_principal,
    require_stepup,
)
from fsweb.authz.permissions import DENY
from fsweb.db import utcnow
from fsweb.errors import bad_request, conflict, forbidden, not_found, unprocessable
from fsweb.models import (
    Client,
    DisclosureChecklistItem,
    Engagement,
    Entity,
    FileVersion,
    FindingRow,
    MgmtItem,
    ReviewNote,
    UnlockRequest,
    User,
)
from fsweb.services import engagements as eng_svc
from fsweb.services import workflow
from fsweb.services.common import get_engagement, notify, users_with_role

router = APIRouter(tags=["engagements"])


def engagement_out(db: Session, e: Engagement, *, counts: bool = False) -> schemas.EngagementOut:
    c = db.get(Client, e.client_id)
    ent = db.get(Entity, e.entity_id)
    out = schemas.EngagementOut.model_validate(e)
    out.client_name = c.name if c else None
    out.entity_name = ent.name if ent else None
    if counts:
        wb = workflow.latest_workbook_version(db, e)
        check = workflow.latest_run(db, e, "check", version_id=wb.id) if wb else None
        out.open_high_findings = workflow.high_findings(db, check) if check else None
        out.open_review_notes = int(db.execute(select(func.count()).select_from(ReviewNote).where(
            ReviewNote.engagement_id == e.id, ReviewNote.status != "Cleared")).scalar_one())
    return out


def load_engagement(request: Request, db: Session, p: Principal, engagement_id: str, permission: str = "engagement.view", **kw: object) -> Engagement:
    e = get_engagement(db, engagement_id)
    if e.tenant_id != p.tenant_id:
        raise not_found("engagement")
    if not p.is_firm_admin and not p.is_member(e.client_id):
        raise not_found("engagement")
    authorize(request, db, p, permission, engagement=e, **kw)  # type: ignore[arg-type]
    return e


def load_engagement_header(request: Request, db: Session, p: Principal, engagement_id: str) -> Engagement:
    """The engagement header (name, team, dates, status). The team reads it through ``engagement.view``; a Firm
    Admin, who assigns teams but does not see engagement contents (05 §2), through ``engagement.assign``."""
    e = get_engagement(db, engagement_id)
    if e.tenant_id == p.tenant_id and p.is_firm_admin and permission_value(db, p, "engagement.view", e.client_id) == DENY:
        authorize(request, db, p, "engagement.assign", engagement=e)
        return e
    return load_engagement(request, db, p, engagement_id)


@router.get("/engagements", response_model=list[schemas.EngagementOut])
def list_engagements(request: Request, status: str | None = None, client_id: str | None = None, p: Principal = Depends(require_principal),
                     db: Session = Depends(get_db)) -> list[schemas.EngagementOut]:
    stmt = select(Engagement).where(Engagement.tenant_id == p.tenant_id).order_by(Engagement.period_end.desc(), Engagement.name)
    if not p.is_firm_admin:
        stmt = stmt.where(Engagement.client_id.in_(p.client_ids))
    if status:
        stmt = stmt.where(Engagement.status == status)
    if client_id:
        stmt = stmt.where(Engagement.client_id == client_id)
    return [engagement_out(db, e, counts=True) for e in db.execute(stmt).scalars()]


@router.post("/engagements", response_model=schemas.EngagementOut, status_code=201)
def create_engagement(body: schemas.EngagementIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.EngagementOut:
    ent = db.get(Entity, body.entity_id)
    if ent is None or ent.tenant_id != p.tenant_id:
        raise not_found("entity")
    authorize(request, db, p, "client.manage", client_id=ent.client_id)
    for uid in (body.partner_id, body.manager_id):
        if uid and db.get(User, uid) is None:
            raise bad_request("unknown_user", "partner / manager not found")
    e = Engagement(tenant_id=p.tenant_id, client_id=ent.client_id, entity_id=ent.id, name=body.name, type=body.type, period_end=body.period_end,
                   periods_in_scope_json=body.periods_in_scope, partner_id=body.partner_id, manager_id=body.manager_id, due_date=body.due_date,
                   status="DRAFT_SETUP", created_by=p.user_id)
    db.add(e)
    db.flush()
    try:
        eng_svc.seed_engagement(db, e, p.user_id)
    except ValueError as exc:
        raise unprocessable("invalid_periods", str(exc)) from exc
    audit.append(db, p.actor(), "engagement.created", client_id=e.client_id, engagement_id=e.id, object_type="engagement", object_id=e.id,
                 details={"type": e.type, "period_end": e.period_end.isoformat()})
    db.commit()
    return engagement_out(db, e)


@router.get("/engagements/{engagement_id}", response_model=schemas.EngagementOut)
def get_engagement_ep(engagement_id: str, request: Request, response: Response, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.EngagementOut:
    e = load_engagement_header(request, db, p, engagement_id)
    response.headers["ETag"] = etag(e.version)
    return engagement_out(db, e, counts=True)


@router.patch("/engagements/{engagement_id}", response_model=schemas.EngagementOut)
def patch_engagement(engagement_id: str, body: schemas.EngagementPatchIn, request: Request, response: Response, p: Principal = Depends(require_principal),
                     db: Session = Depends(get_db)) -> schemas.EngagementOut:
    e = load_engagement_header(request, db, p, engagement_id)
    changes = body.model_dump(exclude_none=True)
    if {"partner_id", "manager_id", "name", "due_date", "ai_enabled"} & changes.keys():
        authorize(request, db, p, "engagement.assign", engagement=e)
    if "legal_hold" in changes:
        authorize(request, db, p, "admin.compliance" if p.is_firm_admin else "engagement.lock", engagement=e)
    check_if_match(request, e.version)
    before = {k: getattr(e, k) for k in changes}
    for k, v in changes.items():
        setattr(e, k, v)
    audit.append(db, p.actor(), "engagement.updated", client_id=e.client_id, engagement_id=e.id, object_type="engagement", object_id=e.id,
                 before=before, after=changes, details={"fields": sorted(changes)})
    db.commit()
    response.headers["ETag"] = etag(e.version)
    return engagement_out(db, e)


@router.get("/engagements/{engagement_id}/gates", response_model=schemas.GatesOut)
def gates(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.GatesOut:
    e = load_engagement(request, db, p, engagement_id)
    return schemas.GatesOut(final_pdf=[schemas.GateOut(**g.as_dict()) for g in workflow.final_pdf_gates(db, e)],
                            final_lock=[schemas.GateOut(**g.as_dict()) for g in workflow.final_lock_gates(db, e)])


@router.post("/engagements/{engagement_id}/transition", response_model=schemas.EngagementOut)
def transition(engagement_id: str, body: schemas.TransitionIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.EngagementOut:
    e = load_engagement(request, db, p, engagement_id)
    key = (e.status, body.to)
    if key not in workflow.MANUAL:
        raise conflict("invalid_transition", f"cannot move from {e.status} to {body.to}")
    perm = workflow.MANUAL[key]
    attrs = {"action_class": "archive"} if key == ("FINAL_LOCKED", "ARCHIVED") else None
    authorize(request, db, p, perm, engagement=e, attrs=attrs)
    st = get_state(request)
    if key == ("DRAFT_SETUP", "DATA_RECEIVED") and workflow.latest_workbook_version(db, e) is None:
        raise conflict("gate_failed", "upload a workbook first")
    if key == ("CHECKED", "REVIEW"):
        wb = workflow.latest_workbook_version(db, e)
        check = workflow.latest_run(db, e, "check", version_id=wb.id) if wb else None
        if check is None or workflow.high_findings(db, check) > 0:
            raise conflict("gate_failed", "the latest check has open High findings; plan and apply fixes first")
    if key == ("DRAFT_RELEASED_TO_CLIENT", "MGMT_CONFIRMED"):
        pending = int(db.execute(select(func.count()).select_from(MgmtItem).where(MgmtItem.engagement_id == e.id, MgmtItem.status != "confirmed")).scalar_one())
        if pending:
            raise conflict("gate_failed", f"{pending} management representation item(s) not confirmed")
    if key == ("MGMT_CONFIRMED", "FINAL_LOCKED"):
        gates_ = [g for g in workflow.final_lock_gates(db, e) if g.code != "mgmt_confirmed"]
        failed = [g.as_dict() for g in gates_ if not g.ok]
        if failed:
            raise conflict("gate_failed", "FINAL_LOCKED preconditions are not met", gates=failed)
        _lock_files(db, st, e)
        workflow.mark_locked(e, p.user_id)
        audit.append(db, p.actor(), "engagement.locked", client_id=e.client_id, engagement_id=e.id, object_type="engagement", object_id=e.id,
                     details={"reason": body.reason, "partner_approval": p.user_id})
    workflow.set_status(db, e, body.to, p.actor(), reason=body.reason, automatic=False)
    db.commit()
    return engagement_out(db, e)


def _lock_files(db: Session, st: object, e: Engagement) -> None:
    """WORM (object lock) on the final workbook and PDF for the retention period."""
    from fsweb.state import AppState

    assert isinstance(st, AppState)
    until = utcnow() + timedelta(days=365 * st.settings.retention_years)
    wb = workflow.latest_workbook_version(db, e)
    render = workflow.latest_run(db, e, "render", version_id=wb.id, mode="final") if wb else None
    targets = [wb] if wb else []
    if render and render.output_version_id:
        pdf = db.get(FileVersion, render.output_version_id)
        if pdf:
            targets.append(pdf)
    for v in targets:
        st.storage.set_worm(v.object_key, until)
        v.worm_until = until


# ------------------------------------------------------------------ dual unlock


@router.post("/engagements/{engagement_id}/unlock-requests", response_model=schemas.UnlockRequestOut, status_code=201)
def request_unlock(engagement_id: str, body: schemas.UnlockRequestIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> UnlockRequest:
    e = load_engagement(request, db, p, engagement_id, "engagement.unlock")
    if e.status != "FINAL_LOCKED":
        raise conflict("not_locked", "only a FINAL_LOCKED engagement can be unlocked")
    ur = UnlockRequest(tenant_id=p.tenant_id, client_id=e.client_id, engagement_id=e.id, reason=body.reason, requested_by=p.user_id,
                       partner_approved_by=p.user_id, created_by=p.user_id)
    db.add(ur)
    db.flush()
    for admin in users_with_role(db, p.tenant_id, "firm_admin"):
        notify(db, p.tenant_id, admin.email, "Unlock request awaiting your co-approval", "A Partner requested to unlock a locked engagement.", "approval")
    audit.append(db, p.actor(), "engagement.unlock_requested", client_id=e.client_id, engagement_id=e.id, object_type="unlock_request", object_id=ur.id,
                 details={"reason": body.reason})
    db.commit()
    return ur


@router.post("/unlock-requests/{request_id}/approve", response_model=schemas.EngagementOut)
def approve_unlock(request_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.EngagementOut:
    """Second half of the dual control: a Firm Admin (not the requester) co-approves."""
    ur = db.get(UnlockRequest, request_id)
    if ur is None or ur.tenant_id != p.tenant_id:
        raise not_found("unlock request")
    e = get_engagement(db, ur.engagement_id)
    authorize(request, db, p, "engagement.unlock_coapprove", engagement=e)
    reason = require_stepup(request, p)
    if ur.status != "pending":
        raise conflict("not_pending", "request already decided")
    if p.user_id in {ur.requested_by, ur.partner_approved_by}:
        raise forbidden("dual control: a different person must co-approve", "segregation_of_duties")
    ur.admin_approved_by = p.user_id
    ur.status = "completed"
    ur.completed_at = utcnow()
    e.locked_at = None
    e.locked_by = None
    e.version_line += 1
    workflow.set_status(db, e, "REVIEW", p.actor(), reason=f"unlocked: {ur.reason}")
    audit.append(db, p.actor(), "engagement.unlocked", client_id=e.client_id, engagement_id=e.id, object_type="unlock_request", object_id=ur.id,
                 details={"partner": ur.partner_approved_by, "firm_admin": p.user_id, "reason": ur.reason, "admin_reason": reason,
                          "new_version_line": e.version_line})
    db.commit()
    return engagement_out(db, e)


@router.get("/engagements/{engagement_id}/unlock-requests", response_model=list[schemas.UnlockRequestOut])
def list_unlock_requests(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[UnlockRequest]:
    e = get_engagement(db, engagement_id)
    if not p.is_firm_admin:
        e = load_engagement(request, db, p, engagement_id)
    return list(db.execute(select(UnlockRequest).where(UnlockRequest.engagement_id == e.id).order_by(UnlockRequest.created_at.desc())).scalars())


# ------------------------------------------------------------------ configuration


@router.get("/engagements/{engagement_id}/config", response_model=schemas.ConfigOut)
def get_config(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.ConfigOut:
    e = load_engagement(request, db, p, engagement_id, "finding.view")
    row = eng_svc.latest_config_row(db, e)
    if row is None:
        from fscore.config import dump_config

        return schemas.ConfigOut(engagement_id=e.id, yaml_text=dump_config(eng_svc.default_config(db, e)), version=0)
    return schemas.ConfigOut(engagement_id=e.id, yaml_text=row.yaml_text, version=row.version, created_at=row.created_at)


@router.put("/engagements/{engagement_id}/config", response_model=schemas.ConfigOut)
def put_config(engagement_id: str, body: schemas.ConfigIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.ConfigOut:
    e = load_engagement(request, db, p, engagement_id, "config.edit")
    try:
        row = eng_svc.save_config(db, e, body.yaml_text, p.user_id)
    except Exception as exc:
        raise unprocessable("invalid_config", str(exc)[:500]) from exc
    audit.append(db, p.actor(), "config.saved", client_id=e.client_id, engagement_id=e.id, object_type="config", object_id=row.id, after=body.yaml_text,
                 details={"version": row.version})
    eng_svc.data_changed(db, e, p.actor(), None, reason="configuration changed")
    db.commit()
    return schemas.ConfigOut(engagement_id=e.id, yaml_text=row.yaml_text, version=row.version, created_at=row.created_at)


# ------------------------------------------------------------------ disclosure checklist


@router.get("/engagements/{engagement_id}/disclosure-checklist", response_model=list[schemas.ChecklistItemOut])
def get_checklist(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[DisclosureChecklistItem]:
    e = load_engagement(request, db, p, engagement_id, "finding.view")
    return list(db.execute(select(DisclosureChecklistItem).where(DisclosureChecklistItem.engagement_id == e.id).order_by(DisclosureChecklistItem.item_code)).scalars())


@router.put("/engagements/{engagement_id}/disclosure-checklist/{item_code}", response_model=schemas.ChecklistItemOut)
def answer_checklist(engagement_id: str, item_code: str, body: schemas.ChecklistAnswerIn, request: Request, p: Principal = Depends(require_principal),
                     db: Session = Depends(get_db)) -> DisclosureChecklistItem:
    e = load_engagement(request, db, p, engagement_id, "disclosure.edit")
    item = db.execute(select(DisclosureChecklistItem).where(DisclosureChecklistItem.engagement_id == e.id, DisclosureChecklistItem.item_code == item_code)).scalar_one_or_none()
    if item is None:
        raise not_found("checklist item")
    before = item.answer
    item.answer = body.answer
    item.evidence_ref = body.evidence_ref
    item.auto = False
    item.answered_by = p.user_id
    item.answered_at = utcnow()
    audit.append(db, p.actor(), "checklist.answered", client_id=e.client_id, engagement_id=e.id, object_type="checklist", object_id=item.id,
                 details={"item": item_code, "before": before, "after": body.answer})
    db.commit()
    return item


AUTO_RULES: dict[str, tuple[str, ...]] = {
    "SIGNATORIES": ("TXT-01",),
    "RATIOS": ("RATIO-",),
    "GC-ASSESS": ("GC-",),
    "RPT-COMPLETE": ("RPT-",),
}


@router.post("/engagements/{engagement_id}/disclosure-checklist/auto", response_model=list[schemas.ChecklistItemOut])
def auto_answer(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[DisclosureChecklistItem]:
    """Auto-answer what the engine can detect from the latest check run (E-PREP-02)."""
    e = load_engagement(request, db, p, engagement_id, "disclosure.edit")
    wb = workflow.latest_workbook_version(db, e)
    check = workflow.latest_run(db, e, "check", version_id=wb.id) if wb else None
    if check is None:
        raise conflict("no_check", "run checks on the latest workbook first")
    rules = [r for (r,) in db.execute(select(FindingRow.rule_id).where(FindingRow.run_id == check.id, FindingRow.severity.in_(["High", "Medium"]),
                                                                       FindingRow.status.not_in(["Not an issue", "Resolved"]))).all()]
    items = list(db.execute(select(DisclosureChecklistItem).where(DisclosureChecklistItem.engagement_id == e.id)).scalars())
    for item in items:
        prefixes = AUTO_RULES.get(item.item_code)
        if not prefixes or (item.answer is not None and not item.auto):
            continue
        clean = not any(r.startswith(prefixes) for r in rules)
        item.answer = "yes" if clean else None
        item.auto = True
        item.evidence_ref = f"run:{check.id}"
        item.answered_by = p.user_id if clean else None
        item.answered_at = utcnow() if clean else None
    audit.append(db, p.actor(), "checklist.auto_answered", client_id=e.client_id, engagement_id=e.id, object_type="run", object_id=check.id)
    db.commit()
    return items


# ------------------------------------------------------------------ management representations


@router.get("/engagements/{engagement_id}/mgmt-items", response_model=list[schemas.MgmtItemOut])
def list_mgmt_items(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[MgmtItem]:
    e = load_engagement(request, db, p, engagement_id)
    return list(db.execute(select(MgmtItem).where(MgmtItem.engagement_id == e.id).order_by(MgmtItem.created_at)).scalars())


@router.post("/engagements/{engagement_id}/mgmt-items", response_model=schemas.MgmtItemOut, status_code=201)
def add_mgmt_item(engagement_id: str, body: schemas.MgmtItemIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> MgmtItem:
    e = load_engagement(request, db, p, engagement_id, "release.client")
    m = MgmtItem(tenant_id=p.tenant_id, client_id=e.client_id, engagement_id=e.id, text=body.text, created_by=p.user_id)
    db.add(m)
    db.flush()
    audit.append(db, p.actor(), "mgmt_item.created", client_id=e.client_id, engagement_id=e.id, object_type="mgmt_item", object_id=m.id)
    db.commit()
    return m
