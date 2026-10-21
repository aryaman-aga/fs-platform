from __future__ import annotations

from pathlib import Path

import pytest

from fsrender_win.config import ConfigError, load_config


def _env(**kw: str) -> dict[str, str]:
    base = {"FSRENDER_API_URL": "https://fs-internal.example-firm.in/", "FSRENDER_WORKER_TOKEN": "tok-123"}
    base.update(kw)
    return base


def test_env_config(tmp_path: Path) -> None:
    cfg = load_config(_env(FSRENDER_WORKER_ID="RENDER-01", FSRENDER_JOB_TIMEOUT_S="120"), ini_path=tmp_path / "missing.ini")
    assert cfg.api_url == "https://fs-internal.example-firm.in"
    assert cfg.worker_id == "RENDER-01"
    assert cfg.job_timeout_s == 120
    assert "tok-123" not in repr(cfg)


def test_ini_then_env_precedence(tmp_path: Path) -> None:
    token_file = tmp_path / "worker.token"
    token_file.write_text("from-file\n")
    ini = tmp_path / "fsrender.ini"
    ini.write_text(
        "[fsrender]\napi_url = https://ini.example-firm.in\nworker_token_file = "
        + str(token_file)
        + "\nworker_id = INI-01\npoll_interval_s = 9\n"
    )
    cfg = load_config({"FSRENDER_WORKER_ID": "ENV-01"}, ini_path=ini)
    assert cfg.api_url == "https://ini.example-firm.in"
    assert cfg.worker_token == "from-file"
    assert cfg.worker_id == "ENV-01"  # env wins
    assert cfg.poll_interval_s == 9.0


def test_https_enforced_except_loopback_or_explicit(tmp_path: Path) -> None:
    missing = tmp_path / "none.ini"
    with pytest.raises(ConfigError, match="https"):
        load_config(_env(FSRENDER_API_URL="http://10.0.0.5:8000"), ini_path=missing)
    assert load_config(_env(FSRENDER_API_URL="http://127.0.0.1:8000"), ini_path=missing).api_url.startswith("http://")
    assert load_config(_env(FSRENDER_API_URL="http://10.0.0.5:8000", FSRENDER_ALLOW_HTTP="1"), ini_path=missing)


@pytest.mark.parametrize(
    ("env", "match"),
    [
        ({"FSRENDER_WORKER_TOKEN": "x"}, "api_url"),
        ({"FSRENDER_API_URL": "https://a.example-firm.in"}, "token"),
        ({"FSRENDER_API_URL": "ftp://a", "FSRENDER_WORKER_TOKEN": "x"}, "http"),
        ({"FSRENDER_API_URL": "https://a.example-firm.in", "FSRENDER_WORKER_TOKEN": "x", "FSRENDER_JOB_TIMEOUT_S": "soon"}, "numeric"),
    ],
)
def test_invalid_configs(tmp_path: Path, env: dict[str, str], match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        load_config(env, ini_path=tmp_path / "none.ini")


def test_ca_bundle_and_verify_flag(tmp_path: Path) -> None:
    missing = tmp_path / "none.ini"
    assert load_config(_env(FSRENDER_CA_BUNDLE="C:/certs/firm-ca.pem"), ini_path=missing).verify_tls == "C:/certs/firm-ca.pem"
    assert load_config(_env(FSRENDER_VERIFY_TLS="false"), ini_path=missing).verify_tls is False
