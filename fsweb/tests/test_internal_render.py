"""Windows render-worker protocol (E-ARCH-03): token auth, heartbeat routing, claim / input /
complete / fail, PDF verification after an Excel render."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.conftest import FakeClock, World, drain, pdf_bytes, upload_workbook

TOKEN = {"X-Worker-Token": "worker-token-for-tests"}


def test_token_required(world: World) -> None:
    c = TestClient(world.app)
    assert c.post("/api/v1/internal/render/heartbeat", json={"worker_id": "w1"}).status_code == 401
    assert c.post("/api/v1/internal/render/heartbeat", json={"worker_id": "w1"}, headers={"X-Worker-Token": "nope"}).status_code == 401
    assert c.post("/api/v1/internal/render/heartbeat", json={"worker_id": "w1", "excel_build": "16.0.17928"}, headers=TOKEN).status_code == 200


def test_render_goes_to_windows_worker_when_fresh(world: World, totp_clock: FakeClock) -> None:
    c = TestClient(world.app)
    staff = world.login("staff", totp_clock)
    upload_workbook(staff, world.engagement_id)
    # Without a fresh heartbeat, renders run on the Linux worker.
    run = staff.post(f"/api/v1/engagements/{world.engagement_id}/render", json={"mode": "draft"}).json()
    assert drain(world) == 1
    assert staff.get(f"/api/v1/runs/{run['id']}").json()["status"] == "succeeded"
    # With a fresh heartbeat they go to the render-win queue.
    c.post("/api/v1/internal/render/heartbeat", json={"worker_id": "win-1", "excel_build": "16.0.17928"}, headers=TOKEN)
    run = staff.post(f"/api/v1/engagements/{world.engagement_id}/render", json={"mode": "draft"}).json()
    assert drain(world) == 0
    claim = c.post("/api/v1/internal/render/claim", json={"worker_id": "win-1", "excel_build": "16.0.17928"}, headers=TOKEN)
    assert claim.status_code == 200, claim.text
    job = claim.json()
    assert job["run_id"] == run["id"] and job["mode"] == "draft" and job["options"]["watermark"] is True
    assert c.post("/api/v1/internal/render/claim", json={"worker_id": "win-1"}, headers=TOKEN).status_code == 204
    wb = c.get(f"/api/v1/internal/render/{job['job_id']}/input", headers=TOKEN)
    assert wb.status_code == 200 and wb.content[:2] == b"PK" and wb.headers["x-content-sha256"]
    bad = c.post(f"/api/v1/internal/render/{job['job_id']}/complete", files={"pdf": ("x.pdf", b"not a pdf", "application/pdf")}, headers=TOKEN)
    assert bad.status_code == 415
    meta = json.dumps({"engine": "excel", "engine_version": "16.0", "excel_build": "16.0.17928"})
    done = c.post(f"/api/v1/internal/render/{job['job_id']}/complete", files={"pdf": ("s.pdf", pdf_bytes(), "application/pdf")},
                  data={"meta": meta}, headers=TOKEN)
    assert done.status_code == 200, done.text
    result = done.json()["result_json"]
    assert done.json()["status"] == "succeeded" and result["engine"] == "excel" and result["misses"] == 0 and result["excel_build"] == "16.0.17928"
    metrics = c.get("/metrics").text
    assert 'fsweb_render_worker_last_heartbeat_timestamp_seconds{worker_id="win-1"}' in metrics
    assert 'fsweb_run_duration_seconds_count{kind="render"}' in metrics


def test_failed_windows_render_is_retried(world: World, totp_clock: FakeClock) -> None:
    c = TestClient(world.app)
    staff = world.login("staff", totp_clock)
    upload_workbook(staff, world.engagement_id)
    c.post("/api/v1/internal/render/heartbeat", json={"worker_id": "win-2"}, headers=TOKEN)
    run = staff.post(f"/api/v1/engagements/{world.engagement_id}/render", json={"mode": "draft"}).json()
    job = c.post("/api/v1/internal/render/claim", json={"worker_id": "win-2"}, headers=TOKEN).json()
    r = c.post(f"/api/v1/internal/render/{job['job_id']}/fail", json={"error": "Excel dialog timeout"}, headers=TOKEN)
    assert r.status_code == 200 and "queued" in r.json()["message"]
    assert staff.get(f"/api/v1/runs/{run['id']}").json()["status"] == "queued"
