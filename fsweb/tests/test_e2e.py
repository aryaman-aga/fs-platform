"""End-to-end engagement with the fake engine: upload → check → plan → approve (other user) →
apply → render → review notes → release → client confirmation → final → lock → audit chain."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from fsweb.models import AuditEvent, FileVersion
from tests.conftest import FakeClock, World, drain, pdf_bytes, upload_workbook


def run_e2e(world: World, clock: FakeClock) -> dict[str, Any]:
    eid = world.engagement_id
    staff = world.login("staff", clock)
    manager = world.login("manager", clock)
    partner = world.login("partner", clock)
    reviewer = world.login("reviewer", clock)
    uploader = world.login("client_uploader", clock)
    cfo = world.login("client_viewer", clock)

    # Client uploads the workbook; the engagement moves to DATA_RECEIVED.
    r = upload_workbook(uploader, eid)
    assert r.status_code == 201, r.text
    v1 = r.json()["version"]
    assert r.json()["engagement_status"] == "DATA_RECEIVED"

    # Staff runs checks (idempotent submission).
    body = {"kind": "check", "input_version_ids": [v1["id"]], "idempotency_key": "chk-1"}
    r1 = staff.post(f"/api/v1/engagements/{eid}/runs", json=body)
    r2 = staff.post(f"/api/v1/engagements/{eid}/runs", json=body)
    assert r1.status_code == 202 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    assert drain(world) == 1
    check = staff.get(f"/api/v1/runs/{r1.json()['id']}").json()
    assert check["status"] == "succeeded", check
    assert check["engine_version"] == "fake-1.0" and check["rule_set_hash"] and check["config_hash"]
    findings = staff.get(f"/api/v1/runs/{check['id']}/findings").json()
    assert {f["rule_id"] for f in findings} >= {"CAST-01", "TXT-01"}
    assert staff.get(f"/api/v1/engagements/{eid}").json()["status"] == "CHECKED"
    arts = staff.get(f"/api/v1/runs/{check['id']}/artefacts").json()
    assert {a["kind"] for a in arts} >= {"result", "register", "html"}

    # Staff proposes; staff cannot approve own plan; the manager approves; balancing needs the partner.
    plan = staff.post(f"/api/v1/runs/{check['id']}/fix-plan").json()
    assert plan["status"] == "planning"
    drain(world)
    plan = staff.get(f"/api/v1/fix-plans/{plan['id']}").json()
    assert plan["status"] == "proposed" and plan["has_balancing"] and plan["plan_sha256"]
    approval_items = [e["eid"] for e in plan["edits"] if e["fix_kind"] == "approval"]
    r = staff.post(f"/api/v1/fix-plans/{plan['id']}/approve", json={"plan_sha256": plan["plan_sha256"], "approved_edit_ids": approval_items})
    assert r.status_code == 403
    r = manager.post(f"/api/v1/fix-plans/{plan['id']}/approve", json={"plan_sha256": "0" * 64})
    assert r.status_code == 409 and r.json()["error"]["code"] == "plan_changed"
    r = manager.post(f"/api/v1/fix-plans/{plan['id']}/approve", json={"plan_sha256": plan["plan_sha256"], "approved_edit_ids": approval_items})
    assert r.status_code == 200 and r.json()["status"] == "pending_balancing"
    assert manager.post(f"/api/v1/fix-plans/{plan['id']}/apply").status_code == 409
    r = manager.post(f"/api/v1/fix-plans/{plan['id']}/approve-balancing", json={"plan_sha256": plan["plan_sha256"]})
    assert r.status_code == 403
    r = partner.post(f"/api/v1/fix-plans/{plan['id']}/approve-balancing", json={"plan_sha256": plan["plan_sha256"]})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert staff.get(f"/api/v1/engagements/{eid}").json()["status"] == "FIX_APPROVED"
    run = manager.post(f"/api/v1/fix-plans/{plan['id']}/apply").json()
    drain(world)
    fixed = manager.get(f"/api/v1/runs/{run['id']}").json()
    assert fixed["status"] == "succeeded", fixed
    assert manager.get(f"/api/v1/engagements/{eid}").json()["status"] == "FIXED_VERIFIED"
    v2 = fixed["output_version_id"]
    with world.db() as db:
        nv = db.get(FileVersion, v2)
        assert nv is not None and nv.parent_version_id == v1["id"] and nv.version_no == 2

    # Re-check the corrected version; no High findings remain.
    rc = staff.post(f"/api/v1/engagements/{eid}/runs", json={"kind": "check"}).json()
    drain(world)
    highs = [f for f in staff.get(f"/api/v1/runs/{rc['id']}/findings").json() if f["severity"] == "High"]
    assert highs == []
    assert manager.post(f"/api/v1/engagements/{eid}/transition", json={"to": "REVIEW"}).status_code == 200

    # Review notes: reviewer raises, staff responds, staff cannot clear, partner clears.
    note = reviewer.post(f"/api/v1/engagements/{eid}/review-notes", json={"target_type": "engagement", "text": "Tie the cash note to the CF."}).json()
    assert staff.post(f"/api/v1/review-notes/{note['id']}/messages", json={"text": "Tied; see note 9."}).json()["status"] == "Responded"
    assert staff.post(f"/api/v1/review-notes/{note['id']}/clear").status_code == 403
    assert partner.post(f"/api/v1/review-notes/{note['id']}/clear").json()["status"] == "Cleared"

    # Draft render and release to the client (C12 change report from the second release on).
    rr = staff.post(f"/api/v1/engagements/{eid}/render", json={"mode": "draft"}).json()
    drain(world)
    render = staff.get(f"/api/v1/runs/{rr['id']}").json()
    assert render["status"] == "succeeded" and render["result_json"]["misses"] == 0
    assert staff.post(f"/api/v1/engagements/{eid}/release", json={"render_run_id": render["id"]}).status_code == 403
    rel = manager.post(f"/api/v1/engagements/{eid}/release", json={"render_run_id": render["id"], "notes": "first draft"})
    assert rel.status_code == 201, rel.text
    assert manager.get(f"/api/v1/engagements/{eid}").json()["status"] == "DRAFT_RELEASED_TO_CLIENT"

    # Client portal: download the watermarked draft, confirm management representations.
    rels = cfo.get(f"/api/v1/portal/engagements/{eid}/releases").json()
    assert len(rels) == 1
    dl = uploader.get(f"/api/v1/portal/releases/{rels[0]['id']}/download")
    assert dl.status_code == 200 and dl.content.startswith(b"%PDF")
    assert cfo.get(f"/api/v1/portal/releases/{rels[0]['id']}/download").status_code == 403  # viewer role: downloads off
    item = manager.post(f"/api/v1/engagements/{eid}/mgmt-items", json={"text": "All related parties have been disclosed."}).json()
    assert uploader.post(f"/api/v1/portal/mgmt-items/{item['id']}/confirm", json={"decision": "confirmed"}).status_code == 403
    assert cfo.post(f"/api/v1/portal/mgmt-items/{item['id']}/confirm", json={"decision": "confirmed"}).status_code == 200
    assert partner.get(f"/api/v1/engagements/{eid}").json()["status"] == "MGMT_CONFIRMED"

    # Lock is gated: disclosure checklist and a verified FINAL PDF are still missing.
    r = partner.post(f"/api/v1/engagements/{eid}/transition", json={"to": "FINAL_LOCKED"})
    assert r.status_code == 409
    failed = {g["code"] for g in r.json()["error"]["details"]["gates"]}
    assert failed == {"final_pdf", "disclosure_checklist"}
    for item in partner.get(f"/api/v1/engagements/{eid}/disclosure-checklist").json():
        assert staff.put(f"/api/v1/engagements/{eid}/disclosure-checklist/{item['item_code']}", json={"answer": "yes"}).status_code == 200
    assert staff.post(f"/api/v1/engagements/{eid}/render", json={"mode": "final"}).status_code == 403
    fr = manager.post(f"/api/v1/engagements/{eid}/render", json={"mode": "final"}).json()
    drain(world)
    assert manager.get(f"/api/v1/runs/{fr['id']}").json()["status"] == "succeeded"
    assert manager.post(f"/api/v1/engagements/{eid}/transition", json={"to": "FINAL_LOCKED"}).status_code == 403
    r = partner.post(f"/api/v1/engagements/{eid}/transition", json={"to": "FINAL_LOCKED", "reason": "signed"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "FINAL_LOCKED" and r.json()["locked_at"]

    # Locked = read-only (ABAC) and WORM on the final files.
    r = upload_workbook(staff, eid)
    assert r.status_code == 403 and r.json()["error"]["code"] == "policy_denied"
    files = staff.get(f"/api/v1/engagements/{eid}/files").json()
    wb = next(f for f in files if f["kind"] == "workbook")
    assert wb["latest_version"]["worm_until"]
    from fsweb.storage import StorageError

    with world.db() as db:
        latest = db.get(FileVersion, wb["latest_version"]["id"])
        assert latest is not None
        try:
            world.state.storage.delete(latest.object_key)
            raise AssertionError("WORM object was deleted")
        except StorageError:
            pass
    return {"staff": staff, "partner": partner, "manager": manager}


def test_full_engagement_flow(world: World, totp_clock: FakeClock) -> None:
    run_e2e(world, totp_clock)
    admin = world.login("firm_admin", totp_clock)
    chain = admin.get("/api/v1/audit/verify").json()
    assert chain["ok"] is True and chain["events"] > 40
    with world.db() as db:
        actions = {a for (a,) in db.execute(select(AuditEvent.action)).all()}
    for expected in ("auth.login", "file.uploaded", "run.created", "run.succeeded", "fixplan.proposed", "fixplan.approved",
                     "fixplan.applied", "render.completed", "engagement.released", "engagement.locked", "authz.denied",
                     "file.downloaded", "review_note.cleared", "mgmt_item.decided"):
        assert expected in actions, expected


def test_dual_unlock(world: World, totp_clock: FakeClock) -> None:
    actors = run_e2e(world, totp_clock)
    partner = actors["partner"]
    eid = world.engagement_id
    req = partner.post(f"/api/v1/engagements/{eid}/unlock-requests", json={"reason": "post-balance-sheet event to disclose"})
    assert req.status_code == 201
    # The same partner cannot co-approve; a manager cannot; a Firm Admin (different person) can, with step-up.
    assert partner.post(f"/api/v1/unlock-requests/{req.json()['id']}/approve").status_code == 403
    admin = world.login("firm_admin", totp_clock)
    admin.step_up(totp_clock)
    r = admin.post(f"/api/v1/unlock-requests/{req.json()['id']}/approve", headers={"X-Action-Reason": "partner request verified"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "REVIEW" and r.json()["version_line"] == 2 and r.json()["locked_at"] is None
    # The engagement is editable again on a new version line; the old final files stay under WORM.
    assert partner.get(f"/api/v1/engagements/{eid}/unlock-requests").json()[0]["status"] == "completed"


def test_data_change_after_verification_returns_to_checked(world: World, totp_clock: FakeClock) -> None:
    eid = world.engagement_id
    staff = world.login("staff", totp_clock)
    manager = world.login("manager", totp_clock)
    upload_workbook(staff, eid)
    staff.post(f"/api/v1/engagements/{eid}/runs", json={"kind": "check"})
    drain(world)
    check = staff.get(f"/api/v1/engagements/{eid}/runs?kind=check").json()[0]
    plan = staff.post(f"/api/v1/runs/{check['id']}/fix-plan").json()
    drain(world)
    plan = staff.get(f"/api/v1/fix-plans/{plan['id']}").json()
    partner = world.login("partner", totp_clock)
    partner.post(f"/api/v1/fix-plans/{plan['id']}/approve", json={"plan_sha256": plan["plan_sha256"]})
    manager.post(f"/api/v1/fix-plans/{plan['id']}/apply")
    drain(world)
    assert staff.get(f"/api/v1/engagements/{eid}").json()["status"] == "FIXED_VERIFIED"
    r = upload_workbook(staff, eid)
    assert r.json()["engagement_status"] == "CHECKED" and r.json()["auto_check_run_id"]
    assert drain(world) == 1


def test_printed_pdf_reconciliation_input(world: World, totp_clock: FakeClock) -> None:
    eid = world.engagement_id
    staff = world.login("staff", totp_clock)
    upload_workbook(staff, eid)
    r = staff.post(f"/api/v1/engagements/{eid}/files", files={"file": ("printed.pdf", pdf_bytes(), "application/pdf")}, data={"kind": "printed_pdf"})
    assert r.status_code == 201
    pdf_vid = r.json()["version"]["id"]
    run = staff.post(f"/api/v1/engagements/{eid}/runs", json={"kind": "check", "params": {"pdf_version_id": pdf_vid}}).json()
    drain(world)
    assert staff.get(f"/api/v1/runs/{run['id']}").json()["status"] == "succeeded"
