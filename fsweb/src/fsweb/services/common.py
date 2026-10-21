"""Shared operations used by routers, the worker and the admin CLI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fsweb import audit
from fsweb.db import new_id, utcnow
from fsweb.errors import not_found
from fsweb.jobs.queue import enqueue
from fsweb.models import Artefact, EmailOutbox, Engagement, File, FileVersion, Role, Run, User, UserClientRole
from fsweb.storage import Storage, object_key

CONTENT_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".html": "text/html",
    ".json": "application/json",
    ".zip": "application/zip",
    ".fspkg": "application/octet-stream",
}


def content_type_for(name: str) -> str:
    return CONTENT_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


def get_engagement(db: Session, engagement_id: str) -> Engagement:
    eng = db.get(Engagement, engagement_id)
    if eng is None:
        raise not_found("engagement")
    return eng


def next_version_no(db: Session, file_id: str) -> int:
    n = db.execute(select(func.max(FileVersion.version_no)).where(FileVersion.file_id == file_id)).scalar()
    return int(n or 0) + 1


def store_version(
    db: Session,
    storage: Storage,
    eng: Engagement,
    file: File,
    data: bytes,
    *,
    original_name: str,
    created_by: str | None,
    parent_version_id: str | None = None,
    produced_by_run_id: str | None = None,
    change_summary: dict[str, Any] | None = None,
    has_macros: bool = False,
) -> FileVersion:
    """Store bytes as a new immutable version (C1). Never overwrites an object."""
    vno = next_version_no(db, file.id)
    sha = hashlib.sha256(data).hexdigest()
    ext = Path(original_name).suffix.lower() or ".bin"
    key = object_key(eng.tenant_id, eng.client_id, eng.id, "files", file.id, f"v{vno}-{sha[:16]}{ext}")
    obj = storage.put_bytes(key, data, content_type_for(original_name))
    v = FileVersion(
        tenant_id=eng.tenant_id,
        client_id=eng.client_id,
        engagement_id=eng.id,
        file_id=file.id,
        version_no=vno,
        version_line=eng.version_line,
        object_key=obj.key,
        sha256=sha,
        size=len(data),
        content_type=content_type_for(original_name),
        original_name=original_name[:300],
        parent_version_id=parent_version_id,
        produced_by_run_id=produced_by_run_id,
        change_summary_json=change_summary or {},
        has_macros=has_macros,
        created_by=created_by,
    )
    db.add(v)
    db.flush()
    return v


def ensure_file(db: Session, eng: Engagement, kind: str, title: str, created_by: str | None) -> File:
    f = db.execute(select(File).where(File.engagement_id == eng.id, File.kind == kind, File.title == title)).scalars().first()
    if f is None:
        f = File(tenant_id=eng.tenant_id, client_id=eng.client_id, engagement_id=eng.id, kind=kind, title=title, created_by=created_by)
        db.add(f)
        db.flush()
    return f


def store_artefact(db: Session, storage: Storage, run: Run, kind: str, name: str, data: bytes, content_type: str | None = None) -> Artefact:
    sha = hashlib.sha256(data).hexdigest()
    key = object_key(run.tenant_id, run.client_id, run.engagement_id, "runs", run.id, f"{kind}-{sha[:12]}-{Path(name).name}")
    storage.put_bytes(key, data, content_type or content_type_for(name))
    a = Artefact(
        tenant_id=run.tenant_id,
        client_id=run.client_id,
        engagement_id=run.engagement_id,
        run_id=run.id,
        kind=kind,
        name=name,
        object_key=key,
        sha256=sha,
        size=len(data),
        content_type=content_type or content_type_for(name),
        created_by=run.created_by,
    )
    db.add(a)
    db.flush()
    return a


def create_run(
    db: Session,
    eng: Engagement,
    kind: str,
    *,
    created_by: str,
    input_versions: list[FileVersion],
    params: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    queue: str = "default",
    correlation_id: str | None = None,
    actor: audit.Actor | None = None,
) -> tuple[Run, bool]:
    """Create a run and its job atomically. Returns (run, created). Idempotent per key."""
    if idempotency_key:
        existing = db.execute(select(Run).where(Run.tenant_id == eng.tenant_id, Run.idempotency_key == idempotency_key)).scalar_one_or_none()
        if existing is not None:
            return existing, False
    run = Run(
        id=new_id(),
        tenant_id=eng.tenant_id,
        client_id=eng.client_id,
        engagement_id=eng.id,
        kind=kind,
        input_version_ids=[v.id for v in input_versions],
        input_hashes=[v.sha256 for v in input_versions],
        status="queued",
        params_json=params or {},
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        created_by=created_by,
    )
    try:
        with db.begin_nested():
            db.add(run)
            db.flush()
            enqueue(db, run, queue=queue)
    except IntegrityError:
        existing = db.execute(select(Run).where(Run.tenant_id == eng.tenant_id, Run.idempotency_key == idempotency_key)).scalar_one()
        return existing, False
    if actor is not None:
        audit.append(db, actor, "run.created", client_id=eng.client_id, engagement_id=eng.id, object_type="run", object_id=run.id,
                     details={"kind": kind, "inputs": run.input_hashes, "queue": queue})
    return run, True


def notify(db: Session, tenant_id: str, to_addr: str, subject: str, body: str, kind: str) -> None:
    """Queue an e-mail notification. Callers must not put client figures in it (E-INT-03)."""
    db.add(EmailOutbox(tenant_id=tenant_id, to_addr=to_addr, subject=subject[:300], body=body, kind=kind))


def users_with_role(db: Session, tenant_id: str, role_code: str, client_id: str | None = None) -> list[User]:
    """Active users currently holding the role (a revoked or lapsed assignment, e.g. an expired elevation, does not count)."""
    stmt = (
        select(User)
        .join(UserClientRole, UserClientRole.user_id == User.id)
        .join(Role, Role.id == UserClientRole.role_id)
        .where(User.tenant_id == tenant_id, Role.code == role_code, UserClientRole.revoked_at.is_(None), User.status == "active",
               or_(UserClientRole.expires_at.is_(None), UserClientRole.expires_at > utcnow()))
    )
    if client_id is not None:
        stmt = stmt.where(UserClientRole.client_id == client_id)
    return list(db.execute(stmt).scalars().unique())


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def now_iso() -> str:
    return utcnow().isoformat()
