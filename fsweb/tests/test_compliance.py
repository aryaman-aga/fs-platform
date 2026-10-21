"""Access recertification (C14 / E-SEC-04), DPDP requests (E-SEC-06), CERT-In incidents
(E-SEC-05), ITGC releases (E-ITGC-01/02), evidence pack (E-ITGC-06), usage, health."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import timedelta

from freezegun import freeze_time
from sqlalchemy import select

from fsweb.db import utcnow
from fsweb.models import User, UserClientRole
from tests.conftest import FakeClock, World, drain, upload_workbook


def test_access_review_campaign(world: World, totp_clock: FakeClock) -> None:
    admin = world.login("firm_admin", totp_clock)
    assert admin.post("/api/v1/admin/access-reviews", json={"period": "2026Q3"}).status_code == 201
    items = admin.get("/api/v1/admin/access-reviews?period=2026Q3").json()
    assert len(items) == 8
    partner = world.login("partner", totp_clock)
    mine = partner.get("/api/v1/admin/access-reviews?mine=true").json()
    assert mine and all(i["reviewer_id"] == world.users["partner"]["id"] for i in mine)
    staff_item = next(i for i in mine if i["user_id"] == world.users["staff"]["id"])
    assert partner.post(f"/api/v1/admin/access-reviews/{staff_item['id']}/decide", json={"decision": "certify"}).status_code == 200
    reviewer_item = next(i for i in mine if i["user_id"] == world.users["reviewer"]["id"])
    assert partner.post(f"/api/v1/admin/access-reviews/{reviewer_item['id']}/decide", json={"decision": "revoke"}).status_code == 200
    with world.db() as db:
        a = db.get(UserClientRole, reviewer_item["assignment_id"])
        assert a is not None and a.revoked_at is not None
    # No self-certification.
    own = next(i for i in items if i["user_id"] == world.users["firm_admin"]["id"])
    assert admin.post(f"/api/v1/admin/access-reviews/{own['id']}/decide", json={"decision": "certify"}).status_code == 403
    # Unanswered items are revoked after 14 days.
    with freeze_time(utcnow() + timedelta(days=15)):
        admin2 = world.login("firm_admin", totp_clock)
        r = admin2.post("/api/v1/admin/access-reviews/auto-revoke")
        assert r.status_code == 200 and "revoked" in r.json()["message"]
        report = admin2.get("/api/v1/admin/access-reviews/report?period=2026Q3").json()
        assert report["summary"]["auto_revoked"] >= 1 and report["summary"]["certify"] == 1


def test_dsr_access_and_erasure(world: World, totp_clock: FakeClock) -> None:
    admin = world.login("firm_admin", totp_clock)
    admin.step_up(totp_clock)
    r = admin.post("/api/v1/admin/dsr", json={"principal_ref": "client_viewer@test.fs", "kind": "access"})
    assert r.status_code == 201 and r.json()["principal_user_id"] == world.users["client_viewer"]["id"]
    done = admin.post(f"/api/v1/admin/dsr/{r.json()['id']}/execute", headers={"X-Action-Reason": "principal request"}).json()
    assert done["status"] == "completed" and "export prepared" in done["resolution"]
    er = admin.post("/api/v1/admin/dsr", json={"principal_ref": "client_viewer@test.fs", "kind": "erasure"}).json()
    done = admin.post(f"/api/v1/admin/dsr/{er['id']}/execute", headers={"X-Action-Reason": "principal request"}).json()
    assert done["status"] == "partially_completed" and "retained" in done["resolution"]
    with world.db() as db:
        u = db.get(User, world.users["client_viewer"]["id"])
        assert u is not None and u.email.startswith("erased-") and u.status == "disabled" and u.erased_at
    assert admin.get("/api/v1/admin/dsr").json()[0]["due_at"]


def test_certin_incident_six_hour_deadline(world: World, totp_clock: FakeClock) -> None:
    sa = world.login("system_admin", totp_clock)
    detected = utcnow() - timedelta(hours=7)
    r = sa.post("/api/v1/admin/incidents", json={"severity": "high", "title": "Suspicious sign-ins", "certin_reportable": True, "detected_at": detected.isoformat()})
    assert r.status_code == 201
    inc = r.json()
    assert inc["report_due_at"] and inc["overdue"] is True
    fixed = sa.patch(f"/api/v1/admin/incidents/{inc['id']}", json={"reported_at": utcnow().isoformat(), "status": "contained"}).json()
    assert fixed["overdue"] is False
    assert world.login("staff", totp_clock).get("/api/v1/admin/incidents").status_code == 403


def test_itgc_release_controls(world: World, totp_clock: FakeClock) -> None:
    sa = world.login("system_admin", totp_clock)
    body = {"version": "1.1.0", "ticket_ref": "CHG-101", "test_evidence": "CI run 512 green", "rollback_plan": "redeploy 1.0.0", "rule_change": True}
    assert sa.post("/api/v1/admin/releases", json=body).status_code == 400  # rule change without regression evidence
    rel = sa.post("/api/v1/admin/releases", json={**body, "regression_evidence": "golden run R-77: 30/30"}).json()
    assert sa.post(f"/api/v1/admin/releases/{rel['id']}/deployed").status_code == 409  # not approved
    assert sa.post(f"/api/v1/admin/releases/{rel['id']}/approve").status_code == 403  # developer ≠ approver
    admin = world.login("firm_admin", totp_clock)
    assert admin.post(f"/api/v1/admin/releases/{rel['id']}/approve").status_code == 200
    assert sa.post(f"/api/v1/admin/releases/{rel['id']}/deployed").json()["deployed_at"]


def test_evidence_pack_usage_and_health(world: World, totp_clock: FakeClock) -> None:
    staff = world.login("staff", totp_clock)
    upload_workbook(staff, world.engagement_id)
    staff.post(f"/api/v1/engagements/{world.engagement_id}/runs", json={"kind": "check"})
    drain(world)
    admin = world.login("firm_admin", totp_clock)
    quarter = f"{utcnow().year}Q{(utcnow().month - 1) // 3 + 1}"
    r = admin.get(f"/api/v1/admin/evidence-pack?quarter={quarter}")
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {"manifest.json", "audit_chain.json", "users_roles.csv", "runs.csv", "backups.csv", "releases.csv"} <= names
    assert json.loads(z.read("audit_chain.json"))["ok"] is True
    usage = admin.get("/api/v1/admin/usage").json()
    assert usage["rows"][0]["runs"] >= 1
    health = admin.get("/api/v1/admin/health").json()
    assert health["database"] and health["storage"] and health["engine"] == "fake" and health["audit_head"]["seq"] > 0
    assert admin.get("/api/v1/admin/evidence-pack?quarter=bad").status_code == 400


def test_backup_log_and_restore_approval(world: World, totp_clock: FakeClock) -> None:
    sa = world.login("system_admin", totp_clock)
    assert sa.post("/api/v1/admin/backups", json={"kind": "pg_full", "size": 1024, "location": "nas://pg/full", "verified": True}).status_code == 201
    req = sa.post("/api/v1/admin/backups", json={"kind": "restore_request", "details": {"engagement": "synthetic"}}).json()
    assert req["status"] == "pending_approval"
    admin = world.login("firm_admin", totp_clock)
    admin.step_up(totp_clock)
    ok = admin.post(f"/api/v1/admin/backups/{req['id']}/approve-restore", headers={"X-Action-Reason": "monthly drill"})
    assert ok.status_code == 200 and ok.json()["details_json"]["approved_by"] == world.users["firm_admin"]["id"]
    assert admin.post("/api/v1/admin/backups", json={"kind": "pg_full"}).status_code == 403
    with world.db() as db:
        assert db.execute(select(User).where(User.email == "system_admin@test.fs")).scalar_one()
