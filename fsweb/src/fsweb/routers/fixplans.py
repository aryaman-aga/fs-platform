"""Fix plans with maker-checker (C2), balancing limited to rounding (C3) and typed-text
narrative edits (C6)."""

from __future__ import annotations

import re
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from fsweb import audit, schemas
from fsweb.authz.deps import Principal, authorize, deny, get_db, require_principal
from fsweb.authz.permissions import ALLOW, MAKER_CHECKER
from fsweb.db import utcnow
from fsweb.errors import bad_request, conflict, not_found, unprocessable
from fsweb.models import EditRow, Engagement, FixPlanRow, NarrativeEdit, Run
from fsweb.routers.engagements import load_engagement
from fsweb.routers.runs import load_run
from fsweb.services import engagements as eng_svc
from fsweb.services import workflow
from fsweb.services.common import create_run, get_engagement, notify, users_with_role

router = APIRouter(tags=["fixes"])


def plan_out(db: Session, fp: FixPlanRow) -> schemas.FixPlanOut:
    out = schemas.FixPlanOut.model_validate(fp)
    plan = fp.plan_json or {}
    out.balancing = plan.get("balancing", [])
    out.printed_moves = plan.get("printed_moves", [])[:1000]
    out.unresolved = plan.get("unresolved", [])
    out.edits = [schemas.EditOut.model_validate(e) for e in db.execute(select(EditRow).where(EditRow.fix_plan_id == fp.id).order_by(EditRow.eid)).scalars()]
    return out


def _plan(request: Request, db: Session, p: Principal, plan_id: str, permission: str, **kw: object) -> tuple[FixPlanRow, Engagement]:
    fp = db.get(FixPlanRow, plan_id)
    if fp is None or fp.tenant_id != p.tenant_id:
        raise not_found("fix plan")
    e = get_engagement(db, fp.engagement_id)
    if not p.is_firm_admin and not p.is_member(e.client_id):
        raise not_found("fix plan")
    authorize(request, db, p, permission, engagement=e, **kw)  # type: ignore[arg-type]
    return fp, e


@router.post("/runs/{run_id}/fix-plan", response_model=schemas.FixPlanOut, status_code=202)
def propose(run_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.FixPlanOut:
    run, e = load_run(request, db, p, run_id, "fix.propose")
    if run.kind != "check" or run.status != "succeeded":
        raise conflict("not_a_check", "fix plans are proposed from a successful check run")
    latest = workflow.latest_workbook_version(db, e)
    base_id = run.input_version_ids[0]
    if latest is None or latest.id != base_id:
        raise conflict("stale_check", "the check ran on an older version; re-run checks on the latest version")
    for old in db.execute(select(FixPlanRow).where(FixPlanRow.engagement_id == e.id, FixPlanRow.status.in_(["planning", "proposed", "pending_balancing"]))).scalars():
        old.status = "superseded"
    fp = FixPlanRow(tenant_id=p.tenant_id, client_id=e.client_id, engagement_id=e.id, run_id=run.id, base_version_id=base_id, status="planning",
                    proposed_by=p.user_id, created_by=p.user_id)
    db.add(fp)
    db.flush()
    plan_run, _ = create_run(db, e, "plan", created_by=p.user_id, input_versions=[latest], params={"fix_plan_id": fp.id},
                             correlation_id=p.correlation_id, actor=p.actor())
    fp.plan_run_id = plan_run.id
    db.commit()
    return plan_out(db, fp)


@router.get("/fix-plans/{plan_id}", response_model=schemas.FixPlanOut)
def get_plan(plan_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.FixPlanOut:
    fp, _ = _plan(request, db, p, plan_id, "finding.view")
    return plan_out(db, fp)


@router.get("/engagements/{engagement_id}/fix-plans", response_model=list[schemas.FixPlanOut])
def list_plans(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[schemas.FixPlanOut]:
    e = load_engagement(request, db, p, engagement_id, "finding.view")
    return [plan_out(db, fp) for fp in db.execute(select(FixPlanRow).where(FixPlanRow.engagement_id == e.id).order_by(FixPlanRow.created_at.desc())).scalars()]


def _balancing_within_threshold(db: Session, e: Engagement, fp: FixPlanRow) -> None:
    threshold = eng_svc.config_for(db, e).policy.balancing_threshold
    for b in (fp.plan_json or {}).get("balancing", []):
        if b.get("status") == "applied" and abs(Decimal(str(b.get("adjustment", "0")))) > threshold:
            raise conflict("balancing_too_large", f"a balancing adjustment exceeds the {threshold} rounding threshold (C3); investigate instead")


@router.post("/fix-plans/{plan_id}/approve", response_model=schemas.FixPlanOut)
def approve(plan_id: str, body: schemas.FixPlanApproveIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.FixPlanOut:
    fp, e = _plan(request, db, p, plan_id, "fix.approve")
    if fp.proposed_by == p.user_id:  # C2 / C10: approver ≠ proposer, whatever the role
        raise deny(request, db, p, "fix.approve", "segregation of duties: you cannot approve your own plan", engagement_id=e.id,
                   client_id=e.client_id, code="segregation_of_duties")
    authorize(request, db, p, "fix.approve", engagement=e, owner_id=fp.proposed_by)
    if fp.status != "proposed":
        raise conflict("not_proposed", f"plan is {fp.status}")
    if body.plan_sha256 != fp.plan_sha256:
        raise conflict("plan_changed", "the plan changed since you reviewed it; reload before approving (approval records the plan hash)")
    approval_ids = {er.eid for er in db.execute(select(EditRow).where(EditRow.fix_plan_id == fp.id, EditRow.fix_kind == "approval")).scalars()}
    unknown = set(body.approved_edit_ids) - approval_ids
    if unknown:
        raise bad_request("invalid_edit_ids", f"not approval items of this plan: {sorted(unknown)}")
    _balancing_within_threshold(db, e, fp)
    fp.approved_by = p.user_id
    fp.approved_at = utcnow()
    fp.approved_plan_sha256 = fp.plan_sha256
    fp.approved_edit_ids = sorted(body.approved_edit_ids)
    for er in db.execute(select(EditRow).where(EditRow.fix_plan_id == fp.id)).scalars():
        er.approved = er.fix_kind == "auto" or er.eid in body.approved_edit_ids
    fully = True
    if fp.has_balancing:
        from fsweb.authz.deps import permission_value

        bal = permission_value(db, p, "balancing.approve", e.client_id)
        if bal == ALLOW:
            fp.balancing_approved_by = p.user_id
            fp.balancing_approved_at = utcnow()
        elif bal == MAKER_CHECKER:
            fully = False
            for partner in users_with_role(db, p.tenant_id, "partner", e.client_id):
                notify(db, p.tenant_id, partner.email, "Balancing adjustment awaiting Partner approval", "A fix plan with balancing lines needs your approval.", "approval")
        else:
            raise deny(request, db, p, "balancing.approve", "your role cannot approve balancing adjustments", engagement_id=e.id, client_id=e.client_id)
    fp.status = "approved" if fully else "pending_balancing"
    audit.append(db, p.actor(), "fixplan.approved", client_id=e.client_id, engagement_id=e.id, object_type="fix_plan", object_id=fp.id,
                 after=fp.plan_sha256, details={"approved_items": fp.approved_edit_ids, "balancing_approved": bool(fp.balancing_approved_by), "status": fp.status})
    if fully:
        workflow.advance(db, e, {"FIX_PLANNED"}, "FIX_APPROVED", p.actor(), "fix plan approved")
    db.commit()
    return plan_out(db, fp)


@router.post("/fix-plans/{plan_id}/approve-balancing", response_model=schemas.FixPlanOut)
def approve_balancing(plan_id: str, body: schemas.BalancingApproveIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.FixPlanOut:
    """The checker for a Manager-approved balancing adjustment (05 §3: Manager = M)."""
    fp, e = _plan(request, db, p, plan_id, "balancing.approve")
    from fsweb.authz.deps import permission_value

    if permission_value(db, p, "balancing.approve", e.client_id) != ALLOW:
        raise deny(request, db, p, "balancing.approve", "a Partner must check this balancing adjustment", engagement_id=e.id, client_id=e.client_id,
                   code="maker_checker")
    if p.user_id in {fp.proposed_by, fp.approved_by}:
        raise deny(request, db, p, "balancing.approve", "segregation of duties: the checker must be a different person", engagement_id=e.id,
                   client_id=e.client_id, code="segregation_of_duties")
    if fp.status != "pending_balancing":
        raise conflict("not_pending", f"plan is {fp.status}")
    if body.plan_sha256 != fp.approved_plan_sha256:
        raise conflict("plan_changed", "the plan changed since the first approval")
    _balancing_within_threshold(db, e, fp)
    fp.balancing_approved_by = p.user_id
    fp.balancing_approved_at = utcnow()
    fp.status = "approved"
    audit.append(db, p.actor(), "fixplan.balancing_approved", client_id=e.client_id, engagement_id=e.id, object_type="fix_plan", object_id=fp.id,
                 after=fp.plan_sha256)
    workflow.advance(db, e, {"FIX_PLANNED"}, "FIX_APPROVED", p.actor(), "balancing adjustment approved")
    db.commit()
    return plan_out(db, fp)


@router.post("/fix-plans/{plan_id}/reject", response_model=schemas.FixPlanOut)
def reject(plan_id: str, body: schemas.RejectIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.FixPlanOut:
    fp, e = _plan(request, db, p, plan_id, "fix.approve")
    if fp.status not in {"proposed", "pending_balancing"}:
        raise conflict("not_proposed", f"plan is {fp.status}")
    fp.status = "rejected"
    fp.rejection_reason = body.reason
    audit.append(db, p.actor(), "fixplan.rejected", client_id=e.client_id, engagement_id=e.id, object_type="fix_plan", object_id=fp.id, details={"reason": body.reason})
    workflow.advance(db, e, {"FIX_PLANNED", "FIX_APPROVED"}, "CHECKED", p.actor(), "fix plan rejected")
    db.commit()
    return plan_out(db, fp)


@router.post("/fix-plans/{plan_id}/apply", response_model=schemas.RunOut, status_code=202)
def apply(plan_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Run:
    fp, e = _plan(request, db, p, plan_id, "fix.approve")
    if fp.status != "approved":
        raise conflict("not_approved", f"plan is {fp.status}; it must be approved (and its balancing lines checked) first")
    latest = workflow.latest_workbook_version(db, e)
    if latest is None or latest.id != fp.base_version_id:
        raise conflict("stale_base", "the workbook changed since the plan was made; propose a new plan")
    fp.status = "applying"
    run, _ = create_run(db, e, "fix", created_by=p.user_id, input_versions=[latest], params={"fix_plan_id": fp.id},
                        idempotency_key=f"apply:{fp.id}", correlation_id=p.correlation_id, actor=p.actor())
    fp.apply_run_id = run.id
    db.commit()
    return run


# ------------------------------------------------------------------ narrative (typed text only)

FORMULA_LIKE = re.compile(
    r"^\s*[=+@]|^\s*-\s*[A-Za-z_$][\w.]*\s*\(|\b(SUM|SUMPRODUCT|ROUND|IF|IFERROR|VLOOKUP|XLOOKUP|TEXT|INDEX|MATCH|INDIRECT|OFFSET|HYPERLINK|CONCATENATE|CONCAT|WEBSERVICE)\s*\(",
    re.IGNORECASE,
)


def is_formula_like(text: str) -> bool:
    return bool(FORMULA_LIKE.search(text))


def _narr(request: Request, db: Session, p: Principal, edit_id: str, permission: str) -> tuple[NarrativeEdit, Engagement]:
    ne = db.get(NarrativeEdit, edit_id)
    if ne is None or ne.tenant_id != p.tenant_id:
        raise not_found("narrative edit")
    e = get_engagement(db, ne.engagement_id)
    if not p.is_firm_admin and not p.is_member(e.client_id):
        raise not_found("narrative edit")
    authorize(request, db, p, permission, engagement=e)
    return ne, e


@router.get("/engagements/{engagement_id}/narrative-edits", response_model=list[schemas.NarrativeEditOut])
def list_narrative(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[NarrativeEdit]:
    e = load_engagement(request, db, p, engagement_id, "finding.view")
    return list(db.execute(select(NarrativeEdit).where(NarrativeEdit.engagement_id == e.id).order_by(NarrativeEdit.created_at.desc())).scalars())


@router.post("/engagements/{engagement_id}/narrative-edits", response_model=schemas.NarrativeEditOut, status_code=201)
def propose_narrative(engagement_id: str, body: schemas.NarrativeEditIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> NarrativeEdit:
    e = load_engagement(request, db, p, engagement_id)
    value = authorize(request, db, p, "narrative.edit", engagement=e)
    if is_formula_like(body.new_text):
        audit.append(db, p.actor(), "narrative.rejected_formula", client_id=e.client_id, engagement_id=e.id, outcome="denied", details={"cell": f"{body.sheet}!{body.cell}"})
        db.commit()
        raise unprocessable("formula_not_allowed", "narrative must be typed text, never a formula (control C6)")
    latest = workflow.latest_workbook_version(db, e)
    if latest is None:
        raise conflict("no_workbook", "upload a workbook first")
    if body.base_version_id and body.base_version_id != latest.id:
        raise conflict("stale_base", "a newer workbook version exists; reload", latest_version_id=latest.id)
    ne = NarrativeEdit(tenant_id=p.tenant_id, client_id=e.client_id, engagement_id=e.id, base_version_id=latest.id, sheet=body.sheet, cell=body.cell,
                       old_text=body.old_text, new_text=body.new_text, reason=body.reason, proposed_by=p.user_id, created_by=p.user_id,
                       status="approved" if value == ALLOW else "pending_approval")
    if value == ALLOW:
        ne.approved_by = p.user_id
        ne.approved_at = utcnow()
    db.add(ne)
    db.flush()
    if value == MAKER_CHECKER:
        for m in users_with_role(db, p.tenant_id, "manager", e.client_id):
            notify(db, p.tenant_id, m.email, "Narrative edit awaiting approval", "A typed-text narrative edit needs your approval.", "approval")
    audit.append(db, p.actor(), "narrative.proposed", client_id=e.client_id, engagement_id=e.id, object_type="narrative_edit", object_id=ne.id,
                 after=body.new_text, details={"cell": f"{body.sheet}!{body.cell}", "status": ne.status})
    db.commit()
    return ne


@router.post("/narrative-edits/{edit_id}/approve", response_model=schemas.NarrativeEditOut)
def approve_narrative(edit_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> NarrativeEdit:
    ne, e = _narr(request, db, p, edit_id, "narrative.edit")
    from fsweb.authz.deps import permission_value

    if permission_value(db, p, "narrative.edit", e.client_id) != ALLOW:
        raise deny(request, db, p, "narrative.edit", "a Manager or Partner must approve this edit", engagement_id=e.id, client_id=e.client_id, code="maker_checker")
    if ne.proposed_by == p.user_id:
        raise deny(request, db, p, "narrative.edit", "segregation of duties: you cannot approve your own edit", engagement_id=e.id, client_id=e.client_id,
                   code="segregation_of_duties")
    if ne.status != "pending_approval":
        raise conflict("not_pending", f"edit is {ne.status}")
    ne.status = "approved"
    ne.approved_by = p.user_id
    ne.approved_at = utcnow()
    audit.append(db, p.actor(), "narrative.approved", client_id=e.client_id, engagement_id=e.id, object_type="narrative_edit", object_id=ne.id)
    db.commit()
    return ne


@router.post("/narrative-edits/{edit_id}/reject", response_model=schemas.NarrativeEditOut)
def reject_narrative(edit_id: str, body: schemas.RejectIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> NarrativeEdit:
    ne, e = _narr(request, db, p, edit_id, "narrative.edit")
    if ne.status not in {"pending_approval", "approved"}:
        raise conflict("not_pending", f"edit is {ne.status}")
    ne.status = "rejected"
    audit.append(db, p.actor(), "narrative.rejected", client_id=e.client_id, engagement_id=e.id, object_type="narrative_edit", object_id=ne.id, details={"reason": body.reason})
    db.commit()
    return ne


@router.post("/engagements/{engagement_id}/narrative-edits/apply", response_model=schemas.ApplyNarrativeOut, status_code=202)
def apply_narrative(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.ApplyNarrativeOut:
    e = load_engagement(request, db, p, engagement_id, "narrative.edit")
    edits = list(db.execute(select(NarrativeEdit).where(NarrativeEdit.engagement_id == e.id, NarrativeEdit.status == "approved")).scalars())
    if not edits:
        raise conflict("nothing_to_apply", "no approved narrative edits")
    latest = workflow.latest_workbook_version(db, e)
    if latest is None:
        raise conflict("no_workbook", "upload a workbook first")
    stale = [ne.id for ne in edits if ne.base_version_id != latest.id]
    if stale:
        raise conflict("stale_base", "some edits were made on an older version; re-propose them", edit_ids=stale)
    run, _ = create_run(db, e, "narrative", created_by=p.user_id, input_versions=[latest], params={"edit_ids": [ne.id for ne in edits]},
                        correlation_id=p.correlation_id, actor=p.actor())
    db.commit()
    return schemas.ApplyNarrativeOut(run_id=run.id, edit_ids=[ne.id for ne in edits])
