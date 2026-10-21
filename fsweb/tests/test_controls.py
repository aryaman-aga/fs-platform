"""Controls not covered elsewhere: maker-checker on fix plans and their balancing lines (C2 / C10), the PDF-match
gate on releases (C7), the release change report (C12), emergency changes (E-ITGC-01), joiner / mover access
(E-ITGC-03) and the last-administrator escalation of the access-review auto-revoke (C14)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pyotp
import pytest
from freezegun import freeze_time
from sqlalchemy import select
from starlette.requests import Request

from fscore.models import PdfMiss, RenderResult
from fsweb.authz.deps import Principal, authorize, load_roles
from fsweb.authz.permissions import ALLOW, NOT_OWN
from fsweb.db import utcnow
from fsweb.engine import FAKE_FIXED
from fsweb.errors import ApiError
from fsweb.models import AuditEvent, Engagement, UserClientRole
from fsweb.services.seed import make_user
from tests.conftest import (
    PASSWORD,
    Api,
    FakeClock,
    World,
    add_user,
    drain,
    login,
    upload_workbook,
    xlsx_bytes,
)

REVIEW_PERIOD = "2026Q3"


def _status(api: Api, world: World) -> str:
    return str(api.get(f"/api/v1/engagements/{world.engagement_id}").json()["status"])


def _code(r: Any) -> str:
    return str(r.json()["error"]["code"])


# ------------------------------------------------------------------ fix plans: maker-checker (C2 / C10)


def _propose(world: World, api: Api) -> dict[str, Any]:
    """`api` uploads the synthetic workbook, runs checks and proposes a fix plan (it has a balancing line)."""
    eid = world.engagement_id
    assert upload_workbook(api, eid).status_code == 201
    api.post(f"/api/v1/engagements/{eid}/runs", json={"kind": "check"})
    drain(world)
    check = api.get(f"/api/v1/engagements/{eid}/runs?kind=check").json()[0]
    plan = api.post(f"/api/v1/runs/{check['id']}/fix-plan").json()
    drain(world)
    plan = api.get(f"/api/v1/fix-plans/{plan['id']}").json()
    assert plan["status"] == "proposed" and plan["has_balancing"], plan
    return dict(plan)


def _approve(api: Api, plan: dict[str, Any]) -> Any:
    return api.post(f"/api/v1/fix-plans/{plan['id']}/approve", json={"plan_sha256": plan["plan_sha256"], "approved_edit_ids": ["E-0003"]})


def _another_partner(world: World, clock: FakeClock) -> tuple[str, Api]:
    """A second Partner on the client, signed in."""
    u = add_user(world, "partner.two@test.fs", "partner", client_id=world.client_id)
    return u["id"], login(world.app, u["email"], PASSWORD, u["secret"], clock)


def test_manager_cannot_approve_own_fix_plan(world: World, totp_clock: FakeClock) -> None:
    """C2 / C10: the Manager who proposed a plan is refused as its approver; a Partner then approves it."""
    manager = world.login("manager", totp_clock)
    plan = _propose(world, manager)
    assert plan["proposed_by"] == world.users["manager"]["id"]
    r = _approve(manager, plan)
    assert r.status_code == 403, r.text
    assert _code(r) == "segregation_of_duties" and r.json()["error"]["details"]["permission"] == "fix.approve"
    unchanged = manager.get(f"/api/v1/fix-plans/{plan['id']}").json()
    assert unchanged["status"] == "proposed" and unchanged["approved_by"] is None
    assert _status(manager, world) == "FIX_PLANNED"

    r = _approve(world.login("partner", totp_clock), plan)
    assert r.status_code == 200, r.text
    pid = world.users["partner"]["id"]
    assert (r.json()["status"], r.json()["approved_by"], r.json()["balancing_approved_by"]) == ("approved", pid, pid)
    assert _status(manager, world) == "FIX_APPROVED"
    with world.db() as db:  # the refusal is on the audit trail
        denials = [e.details for e in db.execute(select(AuditEvent).where(AuditEvent.action == "authz.denied",
                                                                        AuditEvent.user_id == world.users["manager"]["id"])).scalars()]
    assert any(d["permission"] == "fix.approve" and "own plan" in d["reason"] for d in denials)


def test_partner_cannot_approve_own_fix_plan(world: World, totp_clock: FakeClock) -> None:
    """C2 / C10: approver ≠ proposer whatever the role — the Partner's unconditional ✓ on fix.approve does not cover
    their own plan; another Partner can approve it."""
    partner = world.login("partner", totp_clock)
    plan = _propose(world, partner)
    r = _approve(partner, plan)
    assert r.status_code == 403 and _code(r) == "segregation_of_duties", r.text
    assert partner.get(f"/api/v1/fix-plans/{plan['id']}").json()["status"] == "proposed"
    assert _status(partner, world) == "FIX_PLANNED"

    pid, other = _another_partner(world, totp_clock)
    r = _approve(other, plan)
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["proposed_by"], r.json()["approved_by"]) == ("approved", world.users["partner"]["id"], pid)


def test_balancing_checker_must_be_a_different_person(world: World, totp_clock: FakeClock) -> None:
    """Balancing maker-checker (05 §3, Manager = M): the Partner who checks a Manager-approved balancing line may be
    neither the plan's proposer nor its first approver — even once that approver is a Partner too."""
    partner = world.login("partner", totp_clock)
    manager = world.login("manager", totp_clock)
    plan = _propose(world, partner)
    r = _approve(manager, plan)  # a Manager may approve another person's plan, but only as maker of its balancing line
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending_balancing" and r.json()["balancing_approved_by"] is None
    url, body = f"/api/v1/fix-plans/{plan['id']}/approve-balancing", {"plan_sha256": plan["plan_sha256"]}
    r = manager.post(url, json=body)
    assert r.status_code == 403 and _code(r) == "maker_checker", r.text
    r = partner.post(url, json=body)  # the proposer
    assert r.status_code == 403 and _code(r) == "segregation_of_duties", r.text

    admin = world.login("firm_admin", totp_clock)  # the approving Manager is made Partner on the client as well
    r = admin.post("/api/v1/admin/assignments", json={"user_id": world.users["manager"]["id"], "role_code": "partner", "client_id": world.client_id})
    assert r.status_code == 201, r.text
    r = manager.post(url, json=body)  # now holds ✓ for balancing, but is still the first approver
    assert r.status_code == 403 and _code(r) == "segregation_of_duties", r.text
    assert manager.get(f"/api/v1/fix-plans/{plan['id']}").json()["status"] == "pending_balancing"
    assert _status(manager, world) == "FIX_PLANNED"

    pid, checker = _another_partner(world, totp_clock)
    r = checker.post(url, json=body)
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["approved_by"], r.json()["balancing_approved_by"]) == ("approved", world.users["manager"]["id"], pid)
    assert _status(manager, world) == "FIX_APPROVED"


def _principal(db: Any, world: World, role: str) -> Principal:
    uid = world.users[role]["id"]
    return Principal(tenant_id=world.tenant_id, user_id=uid, session_id="", email=f"{role}@test.fs", name=role, is_firm_user=True,
                     is_break_glass=False, mfa_verified=True, stepup_at=None, roles=load_roles(db, uid))


def test_not_own_cell_refuses_the_owner_inside_authorize(world: World) -> None:
    """C10: the matrix cell 'not own' (Manager on fix.approve) refuses the item's owner inside `authorize` itself. The
    routers check the proposer first, so over HTTP this defence-in-depth branch is unreachable; it is called directly."""
    request = Request({"type": "http", "method": "POST", "path": "/api/v1/fix-plans/x/approve", "headers": [], "query_string": b"", "app": world.app})
    with world.db() as db:
        e = db.get(Engagement, world.engagement_id)
        assert e is not None
        manager, partner = _principal(db, world, "manager"), _principal(db, world, "partner")
        assert authorize(request, db, manager, "fix.approve", engagement=e, owner_id=world.users["staff"]["id"]) == NOT_OWN
        with pytest.raises(ApiError) as denied:
            authorize(request, db, manager, "fix.approve", engagement=e, owner_id=manager.user_id)
        # A plain ✓ carries no ownership condition: for a Partner the router's proposer check is the control.
        assert authorize(request, db, partner, "fix.approve", engagement=e, owner_id=partner.user_id) == ALLOW
        event = db.execute(select(AuditEvent).where(AuditEvent.action == "authz.denied").order_by(AuditEvent.seq.desc())).scalars().first()
        assert event is not None
        who, details = event.user_id, dict(event.details)
    assert denied.value.status_code == 403 and denied.value.code == "segregation_of_duties"
    assert who == manager.user_id
    assert (details["permission"], details["reason"]) == ("fix.approve", "segregation of duties: you cannot approve your own item")


# ------------------------------------------------------------------ release to the client (C7 / C12)


def _clean_workbook(title: str) -> bytes:
    """A workbook the fake engine checks clean (no High findings), so it can go to REVIEW without a fix cycle."""
    return xlsx_bytes({"customXml/fsweb-fixed.txt": FAKE_FIXED}, title=title)


def _to_review(world: World, api: Api, data: bytes) -> dict[str, Any]:
    """Upload `data` as the next workbook version, check it and move the engagement to REVIEW; returns the version."""
    eid = world.engagement_id
    up = upload_workbook(api, eid, data)
    assert up.status_code == 201, up.text
    api.post(f"/api/v1/engagements/{eid}/runs", json={"kind": "check"})
    drain(world)
    r = api.post(f"/api/v1/engagements/{eid}/transition", json={"to": "REVIEW"})
    assert r.status_code == 200, r.text
    return dict(up.json()["version"])


def _render(world: World, api: Api) -> dict[str, Any]:
    """A draft render of the latest workbook version, run to completion."""
    run = api.post(f"/api/v1/engagements/{world.engagement_id}/render", json={"mode": "draft"}).json()
    drain(world)
    done = api.get(f"/api/v1/runs/{run['id']}").json()
    assert done["status"] == "succeeded", done
    return dict(done)


def _release(world: World, api: Api, render: dict[str, Any]) -> Any:
    return api.post(f"/api/v1/engagements/{world.engagement_id}/release", json={"render_run_id": render["id"]})


@pytest.mark.parametrize("defect", ["misses", "unreconciled"])
def test_release_refused_when_pdf_does_not_match_workbook(world: World, totp_clock: FakeClock, monkeypatch: pytest.MonkeyPatch,
                                                          defect: str) -> None:
    """C7: a draft whose render has PDF-01 reconciliation misses — or was never reconciled — is not released
    (409 pdf_mismatch); a verified render of the same workbook version is."""
    staff = world.login("staff", totp_clock)
    manager = world.login("manager", totp_clock)
    _to_review(world, staff, _clean_workbook("Balance Sheet"))
    verified = _render(world, staff)
    real = world.state.engine.render_pdf

    def render_pdf(*args: Any, **kwargs: Any) -> RenderResult:
        res: RenderResult = real(*args, **kwargs)
        assert res.verification is not None
        if defect == "unreconciled":
            return res.model_copy(update={"verification": None})
        miss = PdfMiss(sheet="BS", cell="D20", printed="500.01", caption="Total current liabilities", pages=[1])
        return res.model_copy(update={"verification": res.verification.model_copy(update={"misses": [miss]})})

    monkeypatch.setattr(world.state.engine, "render_pdf", render_pdf)
    mismatched = _render(world, staff)
    assert mismatched["result_json"]["misses"] == (1 if defect == "misses" else -1)
    r = _release(world, manager, mismatched)
    assert r.status_code == 409 and _code(r) == "pdf_mismatch", r.text
    assert manager.get(f"/api/v1/engagements/{world.engagement_id}/releases").json() == []
    assert _status(manager, world) == "REVIEW"

    r = _release(world, manager, verified)
    assert r.status_code == 201, r.text
    assert r.json()["pdf_version_id"] == verified["output_version_id"]


def test_second_release_carries_change_report_from_previous_release(world: World, totp_clock: FakeClock) -> None:
    """C12: from the second release on, the release stores the change report between the previously *released*
    workbook and the one now released (not the previous upload), and the client sees it with the release."""
    eid = world.engagement_id
    staff = world.login("staff", totp_clock)
    manager = world.login("manager", totp_clock)
    v1 = _to_review(world, staff, _clean_workbook("Balance Sheet"))
    first = _release(world, manager, _render(world, staff))
    assert first.status_code == 201, first.text
    assert first.json()["diff_json"] == {"first_release": True}

    # Two revisions after the first release; only the later one is released.
    revised = upload_workbook(staff, eid, _clean_workbook("Balance Sheet (revised)")).json()
    assert revised["engagement_status"] == "CHECKED"  # data changed after the release
    v3 = _to_review(world, staff, _clean_workbook("Balance Sheet (second draft)"))
    second = _release(world, manager, _render(world, staff))
    assert second.status_code == 201, second.text
    assert second.json()["workbook_version_id"] == v3["id"]

    diff = second.json()["diff_json"]
    assert diff["first_release"] is False
    assert (diff["from_version_id"], diff["to_version_id"]) == (v1["id"], v3["id"])
    assert revised["version"]["id"] not in (diff["from_version_id"], diff["to_version_id"])
    assert (diff["a_sha256"], diff["b_sha256"]) == (v1["sha256"], v3["sha256"])  # the engine compared exactly these two files
    assert [(m["sheet"], m["cell"], m["before"], m["after"]) for m in diff["printed_moves"]] == [("BS", "D20", "500.00", "500.01")]
    with world.db() as db:
        moves = [e.details["printed_moves"] for e in db.execute(select(AuditEvent).where(AuditEvent.action == "engagement.released")
                                                                .order_by(AuditEvent.seq)).scalars()]
    assert moves == [0, 1]

    portal = world.login("client_viewer", totp_clock).get(f"/api/v1/portal/engagements/{eid}/releases").json()
    assert [r["id"] for r in portal] == [second.json()["id"], first.json()["id"]]
    assert portal[0]["diff_json"] == diff


# ------------------------------------------------------------------ ITGC: emergency change (E-ITGC-01)


def test_emergency_release_is_deployed_first_and_reviewed_after(world: World, totp_clock: FakeClock) -> None:
    """E-ITGC-01: an emergency change may be deployed before approval; the after-the-fact review still needs
    operations rights and a person other than the developer, and the trail shows deployment before review."""
    sa_id, fa_id = world.users["system_admin"]["id"], world.users["firm_admin"]["id"]
    sa = world.login("system_admin", totp_clock)
    body = {"version": "1.0.1", "ticket_ref": "INC-42", "risk": "high", "test_evidence": "hotfix smoke test green",
            "rollback_plan": "redeploy 1.0.0", "emergency": True}
    # An emergency does not waive golden-regression evidence for a rule change (E-ITGC-02).
    r = sa.post("/api/v1/admin/releases", json={**body, "rule_change": True})
    assert r.status_code == 400 and _code(r) == "regression_required", r.text
    r = sa.post("/api/v1/admin/releases", json=body)
    assert r.status_code == 201 and r.json()["emergency"] is True, r.text
    rid = r.json()["id"]

    r = sa.post(f"/api/v1/admin/releases/{rid}/deployed")  # no approval yet
    assert r.status_code == 200, r.text
    assert r.json()["deployed_at"] and r.json()["approved_at"] is None and r.json()["approver_id"] is None

    r = sa.post(f"/api/v1/admin/releases/{rid}/approve")  # the developer cannot review their own change...
    assert r.status_code == 403 and _code(r) == "segregation_of_duties", r.text
    assert world.login("partner", totp_clock).post(f"/api/v1/admin/releases/{rid}/approve").status_code == 403  # ...nor can a non-operator
    r = world.login("firm_admin", totp_clock).post(f"/api/v1/admin/releases/{rid}/approve")
    assert r.status_code == 200, r.text
    rel = r.json()
    assert (rel["created_by"], rel["approver_id"]) == (sa_id, fa_id)
    assert datetime.fromisoformat(rel["approved_at"]) >= datetime.fromisoformat(rel["deployed_at"])
    with world.db() as db:
        trail = [(e.action, e.user_id, dict(e.details)) for e in db.execute(select(AuditEvent).where(AuditEvent.object_id == rid)
                                                                            .order_by(AuditEvent.seq)).scalars()]
    assert [(action, user) for action, user, _ in trail] == [("release.recorded", sa_id), ("release.deployed", sa_id), ("release.approved", fa_id)]
    assert trail[1][2]["emergency"] is True


# ------------------------------------------------------------------ ITGC: joiner / mover (E-ITGC-03)


def _joiner(world: World, email: str) -> dict[str, Any]:
    """A new firm user: account and authenticator, but no role assignment yet."""
    secret = pyotp.random_base32()
    with world.db() as db:
        u = make_user(db, world.state.cipher, world.tenant_id, email, "New Joiner", password=PASSWORD, totp_secret=secret, created_by="test")
        db.commit()
        return {"id": u.id, "email": email, "secret": secret}


def test_assignment_opens_and_revocation_closes_engagement_access(world: World, totp_clock: FakeClock) -> None:
    """E-ITGC-03: assigning a joiner to a client opens that client's engagement to them; when they move to another
    client, revoking the old assignment closes it on their very next request (same session)."""
    eid = world.engagement_id
    j = _joiner(world, "joiner@test.fs")
    joiner = login(world.app, j["email"], PASSWORD, j["secret"], totp_clock)
    assert joiner.get(f"/api/v1/engagements/{eid}").status_code == 404
    assert joiner.get("/api/v1/engagements").json() == []

    partner = world.login("partner", totp_clock)
    r = partner.post("/api/v1/admin/assignments", json={"user_id": j["id"], "role_code": "staff", "client_id": world.client_id})
    assert r.status_code == 201, r.text
    alpha = r.json()
    assert (alpha["role_code"], alpha["client_id"], alpha["client_name"], alpha["revoked_at"]) == ("staff", world.client_id, "Alpha Synthetic Ltd", None)
    assert joiner.get(f"/api/v1/engagements/{eid}").status_code == 200
    assert [e["id"] for e in joiner.get("/api/v1/engagements").json()] == [eid]
    assert upload_workbook(joiner, eid).status_code == 201  # works on it as Staff

    admin = world.login("firm_admin", totp_clock)  # mover: to client Beta, off client Alpha
    r = admin.post("/api/v1/admin/assignments", json={"user_id": j["id"], "role_code": "staff", "client_id": world.other_client_id})
    assert r.status_code == 201, r.text
    beta = r.json()
    r = admin.delete(f"/api/v1/admin/assignments/{alpha['id']}")
    assert r.status_code == 200, r.text
    assert joiner.get(f"/api/v1/engagements/{eid}").status_code == 404
    assert upload_workbook(joiner, eid).status_code == 404
    assert joiner.get("/api/v1/engagements").json() == []
    me = joiner.get("/api/v1/auth/me").json()
    assert [(a["id"], a["client_id"]) for a in me["assignments"]] == [(beta["id"], world.other_client_id)]
    assert world.client_id not in me["permissions"] and me["permissions"][world.other_client_id]["file.upload"] == "allow"
    assert j["id"] not in {a["user_id"] for a in partner.get(f"/api/v1/admin/assignments?client_id={world.client_id}").json()}

    with world.db() as db:
        row = db.get(UserClientRole, alpha["id"])
        assert row is not None and row.revoked_at is not None and row.revoked_reason == "revoked by administrator"
        trail = [(e.action, e.object_id, e.user_id) for e in db.execute(select(AuditEvent).where(AuditEvent.action.in_(["assignment.created", "assignment.revoked"]))
                                                                        .order_by(AuditEvent.seq)).scalars()]
    pid, fid = world.users["partner"]["id"], world.users["firm_admin"]["id"]
    assert trail == [("assignment.created", alpha["id"], pid), ("assignment.created", beta["id"], fid), ("assignment.revoked", alpha["id"], fid)]


# ------------------------------------------------------------------ access review auto-revoke (C14)


def _lapse_review(world: World, clock: FakeClock) -> tuple[list[dict[str, Any]], str]:
    """Open the quarter's access review, leave it unanswered past the 14-day window and run the auto-revoke as the
    Firm Admin. Returns the review items and the auto-revoke message."""
    # FastAPI builds each router's route models on first use; do that before freezegun swaps `datetime`.
    assert world.login("firm_admin", clock).get(f"/api/v1/admin/access-reviews?period={REVIEW_PERIOD}").json() == []
    start = utcnow() + timedelta(minutes=10)  # a JIT elevation granted just before has lapsed when the review opens
    with freeze_time(start):
        admin = world.login("firm_admin", clock)
        assert admin.post("/api/v1/admin/access-reviews", json={"period": REVIEW_PERIOD}).status_code == 201
        items: list[dict[str, Any]] = admin.get(f"/api/v1/admin/access-reviews?period={REVIEW_PERIOD}").json()
    with freeze_time(start + timedelta(days=15)):
        r = world.login("firm_admin", clock).post("/api/v1/admin/access-reviews/auto-revoke")
    assert r.status_code == 200, r.text
    return items, str(r.json()["message"])


def _item(items: list[dict[str, Any]], user_id: str) -> dict[str, Any]:
    return next(i for i in items if i["user_id"] == user_id)


def _revoked(world: World, assignment_id: str) -> bool:
    with world.db() as db:
        a = db.get(UserClientRole, assignment_id)
        assert a is not None
        return a.revoked_at is not None


def _escalations(world: World) -> set[tuple[str, str, str]]:
    with world.db() as db:
        return {(str(e.object_id), e.details["role"], e.details["user_id"])
                for e in db.execute(select(AuditEvent).where(AuditEvent.action == "access_review.escalated")).scalars()}


def test_auto_revoke_escalates_the_last_firm_admin(world: World, totp_clock: FakeClock) -> None:
    """C14 / E-SEC-04: unanswered items are revoked after 14 days, but the last Firm Admin (and the last System Admin)
    is escalated instead — left pending, access kept — so the firm is never locked out of administration."""
    admins = {world.users["firm_admin"]["id"]: "firm_admin", world.users["system_admin"]["id"]: "system_admin"}
    items, message = _lapse_review(world, totp_clock)
    assert len(items) == 8 and message == "6 unanswered assignment(s) revoked"

    admin = world.login("firm_admin", totp_clock)
    assert admin.get("/api/v1/admin/users").status_code == 200  # still administers
    after = {i["id"]: i["decision"] for i in admin.get(f"/api/v1/admin/access-reviews?period={REVIEW_PERIOD}").json()}
    assert after == {i["id"]: "pending" if i["user_id"] in admins else "auto_revoked" for i in items}
    assert {i["user_id"] for i in items if not _revoked(world, i["assignment_id"])} == set(admins)
    assert _escalations(world) == {(_item(items, uid)["id"], role, uid) for uid, role in admins.items()}
    with freeze_time(utcnow() + timedelta(days=16)):  # a later run escalates again and still revokes nothing
        r = world.login("firm_admin", totp_clock).post("/api/v1/admin/access-reviews/auto-revoke")
    assert r.status_code == 200 and r.json()["message"] == "0 unanswered assignment(s) revoked"


def test_auto_revoke_revokes_a_firm_admin_who_is_not_the_last(world: World, totp_clock: FakeClock) -> None:
    """C14: escalation is for the last holder only — of two unanswered Firm Admins one is revoked and the remaining
    one is escalated, so exactly one Firm Admin is left and can still administer."""
    second = add_user(world, "admin.two@test.fs", "firm_admin")
    first_id = world.users["firm_admin"]["id"]
    items, message = _lapse_review(world, totp_clock)
    assert len(items) == 9 and message == "7 unanswered assignment(s) revoked"
    kept = [uid for uid in (first_id, second["id"]) if not _revoked(world, _item(items, uid)["assignment_id"])]
    assert len(kept) == 1
    assert {(role, uid) for _, role, uid in _escalations(world)} == {("firm_admin", kept[0]), ("system_admin", world.users["system_admin"]["id"])}
    survivor = world.login("firm_admin", totp_clock) if kept[0] == first_id else login(world.app, second["email"], PASSWORD, second["secret"], totp_clock)
    assert survivor.get("/api/v1/admin/users").status_code == 200


def test_expired_elevation_does_not_count_as_another_firm_admin(world: World, totp_clock: FakeClock) -> None:
    """C14: a lapsed JIT elevation to Firm Admin grants nothing, so it must not make the real Firm Admin look 'not
    the last one' — that admin is escalated, not auto-revoked (which would lock the firm out)."""
    admin = world.login("firm_admin", totp_clock)
    admin.step_up(totp_clock)
    el = world.login("staff", totp_clock).post("/api/v1/admin/elevations", json={"role_code": "firm_admin", "reason": "cover the admin's leave",
                                                                                 "duration_minutes": 5})
    assert el.status_code == 201, el.text
    r = admin.post(f"/api/v1/admin/elevations/{el.json()['id']}/approve", headers={"X-Action-Reason": "cover for leave"})
    assert r.status_code == 200, r.text  # expires five minutes later, before the review opens

    fa = world.users["firm_admin"]["id"]
    items, _ = _lapse_review(world, totp_clock)
    assert not _revoked(world, _item(items, fa)["assignment_id"])
    assert (_item(items, fa)["id"], "firm_admin", fa) in _escalations(world)
    assert world.login("firm_admin", totp_clock).get("/api/v1/admin/users").status_code == 200


def test_disabled_admin_does_not_count_as_another_firm_admin(world: World, totp_clock: FakeClock) -> None:
    """C14: a disabled Firm Admin (an offboarded leaver) cannot sign in, so the remaining active Firm Admin is the last
    one and is escalated; the leaver's stale admin assignment is the one auto-revoked."""
    leaver = add_user(world, "admin.leaver@test.fs", "firm_admin")
    admin = world.login("firm_admin", totp_clock)
    admin.step_up(totp_clock)
    r = admin.post(f"/api/v1/admin/users/{leaver['id']}/disable", headers={"X-Action-Reason": "left the firm"})
    assert r.status_code == 200, r.text

    fa = world.users["firm_admin"]["id"]
    items, _ = _lapse_review(world, totp_clock)
    assert not _revoked(world, _item(items, fa)["assignment_id"])
    assert _revoked(world, _item(items, leaver["id"])["assignment_id"])
    assert (_item(items, fa)["id"], "firm_admin", fa) in _escalations(world)
    assert world.login("firm_admin", totp_clock).get("/api/v1/admin/users").status_code == 200


# ------------------------------------------------------------------ lapsed roles, revocation rank, emergency reviews


def _lapsed_assignment(world: World, role: str, user_role: str, client_id: str | None) -> str:
    """Give the ``user_role`` test user an assignment of ``role`` that expired an hour ago (a lapsed JIT elevation)."""
    from fsweb.models import Role

    with world.db() as db:
        r = db.execute(select(Role).where(Role.tenant_id == world.tenant_id, Role.code == role)).scalar_one()
        a = UserClientRole(tenant_id=world.tenant_id, user_id=world.users[user_role]["id"], client_id=client_id, role_id=r.id,
                           expires_at=utcnow() - timedelta(hours=1), created_by="test")
        db.add(a)
        db.commit()
        return a.id


def test_lapsed_elevation_is_neither_reviewer_nor_decider(world: World, totp_clock: FakeClock) -> None:
    """C14: an expired Firm Admin elevation grants nothing — the user is not chosen as an access reviewer, and an item
    that still names them (the campaign opened while the elevation was live) cannot be decided by them."""
    from fsweb.models import AccessReview
    from fsweb.services.common import users_with_role

    _lapsed_assignment(world, "firm_admin", "staff", None)
    staff_id, fa_id = world.users["staff"]["id"], world.users["firm_admin"]["id"]
    with world.db() as db:
        assert [u.id for u in users_with_role(db, world.tenant_id, "firm_admin")] == [fa_id]
    admin = world.login("firm_admin", totp_clock)
    assert admin.post("/api/v1/admin/access-reviews", json={"period": REVIEW_PERIOD}).status_code == 201
    items = admin.get(f"/api/v1/admin/access-reviews?period={REVIEW_PERIOD}").json()
    assert staff_id not in {i["reviewer_id"] for i in items}
    fa_item = _item(items, fa_id)
    with world.db() as db:
        row = db.get(AccessReview, fa_item["id"])
        assert row is not None
        row.reviewer_id = staff_id
        db.commit()
    r = world.login("staff", totp_clock).post(f"/api/v1/admin/access-reviews/{fa_item['id']}/decide", json={"decision": "revoke"})
    assert r.status_code == 403, r.text
    assert not _revoked(world, fa_item["assignment_id"])
    assert world.login("firm_admin", totp_clock).get("/api/v1/admin/users").status_code == 200


def test_only_those_who_may_grant_a_role_may_revoke_it(world: World, totp_clock: FakeClock) -> None:
    """E-ITGC-03 / 05 §2: a Manager cannot assign a Partner, so cannot remove the engagement Partner either; revoking
    twice keeps the first revocation's time and reason."""
    with world.db() as db:
        partner_assignment = db.execute(select(UserClientRole).where(UserClientRole.user_id == world.users["partner"]["id"],
                                                                     UserClientRole.client_id == world.client_id)).scalar_one().id
    r = world.login("manager", totp_clock).delete(f"/api/v1/admin/assignments/{partner_assignment}")
    assert r.status_code == 403, r.text
    assert not _revoked(world, partner_assignment)
    admin = world.login("firm_admin", totp_clock)
    assert admin.delete(f"/api/v1/admin/assignments/{partner_assignment}").status_code == 200
    with world.db() as db:
        first = db.get(UserClientRole, partner_assignment)
        assert first is not None
        stamp = (first.revoked_at, first.revoked_reason)
    assert admin.delete(f"/api/v1/admin/assignments/{partner_assignment}").json()["message"] == "assignment already revoked"
    with world.db() as db:
        again = db.get(UserClientRole, partner_assignment)
        assert again is not None and (again.revoked_at, again.revoked_reason) == stamp


def test_lapsed_elevation_does_not_block_a_permanent_assignment(world: World, totp_clock: FakeClock) -> None:
    """E-ITGC-03 (mover): an expired elevation to Reviewer on the client is no obstacle to assigning the role for good."""
    _lapsed_assignment(world, "reviewer", "staff", world.client_id)
    r = world.login("partner", totp_clock).post("/api/v1/admin/assignments", json={"user_id": world.users["staff"]["id"], "role_code": "reviewer",
                                                                                   "client_id": world.client_id})
    assert r.status_code == 201, r.text
    assert r.json()["expires_at"] is None


def test_emergency_review_overdue_is_flagged_audited_and_alerted(world: World, totp_clock: FakeClock) -> None:
    """E-ITGC-01: an emergency change deployed over 48 hours ago without approval shows as overdue, feeds the
    EmergencyChangeReviewOverdue alert, and the developer's refused self-approval is a recorded denial."""
    from fastapi.testclient import TestClient

    from fsweb.models import Release

    sa = world.login("system_admin", totp_clock)
    rid = sa.post("/api/v1/admin/releases", json={"version": "1.0.2", "ticket_ref": "INC-43", "risk": "high", "test_evidence": "smoke green",
                                                  "rollback_plan": "redeploy 1.0.1", "emergency": True}).json()["id"]
    assert sa.post(f"/api/v1/admin/releases/{rid}/deployed").status_code == 200
    with world.db() as db:
        rel = db.get(Release, rid)
        assert rel is not None and rel.deployed_at is not None
        rel.deployed_at = rel.deployed_at - timedelta(hours=49)
        db.commit()

    def overdue_gauge() -> float:
        text = TestClient(world.app).get("/metrics").text
        return float(next(ln.split()[-1] for ln in text.splitlines() if ln.startswith("fsweb_itgc_emergency_reviews_overdue ")))

    listed = next(r for r in sa.get("/api/v1/admin/releases").json() if r["id"] == rid)
    assert listed["review_overdue"] is True and listed["review_due_at"]
    assert overdue_gauge() == 1
    assert sa.post(f"/api/v1/admin/releases/{rid}/approve").status_code == 403
    with world.db() as db:
        denied = db.execute(select(AuditEvent).where(AuditEvent.action == "authz.denied").order_by(AuditEvent.seq.desc())).scalars().first()
        assert denied is not None and denied.user_id == world.users["system_admin"]["id"] and denied.details["permission"] == "ops.manage"
    approved = world.login("firm_admin", totp_clock).post(f"/api/v1/admin/releases/{rid}/approve").json()
    assert approved["review_overdue"] is True  # approved, but late: the exception stays visible in the evidence
    assert overdue_gauge() == 0
