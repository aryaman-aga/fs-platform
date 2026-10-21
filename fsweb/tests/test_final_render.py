"""A FINAL PDF stands on the latest check run of its workbook version: the render job hands that run's findings to
the engine, whose own final gate (control C7) refuses without them, leaving out the findings people closed in the
platform, so the engine agrees with the gate the request passed."""

from __future__ import annotations

from pathlib import Path

from fscore.config import EngagementConfig
from fsweb.engine import FakeEngine
from tests.conftest import FakeClock, World, drain, upload_workbook


def test_the_test_engine_refuses_a_final_without_its_check_run(tmp_path: Path) -> None:
    wb = tmp_path / "wb.xlsx"
    wb.write_bytes(b"PK\x03\x04 synthetic")
    res = FakeEngine().render_pdf(wb, tmp_path / "out.pdf", mode="final", config=EngagementConfig(), run_id="r1")
    assert not res.ok and "check run" in res.message


def test_final_render_uses_the_check_run_without_closed_findings(world: World, totp_clock: FakeClock) -> None:
    eid = world.engagement_id
    staff = world.login("staff", totp_clock)
    manager = world.login("manager", totp_clock)
    upload_workbook(staff, eid)
    staff.post(f"/api/v1/engagements/{eid}/runs", json={"kind": "check"})
    drain(world)
    check = staff.get(f"/api/v1/engagements/{eid}/runs?kind=check").json()[0]
    highs = [f for f in staff.get(f"/api/v1/runs/{check['id']}/findings").json() if f["severity"] == "High"]
    assert highs
    r = manager.post(f"/api/v1/engagements/{eid}/render", json={"mode": "final"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "gate_failed"
    for f in highs:
        r = manager.patch(f"/api/v1/findings/{f['id']}", json={"status": "Not an issue", "status_reason": "agreed with the partner"})
        assert r.status_code == 200, r.text
    fr = manager.post(f"/api/v1/engagements/{eid}/render", json={"mode": "final"})
    assert fr.status_code in (201, 202), fr.text
    drain(world)
    run = manager.get(f"/api/v1/runs/{fr.json()['id']}").json()
    assert run["status"] == "succeeded", run
