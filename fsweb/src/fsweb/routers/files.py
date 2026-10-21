"""Files and immutable versions (C1, 06 §5), check-out / check-in (E-DATA-03),
watermarked downloads and expiring single-use share links (E-SEC-10)."""

from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi import File as FormFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from fsweb import audit, schemas
from fsweb.authz.deps import Principal, authorize, get_db, get_state, require_principal
from fsweb.db import utcnow
from fsweb.errors import ApiError, bad_request, conflict, forbidden, not_found
from fsweb.models import DraftRelease, Engagement, File, FileLock, FileVersion, ShareLink, User
from fsweb.routers.engagements import load_engagement
from fsweb.security.crypto import hash_token, new_token
from fsweb.services import engagements as eng_svc
from fsweb.services import uploads, workflow
from fsweb.services.common import ensure_file, get_engagement, store_version

router = APIRouter(tags=["files"])

FILE_KINDS = {"workbook", "tb", "printed_pdf", "prior_year", "other"}


def active_lock(db: Session, file_id: str) -> FileLock | None:
    now = utcnow()
    return db.execute(
        select(FileLock).where(FileLock.file_id == file_id, FileLock.released_at.is_(None), FileLock.expires_at > now)
    ).scalars().first()


def released_version_ids(db: Session, eng: Engagement) -> set[str]:
    ids: set[str] = set()
    for r in db.execute(select(DraftRelease).where(DraftRelease.engagement_id == eng.id)).scalars():
        ids.add(r.pdf_version_id)
    return ids


def client_visible(db: Session, eng: Engagement, p: Principal, v: FileVersion) -> bool:
    return v.created_by == p.user_id or v.id in released_version_ids(db, eng)


def file_out(db: Session, f: File, *, p: Principal | None = None, eng: Engagement | None = None, shared_only: bool = False) -> schemas.FileOut:
    versions = list(db.execute(select(FileVersion).where(FileVersion.file_id == f.id).order_by(FileVersion.version_no.desc())).scalars())
    if shared_only and p is not None and eng is not None:
        versions = [v for v in versions if client_visible(db, eng, p, v)]
    lock = active_lock(db, f.id)
    out = schemas.FileOut(id=f.id, engagement_id=f.engagement_id, kind=f.kind, title=f.title, created_at=f.created_at,
                          latest_version=schemas.FileVersionOut.model_validate(versions[0]) if versions else None,
                          checked_out_by=lock.user_id if lock else None)
    return out


@router.post("/engagements/{engagement_id}/files", response_model=schemas.UploadOut, status_code=201)
async def upload(
    engagement_id: str,
    request: Request,
    file: Annotated[UploadFile, FormFile()],
    kind: Annotated[str, Form()] = "workbook",
    title: Annotated[str | None, Form()] = None,
    file_id: Annotated[str | None, Form()] = None,
    base_version_id: Annotated[str | None, Form()] = None,
    p: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> schemas.UploadOut:
    st = get_state(request)
    e = load_engagement(request, db, p, engagement_id, "file.upload")
    if kind not in FILE_KINDS:
        raise bad_request("invalid_kind", f"kind must be one of {sorted(FILE_KINDS)}")
    name = Path(file.filename or "upload.bin").name
    data = await file.read(st.settings.max_upload_mb * 1024 * 1024 + 1)
    try:
        info = uploads.validate(name, data, max_mb=st.settings.max_upload_mb)
        if st.settings.clamd_socket:
            sig = uploads.clamav_scan(st.settings.clamd_socket, data)
            if sig:
                raise uploads.UploadRejected("malware", "the file failed the malware scan")
    except uploads.UploadRejected as exc:
        audit.append(db, p.actor(), "file.rejected", client_id=e.client_id, engagement_id=e.id, outcome="denied",
                     details={"code": exc.code, "ext": Path(name).suffix.lower(), "size": len(data)})
        db.commit()
        raise ApiError(415 if exc.code in {"file_type", "bad_magic"} else 422, exc.code, str(exc)) from exc
    if kind == "workbook" and info.ext not in {".xlsx", ".xlsm"}:
        raise bad_request("invalid_kind", "a workbook must be .xlsx or .xlsm")
    if file_id:
        f = db.get(File, file_id)
        if f is None or f.engagement_id != e.id:
            raise not_found("file")
    elif kind == "workbook":
        f = workflow.workbook_file(db, e) or ensure_file(db, e, "workbook", title or "Statements workbook", p.user_id)
    else:
        f = ensure_file(db, e, kind, title or name, p.user_id)
    lock = active_lock(db, f.id)
    if lock is not None and lock.user_id != p.user_id:
        raise conflict("checked_out", "the file is checked out by another user", holder=lock.user_id, expires_at=lock.expires_at.isoformat())
    latest = workflow.latest_version(db, f.id)
    if base_version_id and latest is not None and base_version_id != latest.id:
        raise conflict("stale_base", "a newer version exists; reload and merge your changes", latest_version_id=latest.id)
    v = store_version(db, st.storage, e, f, data, original_name=name, created_by=p.user_id, parent_version_id=latest.id if latest else None,
                      change_summary={"source": "upload"}, has_macros=info.has_macros)
    if lock is not None and lock.user_id == p.user_id:
        lock.released_at = utcnow()
    audit.append(db, p.actor(), "file.uploaded", client_id=e.client_id, engagement_id=e.id, object_type="file_version", object_id=v.id,
                 after=v.sha256, details={"file_id": f.id, "kind": f.kind, "version_no": v.version_no, "size": v.size, "macros": info.has_macros})
    auto_run = None
    if f.kind in {"workbook", "tb"}:
        workflow.advance(db, e, {"DRAFT_SETUP"}, "DATA_RECEIVED", p.actor(), "data uploaded")
        if f.kind == "workbook":
            auto_run = eng_svc.data_changed(db, e, p.actor(), v, reason="new workbook version uploaded")
    db.commit()
    return schemas.UploadOut(file=file_out(db, f), version=schemas.FileVersionOut.model_validate(v), engagement_status=e.status,
                             auto_check_run_id=auto_run.id if auto_run else None)


@router.get("/engagements/{engagement_id}/files", response_model=list[schemas.FileOut])
def list_files(engagement_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[schemas.FileOut]:
    e = load_engagement(request, db, p, engagement_id, "file.view")
    shared_only = p.is_client_user
    files = db.execute(select(File).where(File.engagement_id == e.id).order_by(File.created_at)).scalars()
    out = [file_out(db, f, p=p, eng=e, shared_only=shared_only) for f in files]
    return [f for f in out if f.latest_version is not None] if shared_only else out


def _version(request: Request, db: Session, p: Principal, version_id: str, permission: str) -> tuple[FileVersion, Engagement, str]:
    v = db.get(FileVersion, version_id)
    if v is None or v.tenant_id != p.tenant_id:
        raise not_found("version")
    e = get_engagement(db, v.engagement_id)
    if not p.is_firm_admin and not p.is_member(e.client_id):
        raise not_found("version")
    value = authorize(request, db, p, permission, engagement=e)
    if p.is_client_user and not client_visible(db, e, p, v):
        raise not_found("version")
    return v, e, value


@router.get("/files/{file_id}/versions", response_model=list[schemas.FileVersionOut])
def list_versions(file_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[FileVersion]:
    f = db.get(File, file_id)
    if f is None or f.tenant_id != p.tenant_id:
        raise not_found("file")
    e = load_engagement(request, db, p, f.engagement_id, "file.view")
    vs = list(db.execute(select(FileVersion).where(FileVersion.file_id == f.id).order_by(FileVersion.version_no.desc())).scalars())
    if p.is_client_user:
        vs = [v for v in vs if client_visible(db, e, p, v)]
    return vs


def watermark_text(p: Principal, v: FileVersion, eng: Engagement, label: str | None = None) -> str:
    tag = label or ("FINAL" if eng.status in workflow.LOCKED else "DRAFT – for review")
    return f"{tag}\n{p.email} · {utcnow().strftime('%d-%b-%Y %H:%M UTC')} · v{v.version_no}"


def deliver(request: Request, p: Principal, v: FileVersion, eng: Engagement, *, label: str | None = None) -> Response:
    st = get_state(request)
    data = st.storage.get_bytes(v.object_key)
    if v.content_type == "application/pdf":
        with tempfile.TemporaryDirectory(dir=st.settings.work_path()) as td:
            src = Path(td) / "in.pdf"
            out = Path(td) / "out.pdf"
            src.write_bytes(data)
            st.engine.watermark_pdf(src, out, watermark_text(p, v, eng, label))
            data = out.read_bytes()
    safe = v.original_name.replace('"', "")
    return Response(content=data, media_type=v.content_type, headers={"Content-Disposition": f'attachment; filename="{safe}"', "Cache-Control": "no-store"})


@router.get("/versions/{version_id}/download", responses={200: {"content": {"application/octet-stream": {}}}})
def download(version_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Response:
    v, e, _ = _version(request, db, p, version_id, "file.download")
    audit.append(db, p.actor(), "file.downloaded", client_id=e.client_id, engagement_id=e.id, object_type="file_version", object_id=v.id,
                 details={"sha256": v.sha256, "watermarked": v.content_type == "application/pdf"})
    db.commit()
    return deliver(request, p, v, e)


@router.post("/versions/{version_id}/restore", response_model=schemas.FileVersionOut, status_code=201)
def restore_version(version_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> FileVersion:
    """'Restore version' creates a new version copying the old one; nothing is overwritten (06 §5)."""
    st = get_state(request)
    v, e, _ = _version(request, db, p, version_id, "file.upload")
    f = db.get(File, v.file_id)
    assert f is not None
    lock = active_lock(db, f.id)
    if lock is not None and lock.user_id != p.user_id:
        raise conflict("checked_out", "the file is checked out by another user")
    data = st.storage.get_bytes(v.object_key)
    latest = workflow.latest_version(db, f.id)
    nv = store_version(db, st.storage, e, f, data, original_name=v.original_name, created_by=p.user_id, parent_version_id=latest.id if latest else v.id,
                       change_summary={"source": "restore", "restored_from": v.id, "restored_version_no": v.version_no})
    audit.append(db, p.actor(), "file.restored", client_id=e.client_id, engagement_id=e.id, object_type="file_version", object_id=nv.id,
                 details={"from_version": v.version_no})
    if f.kind == "workbook":
        eng_svc.data_changed(db, e, p.actor(), nv, reason="version restored")
    db.commit()
    return nv


@router.post("/files/{file_id}/checkout", response_model=schemas.CheckoutOut)
def checkout(file_id: str, request: Request, minutes: int = 120, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.CheckoutOut:
    f = db.get(File, file_id)
    if f is None or f.tenant_id != p.tenant_id:
        raise not_found("file")
    e = load_engagement(request, db, p, f.engagement_id, "file.upload")
    lock = active_lock(db, f.id)
    if lock is not None and lock.user_id != p.user_id:
        raise conflict("checked_out", "the file is already checked out", holder=lock.user_id, expires_at=lock.expires_at.isoformat())
    latest = workflow.latest_version(db, f.id)
    if latest is None:
        raise conflict("no_version", "the file has no version to check out")
    minutes = max(5, min(minutes, 8 * 60))
    if lock is None:
        lock = FileLock(tenant_id=p.tenant_id, client_id=e.client_id, file_id=f.id, version_id=latest.id, user_id=p.user_id,
                        expires_at=utcnow() + timedelta(minutes=minutes), created_by=p.user_id)
        db.add(lock)
    else:
        lock.expires_at = utcnow() + timedelta(minutes=minutes)
    audit.append(db, p.actor(), "file.checked_out", client_id=e.client_id, engagement_id=e.id, object_type="file", object_id=f.id,
                 details={"version_id": latest.id, "minutes": minutes})
    db.commit()
    return schemas.CheckoutOut(file_id=f.id, version_id=lock.version_id, user_id=lock.user_id, expires_at=lock.expires_at)


@router.delete("/files/{file_id}/checkout", response_model=schemas.OkOut)
def release_checkout(file_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.OkOut:
    f = db.get(File, file_id)
    if f is None or f.tenant_id != p.tenant_id:
        raise not_found("file")
    e = load_engagement(request, db, p, f.engagement_id, "file.upload")
    lock = active_lock(db, f.id)
    if lock is None:
        return schemas.OkOut(message="not checked out")
    if lock.user_id != p.user_id:
        authorize(request, db, p, "fix.approve", engagement=e)  # a Partner / Manager may break someone else's lock
    lock.released_at = utcnow()
    audit.append(db, p.actor(), "file.checked_in", client_id=e.client_id, engagement_id=e.id, object_type="file", object_id=f.id,
                 details={"forced": lock.user_id != p.user_id})
    db.commit()
    return schemas.OkOut(message="lock released")


@router.get("/engagements/{engagement_id}/diff", response_model=schemas.DiffOut)
def diff(engagement_id: str, request: Request, a: str, b: str, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.DiffOut:
    st = get_state(request)
    e = load_engagement(request, db, p, engagement_id, "file.view")
    va, vb = db.get(FileVersion, a), db.get(FileVersion, b)
    if va is None or vb is None or va.engagement_id != e.id or vb.engagement_id != e.id:
        raise not_found("version")
    with tempfile.TemporaryDirectory(dir=st.settings.work_path()) as td:
        pa = st.storage.download_to(va.object_key, Path(td) / f"a{Path(va.original_name).suffix}")
        pb = st.storage.download_to(vb.object_key, Path(td) / f"b{Path(vb.original_name).suffix}")
        result = st.engine.diff_workbooks(pa, pb, eng_svc.config_for(db, e))
    return schemas.DiffOut(a_version_id=va.id, b_version_id=vb.id, diff=result.model_dump(mode="json"))


# ------------------------------------------------------------------ share links


@router.post("/versions/{version_id}/share-links", response_model=schemas.ShareLinkOut, status_code=201)
def create_share_link(version_id: str, body: schemas.ShareLinkIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.ShareLinkOut:
    st = get_state(request)
    v, e, _ = _version(request, db, p, version_id, "release.client")
    if body.recipient_user_id:
        u = db.get(User, body.recipient_user_id)
        if u is None or u.tenant_id != p.tenant_id:
            raise not_found("recipient")
    token = new_token()
    link = ShareLink(tenant_id=p.tenant_id, client_id=e.client_id, engagement_id=e.id, file_version_id=v.id, token_hash=hash_token(token),
                     recipient_user_id=body.recipient_user_id, expires_at=utcnow() + timedelta(hours=body.hours), created_by=p.user_id)
    db.add(link)
    audit.append(db, p.actor(), "share_link.created", client_id=e.client_id, engagement_id=e.id, object_type="file_version", object_id=v.id,
                 details={"hours": body.hours, "recipient": body.recipient_user_id})
    db.commit()
    return schemas.ShareLinkOut(url=f"{st.settings.public_base_url}/api/v1/share/{token}", expires_at=link.expires_at)


@router.get("/share/{token}", responses={200: {"content": {"application/octet-stream": {}}}})
def open_share_link(token: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Response:
    """Single-use, expiring, recipient-bound (when set) and watermarked with the recipient."""
    link = db.execute(select(ShareLink).where(ShareLink.token_hash == hash_token(token))).scalar_one_or_none()
    if link is None or link.tenant_id != p.tenant_id:
        raise not_found("link")
    if link.used_at is not None or link.expires_at <= utcnow():
        raise ApiError(410, "link_expired", "this link has expired or was already used")
    if link.recipient_user_id and link.recipient_user_id != p.user_id:
        raise forbidden("this link was issued to another person")
    v = db.get(FileVersion, link.file_version_id)
    e = get_engagement(db, link.engagement_id)
    assert v is not None
    link.used_at = utcnow()
    audit.append(db, p.actor(), "file.downloaded", client_id=e.client_id, engagement_id=e.id, object_type="share_link", object_id=link.id,
                 details={"via": "share_link", "sha256": v.sha256})
    db.commit()
    return deliver(request, p, v, e, label="SHARED COPY")

