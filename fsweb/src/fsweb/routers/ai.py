"""AI review gate (02 §9, C9, E-AI-01/03/05).

1. ``POST /engagements/{id}/ai/requests`` builds the pseudonymised payload and
   shows it in full (size, token estimate, provider, model, region, reason).
2. A *different* user approves; the approval token is bound to the payload's
   SHA-256, so any change to the payload invalidates it.
3. ``send`` queues the call for the ai-gateway component — the only process with
   egress — which re-hashes the stored payload and refuses on any mismatch.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from fsweb import audit, schemas
from fsweb.authz.deps import Principal, authorize, deny, get_db, get_state, require_principal, require_stepup
from fsweb.db import utcnow
from fsweb.errors import conflict, forbidden, not_found, unprocessable
from fsweb.models import (
    AIRequest,
    Client,
    Engagement,
    Entity,
    FindingRow,
    Policy,
    Run,
    Signatory,
    Tenant,
    User,
)
from fsweb.routers.engagements import load_engagement
from fsweb.security.crypto import hmac_hex
from fsweb.services import engagements as eng_svc
from fsweb.services.common import create_run, get_engagement
from fsweb.storage import object_key

router = APIRouter(tags=["ai"])

EXTERNAL = {"anthropic", "bedrock", "vertex", "foundry", "gateway"}


def _active_ai_policy(db: Session, tenant_id: str) -> Policy | None:
    return db.execute(select(Policy).where(Policy.tenant_id == tenant_id, Policy.name == "ai_policy", Policy.active.is_(True))
                      .order_by(Policy.version.desc())).scalars().first()


@router.get("/ai/policy", response_model=schemas.AIPolicyOut)
def get_policy(request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.AIPolicyOut:
    pol = _active_ai_policy(db, p.tenant_id)
    if pol is None:
        raise not_found("AI policy")
    u = db.get(User, p.user_id)
    accepted = bool(u and u.ai_policy_version == pol.version)
    return schemas.AIPolicyOut(version=pol.version, body=pol.body, accepted=accepted, accepted_at=u.ai_policy_accepted_at if accepted and u else None)


@router.post("/ai/policy/accept", response_model=schemas.AIPolicyOut)
def accept_policy(request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.AIPolicyOut:
    pol = _active_ai_policy(db, p.tenant_id)
    if pol is None:
        raise not_found("AI policy")
    u = db.get(User, p.user_id)
    assert u is not None
    u.ai_policy_version = pol.version
    u.ai_policy_accepted_at = utcnow()
    audit.append(db, p.actor(), "ai.policy_accepted", object_type="policy", object_id=f"ai_policy:{pol.version}")
    db.commit()
    return schemas.AIPolicyOut(version=pol.version, body=pol.body, accepted=True, accepted_at=u.ai_policy_accepted_at)


@router.put("/admin/ai-policy", response_model=schemas.AIPolicyOut)
def publish_policy(body: schemas.AIPolicyIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.AIPolicyOut:
    authorize(request, db, p, "ai.policy")
    reason = require_stepup(request, p)
    cur = _active_ai_policy(db, p.tenant_id)
    version = (cur.version if cur else 0) + 1
    if cur:
        cur.active = False
    pol = Policy(tenant_id=p.tenant_id, name="ai_policy", version=version, body=body.body, active=True, approved_by=p.user_id, created_by=p.user_id)
    db.add(pol)
    audit.append(db, p.actor(), "ai.policy_published", object_type="policy", object_id=f"ai_policy:{version}", after=body.body, details={"reason": reason})
    db.commit()
    return schemas.AIPolicyOut(version=version, body=body.body, accepted=False)


def _names(request: Request, db: Session, e: Engagement) -> dict[str, str]:
    """Known identities to pseudonymise: client, entity, signatories."""
    st = get_state(request)
    out: dict[str, str] = {}
    c = db.get(Client, e.client_id)
    ent = db.get(Entity, e.entity_id)
    if c:
        out[c.name] = "ENTITY"
        if c.group_name:
            out[c.group_name] = "GROUP"
    if ent:
        out[ent.name] = "ENTITY"
    for s in db.execute(select(Signatory).where(Signatory.entity_id == e.entity_id)).scalars():
        out[s.name] = "PERSON"
        din = st.cipher.decrypt(db, s.tenant_id, s.din_enc)
        if din:
            out[din] = "DIN"
    return out


def request_out(request: Request, db: Session, r: AIRequest, *, with_payload: bool) -> schemas.AIRequestOut:
    st = get_state(request)
    out = schemas.AIRequestOut.model_validate(r)
    if with_payload:
        out.payload = json.loads(st.storage.get_bytes(r.payload_object_key))
        if r.response_object_key:
            out.response = json.loads(st.storage.get_bytes(r.response_object_key))
    return out


@router.post("/engagements/{engagement_id}/ai/requests", response_model=schemas.AIRequestOut, status_code=201)
def create_request(engagement_id: str, body: schemas.AIRequestIn, request: Request, p: Principal = Depends(require_principal),
                   db: Session = Depends(get_db)) -> schemas.AIRequestOut:
    st = get_state(request)
    e = load_engagement(request, db, p, engagement_id, "ai.request")
    tenant = db.get(Tenant, p.tenant_id)
    tenant_ai = bool((tenant.settings_json or {}).get("ai_enabled", False)) if tenant else False
    if not (st.settings.ai_enabled or tenant_ai):
        raise conflict("ai_disabled", "AI review is disabled for the firm")
    if not e.ai_enabled:
        raise conflict("ai_disabled", "AI review is switched off for this engagement")
    pol = _active_ai_policy(db, p.tenant_id)
    u = db.get(User, p.user_id)
    if pol is None or u is None or u.ai_policy_version != pol.version:
        raise forbidden("accept the current AI use policy first (E-AI-01)", "ai_policy_not_accepted")
    cfg = eng_svc.config_for(db, e)
    provider = body.provider or cfg.ai.provider
    if provider == "none":
        raise conflict("ai_provider_none", "no AI provider is configured for this engagement")
    region = body.region or cfg.ai.region
    if provider in EXTERNAL and not region:
        raise unprocessable("region_required", "an approved region is required for external AI providers (no default by design)")
    run = db.get(Run, body.run_id) if body.run_id else db.execute(
        select(Run).where(Run.engagement_id == e.id, Run.kind == "check", Run.status == "succeeded").order_by(Run.created_at.desc())).scalars().first()
    if run is None or run.engagement_id != e.id:
        raise conflict("no_check", "run checks before requesting an AI review")
    stmt = select(FindingRow).where(FindingRow.run_id == run.id)
    if body.finding_ids:
        stmt = stmt.where(FindingRow.id.in_(body.finding_ids))
    else:
        stmt = stmt.where(FindingRow.severity.in_(["High", "Medium"]))
    findings = [
        {"fid": f.fid, "rule_id": f.rule_id, "severity": f.severity, "certainty": f.certainty, "sheet": f.sheet, "cells": f.cells, "caption": f.caption,
         "printed": f.printed, "expected": f.expected, "diff": f.diff, "explanation": f.explanation, "suggested_fix": f.suggested_fix}
        for f in db.execute(stmt.order_by(FindingRow.fid).limit(200)).scalars()
    ]
    if not findings:
        raise conflict("no_findings", "nothing to review")
    metrics = {k.get("key", ""): k.get("value") for k in (run.result_json or {}).get("key_figures", []) if isinstance(k, dict)}
    built: dict[str, Any] = st.engine.ai_build_payload(body.kind, findings, {"units": cfg.units, "metrics": metrics, "names": _names(request, db, e)},
                                                       cfg.ai.model_copy(update={"provider": provider, "region": region}))
    payload = built.get("payload", built)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    key = object_key(e.tenant_id, e.client_id, e.id, "ai", f"payload-{sha}.json")
    if not st.storage.exists(key):
        st.storage.put_bytes(key, raw, "application/json")
    if built.get("mapping"):  # token -> real value, kept locally for mapping responses back
        mkey = object_key(e.tenant_id, e.client_id, e.id, "ai", f"mapping-{sha}.json")
        if not st.storage.exists(mkey):
            st.storage.put_bytes(mkey, json.dumps(built["mapping"], sort_keys=True).encode(), "application/json")
    r = AIRequest(tenant_id=p.tenant_id, client_id=e.client_id, engagement_id=e.id, run_id=run.id, kind=body.kind, reason=body.reason,
                  payload_object_key=key, payload_sha256=sha, payload_size=len(raw), token_estimate=max(1, len(raw) // 4),
                  provider=provider, model=cfg.ai.model, region=region, prompt_version=str(built.get("instructions_version") or payload.get("instructions_version", "triage-v1")),
                  requested_by=p.user_id, created_by=p.user_id)
    db.add(r)
    db.flush()
    audit.append(db, p.actor(), "ai.payload_previewed", client_id=e.client_id, engagement_id=e.id, object_type="ai_request", object_id=r.id,
                 details={"payload_sha256": sha, "size": len(raw), "provider": provider, "model": r.model, "region": region})
    db.commit()
    return request_out(request, db, r, with_payload=True)


def _req(request: Request, db: Session, p: Principal, request_id: str) -> tuple[AIRequest, Engagement]:
    r = db.get(AIRequest, request_id)
    if r is None or r.tenant_id != p.tenant_id:
        raise not_found("AI request")
    e = get_engagement(db, r.engagement_id)
    if not p.is_firm_admin and not p.is_member(e.client_id):
        raise not_found("AI request")
    return r, e


@router.get("/ai/requests/{request_id}", response_model=schemas.AIRequestOut)
def get_request(request_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.AIRequestOut:
    r, e = _req(request, db, p, request_id)
    if p.is_firm_admin and not p.is_member(e.client_id):
        authorize(request, db, p, "ai.approve", engagement=e)
    else:
        authorize(request, db, p, "ai.request", engagement=e)
    return request_out(request, db, r, with_payload=True)


@router.get("/engagements/{engagement_id}/ai/requests", response_model=list[schemas.AIRequestOut])
def list_requests(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[schemas.AIRequestOut]:
    e = get_engagement(db, engagement_id)
    if p.is_firm_admin and not p.is_member(e.client_id):
        authorize(request, db, p, "ai.approve", engagement=e)
    else:
        e = load_engagement(request, db, p, engagement_id, "ai.request")
    rows = db.execute(select(AIRequest).where(AIRequest.engagement_id == e.id).order_by(AIRequest.created_at.desc())).scalars()
    return [request_out(request, db, r, with_payload=False) for r in rows]


def approval_token(secret: str, request_id: str, sha: str) -> str:
    return hmac_hex(secret, "ai-approval", request_id, sha)


@router.post("/ai/requests/{request_id}/approve", response_model=schemas.AIRequestOut)
def approve(request_id: str, body: schemas.AIApproveIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.AIRequestOut:
    st = get_state(request)
    r, e = _req(request, db, p, request_id)
    if r.requested_by == p.user_id:
        raise deny(request, db, p, "ai.approve", "segregation of duties: you cannot approve your own AI request", engagement_id=e.id,
                   client_id=e.client_id, code="segregation_of_duties")
    authorize(request, db, p, "ai.approve", engagement=e, owner_id=r.requested_by)
    if r.status != "preview":
        raise conflict("not_pending", f"request is {r.status}")
    actual = hashlib.sha256(st.storage.get_bytes(r.payload_object_key)).hexdigest()
    if body.payload_sha256 != r.payload_sha256 or actual != r.payload_sha256:
        raise conflict("payload_changed", "the payload differs from the one shown; review it again")
    token = approval_token(st.settings.secret_key, r.id, r.payload_sha256)
    r.approval_token_hash = hashlib.sha256(token.encode()).hexdigest()
    r.approved_by = p.user_id
    r.approved_at = utcnow()
    r.status = "approved"
    audit.append(db, p.actor(), "ai.payload_approved", client_id=e.client_id, engagement_id=e.id, object_type="ai_request", object_id=r.id,
                 details={"payload_sha256": r.payload_sha256, "provider": r.provider, "model": r.model, "region": r.region})
    db.commit()
    return request_out(request, db, r, with_payload=False)


@router.post("/ai/requests/{request_id}/send", response_model=schemas.RunOut, status_code=202)
def send(request_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Run:
    r, e = _req(request, db, p, request_id)
    if p.user_id not in {r.requested_by, r.approved_by}:
        authorize(request, db, p, "ai.approve", engagement=e)
    else:
        authorize(request, db, p, "ai.request" if p.user_id == r.requested_by else "ai.approve", engagement=e)
    if r.status != "approved":
        raise conflict("not_approved", "the payload must be approved by a different person first")
    if not e.ai_enabled:
        raise conflict("ai_disabled", "AI review is switched off for this engagement")
    r.status = "queued"
    run, _ = create_run(db, e, "ai_review", created_by=p.user_id, input_versions=[], params={"ai_request_id": r.id}, queue="ai-gateway",
                        idempotency_key=f"ai:{r.id}", correlation_id=p.correlation_id, actor=p.actor())
    r.send_run_id = run.id
    db.commit()
    return run
