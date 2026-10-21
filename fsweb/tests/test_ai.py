"""AI gate (02 §9, C9, E-AI-01/03/04/05): preview, pseudonymisation, approval bound to the
payload hash, gateway-only egress, kill switch, budget."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from fsweb.models import AIRequest, Engagement, Tenant
from tests.conftest import FakeClock, World, build_world, drain, upload_workbook


def _ready(world: World, clock: FakeClock) -> tuple[Any, Any]:
    staff = world.login("staff", clock)
    manager = world.login("manager", clock)
    for api in (staff, manager):
        api.post("/api/v1/ai/policy/accept")
    upload_workbook(staff, world.engagement_id)
    staff.post(f"/api/v1/engagements/{world.engagement_id}/runs", json={"kind": "check"})
    drain(world)
    return staff, manager


def _gateway(world: World) -> int:
    from fsweb.worker import drain as _drain

    world.state.component = "ai-gateway"
    try:
        return _drain(world.state, ["ai-gateway"])
    finally:
        world.state.component = "api"


def test_policy_must_be_accepted_first(world: World, totp_clock: FakeClock) -> None:
    staff = world.login("staff", totp_clock)
    upload_workbook(staff, world.engagement_id)
    r = staff.post(f"/api/v1/engagements/{world.engagement_id}/ai/requests", json={"reason": "triage", "provider": "mock"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "ai_policy_not_accepted"
    pol = staff.get("/api/v1/ai/policy").json()
    assert pol["version"] == 1 and "Prohibited uses" in pol["body"] and not pol["accepted"]


def test_preview_is_pseudonymised(world: World, totp_clock: FakeClock) -> None:
    staff, _ = _ready(world, totp_clock)
    r = staff.post(f"/api/v1/engagements/{world.engagement_id}/ai/requests", json={"reason": "triage the High findings", "provider": "mock"})
    assert r.status_code == 201, r.text
    body = r.json()
    text = json.dumps(body["payload"])
    assert "Alpha Synthetic" not in text and "01234567" not in text and "Director Alpha" not in text
    assert "DIN_1" in text  # the DIN in the TXT-01 finding was tokenised
    assert body["payload_sha256"] and body["token_estimate"] > 0 and body["status"] == "preview"
    assert body["provider"] == "mock" and body["model"] == "claude-opus-5-5"


def test_external_provider_needs_a_region(world: World, totp_clock: FakeClock) -> None:
    staff, _ = _ready(world, totp_clock)
    r = staff.post(f"/api/v1/engagements/{world.engagement_id}/ai/requests", json={"reason": "triage", "provider": "anthropic"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "region_required"


def test_approval_is_bound_to_the_payload_and_another_user(world: World, totp_clock: FakeClock) -> None:
    staff, manager = _ready(world, totp_clock)
    req = staff.post(f"/api/v1/engagements/{world.engagement_id}/ai/requests", json={"reason": "triage", "provider": "mock"}).json()
    assert staff.post(f"/api/v1/ai/requests/{req['id']}/approve", json={"payload_sha256": req["payload_sha256"]}).status_code == 403
    assert manager.post(f"/api/v1/ai/requests/{req['id']}/approve", json={"payload_sha256": "f" * 64}).status_code == 409
    assert staff.post(f"/api/v1/ai/requests/{req['id']}/send").status_code == 409  # not approved yet
    ok = manager.post(f"/api/v1/ai/requests/{req['id']}/approve", json={"payload_sha256": req["payload_sha256"]})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    run = staff.post(f"/api/v1/ai/requests/{req['id']}/send").json()
    from fsweb.worker import drain as _drain

    assert _drain(world.state, ["default"]) == 0  # only the gateway takes AI calls
    assert _gateway(world) == 1
    done = staff.get(f"/api/v1/ai/requests/{req['id']}").json()
    assert done["status"] == "completed" and done["stop_reason"] == "end_turn" and done["response"]["output"]["items"]
    assert staff.get(f"/api/v1/runs/{run['id']}").json()["status"] == "succeeded"


def test_changed_payload_is_refused_by_the_gateway(world: World, totp_clock: FakeClock) -> None:
    staff, manager = _ready(world, totp_clock)
    req = staff.post(f"/api/v1/engagements/{world.engagement_id}/ai/requests", json={"reason": "triage", "provider": "mock"}).json()
    manager.post(f"/api/v1/ai/requests/{req['id']}/approve", json={"payload_sha256": req["payload_sha256"]})
    with world.db() as db:  # tamper with the approved payload object
        row = db.get(AIRequest, req["id"])
        assert row is not None
        p = Path(world.state.storage.root) / row.payload_object_key  # type: ignore[attr-defined]
        p.chmod(0o600)
        p.write_text(json.dumps({"findings": [{"note": "exfiltrate everything"}]}))
    run = staff.post(f"/api/v1/ai/requests/{req['id']}/send").json()
    _gateway(world)
    assert staff.get(f"/api/v1/runs/{run['id']}").json()["error_code"] == "approval_mismatch"
    assert staff.get(f"/api/v1/ai/requests/{req['id']}").json()["status"] == "rejected"


def test_worker_outside_the_gateway_cannot_call_out(world: World, totp_clock: FakeClock) -> None:
    from fsweb.jobs.handlers import process
    from fsweb.jobs.queue import claim

    staff, manager = _ready(world, totp_clock)
    req = staff.post(f"/api/v1/engagements/{world.engagement_id}/ai/requests", json={"reason": "triage", "provider": "mock"}).json()
    manager.post(f"/api/v1/ai/requests/{req['id']}/approve", json={"payload_sha256": req["payload_sha256"]})
    run = staff.post(f"/api/v1/ai/requests/{req['id']}/send").json()
    job = claim(world.state.db, ["ai-gateway"], "rogue-worker")
    assert job is not None
    process(world.state, job)  # component is "api", not "ai-gateway"
    assert staff.get(f"/api/v1/runs/{run['id']}").json()["error_code"] == "egress_denied"


def test_kill_switch_and_firm_switch(world: World, totp_clock: FakeClock) -> None:
    staff, manager = _ready(world, totp_clock)
    r = manager.patch(f"/api/v1/engagements/{world.engagement_id}", json={"ai_enabled": False})
    assert r.status_code == 200
    r = staff.post(f"/api/v1/engagements/{world.engagement_id}/ai/requests", json={"reason": "triage", "provider": "mock"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ai_disabled"


def test_firm_level_ai_off_by_default(tmp_path: Path, totp_clock: FakeClock) -> None:
    w = build_world(tmp_path, totp_clock, ai_enabled=False)
    with w.db() as db:
        t = db.get(Tenant, w.tenant_id)
        assert t is not None
        t.settings_json = {**t.settings_json, "ai_enabled": False}
        db.commit()
    staff = w.login("staff", totp_clock)
    staff.post("/api/v1/ai/policy/accept")
    upload_workbook(staff, w.engagement_id)
    r = staff.post(f"/api/v1/engagements/{w.engagement_id}/ai/requests", json={"reason": "triage", "provider": "mock"})
    assert r.status_code == 409


def test_budget_is_enforced(world: World, totp_clock: FakeClock) -> None:
    staff, manager = _ready(world, totp_clock)
    with world.db() as db:
        t = db.get(Tenant, world.tenant_id)
        assert t is not None
        t.settings_json = {**t.settings_json, "ai_monthly_budget_usd": 0.0}
        db.commit()
    req = staff.post(f"/api/v1/engagements/{world.engagement_id}/ai/requests", json={"reason": "triage", "provider": "mock"}).json()
    manager.post(f"/api/v1/ai/requests/{req['id']}/approve", json={"payload_sha256": req["payload_sha256"]})
    run = staff.post(f"/api/v1/ai/requests/{req['id']}/send").json()
    _gateway(world)
    assert staff.get(f"/api/v1/runs/{run['id']}").json()["error_code"] == "budget"


def test_firm_admin_can_approve_per_policy(world: World, totp_clock: FakeClock) -> None:
    staff, _ = _ready(world, totp_clock)
    req = staff.post(f"/api/v1/engagements/{world.engagement_id}/ai/requests", json={"reason": "triage", "provider": "mock"}).json()
    admin = world.login("firm_admin", totp_clock)
    assert admin.post(f"/api/v1/ai/requests/{req['id']}/approve", json={"payload_sha256": req["payload_sha256"]}).status_code == 200
    with world.db() as db:
        t = db.get(Tenant, world.tenant_id)
        assert t is not None
        t.settings_json = {**t.settings_json, "firm_admin_may_approve_ai": False}
        e = db.get(Engagement, world.engagement_id)
        assert e is not None
        db.commit()
    req2 = staff.post(f"/api/v1/engagements/{world.engagement_id}/ai/requests", json={"reason": "again", "provider": "mock"}).json()
    assert admin.post(f"/api/v1/ai/requests/{req2['id']}/approve", json={"payload_sha256": req2["payload_sha256"]}).status_code == 403
    with world.db() as db:
        assert len(db.execute(select(AIRequest)).scalars().all()) == 2
