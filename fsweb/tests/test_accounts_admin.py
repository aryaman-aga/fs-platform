"""Accounts without e-mail: invitation links handed over in person, administrator-issued password-reset links, the
forced change of one-time passwords, the administrator who also works on engagements (auto-assigned Partner, flagged in
access reviews) and the desktop backup copy folder."""

from __future__ import annotations

import base64
import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlalchemy import select

from fsweb import audit
from fsweb.db import utcnow
from fsweb.models import AuditEvent, BackupLog, EmailOutbox, RecoveryCode, Role, Tenant, User, UserClientRole
from fsweb.services import accounts
from fsweb.services.seed import bootstrap, make_user
from tests.conftest import PASSWORD, Api, FakeClock, World, add_user, build_world, login

NEW_PASSWORD = "Harbour-Ledger-Spruce-41"


def reason(text: str = "colleague locked out at the front desk") -> dict[str, str]:
    return {"X-Action-Reason": text}


def events(w: World, action: str) -> list[AuditEvent]:
    with w.db() as db:
        return list(db.execute(select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.seq)).scalars())


@pytest.fixture
def desktop(tmp_path: Path, totp_clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    """The desktop edition (FSWEB_ENV=offline): loopback only, no e-mail."""
    monkeypatch.setenv("FSWEB_SMTP_HOST", "smtp.corvus.test")  # ignored offline
    w = build_world(tmp_path, totp_clock, env="offline", kms_master_key=base64.b64encode(os.urandom(32)).decode(),
                    public_base_url="http://127.0.0.1:8700")
    yield w
    w.state.db.dispose()


def local_login(w: World, email: str, password: str, secret: str | None, clock: FakeClock) -> Api:
    c = TestClient(w.app, base_url="http://127.0.0.1:8700")
    r = c.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    csrf = r.json()["csrf_token"]
    if secret:
        r = c.post("/api/v1/auth/mfa/verify", json={"code": clock.code(secret)}, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, r.text
        csrf = r.json()["csrf_token"]
    return Api(c, csrf, email, secret or "")


# ------------------------------------------------------------------ invitations without e-mail


def test_desktop_invite_link_leads_through_password_and_qr_enrolment(desktop: World, totp_clock: FakeClock) -> None:
    u = desktop.users["firm_admin"]
    admin = local_login(desktop, u["email"], PASSWORD, u["secret"], totp_clock)
    r = admin.post("/api/v1/admin/users/invite", json={"email": "meera@corvus.test", "name": "Meera Pillai", "role_code": "staff",
                                                       "client_id": desktop.client_id})
    assert r.status_code == 201, r.text
    link = r.json()["invite_link"]
    assert link.startswith("http://127.0.0.1:8700/invite?token=")  # opens on this PC
    with desktop.db() as db:
        assert db.execute(select(EmailOutbox).where(EmailOutbox.kind == "invite")).scalars().first() is None  # no e-mail offline
    assert events(desktop, "user.invited")[-1].details["delivery"] == "link"
    # the colleague, on the same PC: password, then the authenticator (QR code), then work
    colleague = TestClient(desktop.app, base_url="http://127.0.0.1:8700")
    token = link.split("token=")[1]
    assert colleague.post("/api/v1/auth/invite/accept", json={"token": token, "password": NEW_PASSWORD}).status_code == 200
    r = colleague.post("/api/v1/auth/login", json={"email": "meera@corvus.test", "password": NEW_PASSWORD})
    assert r.json()["status"] == "mfa_enrollment_required"
    csrf = r.json()["csrf_token"]
    enrol = colleague.post("/api/v1/auth/mfa/enroll", headers={"X-CSRF-Token": csrf}).json()
    assert enrol["qr_svg_data_uri"].startswith("data:image/svg+xml")
    r = colleague.post("/api/v1/auth/mfa/enroll/confirm", json={"code": totp_clock.code(enrol["secret"])}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 10
    me = colleague.get("/api/v1/auth/me").json()
    assert me["user"]["email"] == "meera@corvus.test" and me["permissions"][desktop.client_id]["file.upload"] == "allow"


def test_desktop_forgot_password_creates_no_token(desktop: World) -> None:
    from fsweb.models import PasswordReset

    r = TestClient(desktop.app, base_url="http://127.0.0.1:8700").post("/api/v1/auth/password/forgot", json={"email": "staff@test.fs"})
    assert r.status_code == 200 and "ask your administrator" in r.json()["message"]
    with desktop.db() as db:
        assert db.execute(select(PasswordReset)).first() is None
        assert db.execute(select(EmailOutbox)).first() is None


def test_server_with_smtp_e_mails_the_invite(tmp_path: Path, totp_clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FSWEB_SMTP_HOST", "smtp.corvus.test")
    w = build_world(tmp_path, totp_clock, env="prod")  # Settings built directly: the prod checks of from_env do not apply
    admin = w.login("firm_admin", totp_clock)
    r = admin.post("/api/v1/admin/users/invite", json={"email": "kiran@corvus.test", "name": "Kiran Shah"})
    assert r.status_code == 201 and r.json()["invite_link"] is None
    with w.db() as db:
        assert db.execute(select(EmailOutbox).where(EmailOutbox.kind == "invite")).scalars().one().to_addr == "kiran@corvus.test"


def test_server_without_smtp_shows_the_invite_link(tmp_path: Path, totp_clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FSWEB_SMTP_HOST", raising=False)
    w = build_world(tmp_path, totp_clock, env="prod")
    r = w.login("firm_admin", totp_clock).post("/api/v1/admin/users/invite", json={"email": "kiran@corvus.test", "name": "Kiran Shah"})
    assert r.status_code == 201 and "/invite?token=" in r.json()["invite_link"]


# ------------------------------------------------------------------ administrator password reset


def test_reset_link_sets_a_new_password(world: World, totp_clock: FakeClock) -> None:
    staff = world.login("staff", totp_clock)
    admin = world.login("firm_admin", totp_clock)
    uid = world.users["staff"]["id"]
    with freeze_time(utcnow() + timedelta(minutes=6)):  # the sign-in MFA no longer counts as a recent step-up
        r = admin.post(f"/api/v1/admin/users/{uid}/password-reset-link", headers=reason())
        assert r.status_code == 403 and r.json()["error"]["code"] == "step_up_required"
    admin.step_up(totp_clock)
    r = admin.post(f"/api/v1/admin/users/{uid}/password-reset-link")
    assert r.status_code == 400 and r.json()["error"]["code"] == "reason_required"
    r = admin.post(f"/api/v1/admin/users/{uid}/password-reset-link", headers=reason())
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["link"].startswith("http://testserver/reset-password?token=")
    assert timedelta(hours=23) < datetime.fromisoformat(out["expires_at"]) - utcnow() <= timedelta(hours=24)  # 24 hours to hand it over
    ev = events(world, "user.password_reset_link_issued")[-1]
    assert ev.object_id == uid and ev.details["reason"] == "colleague locked out at the front desk"
    assert out["link"].split("token=")[1] not in str(ev.details)  # the token is never in the audit trail
    # the colleague uses the link (the existing reset page)
    anon = TestClient(world.app)
    token = out["link"].split("token=")[1]
    assert anon.post("/api/v1/auth/password/reset", json={"token": token, "password": NEW_PASSWORD}).status_code == 200
    assert staff.get("/api/v1/auth/me").status_code == 401  # signed out everywhere
    assert anon.post("/api/v1/auth/password/reset", json={"token": token, "password": "Another-Ledger-Pass-77"}).status_code == 400
    assert login(world.app, "staff@test.fs", NEW_PASSWORD, world.users["staff"]["secret"], totp_clock)
    r = TestClient(world.app).post("/api/v1/auth/login", json={"email": "staff@test.fs", "password": PASSWORD})
    assert r.status_code == 401


def test_reset_link_needs_users_manage_and_an_enabled_account(world: World, totp_clock: FakeClock) -> None:
    partner = world.login("partner", totp_clock)
    r = partner.post(f"/api/v1/admin/users/{world.users['staff']['id']}/password-reset-link", headers=reason())
    assert r.status_code == 403 and events(world, "authz.denied")[-1].details["permission"] == "users.manage"
    admin = world.login("firm_admin", totp_clock)
    uid = world.users["reviewer"]["id"]
    assert admin.post(f"/api/v1/admin/users/{uid}/disable", headers=reason("left the firm")).status_code == 200
    r = admin.post(f"/api/v1/admin/users/{uid}/password-reset-link", headers=reason())
    assert r.status_code == 409 and r.json()["error"]["code"] == "user_disabled"
    assert admin.post("/api/v1/admin/users/no-such-user/password-reset-link", headers=reason()).status_code == 404


def test_reset_link_accepts_a_lapsed_invitation(world: World, totp_clock: FakeClock) -> None:
    admin = world.login("firm_admin", totp_clock)
    r = admin.post("/api/v1/admin/users/invite", json={"email": "late@test.fs", "name": "Late Comer"})
    uid = r.json()["user"]["id"]
    with freeze_time(utcnow() + timedelta(hours=80)):  # the invitation lapsed; the e-mail cannot be invited again
        fresh = world.login("firm_admin", totp_clock)
        link = fresh.post(f"/api/v1/admin/users/{uid}/password-reset-link", headers=reason("invitation lapsed")).json()["link"]
        anon = TestClient(world.app)
        assert anon.post("/api/v1/auth/password/reset", json={"token": link.split("token=")[1], "password": NEW_PASSWORD}).status_code == 200
        r = anon.post("/api/v1/auth/login", json={"email": "late@test.fs", "password": NEW_PASSWORD})
        assert r.status_code == 200 and r.json()["status"] == "mfa_enrollment_required"


def test_account_service_functions_for_the_recovery_cli(world: World) -> None:
    """``issue_password_reset_link`` and ``reset_mfa`` take a plain audit actor (the CLI has no session)."""
    cli = audit.Actor(world.tenant_id, None, "system:cli", None, "fsweb-manage", None)
    with world.db() as db:
        u = db.get(User, world.users["manager"]["id"])
        assert u is not None
        db.add(RecoveryCode(tenant_id=u.tenant_id, user_id=u.id, code_hash="x" * 64))
        db.flush()
        link, expires = accounts.issue_password_reset_link(db, world.state.settings, u, cli, hours=2)
        assert link.startswith("http://testserver/reset-password?token=") and timedelta(minutes=119) < expires - utcnow() <= timedelta(hours=2)
        with pytest.raises(ValueError, match="hours"):
            accounts.issue_password_reset_link(db, world.state.settings, u, cli, hours=0)
        accounts.reset_mfa(db, u, cli)
        db.commit()
        assert u.totp_secret_enc is None and u.mfa_type == "none" and u.mfa_enrolled_at is None and u.last_totp_step is None
        assert db.execute(select(RecoveryCode).where(RecoveryCode.user_id == u.id)).first() is None
        evs = {e.action: e for e in db.execute(select(AuditEvent).where(AuditEvent.object_id == u.id)).scalars()}
        assert evs["user.password_reset_link_issued"].role_at_time == "system:cli" and "reason" not in evs["user.password_reset_link_issued"].details
        assert "sessions_revoked" in evs["user.mfa_reset"].details
    anon = TestClient(world.app)
    assert anon.post("/api/v1/auth/password/reset", json={"token": link.split("token=")[1], "password": NEW_PASSWORD}).status_code == 200


def test_mfa_reset_endpoint_uses_the_service_and_records_the_reason(world: World, totp_clock: FakeClock) -> None:
    staff = world.login("staff", totp_clock)
    admin = world.login("firm_admin", totp_clock)
    uid = world.users["staff"]["id"]
    r = admin.post(f"/api/v1/admin/users/{uid}/mfa-reset", headers=reason("lost phone"))
    assert r.status_code == 200
    assert staff.get("/api/v1/auth/me").status_code == 401
    ev = events(world, "user.mfa_reset")[-1]
    assert ev.details == {"reason": "lost phone", "sessions_revoked": 1}
    r = TestClient(world.app).post("/api/v1/auth/login", json={"email": "staff@test.fs", "password": PASSWORD})
    assert r.json()["status"] == "mfa_enrollment_required"


# ------------------------------------------------------------------ forced password change


def _bootstrapped_admin(w: World) -> dict[str, Any]:
    """`fsweb.manage bootstrap` (CLI): a one-time password and a TOTP secret printed for the administrator."""
    with w.db() as db:
        res = bootstrap(db, w.state.cipher, "CLI Firm", "boss@cli.test", "Bharat Boss")
        db.commit()
    return {"email": res.admin_email, "password": res.one_time_password, "secret": res.totp_secret, "id": res.admin_user_id}


def test_one_time_password_must_be_changed_before_anything_else(world: World, totp_clock: FakeClock) -> None:
    boss = _bootstrapped_admin(world)
    api = login(world.app, boss["email"], boss["password"], boss["secret"], totp_clock)
    me = api.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["must_change_password"] is True and me.json()["user"]["must_change_password"] is True
    for method, url in (("get", "/api/v1/engagements"), ("get", "/api/v1/clients"), ("get", "/api/v1/admin/users"),
                        ("get", "/api/v1/auth/sessions"), ("post", "/api/v1/clients")):
        r = getattr(api, method)(url, **({"json": {"name": "Sneaky Ltd"}} if method == "post" else {}))
        assert r.status_code == 403 and r.json()["error"]["code"] == "password_change_required", url
    r = api.post("/api/v1/auth/password/change", json={"current_password": boss["password"], "new_password": boss["password"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "password_policy"  # not the one-time password again
    r = api.post("/api/v1/auth/password/change", json={"current_password": "wrong-password-123", "new_password": NEW_PASSWORD})
    assert r.status_code == 400
    r = api.post("/api/v1/auth/password/change", json={"current_password": boss["password"], "new_password": NEW_PASSWORD})
    assert r.status_code == 200, r.text
    assert api.get("/api/v1/auth/me").json()["must_change_password"] is False
    assert api.get("/api/v1/engagements").status_code == 200
    assert events(world, "auth.password_changed")[-1].details["replaced_one_time_password"] is True


def test_pending_change_still_allows_mfa_enrolment_and_sign_out(world: World, totp_clock: FakeClock) -> None:
    with world.db() as db:
        r = db.execute(select(Role).where(Role.tenant_id == world.tenant_id, Role.code == "manager")).scalar_one()
        u = make_user(db, world.state.cipher, world.tenant_id, "temp@test.fs", "Tara Temp", password="Temp-Ledger-Pass-2026",
                      must_change_password=True, created_by="test")
        db.add(UserClientRole(tenant_id=world.tenant_id, user_id=u.id, client_id=world.client_id, role_id=r.id))
        db.commit()
    c = TestClient(world.app)
    csrf = c.post("/api/v1/auth/login", json={"email": "temp@test.fs", "password": "Temp-Ledger-Pass-2026"}).json()["csrf_token"]
    enrol = c.post("/api/v1/auth/mfa/enroll", headers={"X-CSRF-Token": csrf}).json()
    r = c.post("/api/v1/auth/mfa/enroll/confirm", json={"code": totp_clock.code(enrol["secret"])}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    csrf = r.json()["csrf_token"]
    assert c.get(f"/api/v1/engagements/{world.engagement_id}").json()["error"]["code"] == "password_change_required"
    assert c.post("/api/v1/auth/logout-all", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert c.get("/api/v1/auth/me").status_code == 401


def test_set_passwords_do_not_need_a_change(world: World, totp_clock: FakeClock) -> None:
    """Invitation, reset link and self-chosen passwords are not one-time passwords."""
    with world.db() as db:
        u = db.get(User, world.users["staff"]["id"])
        assert u is not None and u.must_change_password is False
        u.must_change_password = True
        db.commit()
    admin = world.login("firm_admin", totp_clock)
    link = admin.post(f"/api/v1/admin/users/{world.users['staff']['id']}/password-reset-link", headers=reason()).json()["link"]
    TestClient(world.app).post("/api/v1/auth/password/reset", json={"token": link.split("token=")[1], "password": NEW_PASSWORD})
    api = login(world.app, "staff@test.fs", NEW_PASSWORD, world.users["staff"]["secret"], totp_clock)
    assert api.get("/api/v1/engagements").status_code == 200


# ------------------------------------------------------------------ administrator who works on engagements


def _works_on_engagements(w: World, clock: FakeClock, on: bool) -> Api:
    admin = w.login("firm_admin", clock)
    r = admin.put("/api/v1/admin/tenant-settings", json={"works_on_engagements": on}, headers=reason("the admin is also a partner"))
    assert r.status_code == 200 and r.json() == {"works_on_engagements": on}
    return admin


def test_firm_admin_who_works_on_engagements_becomes_partner(world: World, totp_clock: FakeClock) -> None:
    admin = _works_on_engagements(world, totp_clock, True)
    c = admin.post("/api/v1/clients", json={"name": "Kestrel Foods Private Limited"}).json()
    me = admin.get("/api/v1/auth/me").json()
    assert {a["role_code"] for a in me["assignments"] if a["client_id"] == c["id"]} == {"partner"}
    assert me["permissions"][c["id"]]["engagement.lock"] == "allow"
    ev = events(world, accounts.AUTO_PARTNER_EVENT)
    assert len(ev) == 1 and ev[0].client_id == c["id"] and ev[0].details["role"] == "partner"
    with world.db() as db:
        a = db.get(UserClientRole, ev[0].object_id)
        assert a is not None and a.user_id == world.users["firm_admin"]["id"] and a.client_id == c["id"]
    # switched off again: the next client gets no assignment
    _works_on_engagements(world, totp_clock, False)
    c2 = admin.post("/api/v1/clients", json={"name": "Osprey Logistics LLP"}).json()
    assert c2["id"] not in admin.get("/api/v1/auth/me").json()["permissions"]
    assert len(events(world, accounts.AUTO_PARTNER_EVENT)) == 1


def test_firm_admin_is_not_assigned_by_default(world: World, totp_clock: FakeClock) -> None:
    admin = world.login("firm_admin", totp_clock)
    assert admin.get("/api/v1/admin/tenant-settings").json() == {"works_on_engagements": False}
    c = admin.post("/api/v1/clients", json={"name": "Plover Paints Ltd"}).json()
    assert c["id"] not in admin.get("/api/v1/auth/me").json()["permissions"]
    # Partners and Managers who create a client are still assigned to it
    partner = world.login("partner", totp_clock)
    c = partner.post("/api/v1/clients", json={"name": "Wren Ceramics Ltd"}).json()
    assert c["id"] in partner.get("/api/v1/auth/me").json()["permissions"]
    assert events(world, accounts.AUTO_PARTNER_EVENT) == []


def test_tenant_settings_are_for_firm_admins_with_step_up(world: World, totp_clock: FakeClock) -> None:
    partner = world.login("partner", totp_clock)
    assert partner.get("/api/v1/admin/tenant-settings").status_code == 403
    assert partner.put("/api/v1/admin/tenant-settings", json={"works_on_engagements": True}, headers=reason()).status_code == 403
    admin = world.login("firm_admin", totp_clock)
    r = admin.put("/api/v1/admin/tenant-settings", json={"works_on_engagements": True})
    assert r.status_code == 400 and r.json()["error"]["code"] == "reason_required"
    with freeze_time(utcnow() + timedelta(minutes=6)):
        r = admin.put("/api/v1/admin/tenant-settings", json={"works_on_engagements": True}, headers=reason())
        assert r.status_code == 403 and r.json()["error"]["code"] == "step_up_required"
    assert admin.put("/api/v1/admin/tenant-settings", json={"works_on_engagements": True}, headers=reason()).status_code == 200
    ev = events(world, "tenant.settings_changed")[-1]
    assert ev.details == {"reason": "colleague locked out at the front desk", "works_on_engagements": True} and ev.before_hash and ev.after_hash
    with world.db() as db:
        t = db.get(Tenant, world.tenant_id)
        assert t is not None and t.settings_json["works_on_engagements"] is True and t.settings_json["ai_enabled"] is True


def test_auto_assigned_partner_is_flagged_in_access_reviews(world: World, totp_clock: FakeClock) -> None:
    admin = _works_on_engagements(world, totp_clock, True)
    c = admin.post("/api/v1/clients", json={"name": "Kestrel Foods Private Limited"}).json()
    assert admin.post("/api/v1/admin/access-reviews", json={"period": "2026Q4"}).status_code == 201
    items = admin.get("/api/v1/admin/access-reviews", params={"period": "2026Q4"}).json()
    flagged = [i for i in items if i["sod_warning"]]
    assert len(flagged) == 1
    item = flagged[0]
    assert item["client_id"] == c["id"] and item["user_id"] == world.users["firm_admin"]["id"]
    assert "made Partner automatically" in item["sod_warning"]
    assert all(i["sod_warning"] is None for i in items if i["user_id"] != world.users["firm_admin"]["id"])
    report = admin.get("/api/v1/admin/access-reviews/report", params={"period": "2026Q4"}).json()
    assert [(w["user"], w["client"], w["role"]) for w in report["sod_warnings"]] == [("firm_admin@test.fs", "Kestrel Foods Private Limited", "partner")]
    # the Firm Admin cannot certify their own assignment; another administrator decides, and still sees the flag
    assert admin.post(f"/api/v1/admin/access-reviews/{item['id']}/decide", json={"decision": "certify"}).status_code == 403
    second = add_user(world, "admin2@test.fs", "firm_admin")
    other = login(world.app, second["email"], PASSWORD, second["secret"], totp_clock)
    r = other.post(f"/api/v1/admin/access-reviews/{item['id']}/decide", json={"decision": "certify"})
    assert r.status_code == 200 and r.json()["decision"] == "certify" and r.json()["sod_warning"]


def test_firm_admin_holding_a_client_role_is_flagged(world: World, totp_clock: FakeClock) -> None:
    """A Firm Admin assigned a client role by hand is shown too (not as automatic)."""
    with world.db() as db:
        r = db.execute(select(Role).where(Role.tenant_id == world.tenant_id, Role.code == "manager")).scalar_one()
        db.add(UserClientRole(tenant_id=world.tenant_id, user_id=world.users["firm_admin"]["id"], client_id=world.client_id, role_id=r.id))
        db.commit()
    admin = world.login("firm_admin", totp_clock)
    admin.post("/api/v1/admin/access-reviews", json={"period": "2026Q4"})
    items = admin.get("/api/v1/admin/access-reviews", params={"period": "2026Q4"}).json()
    flagged = [i for i in items if i["sod_warning"]]
    assert len(flagged) == 1 and "also holds a role on this client" in flagged[0]["sod_warning"]


# ------------------------------------------------------------------ desktop backup copy folder


def test_backup_copy_folder(world: World, totp_clock: FakeClock, tmp_path: Path) -> None:
    admin = world.login("firm_admin", totp_clock)
    assert admin.get("/api/v1/admin/backup-settings").json() == {"backup_copy_dir": None, "exists": None}
    r = admin.put("/api/v1/admin/backup-settings", json={"backup_copy_dir": "backups/fs"}, headers=reason("second copy on the D drive"))
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_path"
    r = admin.put("/api/v1/admin/backup-settings", json={"backup_copy_dir": str(tmp_path / "a" / ".." / "b")}, headers=reason("second copy"))
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_path"
    r = admin.put("/api/v1/admin/backup-settings", json={"backup_copy_dir": str(tmp_path / "copy")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "reason_required"
    folder = tmp_path / "copy"
    r = admin.put("/api/v1/admin/backup-settings", json={"backup_copy_dir": f"  {folder}  "}, headers=reason("second copy on the D drive"))
    assert r.status_code == 200 and r.json() == {"backup_copy_dir": str(folder), "exists": False}
    folder.mkdir()
    assert admin.get("/api/v1/admin/backup-settings").json() == {"backup_copy_dir": str(folder), "exists": True}
    ev = events(world, "backup.settings_changed")[-1]
    assert ev.details == {"reason": "second copy on the D drive", "before": None, "after": str(folder)}
    with world.db() as db:
        t = db.get(Tenant, world.tenant_id)
        assert t is not None and t.settings_json["backup_copy_dir"] == str(folder) and t.settings_json["ai_enabled"] is True
    r = admin.put("/api/v1/admin/backup-settings", json={"backup_copy_dir": ""}, headers=reason("copy no longer wanted"))
    assert r.status_code == 200 and r.json()["backup_copy_dir"] is None
    with world.db() as db:
        t = db.get(Tenant, world.tenant_id)
        assert t is not None and "backup_copy_dir" not in t.settings_json


def test_backup_copy_folder_permissions(world: World, totp_clock: FakeClock, tmp_path: Path) -> None:
    sysadmin = world.login("system_admin", totp_clock)  # operations (ops.manage)
    assert sysadmin.get("/api/v1/admin/backup-settings").status_code == 200
    assert sysadmin.put("/api/v1/admin/backup-settings", json={"backup_copy_dir": str(tmp_path)}, headers=reason("nightly copy")).status_code == 200
    for role in ("partner", "staff", "client_viewer"):
        api = world.login(role, totp_clock)
        assert api.get("/api/v1/admin/backup-settings").status_code == 403, role
        assert api.put("/api/v1/admin/backup-settings", json={"backup_copy_dir": None}, headers=reason()).status_code == 403, role
    assert events(world, "authz.denied")[-1].details["permission"] == "ops.manage"


def test_desktop_backups_show_in_the_backup_list_and_health(world: World, totp_clock: FakeClock) -> None:
    """Rows the desktop launcher writes (kind "desktop")."""
    with world.db() as db:
        db.add(BackupLog(tenant_id=world.tenant_id, kind="desktop", status="ok", size=52_428_800, verified=True,
                         location="C:\\Users\\asha\\AppData\\Local\\FSPlatform\\offline\\backups\\fs-2026-10-21.zip",
                         details_json={"copied_to": "D:\\FS-Platform backups\\fs-2026-10-21.zip"}, created_by="launcher"))
        db.commit()
    admin = world.login("firm_admin", totp_clock)
    rows = admin.get("/api/v1/admin/backups").json()
    assert rows[0]["kind"] == "desktop" and rows[0]["details_json"]["copied_to"].startswith("D:\\")
    assert admin.get("/api/v1/admin/health").json()["last_backup"]["kind"] == "desktop"
    sysadmin = world.login("system_admin", totp_clock)
    r = sysadmin.post("/api/v1/admin/backups", json={"kind": "desktop", "status": "ok", "size": 1024, "location": "C:\\b\\x.zip"})
    assert r.status_code == 201 and r.json()["kind"] == "desktop"
