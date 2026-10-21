"""Desktop edition (FSWEB_ENV=offline) hardening and the served UI: the DNS-rebinding guard (Host / Origin), no
render-worker API and no /metrics, and the SPA's caching and 404 rules."""

from __future__ import annotations

import base64
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import PASSWORD, FakeClock, World, build_world

BASE = "http://127.0.0.1:8700"


@pytest.fixture
def desktop(tmp_path: Path, totp_clock: FakeClock) -> Iterator[World]:
    w = build_world(tmp_path, totp_clock, env="offline", kms_master_key=base64.b64encode(os.urandom(32)).decode(), public_base_url=BASE)
    yield w
    w.state.db.dispose()


def _ui(tmp: Path) -> Path:
    ui = tmp / "dist"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<!doctype html><title>FS-Platform</title><div id=root></div>", encoding="utf-8")
    (ui / "assets" / "index-AbC123.js").write_text("console.log('fs')", encoding="utf-8")
    (ui / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    return ui


# ------------------------------------------------------------------ DNS rebinding


@pytest.mark.parametrize("base", [BASE, "http://localhost:8700", "http://[::1]:8700"])
def test_desktop_answers_on_loopback_names(desktop: World, base: str) -> None:
    r = TestClient(desktop.app, base_url=base).get("/api/v1/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


@pytest.mark.parametrize("host", ["evil.example:8700", "127.0.0.1:8701", "localhost", "127.0.0.1.evil.example:8700", "user@127.0.0.1:8700",
                                  "192.168.1.20:8700", "", "127.0.0.1:notaport"])
def test_desktop_refuses_other_host_headers(desktop: World, host: str) -> None:
    r = TestClient(desktop.app, base_url=BASE).get("/api/v1/auth/options", headers={"Host": host})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_host"
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-correlation-id"]


def test_desktop_checks_the_origin_of_state_changing_requests(desktop: World) -> None:
    c = TestClient(desktop.app, base_url=BASE)
    u = desktop.users["staff"]
    for origin in ("http://evil.example:8700", "http://127.0.0.1:9999", "https://127.0.0.1:8700", "null"):
        r = c.post("/api/v1/auth/login", json={"email": u["email"], "password": PASSWORD}, headers={"Origin": origin})
        assert r.status_code == 403 and r.json()["error"]["code"] == "origin_rejected", origin
    for origin in (BASE, "http://localhost:8700", "http://[::1]:8700"):
        r = c.post("/api/v1/auth/login", json={"email": u["email"], "password": PASSWORD}, headers={"Origin": origin})
        assert r.status_code == 200, origin
    # reads are not state-changing; non-browser clients send no Origin
    assert c.get("/api/v1/auth/options", headers={"Origin": "http://evil.example:8700"}).status_code == 200
    assert c.post("/api/v1/auth/login", json={"email": u["email"], "password": PASSWORD}).status_code == 200


def test_the_server_edition_has_no_host_guard(world: World) -> None:
    assert TestClient(world.app).get("/api/v1/health", headers={"Host": "fs.corvus.example"}).status_code == 200


# ------------------------------------------------------------------ closed endpoints


def test_desktop_has_no_render_worker_api_and_no_metrics(desktop: World, totp_clock: FakeClock) -> None:
    c = TestClient(desktop.app, base_url=BASE)
    token = {"X-Worker-Token": "worker-token-for-tests"}
    assert c.post("/api/v1/internal/render/heartbeat", json={"worker_id": "w1"}, headers=token).status_code == 404
    assert c.post("/api/v1/internal/render/claim", json={"worker_id": "w1"}, headers=token).status_code in (404, 405)
    assert c.get("/metrics").status_code == 404
    paths = c.get("/api/v1/openapi.json").json()["paths"]
    assert not [p for p in paths if p.startswith("/api/v1/internal")]


def test_server_edition_keeps_render_workers_and_metrics(world: World) -> None:
    c = TestClient(world.app)
    assert c.post("/api/v1/internal/render/heartbeat", json={"worker_id": "w1"}, headers={"X-Worker-Token": "worker-token-for-tests"}).status_code == 200
    assert "fsweb_http_requests_total" in c.get("/metrics").text


# ------------------------------------------------------------------ the served UI


def test_spa_caching_and_404s(tmp_path: Path, totp_clock: FakeClock) -> None:
    w = build_world(tmp_path, totp_clock, serve_ui=str(_ui(tmp_path)))
    c = TestClient(w.app)
    for path in ("/", "/clients/abc", "/setup", "/admin/users"):  # client-side routes get the app shell, revalidated
        r = c.get(path)
        assert r.status_code == 200 and "FS-Platform" in r.text, path
        assert r.headers["cache-control"] == "no-cache" and r.headers["content-type"].startswith("text/html"), path
    r = c.get("/index.html")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    r = c.get("/assets/index-AbC123.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert r.headers["content-type"].startswith("text/javascript")
    r = c.get("/assets/index-Old999.js")  # a stale page asking for an old bundle
    assert r.status_code == 404 and "<title>" not in r.text
    r = c.get("/api/v1/no-such-endpoint")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert c.get("/api").status_code == 404
    r = c.get("/favicon.svg")
    assert r.status_code == 200 and "immutable" not in r.headers.get("cache-control", "")
    (tmp_path / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    assert "TOP-SECRET" not in c.get("/%2e%2e/secret.txt").text  # nothing outside the UI folder is served
    assert c.get("/api/v1/health").json()["status"] == "ok"  # real endpoints still win


def test_desktop_spa_with_the_guard(tmp_path: Path, totp_clock: FakeClock) -> None:
    w = build_world(tmp_path, totp_clock, env="offline", kms_master_key=base64.b64encode(os.urandom(32)).decode(), serve_ui=str(_ui(tmp_path)))
    c = TestClient(w.app, base_url=BASE)
    assert c.get("/setup").headers["cache-control"] == "no-cache"
    assert c.get("/metrics").status_code == 404  # not the app shell
    r = c.get("/api/v1/internal/render/job-1/input", headers={"X-Worker-Token": "worker-token-for-tests"})
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
    assert TestClient(w.app, base_url="http://attacker.example:8700").get("/").status_code == 400
