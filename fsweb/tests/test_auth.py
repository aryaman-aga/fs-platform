"""Login, MFA, sessions, CSRF, lockout, invites, password policy, step-up, SSO, break-glass (05 §1, E-SEC-01/03)."""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

import pyotp
import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlalchemy import select

from fsweb.db import utcnow
from fsweb.models import AuditEvent, EmailOutbox, Incident, User, UserSession
from tests.conftest import PASSWORD, FakeClock, World, add_user, login


def test_login_requires_mfa_then_me(world: World, totp_clock: FakeClock) -> None:
    c = TestClient(world.app)
    r = c.post("/api/v1/auth/login", json={"email": "staff@test.fs", "password": PASSWORD})
    assert r.status_code == 200
    assert r.json()["status"] == "mfa_required"
    csrf = r.json()["csrf_token"]
    # The session exists but is not usable until MFA succeeds.
    r = c.get("/api/v1/engagements")
    assert r.status_code == 401 and r.json()["error"]["code"] == "mfa_required"
    r = c.post("/api/v1/auth/mfa/verify", json={"code": "000000"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 401 and r.json()["error"]["code"] == "mfa_invalid"
    r = c.post("/api/v1/auth/mfa/verify", json={"code": totp_clock.code(world.users["staff"]["secret"])}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    me = c.get("/api/v1/auth/me").json()
    assert me["user"]["email"] == "staff@test.fs"
    assert me["permissions"][world.client_id]["run.check"] == "allow"
    assert "fix.approve" not in me["permissions"][world.client_id]
    assert me["permissions"][world.client_id]["narrative.edit"] == "maker_checker"


def test_session_cookie_flags(world: World) -> None:
    c = TestClient(world.app)
    r = c.post("/api/v1/auth/login", json={"email": "staff@test.fs", "password": PASSWORD})
    cookies = r.headers.get_list("set-cookie")
    session = next(h for h in cookies if h.startswith("fs_session="))
    csrf = next(h for h in cookies if h.startswith("fs_csrf="))
    assert "HttpOnly" in session and "SameSite=strict" in session.replace("Strict", "strict")
    assert "HttpOnly" not in csrf


def test_totp_replay_is_rejected(world: World, totp_clock: FakeClock) -> None:
    secret = world.users["staff"]["secret"]
    a = TestClient(world.app)
    r = a.post("/api/v1/auth/login", json={"email": "staff@test.fs", "password": PASSWORD})
    code = totp_clock.code(secret)
    assert a.post("/api/v1/auth/mfa/verify", json={"code": code}, headers={"X-CSRF-Token": r.json()["csrf_token"]}).status_code == 200
    b = TestClient(world.app)
    r = b.post("/api/v1/auth/login", json={"email": "staff@test.fs", "password": PASSWORD})
    assert b.post("/api/v1/auth/mfa/verify", json={"code": code}, headers={"X-CSRF-Token": r.json()["csrf_token"]}).status_code == 401


def test_lockout_after_five_failures(world: World, totp_clock: FakeClock) -> None:
    c = TestClient(world.app)
    for _ in range(5):
        r = c.post("/api/v1/auth/login", json={"email": "manager@test.fs", "password": "wrong-password-xyz"})
        assert r.status_code == 401
    r = c.post("/api/v1/auth/login", json={"email": "manager@test.fs", "password": PASSWORD})
    assert r.status_code == 401 and r.json()["error"]["code"] == "account_locked"
    with world.db() as db:
        actions = [a for (a,) in db.execute(select(AuditEvent.action).where(AuditEvent.user_id == world.users["manager"]["id"])).all()]
    assert "auth.locked" in actions and actions.count("auth.login_failed") >= 5
    # After 15 minutes the lock expires.
    with freeze_time(utcnow() + timedelta(minutes=16)):
        r = c.post("/api/v1/auth/login", json={"email": "manager@test.fs", "password": PASSWORD})
        assert r.status_code == 200


def test_unknown_user_and_wrong_password_look_the_same(world: World) -> None:
    c = TestClient(world.app)
    a = c.post("/api/v1/auth/login", json={"email": "nobody@test.fs", "password": "whatever-password-1"})
    b = c.post("/api/v1/auth/login", json={"email": "staff@test.fs", "password": "whatever-password-1"})
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["code"] == b.json()["error"]["code"] == "invalid_credentials"


def test_csrf_required_for_unsafe_methods(world: World, totp_clock: FakeClock) -> None:
    api = world.login("partner", totp_clock)
    r = api.client.post("/api/v1/clients", json={"name": "New Synthetic Client"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = api.client.post("/api/v1/clients", json={"name": "New Synthetic Client"}, headers={"X-CSRF-Token": "forged"})
    assert r.status_code == 403
    assert api.post("/api/v1/clients", json={"name": "New Synthetic Client"}).status_code == 201


def test_idle_and_absolute_timeouts(world: World, totp_clock: FakeClock) -> None:
    firm = world.login("staff", totp_clock)
    client_user = world.login("client_viewer", totp_clock)
    start = utcnow()
    with freeze_time(start + timedelta(minutes=35)):
        # Client users: 30-minute idle timeout; firm users: 60 minutes.
        assert client_user.get("/api/v1/auth/me").status_code == 401
        assert firm.get("/api/v1/auth/me").status_code == 200
    fresh = world.login("manager", totp_clock)
    for minutes in range(50, 12 * 60, 50):  # keep the session active...
        with freeze_time(start + timedelta(minutes=minutes)):
            assert fresh.get("/api/v1/auth/me").status_code == 200
    token = fresh.client.cookies.get("fs_session")
    with freeze_time(start + timedelta(hours=12, minutes=5)):  # ...but 12 h is absolute (enforced server-side too)
        r = TestClient(world.app).get("/api/v1/auth/me", headers={"Cookie": f"fs_session={token}"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "session_expired"


def test_logout_all_devices(world: World, totp_clock: FakeClock) -> None:
    a = world.login("staff", totp_clock)
    b = world.login("staff", totp_clock)
    assert a.post("/api/v1/auth/logout-all").status_code == 200
    assert b.get("/api/v1/auth/me").status_code == 401
    assert a.get("/api/v1/auth/me").status_code == 401


def test_disabled_user_loses_access_immediately(world: World, totp_clock: FakeClock) -> None:
    staff = world.login("staff", totp_clock)
    admin = world.login("firm_admin", totp_clock)
    uid = world.users["staff"]["id"]
    with freeze_time(utcnow() + timedelta(minutes=6)):  # the sign-in MFA no longer counts as a recent step-up
        r = admin.post(f"/api/v1/admin/users/{uid}/disable", headers={"X-Action-Reason": "left the firm"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "step_up_required"
    admin.step_up(totp_clock)
    r = admin.post(f"/api/v1/admin/users/{uid}/disable")
    assert r.status_code == 400 and r.json()["error"]["code"] == "reason_required"
    r = admin.post(f"/api/v1/admin/users/{uid}/disable", headers={"X-Action-Reason": "left the firm"})
    assert r.status_code == 200 and r.json()["status"] == "disabled"
    assert staff.get("/api/v1/engagements").status_code == 401
    r = TestClient(world.app).post("/api/v1/auth/login", json={"email": "staff@test.fs", "password": PASSWORD})
    assert r.status_code == 401


def test_invite_flow_single_use_and_expiry(world: World, totp_clock: FakeClock) -> None:
    admin = world.login("firm_admin", totp_clock)
    r = admin.post("/api/v1/admin/users/invite", json={"email": "new.staff@test.fs", "name": "New Staff", "role_code": "staff", "client_id": world.client_id})
    assert r.status_code == 201, r.text
    token = r.json()["invite_link"].split("token=")[1]
    anon = TestClient(world.app)
    weak = anon.post("/api/v1/auth/invite/accept", json={"token": token, "password": "password1234"})
    assert weak.status_code == 400 and weak.json()["error"]["code"] == "password_policy"
    assert anon.post("/api/v1/auth/invite/accept", json={"token": token, "password": "Brand-New-Ledger-77"}).status_code == 200
    again = anon.post("/api/v1/auth/invite/accept", json={"token": token, "password": "Brand-New-Ledger-78"})
    assert again.status_code == 400
    # First sign-in: password ok, then mandatory authenticator enrolment.
    r = anon.post("/api/v1/auth/login", json={"email": "new.staff@test.fs", "password": "Brand-New-Ledger-77"})
    assert r.json()["status"] == "mfa_enrollment_required"
    csrf = r.json()["csrf_token"]
    assert anon.get("/api/v1/engagements").status_code == 401
    enrol = anon.post("/api/v1/auth/mfa/enroll", headers={"X-CSRF-Token": csrf}).json()
    assert enrol["otpauth_uri"].startswith("otpauth://totp/") and enrol["qr_svg_data_uri"].startswith("data:image/svg+xml")
    r = anon.post("/api/v1/auth/mfa/enroll/confirm", json={"code": totp_clock.code(enrol["secret"])}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 10
    assert anon.get("/api/v1/engagements").status_code == 200
    # An expired invite cannot be used.
    r = admin.post("/api/v1/admin/users/invite", json={"email": "late@test.fs", "name": "Late Comer"})
    token2 = r.json()["invite_link"].split("token=")[1]
    with freeze_time(utcnow() + timedelta(hours=73)):
        assert anon.post("/api/v1/auth/invite/accept", json={"token": token2, "password": "Brand-New-Ledger-79"}).status_code == 400


def test_recovery_code_works_once(world: World, totp_clock: FakeClock) -> None:
    info = add_user(world, "rc.user@test.fs", "staff", client_id=world.client_id)
    with world.db() as db:  # drop the TOTP so the user enrols and receives recovery codes
        u = db.get(User, info["id"])
        assert u is not None
        u.totp_secret_enc = None
        db.commit()
    c = TestClient(world.app)
    csrf = c.post("/api/v1/auth/login", json={"email": "rc.user@test.fs", "password": PASSWORD}).json()["csrf_token"]
    enrol = c.post("/api/v1/auth/mfa/enroll", headers={"X-CSRF-Token": csrf}).json()
    codes = c.post("/api/v1/auth/mfa/enroll/confirm", json={"code": totp_clock.code(enrol["secret"])}, headers={"X-CSRF-Token": csrf}).json()["recovery_codes"]
    d = TestClient(world.app)
    csrf = d.post("/api/v1/auth/login", json={"email": "rc.user@test.fs", "password": PASSWORD}).json()["csrf_token"]
    assert d.post("/api/v1/auth/mfa/verify", json={"code": codes[0]}, headers={"X-CSRF-Token": csrf}).status_code == 200
    e = TestClient(world.app)
    csrf = e.post("/api/v1/auth/login", json={"email": "rc.user@test.fs", "password": PASSWORD}).json()["csrf_token"]
    assert e.post("/api/v1/auth/mfa/verify", json={"code": codes[0]}, headers={"X-CSRF-Token": csrf}).status_code == 401


@pytest.mark.parametrize(("password", "ok"), [("short-pw-1", False), ("password1234", False), ("Password@1234", False),
                                              ("staffstaff-ledger-9", False), ("Tidy-Ledger-Ninety-9", True)])
def test_password_policy(world: World, totp_clock: FakeClock, password: str, ok: bool) -> None:
    api = world.login("staff", totp_clock)
    r = api.post("/api/v1/auth/password/change", json={"current_password": PASSWORD, "new_password": password})
    assert (r.status_code == 200) is ok, r.text


def test_password_reset_revokes_sessions(world: World, totp_clock: FakeClock) -> None:
    api = world.login("reviewer", totp_clock)
    anon = TestClient(world.app)
    assert anon.post("/api/v1/auth/password/forgot", json={"email": "reviewer@test.fs"}).status_code == 200
    assert anon.post("/api/v1/auth/password/forgot", json={"email": "ghost@test.fs"}).status_code == 200
    with world.db() as db:
        mail = db.execute(select(EmailOutbox).where(EmailOutbox.kind == "password_reset")).scalars().one()
    token = mail.body.split("token=")[1].strip()
    assert anon.post("/api/v1/auth/password/reset", json={"token": token, "password": "Fresh-Ledger-Reset-1"}).status_code == 200
    assert api.get("/api/v1/auth/me").status_code == 401
    assert anon.post("/api/v1/auth/password/reset", json={"token": token, "password": "Fresh-Ledger-Reset-2"}).status_code == 400
    assert login(world.app, "reviewer@test.fs", "Fresh-Ledger-Reset-1", world.users["reviewer"]["secret"], totp_clock)


def test_rate_limit_on_login(world: World) -> None:
    c = TestClient(world.app)
    codes = [c.post("/api/v1/auth/login", json={"email": f"x{i}@test.fs", "password": "nope-nope-nope"}).status_code for i in range(25)]
    assert 429 in codes


def test_breakglass_use_raises_alert(world: World, totp_clock: FakeClock) -> None:
    info = add_user(world, "breakglass@test.fs", "system_admin")
    with world.db() as db:
        u = db.get(User, info["id"])
        assert u is not None
        u.is_break_glass = True
        db.commit()
    login(world.app, "breakglass@test.fs", PASSWORD, info["secret"], totp_clock)
    with world.db() as db:
        assert db.execute(select(Incident).where(Incident.source == "breakglass")).scalars().first() is not None
        assert db.execute(select(AuditEvent).where(AuditEvent.action == "auth.breakglass_used")).scalars().first() is not None
        assert db.execute(select(EmailOutbox).where(EmailOutbox.kind == "alert")).scalars().first() is not None


def test_system_admin_has_no_client_data(world: World, totp_clock: FakeClock) -> None:
    sa = world.login("system_admin", totp_clock)
    assert sa.get("/api/v1/engagements").json() == []
    assert sa.get(f"/api/v1/engagements/{world.engagement_id}").status_code == 404
    assert sa.get("/api/v1/admin/health").status_code == 200


def test_jit_elevation_needs_another_admin(world: World, totp_clock: FakeClock) -> None:
    second = add_user(world, "admin2@test.fs", "firm_admin")
    staff = world.login("staff", totp_clock)
    r = staff.post("/api/v1/admin/elevations", json={"role_code": "manager", "client_id": world.client_id, "reason": "cover for leave", "duration_minutes": 30})
    assert r.status_code == 201
    eid = r.json()["id"]
    admin = world.login("firm_admin", totp_clock)
    admin.step_up(totp_clock)
    ok = admin.post(f"/api/v1/admin/elevations/{eid}/approve", headers={"X-Action-Reason": "manager on leave"})
    assert ok.status_code == 200 and ok.json()["status"] == "active"
    me = staff.get("/api/v1/auth/me").json()
    assert me["permissions"][world.client_id].get("fix.approve") == "not_own"
    # Self-approval of an elevation is refused.
    a2 = login(world.app, second["email"], PASSWORD, second["secret"], totp_clock)
    own = a2.post("/api/v1/admin/elevations", json={"role_code": "partner", "client_id": world.client_id, "reason": "self service", "duration_minutes": 30}).json()
    a2.step_up(totp_clock)
    r = a2.post(f"/api/v1/admin/elevations/{own['id']}/approve", headers={"X-Action-Reason": "trying"})
    assert r.status_code == 403
    # Elevation expires.
    with freeze_time(utcnow() + timedelta(minutes=31)):
        fresh = world.login("staff", totp_clock)
        assert "fix.approve" not in fresh.get("/api/v1/auth/me").json()["permissions"][world.client_id]


# ------------------------------------------------------------------ OIDC SSO with a mocked IdP


def _idp(monkeypatch: pytest.MonkeyPatch, *, email: str, amr: list[str], nonce_override: str | None = None) -> dict[str, Any]:
    from cryptography.hazmat.primitives.asymmetric import rsa
    from joserfc import jwt
    from joserfc.jwk import RSAKey

    from fsweb.security import oidc

    key = RSAKey.import_key(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    jwks = {"keys": [key.as_dict(private=False) | {"kid": "k1", "use": "sig", "alg": "RS256"}]}
    state: dict[str, Any] = {}

    def discovery(self: oidc.OIDCClient) -> dict[str, Any]:
        return {"issuer": "https://idp.example.test", "authorization_endpoint": "https://idp.example.test/authorize",
                "token_endpoint": "https://idp.example.test/token", "jwks_uri": "https://idp.example.test/jwks"}

    def exchange(self: oidc.OIDCClient, code: str, verifier: str) -> dict[str, Any]:
        now = int(time.time())
        claims = {"iss": "https://idp.example.test", "aud": "fs-platform", "sub": "sub-123", "email": email, "email_verified": True,
                  "iat": now, "exp": now + 300, "nonce": nonce_override or state["nonce"], "amr": amr}
        return {"id_token": jwt.encode({"alg": "RS256", "kid": "k1"}, claims, key)}

    monkeypatch.setattr(oidc.OIDCClient, "discovery", discovery)
    monkeypatch.setattr(oidc.OIDCClient, "jwks", lambda self: jwks)
    monkeypatch.setattr(oidc.OIDCClient, "exchange", exchange)
    return state


def _sso_world(tmp_path: Any, clock: FakeClock) -> World:
    from tests.conftest import build_world

    return build_world(tmp_path, clock, oidc_issuer="https://idp.example.test", oidc_client_id="fs-platform", oidc_client_secret="s3cret")


def test_oidc_sso_login(tmp_path: Any, totp_clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _sso_world(tmp_path, totp_clock)
    state = _idp(monkeypatch, email="manager@test.fs", amr=["pwd", "mfa"])
    c = TestClient(w.app, follow_redirects=False)
    r = c.get("/api/v1/auth/oidc/login")
    assert r.status_code == 302 and "code_challenge=" in r.headers["location"]
    from urllib.parse import parse_qs, urlparse

    q = parse_qs(urlparse(r.headers["location"]).query)
    state["nonce"] = q["nonce"][0]
    r = c.get(f"/api/v1/auth/oidc/callback?code=abc&state={q['state'][0]}")
    assert r.status_code == 302 and r.headers["location"].endswith("/")
    assert c.get("/api/v1/auth/me").json()["session"]["auth_method"] == "oidc"


def test_oidc_rejects_bad_nonce_and_unknown_user(tmp_path: Any, totp_clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _sso_world(tmp_path, totp_clock)
    _idp(monkeypatch, email="manager@test.fs", amr=["mfa"], nonce_override="evil")
    c = TestClient(w.app, follow_redirects=False)
    r = c.get("/api/v1/auth/oidc/login")
    from urllib.parse import parse_qs, urlparse

    q = parse_qs(urlparse(r.headers["location"]).query)
    assert c.get(f"/api/v1/auth/oidc/callback?code=abc&state={q['state'][0]}").status_code == 401
    assert c.get("/api/v1/auth/oidc/callback?code=abc&state=forged").status_code == 400


def test_oidc_without_idp_mfa_requires_local_totp(tmp_path: Any, totp_clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _sso_world(tmp_path, totp_clock)
    state = _idp(monkeypatch, email="staff@test.fs", amr=["pwd"])
    c = TestClient(w.app, follow_redirects=False)
    r = c.get("/api/v1/auth/oidc/login")
    from urllib.parse import parse_qs, urlparse

    q = parse_qs(urlparse(r.headers["location"]).query)
    state["nonce"] = q["nonce"][0]
    r = c.get(f"/api/v1/auth/oidc/callback?code=abc&state={q['state'][0]}")
    assert r.headers["location"].endswith("/mfa")
    assert c.get("/api/v1/engagements").status_code == 401


def test_sessions_list_and_revoke(world: World, totp_clock: FakeClock) -> None:
    a = world.login("staff", totp_clock)
    b = world.login("staff", totp_clock)
    sessions = a.get("/api/v1/auth/sessions").json()
    assert len(sessions) == 2
    other = next(s for s in sessions if not s["current"])
    assert a.delete(f"/api/v1/auth/sessions/{other['id']}").status_code == 200
    assert b.get("/api/v1/auth/me").status_code == 401
    with world.db() as db:
        assert db.execute(select(UserSession).where(UserSession.revoked_at.is_not(None))).scalars().first() is not None


def test_pyotp_sanity() -> None:
    assert len(pyotp.random_base32()) == 32
