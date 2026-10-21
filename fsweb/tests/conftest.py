"""Test harness: an app per test on a fresh SQLite database with the fake engine,
TOTP clock control, and helpers to sign in as any role."""

from __future__ import annotations

import io
import os
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from fsweb.db import SYSTEM
from fsweb.engine import FakeEngine
from fsweb.main import create_app
from fsweb.models import Client, Engagement, Entity, Role, Signatory, Tenant, User
from fsweb.security import totp as totp_mod
from fsweb.services.engagements import seed_engagement
from fsweb.services.seed import assign, create_tenant, make_user
from fsweb.settings import Settings
from fsweb.state import AppState, build_state

PASSWORD = "Correct-Horse-Ledger-2026"
ROLE_CODES = ["system_admin", "firm_admin", "partner", "manager", "staff", "reviewer", "client_uploader", "client_viewer"]
CLIENT_SCOPED = {"partner", "manager", "staff", "reviewer", "client_uploader", "client_viewer"}


class FakeClock:
    """Controls the clock the TOTP verifier sees, so every sign-in gets a fresh time step."""

    def __init__(self) -> None:
        import time as _t

        self.now = float(int(_t.time() // 30) * 30)

    def time(self) -> float:
        return self.now

    def code(self, secret: str) -> str:
        self.now += 30
        return pyotp.TOTP(secret).at(self.now)


@pytest.fixture(autouse=True)
def totp_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr(totp_mod, "time", clock)
    return clock


def make_settings(tmp: Path, **over: Any) -> Settings:
    base: dict[str, Any] = {
        "env": "test",
        "database_url": f"sqlite:///{tmp}/fsweb.db",
        "storage": f"local:{tmp}/objects",
        "work_dir": str(tmp / "work"),
        "audit_head_dir": str(tmp / "heads"),
        "engine": "fake",
        "secret_key": "test-secret-key-0123456789abcdef-0123456789",
        "worker_token": "worker-token-for-tests",
        "ai_enabled": True,
        "public_base_url": "http://testserver",
    }
    base.update(over)
    return Settings(**base)


@dataclass
class World:
    state: AppState
    app: Any
    tenant_id: str
    client_id: str
    other_client_id: str
    entity_id: str
    engagement_id: str
    users: dict[str, dict[str, Any]] = field(default_factory=dict)  # role -> {id, email, secret}

    def db(self) -> Any:
        return self.state.db.owner_session(SYSTEM)

    def login(self, role: str, clock: FakeClock) -> Api:
        u = self.users[role]
        return login(self.app, u["email"], PASSWORD, u["secret"], clock)


@dataclass
class Api:
    client: TestClient
    csrf: str
    email: str = ""
    secret: str = ""

    def _h(self, headers: dict[str, str] | None) -> dict[str, str]:
        h = {"X-CSRF-Token": self.csrf}
        if headers:
            h.update(headers)
        return h

    def get(self, url: str, **kw: Any) -> Any:
        return self.client.get(url, **kw)

    def post(self, url: str, headers: dict[str, str] | None = None, **kw: Any) -> Any:
        return self.client.post(url, headers=self._h(headers), **kw)

    def put(self, url: str, headers: dict[str, str] | None = None, **kw: Any) -> Any:
        return self.client.put(url, headers=self._h(headers), **kw)

    def patch(self, url: str, headers: dict[str, str] | None = None, **kw: Any) -> Any:
        return self.client.patch(url, headers=self._h(headers), **kw)

    def delete(self, url: str, headers: dict[str, str] | None = None, **kw: Any) -> Any:
        return self.client.delete(url, headers=self._h(headers), **kw)

    def step_up(self, clock: FakeClock) -> None:
        r = self.post("/api/v1/auth/step-up", json={"code": clock.code(self.secret)})
        assert r.status_code == 200, r.text


def login(app: Any, email: str, password: str, secret: str | None, clock: FakeClock) -> Api:
    c = TestClient(app, base_url="http://testserver")
    r = c.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    csrf = r.json()["csrf_token"]
    if secret:
        r = c.post("/api/v1/auth/mfa/verify", json={"code": clock.code(secret)}, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, r.text
        csrf = r.json()["csrf_token"]
    return Api(c, csrf, email, secret or "")


def xlsx_bytes(extra_parts: dict[str, bytes] | None = None, *, title: str = "Balance Sheet") -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "BS"
    ws.append([title])
    ws.append(["Cash", 10.5])
    ws.append(["Total", "=SUM(B2:B2)"])
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    if not extra_parts:
        return data
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for item in src.infolist():
            z.writestr(item, src.read(item.filename))
        for name, body in extra_parts.items():
            z.writestr(name, body)
    return out.getvalue()


def pdf_bytes(text: str = "Synthetic statements") -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def build_world(tmp: Path, clock: FakeClock, **settings_over: Any) -> World:
    s = make_settings(tmp, **settings_over)
    st = build_state(s, engine=FakeEngine())
    if not st.db.is_postgres:
        st.db.create_all()
    app = create_app(s, state=st)
    with st.db.owner_session(SYSTEM) as db:
        t = create_tenant(db, "Test Firm", settings={"firm_admin_may_approve_ai": True, "ai_enabled": True})
        roles = {r.code: r for r in db.execute(select(Role).where(Role.tenant_id == t.id)).scalars()}
        ca = Client(tenant_id=t.id, name="Alpha Synthetic Ltd", group_name="Alpha Group", created_by="test")
        ca.pan_enc = st.cipher.encrypt(db, t.id, "AAACA1234A")
        cb = Client(tenant_id=t.id, name="Beta Synthetic Ltd", created_by="test")
        db.add_all([ca, cb])
        db.flush()
        ent = Entity(tenant_id=t.id, client_id=ca.id, name="Alpha Synthetic Ltd", created_by="test")
        db.add(ent)
        db.flush()
        sig = Signatory(tenant_id=t.id, client_id=ca.id, entity_id=ent.id, name="Director Alpha", role="Director", created_by="test")
        sig.din_enc = st.cipher.encrypt(db, t.id, "01234567")
        db.add(sig)
        users: dict[str, dict[str, Any]] = {}
        for code in ROLE_CODES:
            firm = code not in {"client_uploader", "client_viewer"}
            email = f"{code}@test.fs"
            secret = pyotp.random_base32()
            u = make_user(db, st.cipher, t.id, email, code.replace("_", " ").title(), password=PASSWORD, is_firm_user=firm,
                          totp_secret=secret, created_by="test")
            assign(db, t.id, u, roles[code], ca.id if code in CLIENT_SCOPED else None, "test")
            users[code] = {"id": u.id, "email": email, "secret": secret}
        e = Engagement(tenant_id=t.id, client_id=ca.id, entity_id=ent.id, name="FY26 statutory", type="annual", period_end=date(2026, 3, 31),
                       partner_id=users["partner"]["id"], manager_id=users["manager"]["id"], created_by="test")
        db.add(e)
        db.flush()
        seed_engagement(db, e, "test")
        db.commit()
        w = World(st, app, t.id, ca.id, cb.id, ent.id, e.id, users)
    return w


@pytest.fixture
def world(tmp_path: Path, totp_clock: FakeClock) -> Iterator[World]:
    w = build_world(tmp_path, totp_clock)
    yield w
    w.state.db.dispose()


def add_user(w: World, email: str, role: str, *, client_id: str | None = None, firm: bool = True) -> dict[str, Any]:
    with w.db() as db:
        r = db.execute(select(Role).where(Role.tenant_id == w.tenant_id, Role.code == role)).scalar_one()
        secret = pyotp.random_base32()
        u = make_user(db, w.state.cipher, w.tenant_id, email, email.split("@")[0], password=PASSWORD, is_firm_user=firm, totp_secret=secret, created_by="test")
        assign(db, w.tenant_id, u, r, client_id, "test")
        db.commit()
        return {"id": u.id, "email": email, "secret": secret}


def set_status(w: World, status: str, engagement_id: str | None = None) -> None:
    with w.db() as db:
        e = db.get(Engagement, engagement_id or w.engagement_id)
        assert e is not None
        e.status = status
        db.commit()


def drain(w: World, queues: tuple[str, ...] = ("default",)) -> int:
    from fsweb.worker import drain as _drain

    return _drain(w.state, list(queues))


def upload_workbook(api: Api, engagement_id: str, data: bytes | None = None, name: str = "FS FY26.xlsx", **form: str) -> Any:
    files = {"file": (name, data or xlsx_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    return api.post(f"/api/v1/engagements/{engagement_id}/files", files=files, data={"kind": "workbook", **form})


def tenant_of(w: World) -> Tenant:
    with w.db() as db:
        t = db.get(Tenant, w.tenant_id)
        assert t is not None
        db.expunge(t)
        return t


def user_row(w: World, role: str) -> User:
    with w.db() as db:
        u = db.get(User, w.users[role]["id"])
        assert u is not None
        db.expunge(u)
        return u


os.environ.setdefault("FSWEB_ENGINE", "fake")
