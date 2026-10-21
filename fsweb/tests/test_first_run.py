"""First run without the CLI: GET /auth/options and POST /setup (once, loopback only, one-time launcher token, rate
limit, password policy), then the administrator's first sign-in enrols the authenticator with a QR code."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlalchemy import func, select

from fsweb import audit
from fsweb.db import SYSTEM
from fsweb.engine import FakeEngine
from fsweb.main import create_app
from fsweb.models import AuditEvent, Permission, Policy, Role, RolePermission, Tenant, User, UserClientRole
from fsweb.state import AppState, build_state
from tests.conftest import FakeClock, World, make_settings

LOCAL = ("127.0.0.1", 50000)
ADMIN_PASSWORD = "Ledger-Lantern-Quay-58"


def body(**over: Any) -> dict[str, Any]:
    b: dict[str, Any] = {"firm_name": "Corvus & Rao Chartered Accountants", "admin_name": "Asha Rao", "admin_email": "asha@corvus.test",
                         "password": ADMIN_PASSWORD, "works_on_engagements": True}
    b.update(over)
    return b


class Fresh:
    """A new installation: schema, no firm."""

    def __init__(self, tmp: Path, **settings: Any) -> None:
        s = make_settings(tmp, **settings)
        self.state: AppState = build_state(s, engine=FakeEngine())
        if not self.state.db.is_postgres:
            self.state.db.create_all()
        self.app = create_app(s, state=self.state)

    def client(self, peer: tuple[str, int] = LOCAL, **kw: Any) -> TestClient:
        return TestClient(self.app, client=peer, **kw)


@pytest.fixture
def fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Fresh]:
    monkeypatch.delenv("FSWEB_SETUP_TOKEN_FILE", raising=False)
    monkeypatch.delenv("FSWEB_SMTP_HOST", raising=False)
    f = Fresh(tmp_path)
    yield f
    f.state.db.dispose()


# ------------------------------------------------------------------ options


def test_options_on_a_fresh_installation_and_after_setup(fresh: Fresh) -> None:
    c = fresh.client()
    opts = c.get("/api/v1/auth/options").json()
    assert opts == {"setup_required": True, "sso": False, "edition": "server", "self_service_reset": False,
                    "mfa_required": True, "signup": False}
    assert c.post("/api/v1/setup", json=body()).status_code == 201
    assert c.get("/api/v1/auth/options").json()["setup_required"] is False


def test_options_with_an_existing_firm(world: World) -> None:
    assert TestClient(world.app).get("/api/v1/auth/options").json()["setup_required"] is False


def test_options_sso_and_self_service_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FSWEB_SMTP_HOST", "smtp.corvus.test")
    f = Fresh(tmp_path, oidc_issuer="https://idp.example.test", oidc_client_id="fs-platform")
    opts = f.client().get("/api/v1/auth/options").json()
    assert opts["sso"] is True and opts["self_service_reset"] is True and opts["edition"] == "server"


def test_options_desktop_edition_never_offers_email_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import base64
    import os

    monkeypatch.setenv("FSWEB_SMTP_HOST", "smtp.corvus.test")  # even if someone sets it: no e-mail offline
    f = Fresh(tmp_path, env="offline", kms_master_key=base64.b64encode(os.urandom(32)).decode())
    opts = f.client(base_url="http://127.0.0.1:8700").get("/api/v1/auth/options").json()
    # Settings' defaults; the launcher switches off the authenticator, on sign-up (test_password_sign_in.py)
    assert opts == {"setup_required": True, "sso": False, "edition": "desktop", "self_service_reset": False,
                    "mfa_required": True, "signup": False}


def test_setup_is_not_offered_on_the_firm_server(tmp_path: Path) -> None:
    f = Fresh(tmp_path, env="prod")
    c = f.client()
    assert c.get("/api/v1/auth/options").json()["setup_required"] is False
    r = c.post("/api/v1/setup", json=body())
    assert r.status_code == 403 and r.json()["error"]["code"] == "setup_disabled"
    with f.state.db.owner_session(SYSTEM) as db:
        assert db.execute(select(func.count()).select_from(Tenant)).scalar_one() == 0


# ------------------------------------------------------------------ setup


def test_setup_creates_the_firm_once(fresh: Fresh) -> None:
    c = fresh.client()
    r = c.post("/api/v1/setup", json=body())
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["admin_email"] == "asha@corvus.test"
    with fresh.state.db.owner_session(SYSTEM) as db:
        t = db.get(Tenant, out["tenant_id"])
        assert t is not None and t.name == "Corvus & Rao Chartered Accountants"
        assert t.settings_json["works_on_engagements"] is True and t.settings_json["ai_enabled"] is False
        # the seed helpers ran: roles, the permission matrix, policies, the audit head
        assert {r.code for r in db.execute(select(Role).where(Role.tenant_id == t.id)).scalars()} >= {"firm_admin", "partner", "system_admin"}
        assert db.execute(select(func.count()).select_from(RolePermission).where(RolePermission.tenant_id == t.id)).scalar_one() > 20
        assert db.execute(select(func.count()).select_from(Permission)).scalar_one() > 20
        assert {p.name for p in db.execute(select(Policy).where(Policy.tenant_id == t.id)).scalars()} == {"abac", "ai_policy"}
        admin = db.execute(select(User).where(User.email == "asha@corvus.test")).scalar_one()
        assert admin.status == "active" and admin.is_firm_user and admin.name == "Asha Rao"
        assert admin.totp_secret_enc is None and admin.mfa_type == "none"  # enrolled with a QR code at the first sign-in
        assert admin.must_change_password is False  # the administrator chose this password
        roles = db.execute(select(Role.code).join(UserClientRole, UserClientRole.role_id == Role.id).where(UserClientRole.user_id == admin.id)).scalars().all()
        assert roles == ["firm_admin"]
        actions = [a for (a,) in db.execute(select(AuditEvent.action).where(AuditEvent.tenant_id == t.id).order_by(AuditEvent.seq)).all()]
        assert actions == ["tenant.setup_completed", "assignment.created"]
        ev = db.execute(select(AuditEvent).where(AuditEvent.action == "tenant.setup_completed")).scalar_one()
        assert ev.details["admin_user_id"] == admin.id and ev.details["works_on_engagements"] is True and ev.ip == "127.0.0.1"
        assert audit.verify_chain(db, t.id).ok
    again = c.post("/api/v1/setup", json=body(firm_name="Second Firm", admin_email="other@corvus.test"))
    assert again.status_code == 409 and again.json()["error"]["code"] == "setup_complete"
    with fresh.state.db.owner_session(SYSTEM) as db:
        assert db.execute(select(func.count()).select_from(Tenant)).scalar_one() == 1


def test_setup_only_from_this_pc(fresh: Fresh) -> None:
    r = fresh.client(peer=("192.168.1.20", 50000)).post("/api/v1/setup", json=body())
    assert r.status_code == 403 and r.json()["error"]["code"] == "setup_local_only"
    # a local reverse proxy forwarding a remote browser does not count as this PC
    r = fresh.client().post("/api/v1/setup", json=body(), headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "setup_local_only"
    r = fresh.client().post("/api/v1/setup", json=body(), headers={"Forwarded": 'for="[2001:db8::7]:4711";proto=http'})
    assert r.status_code == 403
    assert fresh.client(peer=("::1", 50000)).post("/api/v1/setup", json=body()).status_code == 201


@pytest.mark.parametrize(("password", "message"), [
    ("short-pw-1", "at least 12 characters"),
    ("password1234", "breached or common"),
    ("Asha-Rao-Ledger-2026", "must not contain your e-mail name"),  # "asha" is the e-mail name
    ("Quill-Rao-Ledger-2026x", None),  # "rao" is shorter than 4 letters: allowed
    ("aaaaaaaaaaaaaaaa", "too repetitive"),
])
def test_setup_password_policy(fresh: Fresh, password: str, message: str | None) -> None:
    r = fresh.client().post("/api/v1/setup", json=body(password=password))
    if message is None:
        assert r.status_code == 201, r.text
        return
    assert r.status_code == 400 and r.json()["error"]["code"] == "password_policy"
    assert message in r.json()["error"]["message"]
    with fresh.state.db.owner_session(SYSTEM) as db:  # nothing was created
        assert db.execute(select(func.count()).select_from(Tenant)).scalar_one() == 0


def test_setup_password_must_not_contain_the_name(fresh: Fresh) -> None:
    r = fresh.client().post("/api/v1/setup", json=body(admin_name="Ishaan Mehrotra", password="Mehrotra-Ledger-2026"))
    assert r.status_code == 400 and "your name" in r.json()["error"]["message"]


def test_setup_validates_the_request(fresh: Fresh) -> None:
    c = fresh.client()
    assert c.post("/api/v1/setup", json=body(admin_email="not-an-email")).status_code == 422
    assert c.post("/api/v1/setup", json=body(firm_name="")).status_code == 422
    assert c.post("/api/v1/setup", json={**body(), "role": "system_admin"}).status_code == 422  # unknown fields refused


def test_setup_is_rate_limited(fresh: Fresh) -> None:
    c = fresh.client()
    with freeze_time("2026-10-21 10:00:05"):  # one rate-limit window
        codes = [c.post("/api/v1/setup", json=body(password="short")).status_code for _ in range(12)]
    assert codes[:10] == [400] * 10 and codes[10:] == [429, 429]


def test_works_on_engagements_defaults_to_off(fresh: Fresh) -> None:
    b = body()
    del b["works_on_engagements"]
    assert fresh.client().post("/api/v1/setup", json=b).status_code == 201
    with fresh.state.db.owner_session(SYSTEM) as db:
        assert db.execute(select(Tenant)).scalar_one().settings_json["works_on_engagements"] is False


def test_concurrent_setups_create_one_firm(fresh: Fresh) -> None:
    """The check that no firm exists and the creation are one locked transaction."""
    results: list[int] = []
    barrier = threading.Barrier(2)

    def go(i: int) -> None:
        c = fresh.client()
        barrier.wait()
        results.append(c.post("/api/v1/setup", json=body(admin_email=f"admin{i}@corvus.test")).status_code)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert sorted(results) == [201, 409]
    with fresh.state.db.owner_session(SYSTEM) as db:
        assert db.execute(select(func.count()).select_from(Tenant)).scalar_one() == 1
        assert db.execute(select(func.count()).select_from(User)).scalar_one() == 1


# ------------------------------------------------------------------ the launcher's one-time token


def test_setup_needs_the_launcher_token_when_configured(fresh: Fresh, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token_file = tmp_path / "setup.token"
    monkeypatch.setenv("FSWEB_SETUP_TOKEN_FILE", str(token_file))
    c = fresh.client()
    r = c.post("/api/v1/setup", json=body(setup_token="anything"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "setup_token_missing"  # no file: no setup
    token_file.write_text("k3y-0f-th3-launch3r-Zq9\n", encoding="utf-8")
    for wrong in (None, "", "k3y-0f-th3-launch3r", "k3y-0f-th3-launch3r-Zq9x"):
        r = c.post("/api/v1/setup", json=body(setup_token=wrong))
        assert r.status_code == 403 and r.json()["error"]["code"] == "setup_token_invalid", wrong
    r = c.post("/api/v1/setup", json=body(setup_token="k3y-0f-th3-launch3r-Zq9"))
    assert r.status_code == 201, r.text
    assert not token_file.exists()  # one-time: deleted after the firm is created
    with fresh.state.db.owner_session(SYSTEM) as db:
        assert db.execute(select(AuditEvent).where(AuditEvent.action == "tenant.setup_completed")).scalar_one().details["setup_token"] is True
    r = c.post("/api/v1/setup", json=body(setup_token="k3y-0f-th3-launch3r-Zq9"))
    assert r.status_code == 409


def test_a_wrong_token_creates_nothing(fresh: Fresh, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token_file = tmp_path / "setup.token"
    token_file.write_text("right-token", encoding="utf-8")
    monkeypatch.setenv("FSWEB_SETUP_TOKEN_FILE", str(token_file))
    assert fresh.client().post("/api/v1/setup", json=body(setup_token="wrong-token")).status_code == 403
    assert token_file.exists()
    with fresh.state.db.owner_session(SYSTEM) as db:
        assert db.execute(select(func.count()).select_from(Tenant)).scalar_one() == 0


# ------------------------------------------------------------------ first sign-in


def test_first_sign_in_enrols_the_authenticator_with_a_qr_code(fresh: Fresh, totp_clock: FakeClock) -> None:
    c = fresh.client()
    assert c.post("/api/v1/setup", json=body()).status_code == 201
    r = c.post("/api/v1/auth/login", json={"email": "asha@corvus.test", "password": ADMIN_PASSWORD})
    assert r.status_code == 200 and r.json()["status"] == "mfa_enrollment_required"
    csrf = r.json()["csrf_token"]
    me = c.get("/api/v1/auth/me").json()
    assert me["user"]["mfa_enrolled_at"] is None and me["permissions"] == {} and me["must_change_password"] is False
    assert c.get("/api/v1/clients").status_code == 401  # nothing before the second factor
    enrol = c.post("/api/v1/auth/mfa/enroll", headers={"X-CSRF-Token": csrf}).json()
    assert enrol["qr_svg_data_uri"].startswith("data:image/svg+xml") and enrol["otpauth_uri"].startswith("otpauth://totp/")
    r = c.post("/api/v1/auth/mfa/enroll/confirm", json={"code": totp_clock.code(enrol["secret"])}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 10
    csrf = r.json()["csrf_token"]
    me = c.get("/api/v1/auth/me").json()
    assert me["permissions"]["*"]["users.manage"] == "allow" and me["tenant_name"] == "Corvus & Rao Chartered Accountants"
    # the dashboard's data; the administrator adds a client (and, working on engagements, becomes its Partner) and a colleague
    assert c.get("/api/v1/engagements").status_code == 200
    client = c.post("/api/v1/clients", json={"name": "Heron Textiles Private Limited"}, headers={"X-CSRF-Token": csrf}).json()
    assert client["id"] in c.get("/api/v1/auth/me").json()["permissions"]
    r = c.post("/api/v1/admin/users/invite", json={"email": "ravi@corvus.test", "name": "Ravi Iyer", "role_code": "staff", "client_id": client["id"]},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201 and "/invite?token=" in r.json()["invite_link"]
