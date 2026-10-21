"""FS-Platform Recovery: for the PC's Windows administrator when nobody can sign in, and for backups.

Started from the Start menu (it asks for administrator rights), or non-interactively as
``python -m fsoffline recover <action>``. Every account change is recorded in the audit trail.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import subprocess
import sys
import zipfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fsoffline import backup as backups
from fsoffline.desktop import read_runtime, request_stop
from fsoffline.install import DEFAULT_PORT, load_install_config
from fsoffline.settings import OfflineSettings, build_env, ensure_layout, redacted

USERS_GROUP = "FS-Platform Users"  # the Windows accounts allowed to use FS-Platform on this PC (created by the installer)

MENU = """
FS-Platform Recovery
  1. Reset an administrator's password (shows a one-time link)
  2. Reset an administrator's authenticator (MFA)
  3. Allow another Windows account to use FS-Platform
  4. Restore a backup
  5. Save diagnostics for support
  6. Stop FS-Platform
  0. Exit
"""


class RecoveryError(RuntimeError):
    pass


# --------------------------------------------------------------------------- administrator rights
def is_admin() -> bool:
    if os.name != "nt":
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    import ctypes  # pragma: no cover - Windows

    return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]  # pragma: no cover


def relaunch_elevated(argv: Sequence[str]) -> bool:  # pragma: no cover - Windows
    """Start this tool again with administrator rights (UAC prompt); True if the new process started."""
    import ctypes
    import subprocess as sp

    params = sp.list2cmdline(["-X", "utf8", "-m", "fsoffline", *argv])
    exe = sys.executable.replace("pythonw.exe", "python.exe")
    return int(ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)) > 32  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- account recovery
def _fsweb_env(settings: OfflineSettings) -> None:
    ensure_layout(settings)
    rt = read_runtime(settings)
    port = int(rt["port"]) if rt else (load_install_config().port or DEFAULT_PORT)
    os.environ.update(build_env(settings, port))


def _actor(tenant_id: str) -> Any:
    from fsweb import audit

    return audit.Actor(tenant_id, None, "system:recovery", None, "fsoffline-recovery", None)


def _with_user(settings: OfflineSettings, email: str, action: Any) -> Any:
    _fsweb_env(settings)
    from sqlalchemy import select

    from fsweb.db import SYSTEM, Database
    from fsweb.models import User
    from fsweb.settings import Settings

    s_obj = Settings.from_env()
    db = Database(s_obj.database_url)
    try:
        with db.owner_session(SYSTEM) as s:
            user = s.execute(select(User).where(User.email == email.strip().lower())).scalar_one_or_none()
            if user is None:
                raise RecoveryError(f"no FS-Platform account uses {email!r}")
            out = action(s, s_obj, user)
            s.commit()
            return out
    finally:
        db.dispose()


def reset_password(settings: OfflineSettings, email: str) -> tuple[str, datetime]:
    """A one-time link that lets the account set a new password (valid 24 hours)."""
    from fsweb.services.accounts import issue_password_reset_link

    return _with_user(settings, email, lambda s, cfg, user: issue_password_reset_link(s, cfg, user, _actor(user.tenant_id)))  # type: ignore[no-any-return]


def reset_mfa(settings: OfflineSettings, email: str) -> None:
    """Remove the account's authenticator; it enrols a new one (QR code) at the next sign-in."""
    from fsweb.services.accounts import reset_mfa as _reset

    _with_user(settings, email, lambda s, cfg, user: _reset(s, user, _actor(user.tenant_id)))


def allow_windows_account(account: str) -> None:  # pragma: no cover - Windows
    """Add a Windows account to the group that may use FS-Platform's data folder (effective at its next sign-in)."""
    net = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "net.exe"
    proc = subprocess.run([str(net), "localgroup", USERS_GROUP, account, "/add"], capture_output=True, text=True,  # noqa: S603
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    if proc.returncode != 0 and "1378" not in (proc.stderr + proc.stdout):  # 1378: already a member
        raise RecoveryError((proc.stderr or proc.stdout).strip() or f"net localgroup failed ({proc.returncode})")


# --------------------------------------------------------------------------- backups and support
def restore(settings: OfflineSettings, backup_dir: Path) -> Path:
    if not request_stop(settings, timeout_s=90):
        raise RecoveryError("FS-Platform did not stop; close it from the tray icon and try again")
    return backups.restore_backup(settings, backup_dir)


def diagnostics(settings: OfflineSettings, out: Path) -> Path:
    """A zip for support: logs, versions, redacted settings, the list of backups. No database, files or keys."""
    from fsoffline import __version__

    out.parent.mkdir(parents=True, exist_ok=True)
    info = {
        "created": datetime.now(UTC).isoformat(), "fsoffline": __version__, "python": sys.version, "platform": platform.platform(),
        "install": {k: str(v) for k, v in vars(load_install_config()).items()}, "runtime": read_runtime(settings),
        "backups": [b.name for b in backups.list_backups(settings)],
    }
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("info.json", json.dumps(info, indent=2, default=str))
        with contextlib.suppress(Exception):
            z.writestr("settings.txt", "\n".join(f"{k}={v}" for k, v in sorted(redacted(build_env(settings, 0)).items())))
        for f in sorted(settings.logs_dir.glob("*.log*")) if settings.logs_dir.exists() else []:
            z.write(f, f"logs/{f.name}")
    return out


# --------------------------------------------------------------------------- the menu
def _ask(prompt: str) -> str:
    return input(prompt).strip()


def interactive(settings: OfflineSettings) -> int:  # pragma: no cover - console UI
    print(f"Data folder: {settings.data_dir}")
    while True:
        print(MENU)
        choice = _ask("Choose: ")
        try:
            if choice == "1":
                link, expires = reset_password(settings, _ask("Administrator's e-mail: "))
                print(f"\nOpen this link on this PC to set a new password (valid until {expires:%d-%b-%Y %H:%M} UTC):\n{link}\n")
            elif choice == "2":
                email = _ask("Administrator's e-mail: ")
                reset_mfa(settings, email)
                print(f"\n{email} enrols a new authenticator at the next sign-in.\n")
            elif choice == "3":
                account = _ask(r"Windows account (e.g. OFFICE-PC\priya): ")
                allow_windows_account(account)
                print(f"\n{account} can use FS-Platform after signing out of Windows and back in.\n")
            elif choice == "4":
                found = backups.list_backups(settings)[:10]
                if not found:
                    print("\nNo backups yet.\n")
                    continue
                for i, b in enumerate(found, 1):
                    print(f"  {i}. {b.name}")
                pick = _ask("Backup number to restore (blank to cancel): ")
                if not pick:
                    continue
                target = found[int(pick) - 1]
                if _ask(f"Type RESTORE to replace the current data with {target.name}: ") != "RESTORE":
                    continue
                aside = restore(settings, target)
                print(f"\nRestored. The data it replaced is kept in {aside}. Start FS-Platform again from the Start menu.\n")
            elif choice == "5":
                out = diagnostics(settings, Path.home() / "Desktop" / f"FS-Platform diagnostics {datetime.now():%Y-%m-%d %H%M}.zip")
                print(f"\nSaved {out}\n")
            elif choice == "6":
                print("\nStopped.\n" if request_stop(settings) else "\nFS-Platform did not stop in time.\n")
            elif choice in ("0", ""):
                return 0
        except (RecoveryError, backups.BackupError, ValueError, IndexError) as exc:
            print(f"\nNot done: {exc}\n")


__all__ = ["USERS_GROUP", "RecoveryError", "allow_windows_account", "diagnostics", "interactive", "is_admin", "relaunch_elevated",
           "reset_mfa", "reset_password", "restore"]
