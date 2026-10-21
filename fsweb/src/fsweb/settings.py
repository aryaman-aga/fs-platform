"""Runtime settings, read from FSWEB_* environment variables (09 §2: no hard-coded paths).

Settings are an immutable value object so tests can build an app with explicit
values (``create_app(Settings(...))``) without touching the process environment.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Env = Literal["dev", "test", "prod", "offline"]


def _bool(v: str | None, default: bool) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _list(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    env: Env = "dev"
    database_url: str = "sqlite:///./var/fsweb.db"
    app_database_url: str | None = None  # runtime role subject to RLS (Postgres)
    storage: str = "local:./var/objects"
    s3_endpoint: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "ap-south-1"
    secret_key: str = "dev-insecure-secret-key-change-me-0123456789"  # noqa: S105 - rejected in prod/offline
    kms_master_key: str | None = None  # base64 of 32 bytes; "id:key,id:key" for rotation history
    cookie_secure: bool = False
    allowed_origins: list[str] = Field(default_factory=list)
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str | None = None
    redis_url: str | None = None
    office_cidrs: list[str] = Field(default_factory=list)
    work_dir: str = "./var/work"
    serve_ui: str | None = None
    worker_token: str | None = None
    ai_enabled: bool = False
    pkg_signing_key: str | None = None  # base64 Ed25519 seed (32 bytes)
    pkg_trusted_keys: list[str] = Field(default_factory=list)  # base64 Ed25519 public keys
    audit_head_dir: str = "./var/audit-heads"
    max_upload_mb: int = 200
    clamd_socket: str | None = None
    login_rate_per_minute: int = 20
    session_idle_minutes_firm: int = 60
    session_idle_minutes_client: int = 30
    session_absolute_hours: int = 12
    stepup_minutes: int = 5
    invite_hours: int = 72
    reset_minutes: int = 60
    lockout_threshold: int = 5
    lockout_minutes: int = 15
    retention_years: int = 7
    ai_monthly_budget_usd: float = 200.0
    dsr_due_days: int = 30
    log_level: str = "INFO"
    public_base_url: str = "http://localhost:5173"
    trust_proxy: bool = False
    engine: str = "real"  # real | fake (tests / demos without the engine)

    # ------------------------------------------------------------------ helpers
    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Settings:
        e = dict(os.environ if environ is None else environ)
        g = e.get
        data: dict[str, object] = {}
        mapping: dict[str, str] = {
            "env": "FSWEB_ENV",
            "database_url": "FSWEB_DATABASE_URL",
            "app_database_url": "FSWEB_APP_DATABASE_URL",
            "storage": "FSWEB_STORAGE",
            "s3_endpoint": "FSWEB_S3_ENDPOINT",
            "s3_access_key": "FSWEB_S3_ACCESS_KEY",
            "s3_secret_key": "FSWEB_S3_SECRET_KEY",
            "s3_region": "FSWEB_S3_REGION",
            "secret_key": "FSWEB_SECRET_KEY",
            "kms_master_key": "FSWEB_KMS_MASTER_KEY",
            "oidc_issuer": "FSWEB_OIDC_ISSUER",
            "oidc_client_id": "FSWEB_OIDC_CLIENT_ID",
            "oidc_client_secret": "FSWEB_OIDC_CLIENT_SECRET",
            "oidc_redirect_uri": "FSWEB_OIDC_REDIRECT_URI",
            "redis_url": "FSWEB_REDIS_URL",
            "work_dir": "FSWEB_WORK_DIR",
            "serve_ui": "FSWEB_SERVE_UI",
            "worker_token": "FSWEB_WORKER_TOKEN",
            "pkg_signing_key": "FSWEB_PKG_SIGNING_KEY",
            "audit_head_dir": "FSWEB_AUDIT_HEAD_DIR",
            "clamd_socket": "FSWEB_CLAMD_SOCKET",
            "log_level": "FSWEB_LOG_LEVEL",
            "public_base_url": "FSWEB_PUBLIC_BASE_URL",
        }
        for field, var in mapping.items():
            if g(var):
                data[field] = g(var)
        if g("FSWEB_COOKIE_SECURE") is not None:
            data["cookie_secure"] = _bool(g("FSWEB_COOKIE_SECURE"), False)
        if g("FSWEB_TRUST_PROXY") is not None:
            data["trust_proxy"] = _bool(g("FSWEB_TRUST_PROXY"), False)
        if g("FSWEB_ENGINE"):
            data["engine"] = g("FSWEB_ENGINE")
        if g("FSWEB_AI_ENABLED") is not None:
            data["ai_enabled"] = _bool(g("FSWEB_AI_ENABLED"), False)
        if g("FSWEB_ALLOWED_ORIGINS"):
            data["allowed_origins"] = _list(g("FSWEB_ALLOWED_ORIGINS"))
        if g("FSWEB_OFFICE_CIDRS"):
            data["office_cidrs"] = _list(g("FSWEB_OFFICE_CIDRS"))
        if g("FSWEB_PKG_TRUSTED_KEYS"):
            data["pkg_trusted_keys"] = _list(g("FSWEB_PKG_TRUSTED_KEYS"))
        for field, var in {"max_upload_mb": "FSWEB_MAX_UPLOAD_MB", "retention_years": "FSWEB_RETENTION_YEARS"}.items():
            if g(var):
                data[field] = int(g(var) or "0")
        s = cls.model_validate(data)
        s.validate_for_env()
        return s

    @property
    def production_like(self) -> bool:
        """``prod`` (Mode A) and ``offline`` (Mode C launcher) both run with production security."""
        return self.env in ("prod", "offline")

    def validate_for_env(self) -> None:
        """Refuse insecure production settings (fail closed)."""
        if not self.production_like:
            return
        problems = []
        if self.secret_key.startswith("dev-") or len(self.secret_key) < 32:
            problems.append("FSWEB_SECRET_KEY must be a random value of 32+ characters")
        if not self.kms_master_key:
            problems.append(f"FSWEB_KMS_MASTER_KEY is required in {self.env}")
        if self.env == "prod":
            # Mode C binds to loopback over plain http; secure cookies and PostgreSQL are Mode A requirements.
            if not self.cookie_secure:
                problems.append("FSWEB_COOKIE_SECURE must be true in prod")
            if self.database_url.startswith("sqlite"):
                problems.append("prod (Mode A) requires PostgreSQL; SQLite is for the offline edition (FSWEB_ENV=offline) and tests")
        if problems:
            raise RuntimeError("insecure configuration: " + "; ".join(problems))

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def runtime_database_url(self) -> str:
        return self.app_database_url or self.database_url

    def master_keys(self) -> dict[str, bytes]:
        """KEK versions for envelope encryption. Last entry is the active one.

        ``FSWEB_KMS_MASTER_KEY`` is either one base64 key or ``id:b64,id:b64``.
        In dev/test a key is derived from the secret key so the app still runs.
        """
        raw = self.kms_master_key
        if not raw:
            derived = hashlib.sha256(("kms:" + self.secret_key).encode()).digest()
            return {"k-dev": derived}
        out: dict[str, bytes] = {}
        for i, part in enumerate(_list(raw)):
            if ":" in part:
                kid, b64 = part.split(":", 1)
            else:
                kid, b64 = f"k{i + 1}", part
            key = base64.b64decode(b64)
            if len(key) != 32:
                raise RuntimeError(f"KMS master key {kid} must be 32 bytes")
            out[kid] = key
        return out

    def work_path(self) -> Path:
        p = Path(self.work_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def set_settings(s: Settings) -> None:
    global _settings
    _settings = s
