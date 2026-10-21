"""Worker configuration from environment variables and an optional INI file.

Precedence: environment > INI file > defaults. The worker token is never part
of ``repr()`` or logs; prefer ``FSRENDER_WORKER_TOKEN_FILE`` pointing at a file
readable only by the service account.

INI (default ``%PROGRAMDATA%\\FSRender\\fsrender.ini`` on Windows)::

    [fsrender]
    api_url = https://fs-internal.example-firm.in
    worker_token_file = C:\\ProgramData\\FSRender\\worker.token
    worker_id = RENDER-01
    job_timeout_s = 300
"""

from __future__ import annotations

import configparser
import os
import socket
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

ENV_PREFIX = "FSRENDER_"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ConfigError(ValueError):
    pass


def default_ini_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("PROGRAMDATA") or (r"C:\ProgramData" if sys.platform == "win32" else "/etc")
    return Path(base) / "FSRender" / "fsrender.ini"


@dataclass(frozen=True)
class WorkerConfig:
    api_url: str
    worker_token: str = field(repr=False)
    worker_id: str
    poll_interval_s: float = 5.0
    heartbeat_interval_s: float = 30.0
    job_timeout_s: int = 300
    work_dir: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "fsrender")
    http_timeout_s: float = 120.0
    max_backoff_s: float = 300.0
    max_input_bytes: int = 512 * 1024 * 1024
    verify_tls: bool | str = True
    excel_build: str | None = None  # override of the detected Excel build
    log_dir: Path | None = None


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config(env: Mapping[str, str] | None = None, ini_path: Path | None = None) -> WorkerConfig:
    env = dict(os.environ if env is None else env)
    values: dict[str, str] = {}
    path = ini_path or Path(env.get(ENV_PREFIX + "CONFIG", "") or default_ini_path(env))
    if path.is_file():
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(path, encoding="utf-8")
        if parser.has_section("fsrender"):
            values.update({k.lower(): v for k, v in parser.items("fsrender")})
    for key, value in env.items():
        if key.startswith(ENV_PREFIX) and key != ENV_PREFIX + "CONFIG":
            values[key[len(ENV_PREFIX):].lower()] = value

    api_url = values.get("api_url", "").rstrip("/")
    if not api_url:
        raise ConfigError("api_url is required (FSRENDER_API_URL or [fsrender] api_url)")
    parsed = urlparse(api_url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ConfigError("api_url must be an absolute http(s) URL")
    if parsed.scheme == "http" and parsed.hostname not in _LOCAL_HOSTS and not _as_bool(values.get("allow_http", "false")):
        raise ConfigError("api_url must use https (set FSRENDER_ALLOW_HTTP=1 only on an isolated lab network)")

    token = values.get("worker_token", "")
    token_file = values.get("worker_token_file")
    if not token and token_file:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    if not token:
        raise ConfigError("worker token is required (FSRENDER_WORKER_TOKEN or worker_token_file)")

    verify: bool | str = True
    if "ca_bundle" in values:
        verify = values["ca_bundle"]
    elif "verify_tls" in values:
        verify = _as_bool(values["verify_tls"])

    try:
        return WorkerConfig(
            api_url=api_url,
            worker_token=token,
            worker_id=values.get("worker_id") or socket.gethostname(),
            poll_interval_s=float(values.get("poll_interval_s", 5.0)),
            heartbeat_interval_s=float(values.get("heartbeat_interval_s", 30.0)),
            job_timeout_s=int(values.get("job_timeout_s", 300)),
            work_dir=Path(values["work_dir"]) if values.get("work_dir") else Path(tempfile.gettempdir()) / "fsrender",
            http_timeout_s=float(values.get("http_timeout_s", 120.0)),
            max_backoff_s=float(values.get("max_backoff_s", 300.0)),
            max_input_bytes=int(values.get("max_input_bytes", 512 * 1024 * 1024)),
            verify_tls=verify,
            excel_build=values.get("excel_build") or None,
            log_dir=Path(values["log_dir"]) if values.get("log_dir") else None,
        )
    except ValueError as exc:
        raise ConfigError(f"invalid numeric setting: {exc}") from exc
