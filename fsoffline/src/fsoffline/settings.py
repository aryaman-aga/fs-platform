"""Mode C settings: per-user data directory, loopback-only binding, local secrets.

Secrets (session key, KMS master key, worker token, package-signing key) are
generated on first start and stored in the data directory with owner-only
permissions; nothing is ever written next to the program files.
"""

from __future__ import annotations

import base64
import os
import secrets
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
APP_DIR_NAME = "FSPlatform"


class OfflineConfigError(ValueError):
    pass


def validate_bind_host(host: str) -> str:
    """Mode C serves the loopback interface only; remote access is what Mode A is for."""
    normalised = host.strip().lower().strip("[]")
    if normalised not in LOOPBACK_HOSTS:
        raise OfflineConfigError(
            f"the offline edition binds to loopback only (127.0.0.1 / ::1), not {host!r}; "
            "use the firm server (Mode A) for remote access"
        )
    return "127.0.0.1" if normalised == "localhost" else normalised


def default_data_dir(platform: str | None = None, env: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    if override := env.get("FSOFFLINE_DATA_DIR"):
        return Path(override)
    if platform.startswith("win"):
        base = env.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
        return Path(base) / APP_DIR_NAME / "offline"
    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_DIR_NAME / "offline"
    base = env.get("XDG_DATA_HOME") or str(home / ".local" / "share")
    return Path(base) / APP_DIR_NAME.lower() / "offline"


def bundled_ui_dist(env: Mapping[str, str] | None = None) -> Path | None:
    """The built SPA: env override, the PyInstaller bundle, or the repository checkout."""
    env = os.environ if env is None else env
    candidates: list[Path] = []
    if override := env.get("FSOFFLINE_UI_DIST"):
        candidates.append(Path(override))
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "fsui" / "dist")
    candidates.append(Path(__file__).resolve().parents[3] / "fsui" / "dist")
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


@dataclass(frozen=True)
class OfflineSettings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 0  # 0 = pick a free port
    open_browser: bool = True
    ui_dist: Path | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "fsweb.sqlite3"

    @property
    def objects_dir(self) -> Path:
        return self.data_dir / "objects"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    @property
    def audit_head_dir(self) -> Path:
        return self.data_dir / "audit-heads"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "fsoffline.lock"

    @property
    def trusted_keys_path(self) -> Path:
        """Public keys (base64, one per line) of servers whose packages may be imported."""
        return self.data_dir / "trusted_package_keys.txt"


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        path.chmod(0o700)


def ensure_layout(settings: OfflineSettings) -> None:
    _private_dir(settings.data_dir)
    for sub in (settings.objects_dir, settings.work_dir, settings.secrets_dir, settings.audit_head_dir):
        _private_dir(sub)


def load_or_create_secret(path: Path, nbytes: int = 32) -> bytes:
    """Read a random secret, creating it (mode 0600, exclusive create) on first use."""
    if path.is_file():
        data = base64.b64decode(path.read_text(encoding="ascii").strip())
        if len(data) != nbytes:
            raise OfflineConfigError(f"{path.name} is corrupt (expected {nbytes} bytes)")
        return data
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_bytes(nbytes)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(base64.b64encode(value).decode("ascii") + "\n")
    return value


def package_public_key(settings: OfflineSettings) -> str:
    """Base64 Ed25519 public key of this installation (register it on the firm server)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    seed = load_or_create_secret(settings.secrets_dir / "package_signing.key")
    public = Ed25519PrivateKey.from_private_bytes(seed).public_key()
    raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def _trusted_keys(settings: OfflineSettings) -> list[str]:
    if not settings.trusted_keys_path.is_file():
        return []
    lines = settings.trusted_keys_path.read_text(encoding="ascii").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def url_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def build_env(settings: OfflineSettings, port: int, env_name: str = "offline") -> dict[str, str]:
    """The FSWEB_* environment for an offline instance (see fsweb/.env.example)."""
    host = validate_bind_host(settings.host)
    origin = f"http://{url_host(host)}:{port}"
    secrets_dir = settings.secrets_dir
    env = {
        "FSWEB_ENV": env_name,
        "FSWEB_DATABASE_URL": "sqlite:///" + settings.db_path.as_posix(),
        "FSWEB_STORAGE": "local:" + settings.objects_dir.as_posix(),
        "FSWEB_WORK_DIR": settings.work_dir.as_posix(),
        "FSWEB_AUDIT_HEAD_DIR": settings.audit_head_dir.as_posix(),
        "FSWEB_SECRET_KEY": base64.urlsafe_b64encode(load_or_create_secret(secrets_dir / "session.key", 48)).decode(),
        "FSWEB_KMS_MASTER_KEY": base64.b64encode(load_or_create_secret(secrets_dir / "kms_master.key")).decode(),
        "FSWEB_WORKER_TOKEN": base64.urlsafe_b64encode(load_or_create_secret(secrets_dir / "worker.token")).decode(),
        "FSWEB_PKG_SIGNING_KEY": base64.b64encode(load_or_create_secret(secrets_dir / "package_signing.key")).decode(),
        "FSWEB_COOKIE_SECURE": "false",  # plain http on the loopback interface
        "FSWEB_ALLOWED_ORIGINS": origin,
        "FSWEB_PUBLIC_BASE_URL": origin,
        "FSWEB_OFFICE_CIDRS": "127.0.0.1/32,::1/128",
        "FSWEB_AI_ENABLED": "false",  # offline by default (03 §3.7)
        "FSWEB_LOG_LEVEL": "INFO",
    }
    trusted = _trusted_keys(settings)
    if trusted:
        env["FSWEB_PKG_TRUSTED_KEYS"] = ",".join(trusted)
    ui = settings.ui_dist or bundled_ui_dist()
    if ui is not None:
        env["FSWEB_SERVE_UI"] = ui.as_posix()
    return env


def redacted(env: Mapping[str, str]) -> dict[str, str]:
    hidden = ("SECRET", "KEY", "TOKEN", "PASSWORD")
    return {k: ("***" if any(h in k for h in hidden) and k != "FSWEB_PKG_TRUSTED_KEYS" else v) for k, v in env.items()}
