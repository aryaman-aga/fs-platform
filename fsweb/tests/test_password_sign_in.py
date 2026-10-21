"""Sign-in without an authenticator and "Create an account" (the desktop edition's defaults,
FSWEB_MFA_REQUIRED=false and FSWEB_SIGNUP_ENABLED=true): the password signs in and confirms privileged actions
(step-up, lockout, rate limit); a self-service account sees nothing until an administrator assigns a role; the
firm server keeps both refused (05 §1, E-SEC-01, E-SEC-03, E-ITGC-03)."""

from __future__ import annotations

import base64
import os
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlalchemy import select

from fsweb.db import utcnow
from fsweb.engine import FakeEngine
from fsweb.main import create_app
from fsweb.models import AuditEvent, Incident, User, UserClientRole, UserSession
from fsweb.settings import Settings
from fsweb.state import build_state
from tests.conftest import PASSWORD, Api, FakeClock, World, add_user, build_world, make_settings
from tests.test_first_run import ADMIN_PASSWORD, Fresh, body

BASE = "http://127.0.0.1:8700"
WRONG = "Not-The-Password-2026"
COLLEAGUE = {"email": "nisha@corvus.test", "name": "Nisha Kulkarni", "password": "Quayside-Ledger-Otter-62"}
REASON = {"X-Action-Reason": "give the new colleague access"}


def desktop_world(tmp: Path, clock: FakeClock, **over: Any) -> World:
    """The desktop edition as the launcher sets it up: loopback, no e-mail, no authenticator, sign-up on."""
    settings: dict[str, Any] = {"env": "offline", "kms_master_key": base64.b64encode(os.urandom(32)).decode(),
                                "public_base_url": BASE, "mfa_required": False, "signup_enabled": True}
    return build_world(tmp, clock, **(settings | over))


@pytest.fixture
def desktop(tmp_path: Path, totp_clock: FakeClock) -> Iterator[World]:
    w = desktop_world(tmp_path, totp_clock)
    yield w
    w.state.db.dispose()


def browser(w: World) -> TestClient:
    return TestClient(w.app, base_url=BASE)


def login(w: World, email: str, password: str = PASSWORD) -> Any:
    return browser(w).post("/api/v1/auth/login", json={"email": email, "password": password})


def sign_in(w: World, email: str, password: str = PASSWORD) -> Api:
    c = browser(w)
    r = c.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200 and r.json()["status"] == "ok", r.text
    return Api(c, r.json()["csrf_token"], email)


def sign_up(w: World, **over: str) -> Any:
    return browser(w).post("/api/v1/auth/signup", json=COLLEAGUE | over)


def account(w: World, response: Any) -> Api:
    """The browser that created the account (signed in by the response's cookies)."""
    assert response.status_code == 201, response.text
    c = browser(w)
    c.cookies = response.cookies
    return Api(c, response.json()["csrf_token"])


def step_up(api: Api, **credentials: str) -> Any:
    return api.post("/api/v1/auth/step-up", json=credentials)


def error(r: Any) -> str:
    return str(r.json()["error"]["code"])


def events(w: World | Fresh, action: str) -> list[AuditEvent]:
    with w.state.db.owner_session() as db:
        stmt = select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.seq)
        return list(db.execute(stmt).scalars())


def user_by_email(w: World, email: str) -> User:
    with w.db() as db:
        u = db.execute(select(User).where(User.email == email)).scalar_one()
        db.expunge(u)
        return u


def reset_link(api: Api, w: World, role: str = "staff") -> Any:
    """A privileged action (step-up and a reason): an administrator's password-reset link for a colleague."""
    return api.post(f"/api/v1/admin/users/{w.users[role]['id']}/password-reset-link", headers=REASON)


# ------------------------------------------------------------------ sign-in without an authenticator


def test_the_password_alone_signs_in(desktop: World) -> None:
    api = sign_in(desktop, "staff@test.fs")
    me = api.get("/api/v1/auth/me").json()
    assert me["mfa_required"] is False and me["user"]["email"] == "staff@test.fs"
    assert me["permissions"][desktop.client_id]["run.check"] == "allow"  # no "mfa_required" stage in between
    assert me["session"]["auth_method"] == "password" and me["session"]["stepup_valid_until"] is not None
    assert [c["id"] for c in api.get("/api/v1/clients").json()] == [desktop.client_id]
    assert api.get(f"/api/v1/engagements/{desktop.engagement_id}").status_code == 200
    signed_in = events(desktop, "auth.login")[-1]
    assert signed_in.details == {"mfa": "off"} and signed_in.user_id == desktop.users["staff"]["id"]
    assert events(desktop, "auth.password_ok") == []
    with desktop.db() as db:
        u = db.get(User, desktop.users["staff"]["id"])
        assert u is not None and u.last_login_at is not None
        # the session records that no second factor was checked (see the switching test below)
        stmt = select(UserSession.mfa_verified).where(UserSession.user_id == u.id)
        assert db.execute(stmt).scalar_one() is False


def test_sign_in_counts_as_a_confirmation_for_five_minutes(desktop: World) -> None:
    admin = sign_in(desktop, "firm_admin@test.fs")
    assert reset_link(admin, desktop).status_code == 200  # within the step-up window of the sign-in
    with freeze_time(utcnow() + timedelta(minutes=6)):
        r = reset_link(admin, desktop)
        assert r.status_code == 403 and error(r) == "step_up_required"
        assert r.json()["error"]["message"] == "re-enter your password to confirm this administrative action"
        r = step_up(admin, password=PASSWORD)
        assert r.status_code == 200, r.text
        assert reset_link(admin, desktop).status_code == 200
    confirmed = events(desktop, "auth.step_up")[-1]
    assert confirmed.details == {"mfa": "off"} and confirmed.user_id == desktop.users["firm_admin"]["id"]


def test_password_step_up_errors(desktop: World) -> None:
    admin = sign_in(desktop, "firm_admin@test.fs")
    r = step_up(admin, code="123456")  # an authenticator code is not the password
    assert r.status_code == 400 and error(r) == "password_required"
    assert error(step_up(admin)) == "password_required"
    assert step_up(admin, password="").status_code == 422
    r = step_up(admin, password=WRONG)
    assert r.status_code == 401 and error(r) == "invalid_password"
    failed = events(desktop, "auth.stepup_failed")[-1]
    assert failed.outcome == "denied" and failed.details == {"reason": "bad_password"}
    assert user_by_email(desktop, "firm_admin@test.fs").failed_logins == 1
    assert step_up(admin, password=PASSWORD).status_code == 200
    assert user_by_email(desktop, "firm_admin@test.fs").failed_logins == 0  # the right password resets it
    no_session = browser(desktop).post("/api/v1/auth/step-up", json={"password": PASSWORD})
    assert no_session.status_code == 401


def test_wrong_passwords_at_step_up_lock_the_account_and_end_its_sessions(desktop: World) -> None:
    admin = sign_in(desktop, "firm_admin@test.fs")
    other = sign_in(desktop, "firm_admin@test.fs")
    for _ in range(4):
        r = step_up(admin, password=WRONG)
        assert r.status_code == 401 and error(r) == "invalid_password"
    assert admin.get("/api/v1/auth/me").status_code == 200  # not yet
    r = step_up(admin, password=WRONG)  # the fifth
    assert r.status_code == 401 and error(r) == "invalid_password"
    for api in (admin, other):  # signed out everywhere
        assert error(api.get("/api/v1/auth/me")) == "session_invalid"
    r = login(desktop, "firm_admin@test.fs")
    assert r.status_code == 401 and error(r) == "account_locked"
    assert [e.user_id for e in events(desktop, "auth.locked")] == [desktop.users["firm_admin"]["id"]]
    with freeze_time(utcnow() + timedelta(minutes=16)):
        assert sign_in(desktop, "firm_admin@test.fs").get("/api/v1/auth/me").status_code == 200


def test_step_up_is_refused_while_the_account_is_locked_or_disabled(desktop: World) -> None:
    admin = sign_in(desktop, "firm_admin@test.fs")
    for _ in range(5):  # someone guesses at the sign-in page: the account locks, the open session stays
        assert login(desktop, "firm_admin@test.fs", "guess-guess-guess").status_code == 401
    r = step_up(admin, password=PASSWORD)
    assert r.status_code == 401 and error(r) == "account_locked" and r.headers["retry-after"] == "900"
    assert events(desktop, "auth.stepup_failed")[-1].details == {"reason": "locked"}
    assert admin.get("/api/v1/auth/me").status_code == 200  # the right password is not counted as a failure
    with freeze_time(utcnow() + timedelta(minutes=16)):
        assert step_up(admin, password=PASSWORD).status_code == 200
        staff = sign_in(desktop, "staff@test.fs")
        with desktop.db() as db:  # disabled behind the API's back (the API would also end the sessions)
            u = db.get(User, desktop.users["staff"]["id"])
            assert u is not None
            u.status = "disabled"
            db.commit()
        r = step_up(staff, password=PASSWORD)
        assert r.status_code == 401 and error(r) == "account_disabled"


def test_password_step_up_is_rate_limited(desktop: World) -> None:
    admin = sign_in(desktop, "firm_admin@test.fs")
    with freeze_time(utcnow()):  # one rate-limit window
        codes = [step_up(admin, password=PASSWORD).status_code for _ in range(11)]
    assert codes == [200] * 10 + [429]


def test_breakglass_account_without_an_authenticator(desktop: World) -> None:
    """The sealed account still raises the alert, and its sealed password is replaced before anything else."""
    info = add_user(desktop, "breakglass@test.fs", "system_admin")
    with desktop.db() as db:
        u = db.get(User, info["id"])
        assert u is not None
        u.is_break_glass, u.must_change_password = True, True
        db.commit()
    api = sign_in(desktop, "breakglass@test.fs")
    with desktop.db() as db:
        assert db.execute(select(Incident).where(Incident.source == "breakglass")).first() is not None
    assert [e.user_id for e in events(desktop, "auth.breakglass_used")] == [info["id"]]
    assert api.get("/api/v1/auth/me").json()["session"]["auth_method"] == "breakglass"
    assert error(api.get("/api/v1/admin/users")) == "password_change_required"
    assert error(step_up(api, password=PASSWORD)) == "password_change_required"
    new = {"current_password": PASSWORD, "new_password": "Sealed-Envelope-Opened-81"}
    assert api.post("/api/v1/auth/password/change", json=new).status_code == 200
    assert api.get("/api/v1/admin/health").status_code == 200


# ------------------------------------------------------------------ with the authenticator (server edition)


def test_with_the_authenticator_step_up_takes_the_code_only(world: World, totp_clock: FakeClock) -> None:
    admin = world.login("firm_admin", totp_clock)
    with freeze_time(utcnow() + timedelta(minutes=6)):
        r = reset_link(admin, world)
        assert r.json()["error"]["message"] == "re-enter your MFA code to confirm this administrative action"
        r = step_up(admin, password=PASSWORD)
        assert r.status_code == 400 and error(r) == "code_required"
        assert step_up(admin, code="12345").status_code == 422
        r = step_up(admin, code="000000", password=PASSWORD)  # the password does not help
        assert r.status_code == 401 and error(r) == "mfa_invalid"
        admin.step_up(totp_clock)
        assert reset_link(admin, world).status_code == 200
    assert events(world, "auth.step_up")[-1].details == {}  # as before: no sign-in mode recorded


def test_with_the_authenticator_sign_in_still_asks_for_the_code(world: World) -> None:
    c = TestClient(world.app)
    r = c.post("/api/v1/auth/login", json={"email": "staff@test.fs", "password": PASSWORD})
    assert r.json()["status"] == "mfa_required"
    assert error(c.get("/api/v1/clients")) == "mfa_required"
    me = c.get("/api/v1/auth/me").json()
    assert me["mfa_required"] is True and me["permissions"] == {}
    assert [e.details for e in events(world, "auth.password_ok")] == [{}]


def test_switching_the_authenticator_on_asks_open_sessions_for_a_code(tmp_path: Path,
                                                                       totp_clock: FakeClock) -> None:
    """After the authenticator is switched on, open sessions need a code at their next request; a session
    waiting for its code is enough once the authenticator is switched off again."""
    w = build_world(tmp_path, totp_clock, mfa_required=False)
    settings_on = make_settings(tmp_path)  # the same database, authenticator on
    on = build_state(settings_on, engine=FakeEngine())
    app_on = create_app(settings_on, state=on)
    try:
        c = TestClient(w.app)
        r = c.post("/api/v1/auth/login", json={"email": "staff@test.fs", "password": PASSWORD})
        assert r.json()["status"] == "ok" and c.get("/api/v1/clients").status_code == 200
        later = TestClient(app_on)
        later.cookies = c.cookies
        assert error(later.get("/api/v1/clients")) == "mfa_required"
        assert later.get("/api/v1/auth/me").json()["permissions"] == {}
        code = totp_clock.code(w.users["staff"]["secret"])
        csrf = {"X-CSRF-Token": c.cookies["fs_csrf"]}
        r = later.post("/api/v1/auth/mfa/verify", json={"code": code}, headers=csrf)
        assert r.status_code == 200, r.text
        assert later.get("/api/v1/clients").status_code == 200
        # the other way round: the password given, the code not yet, then the authenticator is switched off
        pending = TestClient(app_on)
        r = pending.post("/api/v1/auth/login", json={"email": "manager@test.fs", "password": PASSWORD})
        assert r.json()["status"] == "mfa_required" and pending.get("/api/v1/clients").status_code == 401
        off = TestClient(w.app)
        off.cookies = pending.cookies
        assert [x["id"] for x in off.get("/api/v1/clients").json()] == [w.client_id]
    finally:
        on.db.dispose()
        w.state.db.dispose()


# ------------------------------------------------------------------ "Create an account"


def test_a_new_account_is_signed_in_but_sees_nothing(desktop: World) -> None:
    r = sign_up(desktop, email=" Nisha@Corvus.TEST ")
    assert r.json()["status"] == "ok" and "fs_session" in r.cookies
    nisha = account(desktop, r)
    me = nisha.get("/api/v1/auth/me").json()
    assert me["user"]["email"] == "nisha@corvus.test" and me["user"]["name"] == "Nisha Kulkarni"
    assert me["assignments"] == [] and me["permissions"] == {"*": {}} and me["mfa_required"] is False
    assert nisha.get("/api/v1/clients").json() == [] and nisha.get("/api/v1/engagements").json() == []
    assert nisha.get(f"/api/v1/clients/{desktop.client_id}").status_code == 404
    assert nisha.get(f"/api/v1/engagements/{desktop.engagement_id}").status_code == 404
    for path in ("/api/v1/admin/users", "/api/v1/admin/assignments", "/api/v1/admin/tenant-settings",
                 "/api/v1/admin/permission-matrix", "/api/v1/audit"):
        assert nisha.get(path).status_code == 403, path
    assert nisha.post("/api/v1/clients", json={"name": "Self Service Ltd"}).status_code == 403
    u = user_by_email(desktop, "nisha@corvus.test")
    assert u.status == "active" and u.is_firm_user and u.tenant_id == desktop.tenant_id
    assert not u.must_change_password and u.totp_secret_enc is None
    assert u.password_hash and COLLEAGUE["password"] not in u.password_hash
    with desktop.db() as db:
        assert db.execute(select(UserClientRole).where(UserClientRole.user_id == u.id)).first() is None
    signed_up = [(e.user_id, e.object_id, e.details) for e in events(desktop, "user.signed_up")]
    assert signed_up == [(u.id, u.id, {"via": "sign-up page", "roles": 0})]
    assert events(desktop, "auth.login")[-1].user_id == u.id
    assert sign_in(desktop, "nisha@corvus.test", COLLEAGUE["password"]).get("/api/v1/clients").json() == []


def test_an_administrator_gives_the_new_account_access(desktop: World) -> None:
    nisha = account(desktop, sign_up(desktop))
    admin = sign_in(desktop, "firm_admin@test.fs")
    listed = {u["email"]: u for u in admin.get("/api/v1/admin/users").json()}
    assert listed["nisha@corvus.test"]["status"] == "active"
    uid = listed["nisha@corvus.test"]["id"]
    grant = {"user_id": uid, "role_code": "manager", "client_id": desktop.client_id}
    r = admin.post("/api/v1/admin/assignments", headers=REASON, json=grant)
    assert r.status_code == 201, r.text
    assert [c["id"] for c in nisha.get("/api/v1/clients").json()] == [desktop.client_id]
    assert nisha.get(f"/api/v1/engagements/{desktop.engagement_id}").status_code == 200
    assert nisha.get("/api/v1/auth/me").json()["permissions"][desktop.client_id]["fix.propose"] == "allow"
    assert nisha.get(f"/api/v1/clients/{desktop.other_client_id}").status_code == 404  # only that client
    created = events(desktop, "assignment.created")[-1]
    assert created.details["user_id"] == uid and created.details["role"] == "manager"
    assert created.client_id == desktop.client_id


@pytest.mark.parametrize(("over", "status", "code"), [
    ({"email": "staff@test.fs"}, 409, "email_taken"),
    ({"email": "STAFF@Test.FS"}, 409, "email_taken"),
    ({"password": "short-pw-1"}, 400, "password_policy"),
    ({"password": "password1234"}, 400, "password_policy"),
    ({"password": "Kulkarni-Ledger-2026"}, 400, "password_policy"),  # contains the name
    ({"email": "not-an-email"}, 422, None),
    ({"name": "   "}, 422, None),
    ({"role_code": "firm_admin"}, 422, None),  # nothing but name, e-mail and password
])
def test_signup_validation(desktop: World, over: dict[str, str], status: int, code: str | None) -> None:
    with desktop.db() as db:
        before = len(db.execute(select(User.id)).all())
    r = sign_up(desktop, **over)
    assert r.status_code == status, r.text
    if code:
        assert error(r) == code
    with desktop.db() as db:
        assert len(db.execute(select(User.id)).all()) == before
    assert events(desktop, "user.signed_up") == []


def test_signup_is_rate_limited(desktop: World) -> None:
    with freeze_time(utcnow()):  # one rate-limit window; every browser on the PC is the same address
        codes = [sign_up(desktop, email=f"n{i}@corvus.test", password="short").status_code for i in range(7)]
    assert codes == [400] * 5 + [429, 429]


def test_signup_is_refused_where_it_is_not_switched_on(world: World, tmp_path: Path) -> None:
    c = TestClient(world.app)
    assert c.get("/api/v1/auth/options").json()["signup"] is False
    r = c.post("/api/v1/auth/signup", json=COLLEAGUE)
    assert r.status_code == 403 and error(r) == "signup_disabled"
    with world.db() as db:
        assert db.execute(select(User).where(User.email == COLLEAGUE["email"])).first() is None
    # the firm server refuses it even if the setting slipped through (from_env refuses it at start-up)
    (tmp_path / "prod").mkdir()
    prod = Fresh(tmp_path / "prod", env="prod", signup_enabled=True, mfa_required=False)
    try:
        c = prod.client()
        assert c.get("/api/v1/auth/options").json()["signup"] is False
        assert error(c.post("/api/v1/auth/signup", json=COLLEAGUE)) == "signup_disabled"
    finally:
        prod.state.db.dispose()


def test_signup_before_the_firm_is_set_up(tmp_path: Path) -> None:
    f = Fresh(tmp_path, mfa_required=False, signup_enabled=True)
    try:
        c = f.client()
        assert c.get("/api/v1/auth/options").json()["setup_required"] is True
        r = c.post("/api/v1/auth/signup", json=COLLEAGUE)
        assert r.status_code == 409 and error(r) == "setup_required"
    finally:
        f.state.db.dispose()


def test_signup_with_the_authenticator_on_enrols_one(tmp_path: Path, totp_clock: FakeClock) -> None:
    w = desktop_world(tmp_path, totp_clock, mfa_required=True)
    try:
        r = sign_up(w)
        assert r.status_code == 201 and r.json()["status"] == "mfa_enrollment_required"
        nisha = account(w, r)
        assert error(nisha.get("/api/v1/clients")) == "mfa_required"
        assert events(w, "auth.login") == []  # signed in only with the authenticator
        enrol = nisha.post("/api/v1/auth/mfa/enroll").json()
        r = nisha.post("/api/v1/auth/mfa/enroll/confirm", json={"code": totp_clock.code(enrol["secret"])})
        assert r.status_code == 200, r.text
        nisha.csrf = r.json()["csrf_token"]
        assert nisha.get("/api/v1/clients").json() == []
    finally:
        w.state.db.dispose()


# ------------------------------------------------------------------ settings, options and messages per mode


def test_the_firm_server_refuses_password_only_sign_in_and_signup() -> None:
    keys = {"FSWEB_SECRET_KEY": "x" * 40, "FSWEB_KMS_MASTER_KEY": base64.b64encode(b"k" * 32).decode()}
    prod = keys | {"FSWEB_ENV": "prod", "FSWEB_COOKIE_SECURE": "true",
                   "FSWEB_DATABASE_URL": "postgresql+psycopg://u:p@db/fsweb"}
    s = Settings.from_env(prod)
    assert s.mfa_required is True and s.signup_enabled is False
    with pytest.raises(RuntimeError, match="FSWEB_MFA_REQUIRED cannot be switched off"):
        Settings.from_env(prod | {"FSWEB_MFA_REQUIRED": "false"})
    with pytest.raises(RuntimeError, match="FSWEB_SIGNUP_ENABLED is for the desktop edition"):
        Settings.from_env(prod | {"FSWEB_SIGNUP_ENABLED": "true"})
    assert Settings.from_env(prod | {"FSWEB_MFA_REQUIRED": "on", "FSWEB_SIGNUP_ENABLED": "no"}).mfa_required
    desktop = keys | {"FSWEB_ENV": "offline"}
    s = Settings.from_env(desktop | {"FSWEB_MFA_REQUIRED": "false", "FSWEB_SIGNUP_ENABLED": "true"})
    assert s.mfa_required is False and s.signup_enabled is True
    s = Settings.from_env(desktop | {"FSWEB_MFA_REQUIRED": "", "FSWEB_SIGNUP_ENABLED": ""})  # the defaults
    assert s.mfa_required is True and s.signup_enabled is False


def test_options_tell_the_sign_in_page_what_to_offer(desktop: World) -> None:
    opts = browser(desktop).get("/api/v1/auth/options").json()  # the server edition's: test_first_run.py
    assert opts == {"setup_required": False, "sso": False, "edition": "desktop", "self_service_reset": False,
                    "mfa_required": False, "signup": True}


@pytest.mark.parametrize(("mfa", "message", "status"), [
    (False, "FS-Platform is set up. Sign in with your e-mail and password.", "ok"),
    (True, "FS-Platform is set up. Sign in, then scan the QR code with your authenticator app.",
     "mfa_enrollment_required"),
])
def test_first_run_per_mode(tmp_path: Path, mfa: bool, message: str, status: str) -> None:
    f = Fresh(tmp_path, mfa_required=mfa, signup_enabled=not mfa)
    try:
        c = f.client()
        r = c.post("/api/v1/setup", json=body())
        assert r.status_code == 201 and r.json()["message"] == message
        assert [e.details["mfa"] for e in events(f, "tenant.setup_completed")] == [
            "enrol at first sign-in" if mfa else "off"]
        r = c.post("/api/v1/auth/login", json={"email": "asha@corvus.test", "password": ADMIN_PASSWORD})
        assert r.json()["status"] == status
        me = c.get("/api/v1/auth/me").json()
        assert me["mfa_required"] is mfa
        # without the authenticator the administrator works at once; with it, after the QR code
        assert ("users.manage" in me["permissions"].get("*", {})) is not mfa
    finally:
        f.state.db.dispose()


def test_invite_and_mfa_reset_messages_without_an_authenticator(desktop: World) -> None:
    admin = sign_in(desktop, "firm_admin@test.fs")
    r = admin.post("/api/v1/admin/users/invite", json={"email": "ravi@corvus.test", "name": "Ravi Iyer"})
    token = r.json()["invite_link"].split("token=")[1]
    accept = {"token": token, "password": COLLEAGUE["password"]}
    r = browser(desktop).post("/api/v1/auth/invite/accept", json=accept)
    assert r.status_code == 200 and r.json()["message"] == "Your password is set. Sign in with your e-mail and password."
    assert sign_in(desktop, "ravi@corvus.test", COLLEAGUE["password"]).get("/api/v1/clients").json() == []
    r = admin.post(f"/api/v1/admin/users/{desktop.users['staff']['id']}/mfa-reset", headers=REASON)
    assert r.status_code == 200 and r.json()["message"].endswith("once it is switched on")
