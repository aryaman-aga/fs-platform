"""Maker-checker on fix plans (C2), balancing limited to rounding (C3), verification (C5),
typed-text narrative only (C6) with Staff edits needing approval."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from fsweb.models import FixPlanRow, NarrativeEdit
from tests.conftest import FakeClock, World, drain, set_status, upload_workbook


def _proposed(world: World, clock: FakeClock) -> tuple[Any, dict[str, Any]]:
    staff = world.login("staff", clock)
    upload_workbook(staff, world.engagement_id)
    staff.post(f"/api/v1/engagements/{world.engagement_id}/runs", json={"kind": "check"})
    drain(world)
    check = staff.get(f"/api/v1/engagements/{world.engagement_id}/runs?kind=check").json()[0]
    plan = staff.post(f"/api/v1/runs/{check['id']}/fix-plan").json()
    drain(world)
    return staff, staff.get(f"/api/v1/fix-plans/{plan['id']}").json()


def test_partner_approval_includes_balancing(world: World, totp_clock: FakeClock) -> None:
    _, plan = _proposed(world, totp_clock)
    partner = world.login("partner", totp_clock)
    r = partner.post(f"/api/v1/fix-plans/{plan['id']}/approve", json={"plan_sha256": plan["plan_sha256"], "approved_edit_ids": ["E-0003"]})
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["balancing_approved_by"]
    edits = {e["eid"]: e for e in r.json()["edits"]}
    assert edits["E-0003"]["approved"] and edits["E-0001"]["approved"]


def test_unknown_approval_items_are_refused(world: World, totp_clock: FakeClock) -> None:
    _, plan = _proposed(world, totp_clock)
    partner = world.login("partner", totp_clock)
    r = partner.post(f"/api/v1/fix-plans/{plan['id']}/approve", json={"plan_sha256": plan["plan_sha256"], "approved_edit_ids": ["E-0001"]})
    assert r.status_code == 400  # E-0001 is an automatic item, not an approval item


def test_balancing_above_threshold_is_refused(world: World, totp_clock: FakeClock) -> None:
    _, plan = _proposed(world, totp_clock)
    with world.db() as db:
        fp = db.get(FixPlanRow, plan["id"])
        assert fp is not None
        body = dict(fp.plan_json)
        body["balancing"] = [dict(body["balancing"][0], adjustment="0.35", gap="0.35")]
        fp.plan_json = body
        db.commit()
    partner = world.login("partner", totp_clock)
    r = partner.post(f"/api/v1/fix-plans/{plan['id']}/approve", json={"plan_sha256": plan["plan_sha256"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "balancing_too_large"


def test_plan_changed_after_approval_is_not_applied(world: World, totp_clock: FakeClock) -> None:
    _, plan = _proposed(world, totp_clock)
    partner = world.login("partner", totp_clock)
    partner.post(f"/api/v1/fix-plans/{plan['id']}/approve", json={"plan_sha256": plan["plan_sha256"]})
    with world.db() as db:  # someone edits the stored plan after approval
        fp = db.get(FixPlanRow, plan["id"])
        assert fp is not None
        body = dict(fp.plan_json)
        body["edits"] = [dict(body["edits"][0], new_formula="SUM(D15:D19)+0.01"), *body["edits"][1:]]
        fp.plan_json = body
        db.commit()
    run = partner.post(f"/api/v1/fix-plans/{plan['id']}/apply").json()
    drain(world)
    done = partner.get(f"/api/v1/runs/{run['id']}").json()
    assert done["status"] == "failed" and done["error_code"] == "plan_not_approved"
    assert partner.get(f"/api/v1/engagements/{world.engagement_id}").json()["status"] == "FIX_APPROVED"


def test_failed_verification_keeps_the_engagement_unverified(world: World, totp_clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    from fscore.models import ApplyResult, VerificationReport, VerificationStep

    _, plan = _proposed(world, totp_clock)
    partner = world.login("partner", totp_clock)
    partner.post(f"/api/v1/fix-plans/{plan['id']}/approve", json={"plan_sha256": plan["plan_sha256"]})
    monkeypatch.setattr(world.state.engine, "apply_fixes", lambda *a, **k: ApplyResult(
        ok=False, message="unplanned cell changed", verification=VerificationReport(ok=False, steps=[VerificationStep(name="cell diff", ok=False)])))
    run = partner.post(f"/api/v1/fix-plans/{plan['id']}/apply").json()
    drain(world)
    done = partner.get(f"/api/v1/runs/{run['id']}").json()
    assert done["status"] == "failed" and done["error_code"] == "verification_failed"
    assert partner.get(f"/api/v1/fix-plans/{plan['id']}").json()["status"] == "failed"
    assert partner.get(f"/api/v1/engagements/{world.engagement_id}").json()["status"] == "FIX_APPROVED"


def test_reject_returns_to_checked(world: World, totp_clock: FakeClock) -> None:
    _, plan = _proposed(world, totp_clock)
    manager = world.login("manager", totp_clock)
    r = manager.post(f"/api/v1/fix-plans/{plan['id']}/reject", json={"reason": "wrong balancing line"})
    assert r.json()["status"] == "rejected"
    assert manager.get(f"/api/v1/engagements/{world.engagement_id}").json()["status"] == "CHECKED"


def test_new_upload_makes_check_stale(world: World, totp_clock: FakeClock) -> None:
    staff, _ = _proposed(world, totp_clock)
    check = staff.get(f"/api/v1/engagements/{world.engagement_id}/runs?kind=check").json()[0]
    upload_workbook(staff, world.engagement_id)
    r = staff.post(f"/api/v1/runs/{check['id']}/fix-plan")
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_check"


# ------------------------------------------------------------------ narrative (C6)


@pytest.mark.parametrize("text", ["=SUM(B2:B9)", "+B12*2", "@INDIRECT(\"A1\")", "-HYPERLINK(\"http://x\")", "Loss of TEXT(B4,\"0.00\") lakhs"])
def test_formula_like_narrative_is_rejected(world: World, totp_clock: FakeClock, text: str) -> None:
    manager = world.login("manager", totp_clock)
    upload_workbook(manager, world.engagement_id)
    r = manager.post(f"/api/v1/engagements/{world.engagement_id}/narrative-edits", json={"sheet": "Notes", "cell": "B12", "new_text": text})
    assert r.status_code == 422 and r.json()["error"]["code"] == "formula_not_allowed"


def test_staff_narrative_needs_manager_approval(world: World, totp_clock: FakeClock) -> None:
    staff = world.login("staff", totp_clock)
    upload_workbook(staff, world.engagement_id)
    text = "The Company incurred a loss of ₹ 8.23 lakhs for the year ended March 31, 2026."
    ne = staff.post(f"/api/v1/engagements/{world.engagement_id}/narrative-edits", json={"sheet": "Notes", "cell": "B12", "new_text": text, "reason": "GC-01"}).json()
    assert ne["status"] == "pending_approval"
    assert staff.post(f"/api/v1/narrative-edits/{ne['id']}/approve").status_code == 403
    reviewer = world.login("reviewer", totp_clock)
    assert reviewer.post(f"/api/v1/narrative-edits/{ne['id']}/approve").status_code == 403
    manager = world.login("manager", totp_clock)
    assert manager.post(f"/api/v1/narrative-edits/{ne['id']}/approve").json()["status"] == "approved"
    set_status(world, "REVIEW")
    applied = staff.post(f"/api/v1/engagements/{world.engagement_id}/narrative-edits/apply").json()
    drain(world)  # applies the edit, then the automatic re-check
    run = staff.get(f"/api/v1/runs/{applied['run_id']}").json()
    assert run["status"] == "succeeded" and run["output_version_id"]
    assert staff.get(f"/api/v1/engagements/{world.engagement_id}").json()["status"] == "CHECKED"  # data changed after verification
    with world.db() as db:
        assert db.execute(select(NarrativeEdit)).scalars().one().status == "applied"


def test_manager_narrative_is_approved_directly(world: World, totp_clock: FakeClock) -> None:
    manager = world.login("manager", totp_clock)
    upload_workbook(manager, world.engagement_id)
    ne = manager.post(f"/api/v1/engagements/{world.engagement_id}/narrative-edits", json={"sheet": "Notes", "cell": "B12", "new_text": "Typed text only."}).json()
    assert ne["status"] == "approved"
