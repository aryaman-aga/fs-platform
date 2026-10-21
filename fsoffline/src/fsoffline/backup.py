"""Desktop backups (C13) and restore.

A backup is a dated folder in ``<data>\\backups`` holding a consistent copy of the database (SQLite online backup, so
it runs while the server is up), the keys that decrypt it (``secrets\\``) and a manifest. Stored files never change
once written, so they are mirrored incrementally into ``<data>\\backups\\objects`` instead of being copied every day.
The newest :data:`KEEP` dated folders are kept. When an administrator has set a second folder (Admin → Backups, tenant
setting ``backup_copy_dir``), every backup is also copied there under ``FS-Platform Backups\\<computer>``. Each run is
recorded in the backup log (``BackupLog``, kind ``desktop``) so it shows on the Admin → Backups screen and in the
evidence pack.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import platform
import shutil
import sqlite3
import stat
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fsoffline.settings import OfflineSettings

log = logging.getLogger("fsoffline.backup")

KEEP = 14
OBJECTS = "objects"
MANIFEST = "manifest.json"
COPY_FOLDER = "FS-Platform Backups"


class BackupError(RuntimeError):
    pass


@dataclass
class BackupResult:
    path: Path
    ok: bool = True
    size: int = 0
    objects_copied: int = 0
    copied_to: Path | None = None
    copy_error: str | None = None
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def _native(p: Path) -> Path:
    from fsweb.storage import native_path

    return native_path(p.absolute())


def _writable(path: str | os.PathLike[str]) -> None:
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)


def _rmtree(path: Path) -> None:
    """Delete a folder even though stored files are read-only (Windows refuses to delete read-only files)."""

    def _retry(func: Any, p: str, _exc: Any) -> None:
        _writable(p)
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(_native(path), onexc=_retry)
    else:  # pragma: no cover - Python 3.11
        shutil.rmtree(_native(path), onerror=_retry)


def _iter_files(root: Path) -> Iterator[Path]:
    base = _native(root)
    if not base.exists():
        return
    for dirpath, _dirs, files in os.walk(base):
        for name in files:
            yield Path(dirpath) / name


def mirror_objects(src: Path, dst: Path) -> int:
    """Copy stored files that the mirror does not have yet (they are immutable); WORM metadata is refreshed."""
    copied = 0
    src_n, dst_n = _native(src), _native(dst)
    for f in _iter_files(src):
        rel = f.relative_to(src_n)
        target = dst_n / rel
        if target.exists() and (".worm" not in rel.parts or (target.stat().st_size == f.stat().st_size and
                                target.read_bytes() == f.read_bytes())):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            _writable(target)
        shutil.copyfile(f, target)
        copied += 1
    return copied


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sqlite_copy(src: Path, dst: Path) -> None:
    """A consistent copy of a live WAL database (the online backup API), checked before it counts as a backup."""
    with contextlib.closing(sqlite3.connect(src, timeout=30)) as source, contextlib.closing(sqlite3.connect(dst, timeout=30)) as target:
        source.backup(target)
        target.execute("PRAGMA journal_mode=DELETE")
        result = target.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise BackupError(f"the database copy failed its integrity check: {result}")


def schema_revision(db_path: Path) -> str | None:
    with contextlib.closing(sqlite3.connect(db_path, timeout=30)) as conn:
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.Error:
            return None
    return str(row[0]) if row else None


def copy_dir_setting(db_path: Path) -> Path | None:
    """The second backup folder an administrator set on the Admin → Backups screen (first firm that has one)."""
    if not db_path.exists():
        return None
    with contextlib.closing(sqlite3.connect(db_path, timeout=30)) as conn:
        try:
            rows = conn.execute("SELECT settings_json FROM tenant").fetchall()
        except sqlite3.Error:
            return None
    for (raw,) in rows:
        with contextlib.suppress(TypeError, ValueError):
            value = (json.loads(raw) if isinstance(raw, str) else raw or {}).get("backup_copy_dir")
            if value:
                return Path(str(value))
    return None


def list_backups(settings: OfflineSettings) -> list[Path]:
    """Dated backup folders, newest first."""
    root = settings.backups_dir
    if not root.exists():
        return []
    return sorted((p for p in root.iterdir() if p.is_dir() and (p / MANIFEST).is_file()), key=lambda p: p.name, reverse=True)


def last_backup_time(settings: OfflineSettings) -> datetime | None:
    for b in list_backups(settings):
        with contextlib.suppress(OSError, ValueError, KeyError):
            return datetime.fromisoformat(json.loads((b / MANIFEST).read_text(encoding="utf-8"))["created"])
    return None


def backup_due(settings: OfflineSettings, now: datetime | None = None, every: timedelta = timedelta(hours=24)) -> bool:
    last = last_backup_time(settings)
    return last is None or (now or datetime.now(UTC)) - last >= every


def _prune(settings: OfflineSettings, keep: int) -> list[str]:
    removed = []
    for old in list_backups(settings)[keep:]:
        _rmtree(old)
        removed.append(old.name)
    return removed


def _copy_to_second_folder(settings: OfflineSettings, dated: Path, copy_root: Path) -> Path:
    target_root = copy_root / COPY_FOLDER / (platform.node() or "this-pc")
    target = _native(target_root / dated.name)
    if target.exists():
        _rmtree(target)
    shutil.copytree(_native(dated), target)
    mirror_objects(settings.backups_dir / OBJECTS, target_root / OBJECTS)
    for old in sorted((p for p in _native(target_root).iterdir() if p.is_dir() and p.name != OBJECTS), key=lambda p: p.name,
                      reverse=True)[KEEP:]:
        _rmtree(old)
    return target_root


def run_backup(settings: OfflineSettings, *, reason: str = "daily", now: datetime | None = None, keep: int = KEEP,
               record: bool = True) -> BackupResult:
    now = now or datetime.now(UTC)
    if not settings.db_path.exists():
        raise BackupError("there is no database to back up yet")
    stamp = now.strftime("%Y%m%d-%H%M%S")
    dated = settings.backups_dir / stamp
    n = 1
    while dated.exists():
        n += 1
        dated = settings.backups_dir / f"{stamp}-{n}"
    dated.mkdir(parents=True)
    result = BackupResult(path=dated)
    try:
        _sqlite_copy(settings.db_path, dated / "fsweb.sqlite3")
        shutil.copytree(settings.secrets_dir, dated / "secrets", ignore=shutil.ignore_patterns("setup.token"))
        result.objects_copied = mirror_objects(settings.objects_dir, settings.backups_dir / OBJECTS)
        result.size = sum(f.stat().st_size for f in _iter_files(dated))
        manifest = {
            "created": now.isoformat(), "reason": reason, "computer": platform.node(),
            "schema_revision": schema_revision(dated / "fsweb.sqlite3"),
            "database_sha256": _sha256(dated / "fsweb.sqlite3"), "objects_copied": result.objects_copied,
            "objects_total": sum(1 for _ in _iter_files(settings.objects_dir)),
        }
        (dated / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        result.details = {"reason": reason, "pruned": _prune(settings, keep), **manifest}
    except Exception as exc:  # recorded and shown in the tray; the partial folder is removed
        result.ok, result.error = False, f"{type(exc).__name__}: {exc}"
        log.exception("backup failed")
        with contextlib.suppress(OSError):
            _rmtree(dated)
    if result.ok:
        copy_root = copy_dir_setting(settings.db_path)
        if copy_root is not None:
            try:
                result.copied_to = _copy_to_second_folder(settings, dated, copy_root)
            except OSError as exc:
                result.copy_error = f"could not copy the backup to {copy_root}: {exc}"
                log.warning("%s", result.copy_error)
    if record:
        with contextlib.suppress(Exception):
            record_backup(settings, result)
    return result


def record_backup(settings: OfflineSettings, result: BackupResult, kind: str = "desktop") -> None:
    """A row in the backup log and an audit event for every firm in this installation."""
    from fsweb import audit
    from fsweb.db import SYSTEM, Database, utcnow
    from fsweb.models import BackupLog, Tenant

    db = Database("sqlite:///" + settings.db_path.as_posix())
    try:
        with db.owner_session(SYSTEM) as s:
            for tenant in s.query(Tenant).all():
                status = "ok" if result.ok and not result.copy_error else "failed"
                details = {**result.details, "copied_to": str(result.copied_to) if result.copied_to else None,
                           "copy_error": result.copy_error, "error": result.error}
                s.add(BackupLog(tenant_id=tenant.id, kind=kind, started=utcnow(), finished=utcnow(), status=status,
                                size=result.size, location=str(result.path)[:300], verified=result.ok, details_json=details,
                                created_by="fsoffline"))
                audit.append(s, audit.Actor(tenant.id, None, "system:fsoffline", None, "fsoffline", None), f"backup.{kind}",
                             object_type="backup", object_id=result.path.name, outcome="ok" if status == "ok" else "failed",
                             details={"status": status, "objects_copied": result.objects_copied})
            s.commit()
    finally:
        db.dispose()


def restore_backup(settings: OfflineSettings, backup_dir: Path) -> Path:
    """Put a backup back (the server must be stopped). The current database and keys are kept aside first."""
    manifest_path = backup_dir / MANIFEST
    if not manifest_path.is_file() or not (backup_dir / "fsweb.sqlite3").is_file():
        raise BackupError(f"{backup_dir} is not an FS-Platform backup")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if _sha256(backup_dir / "fsweb.sqlite3") != manifest.get("database_sha256"):
        raise BackupError("the backup's database does not match its manifest (damaged copy)")
    aside = settings.data_dir / f"before-restore-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    aside.mkdir(parents=True)
    for name in ("fsweb.sqlite3", "fsweb.sqlite3-wal", "fsweb.sqlite3-shm"):
        current = settings.data_dir / name
        if current.exists():
            shutil.move(str(current), str(aside / name))
    if settings.secrets_dir.exists():
        shutil.copytree(settings.secrets_dir, aside / "secrets")
    shutil.copyfile(backup_dir / "fsweb.sqlite3", settings.db_path)
    for f in (backup_dir / "secrets").glob("*"):
        target = settings.secrets_dir / f.name
        if target.exists():
            _writable(target)
        shutil.copyfile(f, target)
    mirror = backup_dir.parent / OBJECTS
    if mirror.exists():
        mirror_objects(mirror, settings.objects_dir)
    result = BackupResult(path=backup_dir, details={"restored_from": backup_dir.name, "previous_kept_in": aside.name,
                                                    "schema_revision": manifest.get("schema_revision")})
    with contextlib.suppress(Exception):
        record_backup(settings, result, kind="restore")
    return aside


__all__ = ["KEEP", "BackupError", "BackupResult", "backup_due", "copy_dir_setting", "last_backup_time", "list_backups",
           "mirror_objects", "record_backup", "restore_backup", "run_backup"]
