from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from fsoffline.settings import (
    OfflineConfigError,
    OfflineSettings,
    build_env,
    default_data_dir,
    ensure_layout,
    load_or_create_secret,
    package_public_key,
    redacted,
    validate_bind_host,
)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]", " LOCALHOST "])
def test_loopback_hosts_accepted(host: str) -> None:
    assert validate_bind_host(host) in {"127.0.0.1", "::1"}


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "10.0.0.5", "fs.example-firm.in", ""])
def test_non_loopback_hosts_rejected(host: str) -> None:
    with pytest.raises(OfflineConfigError, match="loopback"):
        validate_bind_host(host)


def test_default_data_dir_per_platform(tmp_path: Path) -> None:
    home = tmp_path / "home"
    win = default_data_dir("win32", {"LOCALAPPDATA": r"C:\Users\a\AppData\Local"}, home)
    assert str(win).endswith("FSPlatform" + os.sep + "offline") or win.parts[-2:] == ("FSPlatform", "offline")
    assert default_data_dir("darwin", {}, home) == home / "Library" / "Application Support" / "FSPlatform" / "offline"
    assert default_data_dir("linux", {}, home) == home / ".local" / "share" / "fsplatform" / "offline"
    assert default_data_dir("linux", {"XDG_DATA_HOME": str(tmp_path / "xdg")}, home) == tmp_path / "xdg" / "fsplatform" / "offline"
    assert default_data_dir("linux", {"FSOFFLINE_DATA_DIR": str(tmp_path / "x")}, home) == tmp_path / "x"


def test_secret_created_once_with_private_permissions(tmp_path: Path) -> None:
    path = tmp_path / "secrets" / "kms_master.key"
    first = load_or_create_secret(path)
    second = load_or_create_secret(path)
    assert first == second
    assert len(first) == 32
    if os.name == "posix":
        assert (path.stat().st_mode & 0o777) == 0o600


def test_corrupt_secret_rejected(tmp_path: Path) -> None:
    path = tmp_path / "k"
    path.write_text(base64.b64encode(b"short").decode())
    with pytest.raises(OfflineConfigError, match="corrupt"):
        load_or_create_secret(path)


def test_layout_is_private(tmp_path: Path) -> None:
    settings = OfflineSettings(data_dir=tmp_path / "data")
    ensure_layout(settings)
    for sub in (settings.objects_dir, settings.work_dir, settings.secrets_dir, settings.audit_head_dir):
        assert sub.is_dir()
    if os.name == "posix":
        assert (settings.data_dir.stat().st_mode & 0o777) == 0o700


def test_build_env_for_fsweb(tmp_path: Path) -> None:
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html>")
    settings = OfflineSettings(data_dir=tmp_path / "data", ui_dist=ui)
    ensure_layout(settings)
    env = build_env(settings, 8765)
    assert env["FSWEB_ENV"] == "offline"
    assert env["FSWEB_DATABASE_URL"] == "sqlite:///" + (tmp_path / "data" / "fsweb.sqlite3").as_posix()
    assert env["FSWEB_STORAGE"].startswith("local:")
    assert env["FSWEB_SERVE_UI"] == ui.as_posix()
    assert env["FSWEB_ALLOWED_ORIGINS"] == "http://127.0.0.1:8765"
    assert env["FSWEB_COOKIE_SECURE"] == "false"
    assert env["FSWEB_AI_ENABLED"] == "false"
    assert len(base64.b64decode(env["FSWEB_KMS_MASTER_KEY"])) == 32
    assert len(env["FSWEB_SECRET_KEY"]) >= 32
    # stable across restarts (sessions, encrypted fields and packages stay readable)
    assert build_env(settings, 9999)["FSWEB_KMS_MASTER_KEY"] == env["FSWEB_KMS_MASTER_KEY"]


def test_build_env_ipv6_origin_and_trusted_keys(tmp_path: Path) -> None:
    settings = OfflineSettings(data_dir=tmp_path / "d", host="::1")
    ensure_layout(settings)
    settings.trusted_keys_path.write_text("# firm server\n" + base64.b64encode(b"k" * 32).decode() + "\n")
    env = build_env(settings, 4000)
    assert env["FSWEB_ALLOWED_ORIGINS"] == "http://[::1]:4000"
    assert env["FSWEB_PKG_TRUSTED_KEYS"] == base64.b64encode(b"k" * 32).decode()


def test_build_env_refuses_public_bind(tmp_path: Path) -> None:
    with pytest.raises(OfflineConfigError):
        build_env(OfflineSettings(data_dir=tmp_path, host="0.0.0.0"), 80)


def test_redaction_hides_secrets(tmp_path: Path) -> None:
    settings = OfflineSettings(data_dir=tmp_path / "d")
    ensure_layout(settings)
    shown = redacted(build_env(settings, 1234))
    for key in ("FSWEB_SECRET_KEY", "FSWEB_KMS_MASTER_KEY", "FSWEB_WORKER_TOKEN", "FSWEB_PKG_SIGNING_KEY"):
        assert shown[key] == "***"
    assert shown["FSWEB_ENV"] == "offline"


def test_package_public_key_is_stable_ed25519(tmp_path: Path) -> None:
    settings = OfflineSettings(data_dir=tmp_path / "d")
    ensure_layout(settings)
    key = package_public_key(settings)
    assert len(base64.b64decode(key)) == 32
    assert package_public_key(settings) == key
