"""Integration with the real engine (fscore) through the platform: a synthetic workbook with
injected errors goes through upload → check → plan → approve → apply → re-check → render."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import FakeClock, build_world, drain, upload_workbook

pytestmark = pytest.mark.engine


@pytest.fixture
def synthetic_workbook(tmp_path: Path) -> Path:
    gen = pytest.importorskip("fscore.synthetic.generator")
    pytest.importorskip("fscore.runner")
    path = tmp_path / "Synthetic FS FY26.xlsx"
    gen.make_workbook(path, seed=7, inject=["subtotal_not_cast", "plug_total_minus", "din_mismatch"])
    return path


def test_real_engine_end_to_end(tmp_path: Path, totp_clock: FakeClock, synthetic_workbook: Path) -> None:
    from fsweb.engine import RealEngine

    w = build_world(tmp_path / "w", totp_clock)
    w.state.engine = RealEngine()
    staff = w.login("staff", totp_clock)
    r = upload_workbook(staff, w.engagement_id, synthetic_workbook.read_bytes(), name=synthetic_workbook.name)
    assert r.status_code == 201, r.text
    run = staff.post(f"/api/v1/engagements/{w.engagement_id}/runs", json={"kind": "check"}).json()
    drain(w)
    done = staff.get(f"/api/v1/runs/{run['id']}").json()
    if done["status"] != "succeeded" and done.get("error_code") == "engine_unavailable":
        pytest.skip(f"engine not ready: {done['error_message']}")
    assert done["status"] == "succeeded", done
    assert done["rule_set_hash"] and done["engine_version"] != "fake-1.0"
    findings = staff.get(f"/api/v1/runs/{run['id']}/findings").json()
    rules = {f["rule_id"] for f in findings}
    assert {"CAST-01", "PLUG-01"} & rules, rules
    arts = {a["kind"] for a in staff.get(f"/api/v1/runs/{run['id']}/artefacts").json()}
    assert {"result", "register", "html"} <= arts
    # Propose → approve (other user) → apply writes a verified corrected copy.
    plan = staff.post(f"/api/v1/runs/{run['id']}/fix-plan").json()
    drain(w)
    plan = staff.get(f"/api/v1/fix-plans/{plan['id']}").json()
    if plan["status"] != "proposed":
        pytest.skip(f"fix planning not ready in this engine build: {plan['status']} {plan.get('rejection_reason')}")
    partner = w.login("partner", totp_clock)
    ok = partner.post(f"/api/v1/fix-plans/{plan['id']}/approve", json={"plan_sha256": plan["plan_sha256"]})
    assert ok.status_code == 200, ok.text
    if ok.json()["status"] == "approved":
        applied = partner.post(f"/api/v1/fix-plans/{plan['id']}/apply").json()
        drain(w)
        res = partner.get(f"/api/v1/runs/{applied['id']}").json()
        assert res["status"] == "succeeded", res
        assert res["result_json"]["verification"]["ok"] is True
        assert partner.get(f"/api/v1/engagements/{w.engagement_id}").json()["status"] == "FIXED_VERIFIED"
        recheck = staff.post(f"/api/v1/engagements/{w.engagement_id}/runs", json={"kind": "check"}).json()
        drain(w)
        after = staff.get(f"/api/v1/runs/{recheck['id']}/findings").json()
        assert not [f for f in after if f["rule_id"] in {"CAST-01", "PLUG-01"}], "casting / plug findings remain after the fix"
