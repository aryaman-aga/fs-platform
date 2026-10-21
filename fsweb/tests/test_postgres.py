"""PostgreSQL: migrations, row-level security as the runtime role, the append-only audit
trail, and the whole engagement flow running as ``fsweb_app`` under RLS.

Needs a server: FSWEB_TEST_PG_URL (owner role with CREATEDB) and FSWEB_TEST_PG_APP
(runtime role name/password). Defaults match the local dev cluster on port 54329.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from fsweb import audit
from fsweb.db import SYSTEM, Database
from fsweb.models import Client, Engagement, Entity, File, FileVersion, FindingRow, Role, Run, User
from fsweb.security.crypto import FieldCipher, LocalKMS
from fsweb.services.seed import assign, create_tenant, make_user
from tests.conftest import FakeClock, build_world

pytestmark = pytest.mark.postgres

OWNER_URL = os.environ.get("FSWEB_TEST_PG_URL", "postgresql+psycopg://fsweb_owner:fsweb_owner@localhost:54329/postgres")
APP_ROLE = os.environ.get("FSWEB_TEST_PG_APP", "fsweb_app:fsweb_app")


def _admin_url() -> str:
    return OWNER_URL


@pytest.fixture
def pg(tmp_path: Path) -> Iterator[dict[str, str]]:
    try:
        eng = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL not available: {exc}")
    name = f"fsweb_t_{uuid.uuid4().hex[:10]}"
    with eng.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    owner = make_url(_admin_url()).set(database=name)
    user, pw = APP_ROLE.split(":", 1)
    app = owner.set(username=user, password=pw)
    from fsweb.manage import run_migrations

    run_migrations(owner.render_as_string(hide_password=False))
    yield {"owner": owner.render_as_string(hide_password=False), "app": app.render_as_string(hide_password=False)}
    _drop_database(eng, name)
    eng.dispose()


def _drop_database(eng: Any, name: str, wait_s: float = 30.0) -> None:
    """Drop a test database. The owner may end only its own sessions; one of the runtime role's that is still being
    closed (a busy machine) is waited for instead of failing the test."""
    deadline = time.monotonic() + wait_s
    with eng.connect() as c:
        while True:
            c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n AND pid <> pg_backend_pid() "
                           "AND usename = current_user"), {"n": name})
            try:
                c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
                return
            except DBAPIError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)


def _seed(owner_url: str) -> dict[str, Any]:
    db = Database(owner_url)
    cipher = FieldCipher(LocalKMS({"k1": b"k" * 32}))
    out: dict[str, Any] = {}
    with db.owner_session(SYSTEM) as s:
        for tname in ("Firm One", "Firm Two"):
            t = create_tenant(s, tname)
            roles = {r.code: r for r in s.query(Role).filter(Role.tenant_id == t.id)}
            clients = []
            for cname in ("A", "B"):
                c = Client(tenant_id=t.id, name=f"{tname} client {cname}")
                s.add(c)
                s.flush()
                ent = Entity(tenant_id=t.id, client_id=c.id, name=c.name)
                s.add(ent)
                s.flush()
                e = Engagement(tenant_id=t.id, client_id=c.id, entity_id=ent.id, name="FY26", period_end=date(2026, 3, 31))
                s.add(e)
                s.flush()
                f = File(tenant_id=t.id, client_id=c.id, engagement_id=e.id, kind="workbook", title="wb")
                s.add(f)
                s.flush()
                s.add(FileVersion(tenant_id=t.id, client_id=c.id, engagement_id=e.id, file_id=f.id, version_no=1, object_key=f"k/{c.id}",
                                  sha256="0" * 64, size=1, content_type="x", original_name="wb.xlsx"))
                r = Run(tenant_id=t.id, client_id=c.id, engagement_id=e.id, kind="check", status="succeeded")
                s.add(r)
                s.flush()
                s.add(FindingRow(tenant_id=t.id, client_id=c.id, engagement_id=e.id, run_id=r.id, fid="F-0001", fingerprint=c.id[:16], rule_id="CAST-01",
                                 severity="High", certainty="Certain"))
                clients.append(c)
            partner = make_user(s, cipher, t.id, f"partner@{tname.replace(' ', '').lower()}.test", "Partner", password=None)
            admin = make_user(s, cipher, t.id, f"admin@{tname.replace(' ', '').lower()}.test", "Admin", password=None)
            assign(s, t.id, partner, roles["partner"], clients[0].id, "test")
            assign(s, t.id, admin, roles["firm_admin"], None, "test")
            audit.append(s, audit.Actor(t.id, partner.id), "test.event", client_id=clients[0].id)
            out[tname] = {"tenant": t.id, "a": clients[0].id, "b": clients[1].id, "partner": partner.id, "admin": admin.id}
        s.commit()
    db.dispose()
    return out


def _as_app(app_url: str, tenant: str, user: str, scope: str, sql: str) -> list[Any]:
    eng = create_engine(app_url)
    try:
        with eng.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true), set_config('app.user_id', :u, true), set_config('app.scope', :s, true)"),
                      {"t": tenant, "u": user, "s": scope})
            return list(c.execute(text(sql)).all())
    finally:
        eng.dispose()


def test_rls_scopes_rows_to_assigned_clients(pg: dict[str, str]) -> None:
    ids = _seed(pg["owner"])
    one, two = ids["Firm One"], ids["Firm Two"]
    for table in ("finding", "engagement", "file_version", "run"):
        rows = _as_app(pg["app"], one["tenant"], one["partner"], "user", f"SELECT DISTINCT client_id FROM {table}")
        assert [r[0] for r in rows] == [one["a"]], table
    clients = _as_app(pg["app"], one["tenant"], one["partner"], "user", "SELECT id FROM client")
    assert [r[0] for r in clients] == [one["a"]]
    # Firm Admin: master data of the whole firm, but no client content.
    eng = {r[0] for r in _as_app(pg["app"], one["tenant"], one["admin"], "firm", "SELECT client_id FROM engagement")}
    assert eng == {one["a"], one["b"]}
    assert _as_app(pg["app"], one["tenant"], one["admin"], "firm", "SELECT * FROM finding") == []
    # Another firm's rows are invisible even with a valid user id from that firm.
    assert _as_app(pg["app"], one["tenant"], two["partner"], "user", "SELECT * FROM finding") == []
    # No context at all: nothing.
    assert _as_app(pg["app"], "", "", "", "SELECT * FROM engagement") == []
    assert _as_app(pg["app"], "", "", "", "SELECT * FROM app_user") == []
    # Audit events of other clients are hidden from a client-scoped user.
    ev = _as_app(pg["app"], one["tenant"], one["partner"], "user", "SELECT client_id FROM audit_event WHERE action = 'test.event'")
    assert [r[0] for r in ev] == [one["a"]]


def test_rls_blocks_writes_into_foreign_clients(pg: dict[str, str]) -> None:
    ids = _seed(pg["owner"])
    one = ids["Firm One"]
    owner = create_engine(pg["owner"])
    with owner.begin() as c:
        c.execute(text("SELECT set_config('app.scope', 'system', true)"))
        eng_id, run_id = c.execute(text("SELECT engagement_id, id FROM run WHERE client_id = :b"), {"b": one["b"]}).one()
    owner.dispose()
    with pytest.raises(DBAPIError, match="row-level security"):
        _as_app(pg["app"], one["tenant"], one["partner"], "user",
                "INSERT INTO finding (id, tenant_id, client_id, engagement_id, run_id, fid, fingerprint, rule_id, rule_version, family, severity, "
                "certainty, category, cells, evidence_json, explanation, fix_kind, status, shared_with_client, version, created_at) VALUES "
                f"('x', '{one['tenant']}', '{one['b']}', '{eng_id}', '{run_id}', 'F', 'f', 'CAST-01', '1', '', 'High', 'Certain', '', '[]', '[]', '', "
                "'auto', 'Open', false, 1, now()) RETURNING id")


def test_audit_trail_is_append_only(pg: dict[str, str]) -> None:
    ids = _seed(pg["owner"])
    one = ids["Firm One"]
    with pytest.raises(DBAPIError, match="permission denied"):
        _as_app(pg["app"], one["tenant"], one["partner"], "user", "UPDATE audit_event SET action = 'x' RETURNING id")
    owner = create_engine(pg["owner"])

    def as_owner(sql: str) -> None:
        with owner.begin() as c:
            c.execute(text("SELECT set_config('app.scope', 'system', true)"))
            c.execute(text(sql))

    with pytest.raises(DBAPIError, match="append-only"):
        as_owner("UPDATE audit_event SET action = 'x'")
    with pytest.raises(DBAPIError, match="append-only"):
        as_owner("DELETE FROM audit_event")
    # An attacker with owner rights disables the trigger: verification still detects the edit.
    with owner.begin() as c:
        c.execute(text("ALTER TABLE audit_event DISABLE TRIGGER audit_event_no_update"))
        c.execute(text("SELECT set_config('app.scope', 'system', true)"))
        c.execute(text("UPDATE audit_event SET details = '{\"forged\": true}' WHERE action = 'test.event' AND tenant_id = :t"), {"t": one["tenant"]})
        c.execute(text("ALTER TABLE audit_event ENABLE TRIGGER audit_event_no_update"))
    owner.dispose()
    db = Database(pg["owner"])
    with db.owner_session(SYSTEM) as s:
        rep = audit.verify_chain(s, one["tenant"])
        assert not rep.ok and "tampered" in " ".join(rep.problems)
        assert audit.verify_chain(s, ids["Firm Two"]["tenant"]).ok
    db.dispose()


def test_full_flow_as_runtime_role_under_rls(pg: dict[str, str], tmp_path: Path, totp_clock: FakeClock) -> None:
    """The whole engagement workflow with the API connected as fsweb_app (RLS enforced)."""
    from tests.test_e2e import run_e2e

    w = build_world(tmp_path, totp_clock, database_url=pg["owner"], app_database_url=pg["app"])
    try:
        run_e2e(w, totp_clock)
        admin = w.login("firm_admin", totp_clock)
        assert admin.get("/api/v1/audit/verify").json()["ok"] is True
        partner = w.login("partner", totp_clock)
        assert partner.get(f"/api/v1/engagements/{w.engagement_id}/timeline").status_code == 200
        # Another client's existence is not even revealed under RLS.
        r = partner.post("/api/v1/admin/assignments", json={"user_id": w.users["staff"]["id"], "role_code": "reviewer", "client_id": w.other_client_id})
        assert r.status_code == 404
        with w.db() as db:
            assert db.query(User).count() >= 8
    finally:
        w.state.db.dispose()


def test_signup_and_password_sign_in_under_rls(pg: dict[str, str], tmp_path: Path,
                                                totp_clock: FakeClock) -> None:
    """"Create an account" and sign-in without an authenticator, the API connected as fsweb_app: the firm is
    found in the 'auth' scope; the account, its session and the audit events are written under RLS."""
    from fastapi.testclient import TestClient

    from tests.conftest import PASSWORD, Api

    w = build_world(tmp_path, totp_clock, database_url=pg["owner"], app_database_url=pg["app"],
                    mfa_required=False, signup_enabled=True)
    try:
        c = TestClient(w.app)
        new = {"email": "nisha@corvus.test", "name": "Nisha Kulkarni", "password": "Quayside-Ledger-Otter-62"}
        r = c.post("/api/v1/auth/signup", json=new)
        assert r.status_code == 201 and r.json()["status"] == "ok", r.text
        nisha = Api(c, r.json()["csrf_token"])
        assert nisha.get("/api/v1/clients").json() == [] and nisha.get("/api/v1/engagements").json() == []
        uid = nisha.get("/api/v1/auth/me").json()["user"]["id"]
        admin_client = TestClient(w.app)
        r = admin_client.post("/api/v1/auth/login", json={"email": "firm_admin@test.fs",
                                                          "password": PASSWORD})
        assert r.json()["status"] == "ok"
        admin = Api(admin_client, r.json()["csrf_token"])
        assert admin.post("/api/v1/auth/step-up", json={"password": PASSWORD}).status_code == 200
        grant = {"user_id": uid, "role_code": "manager", "client_id": w.client_id}
        r = admin.post("/api/v1/admin/assignments", json=grant)
        assert r.status_code == 201, r.text
        assert [x["id"] for x in nisha.get("/api/v1/clients").json()] == [w.client_id]
        assert admin.get("/api/v1/audit/verify").json()["ok"] is True
        actions = {e["action"] for e in admin.get("/api/v1/audit", params={"user_id": uid}).json()["items"]}
        assert {"user.signed_up", "auth.login"} <= actions
    finally:
        w.state.db.dispose()
