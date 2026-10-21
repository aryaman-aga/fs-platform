"""Rule registry, health / readiness, and engagement packages (export / import / promote)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi import File as FormFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from fsweb import __version__, audit, schemas
from fsweb.authz.deps import Principal, authorize, get_db, get_state, require_principal
from fsweb.errors import ApiError, conflict
from fsweb.models import Client
from fsweb.routers.engagements import engagement_out, load_engagement
from fsweb.services import fspkg

router = APIRouter()


@router.get("/rules", response_model=schemas.RuleOut, tags=["rules"])
def rules(request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.RuleOut:
    """E-RULE-01: registered, versioned rules; every run records the rule-set version it used."""
    authorize(request, db, p, "rules.view")
    st = get_state(request)
    version, digest = st.engine.rule_set()
    return schemas.RuleOut(rules=st.engine.list_rules(), rule_set_version=version, rule_set_hash=digest)


@router.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/ready", tags=["health"])
def ready(request: Request) -> JSONResponse:
    st = get_state(request)
    db_ok = True
    try:
        with st.db.engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    storage_ok = st.storage.health()
    ok = db_ok and storage_ok
    return JSONResponse({"status": "ready" if ok else "not_ready", "database": db_ok, "storage": storage_ok}, status_code=200 if ok else 503)


@router.post("/engagements/{engagement_id}/export", tags=["packages"], responses={200: {"content": {"application/octet-stream": {}}}})
def export_package(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Response:
    st = get_state(request)
    e = load_engagement(request, db, p, engagement_id, "engagement.export")
    data = fspkg.export_engagement(db, st, e)
    import hashlib

    audit.append(db, p.actor(), "engagement.exported", client_id=e.client_id, engagement_id=e.id, object_type="engagement", object_id=e.id,
                 after=hashlib.sha256(data).hexdigest(), details={"bytes": len(data)})
    db.commit()
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in e.name)[:80]
    return Response(data, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{safe}.fspkg"'})


@router.post("/engagements/import", response_model=schemas.EngagementOut, status_code=201, tags=["packages"])
async def import_package(request: Request, file: Annotated[UploadFile, FormFile()], p: Principal = Depends(require_principal),
                         db: Session = Depends(get_db)) -> schemas.EngagementOut:
    """Imports always land in a quarantined engagement; a Partner promotes it (07 §5)."""
    st = get_state(request)
    data = await file.read()
    try:
        pkg = fspkg.read_package(data, st.settings)
    except fspkg.PackageError as exc:
        audit.append(db, p.actor(), "engagement.import_rejected", outcome="denied", details={"reason": str(exc)})
        db.commit()
        raise ApiError(422, "package_rejected", str(exc)) from exc
    client = db.get(Client, pkg.manifest.get("client_id"))
    if client is None or client.tenant_id != p.tenant_id:
        raise ApiError(422, "package_rejected", "the package's client does not exist in this firm")
    authorize(request, db, p, "engagement.export", client_id=client.id)
    try:
        eng = fspkg.import_engagement(db, st, data, p.actor(), tenant_id=p.tenant_id)
    except fspkg.PackageError as exc:
        raise ApiError(422, "package_rejected", str(exc)) from exc
    db.commit()
    return engagement_out(db, eng)


@router.post("/engagements/{engagement_id}/merge", response_model=schemas.EngagementOut, tags=["packages"])
def merge(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.EngagementOut:
    """A Manager confirms the conflicting branch was reconciled (E-DATA-05)."""
    e = load_engagement(request, db, p, engagement_id, "engagement.export", attrs={"engagement_quarantine": False})
    if not e.merge_required:
        raise conflict("no_conflict", "nothing to merge")
    e.merge_required = False
    audit.append(db, p.actor(), "engagement.merged", client_id=e.client_id, engagement_id=e.id, object_type="engagement", object_id=e.id)
    db.commit()
    return engagement_out(db, e)


@router.post("/engagements/{engagement_id}/promote", response_model=schemas.EngagementOut, tags=["packages"])
def promote(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.EngagementOut:
    e = load_engagement(request, db, p, engagement_id, "engagement.promote", attrs={"engagement_quarantine": False})
    if not e.is_quarantine:
        raise conflict("not_quarantined", "the engagement is not quarantined")
    if e.merge_required:
        raise conflict("merge_required", "conflicting changes must be merged by a Manager first")
    e.is_quarantine = False
    audit.append(db, p.actor(), "engagement.promoted", client_id=e.client_id, engagement_id=e.id, object_type="engagement", object_id=e.id)
    db.commit()
    return engagement_out(db, e)
