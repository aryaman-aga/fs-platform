"""Edge behaviour: security headers, error shape + correlation id, metrics, settings
(prod / offline fail closed), the worker loop and the admin CLI."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fsweb import manage
from fsweb.settings import Settings
from tests.conftest import FakeClock, World, upload_workbook


def test_security_headers_and_error_shape(world: World) -> None:
    c = TestClient(world.app)
    r = c.get("/api/v1/engagements", headers={"X-Correlation-ID": "abc-12345678"})
    assert r.status_code == 401
    body = r.json()["error"]
    assert body["code"] == "unauthenticated" and body["correlation_id"] == "abc-12345678"
    for h, v in {"x-frame-options": "DENY", "x-content-type-options": "nosniff", "referrer-policy": "no-referrer", "cache-control": "no-store"}.items():
        assert r.headers[h] == v
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    caddyfile = Path(__file__).resolve().parents[2] / "ops" / "caddy" / "Caddyfile"
    if caddyfile.exists():  # the API sends the same policy as the reverse proxy in front of the UI
        assert f'Content-Security-Policy "{r.headers["content-security-policy"]}"' in caddyfile.read_text()
    assert r.headers["x-correlation-id"] == "abc-12345678"
    r = c.get("/api/v1/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = c.post("/api/v1/auth/login", json={"email": "not-an-email", "password": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert "strict-transport-security" not in r.headers  # only with secure cookies (prod)


def test_hsts_in_secure_mode(tmp_path: Path, totp_clock: FakeClock) -> None:
    from tests.conftest import build_world

    w = build_world(tmp_path, totp_clock, cookie_secure=True)
    r = TestClient(w.app, base_url="https://testserver").get("/api/v1/health")
    assert r.headers["strict-transport-security"].startswith("max-age=")


def test_health_ready_metrics(world: World, totp_clock: FakeClock) -> None:
    c = TestClient(world.app)
    assert c.get("/api/v1/health").json()["status"] == "ok"
    ready = c.get("/api/v1/ready").json()
    assert ready == {"status": "ready", "database": True, "storage": True}
    staff = world.login("staff", totp_clock)
    upload_workbook(staff, world.engagement_id)
    staff.post(f"/api/v1/engagements/{world.engagement_id}/runs", json={"kind": "check"})
    from tests.conftest import drain

    drain(world)
    text = c.get("/metrics").text
    for metric in ("fsweb_http_requests_total", "fsweb_http_request_seconds_bucket", "fsweb_job_queue_depth", "fsweb_run_duration_seconds_bucket"):
        assert metric in text, metric
    assert 'fsweb_run_duration_seconds_count{kind="check"}' in text
    assert 'route="/api/v1/engagements/{engagement_id}/runs"' in text


def test_openapi_has_versioned_paths(world: World) -> None:
    spec = TestClient(world.app).get("/api/v1/openapi.json").json()
    assert all(p.startswith("/api/v1/") for p in spec["paths"])
    assert "/api/v1/engagements/{engagement_id}/runs" in spec["paths"] and len(spec["paths"]) > 100


@pytest.mark.parametrize("env", ["prod", "offline"])
def test_production_like_settings_fail_closed(env: str) -> None:
    with pytest.raises(RuntimeError, match="FSWEB_SECRET_KEY"):
        Settings.from_env({"FSWEB_ENV": env})
    ok_env = {"FSWEB_ENV": env, "FSWEB_SECRET_KEY": "x" * 40, "FSWEB_KMS_MASTER_KEY": "a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2U="}
    if env == "prod":
        with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
            Settings.from_env(ok_env)
        ok_env |= {"FSWEB_COOKIE_SECURE": "true", "FSWEB_DATABASE_URL": "postgresql+psycopg://u:p@db/fsweb"}
    s = Settings.from_env(ok_env)
    assert s.production_like


def test_offline_mode_hides_docs_and_hands_out_invite_links(tmp_path: Path, totp_clock: FakeClock) -> None:
    import base64

    from tests.conftest import PASSWORD, Api, build_world

    key = base64.b64encode(os.urandom(32)).decode()
    w = build_world(tmp_path, totp_clock, env="offline", kms_master_key=key)
    c = TestClient(w.app, base_url="http://127.0.0.1:8700")  # the desktop edition answers on loopback only
    assert c.get("/api/v1/docs").status_code == 404
    u = w.users["firm_admin"]
    r = c.post("/api/v1/auth/login", json={"email": u["email"], "password": PASSWORD})
    r = c.post("/api/v1/auth/mfa/verify", json={"code": totp_clock.code(u["secret"])}, headers={"X-CSRF-Token": r.json()["csrf_token"]})
    admin = Api(c, r.json()["csrf_token"])
    r = admin.post("/api/v1/admin/users/invite", json={"email": "offline.user@test.fs", "name": "Offline User"})
    # no e-mail on the desktop edition: the administrator hands the link over on this PC
    assert r.status_code == 201 and r.json()["invite_link"].startswith("http://testserver/invite?token=")


def test_run_forever_in_a_thread(world: World, totp_clock: FakeClock) -> None:
    from fsweb.worker import component_for, run_forever

    staff = world.login("staff", totp_clock)
    upload_workbook(staff, world.engagement_id)
    run = staff.post(f"/api/v1/engagements/{world.engagement_id}/runs", json={"kind": "check"}).json()
    stop = threading.Event()
    t = threading.Thread(target=run_forever, args=(stop, ["default"]), kwargs={"state": world.state, "poll": 0.05}, daemon=True)
    t.start()
    deadline = time.time() + 20
    while time.time() < deadline and staff.get(f"/api/v1/runs/{run['id']}").json()["status"] != "succeeded":
        time.sleep(0.1)
    stop.set()
    t.join(5)
    assert not t.is_alive()
    assert staff.get(f"/api/v1/runs/{run['id']}").json()["status"] == "succeeded"
    assert component_for(["ai-gateway"]) == "ai-gateway"
    with pytest.raises(ValueError):
        component_for(["default", "ai-gateway"])


# ------------------------------------------------------------------ admin CLI


def _cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    env = {
        "FSWEB_ENV": "dev",
        "FSWEB_DATABASE_URL": f"sqlite:///{tmp_path}/cli.db",
        "FSWEB_STORAGE": f"local:{tmp_path}/objects",
        "FSWEB_WORK_DIR": str(tmp_path / "work"),
        "FSWEB_AUDIT_HEAD_DIR": str(tmp_path / "heads"),
        "FSWEB_ENGINE": "fake",
        "FSWEB_SECRET_KEY": "cli-test-secret-0123456789abcdef-0123456789",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


def test_cli_bootstrap_chain_head_seed_and_openapi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _cli_env(tmp_path, monkeypatch)
    assert manage.main(["migrate"]) == 0
    capsys.readouterr()
    assert manage.main(["bootstrap", "--tenant", "CLI Firm", "--admin-email", "boss@cli.test"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["one_time_password"] and out["totp_secret"]
    assert manage.main(["bootstrap", "--tenant", "CLI Firm", "--admin-email", "boss@cli.test"]) == 1
    assert manage.main(["verify-audit-chain"]) == 0
    assert manage.main(["audit-head", "--day", "2026-10-21"]) == 0
    assert manage.main(["audit-head", "--day", "2026-10-21"]) == 1  # write-once
    capsys.readouterr()
    assert manage.main(["seed-demo"]) == 0
    demo = json.loads(capsys.readouterr().out)
    assert {u["role"] for u in demo["users"]} == {"system_admin", "firm_admin", "partner", "manager", "staff", "reviewer",
                                                  "client_uploader", "client_viewer"}
    assert manage.main(["seed-demo"]) == 1  # already seeded
    assert manage.main(["create-breakglass", "--tenant", "CLI Firm", "--email", "glass@cli.test"]) == 0
    assert manage.main(["restore-drill", "--seed", "3"]) == 0
    assert manage.main(["access-review-report"]) == 0
    capsys.readouterr()
    assert manage.main(["start-access-review", "--period", "2026Q4"]) == 0  # the quarterly campaign (C14)
    opened = capsys.readouterr().out
    assert "2026Q4" in opened and not opened.split(": ")[1].startswith("0 ")
    assert manage.main(["start-access-review", "--period", "2026Q4"]) == 0
    assert ": 0 assignment(s)" in capsys.readouterr().out  # a quarter's campaign opens once
    assert manage.main(["incident-check"]) == 0
    assert manage.main(["auto-revoke"]) == 0
    assert manage.main(["rotate-kek"]) == 0
    assert manage.main(["send-mail"]) == 0
    assert manage.main(["evidence-pack", "--quarter", "2026Q4", "--out", str(tmp_path / "evidence")]) == 0
    assert list((tmp_path / "evidence").glob("*.zip"))
    out_file = tmp_path / "openapi.json"
    assert manage.main(["openapi", "--out", str(out_file)]) == 0
    assert json.loads(out_file.read_text())["info"]["title"] == "FS-Platform API"


def test_seed_demo_users_can_sign_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, totp_clock: FakeClock) -> None:
    import pyotp

    from fsweb.main import create_app
    from tests.conftest import login

    _cli_env(tmp_path, monkeypatch)
    manage.main(["migrate"])
    st = manage._state()
    demo: dict[str, Any] = manage.seed_demo(st)
    app = create_app(st.settings, state=st)
    for u in demo["users"]:
        api = login(app, u["email"], u["password"], u["totp_secret"], totp_clock)
        assert api.get("/api/v1/auth/me").status_code == 200
    assert pyotp.TOTP(demo["users"][0]["totp_secret"]).now()
    assert demo["workbook"]["version_id"]
