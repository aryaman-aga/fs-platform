"""Migration 0002 (``app_user.must_change_password``) on PostgreSQL: it upgrades a database of the first release
(revision 0001) in place, the models match the migrated schema, and the first-run setup, the forced password change
and the access-review flag work as the RLS-bound runtime role. Needs FSWEB_TEST_PG_URL (see test_postgres.py)."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from fsweb.db import Base
from fsweb.engine import FakeEngine
from fsweb.main import create_app
from fsweb.manage import alembic_config, run_migrations
from fsweb.state import build_state
from tests.conftest import FakeClock, make_settings

pytestmark = pytest.mark.postgres

OWNER_URL = os.environ.get("FSWEB_TEST_PG_URL", "postgresql+psycopg://fsweb_owner:fsweb_owner@localhost:54329/postgres")
APP_ROLE = os.environ.get("FSWEB_TEST_PG_APP", "fsweb_app:fsweb_app")


@pytest.fixture
def pg_0001() -> Iterator[dict[str, str]]:
    """A database migrated to the first release only."""
    try:
        eng = create_engine(OWNER_URL, isolation_level="AUTOCOMMIT")
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL not available: {exc}")
    name = f"fsweb_m_{uuid.uuid4().hex[:10]}"
    with eng.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    owner = make_url(OWNER_URL).set(database=name)
    user, pw = APP_ROLE.split(":", 1)
    urls = {"owner": owner.render_as_string(hide_password=False), "app": owner.set(username=user, password=pw).render_as_string(hide_password=False)}
    run_migrations(urls["owner"], "0001")
    yield urls
    with eng.connect() as c:
        c.execute(text(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{name}' AND pid <> pg_backend_pid()"))
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    eng.dispose()


def _owner(url: str, sql: str, **params: object) -> list[tuple[object, ...]]:
    eng = create_engine(url)
    try:
        with eng.begin() as c:
            c.execute(text("SELECT set_config('app.scope', 'system', true)"))
            res = c.execute(text(sql), params)
            return [tuple(r) for r in res.all()] if res.returns_rows else []
    finally:
        eng.dispose()


def _version(url: str) -> str | None:
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return MigrationContext.configure(c).get_current_revision()
    finally:
        eng.dispose()


def _user_columns(url: str) -> set[str]:
    eng = create_engine(url)
    try:
        return {c["name"] for c in inspect(eng).get_columns("app_user")}
    finally:
        eng.dispose()


def _downgrade(url: str, revision: str) -> None:
    from alembic import command

    from fsweb.manage import alembic_config

    command.downgrade(alembic_config(url), revision)


def _head(url: str) -> str | None:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(alembic_config(url)).get_current_head()


def test_0002_upgrades_a_0001_database(pg_0001: dict[str, str]) -> None:
    owner = pg_0001["owner"]
    assert _version(owner) == "0001"
    # rows written by the first release
    _owner(owner, "INSERT INTO tenant (id, name, settings_json, created_at) VALUES ('t1', 'Corvus Advisors', '{}', now())")
    _owner(owner, "INSERT INTO app_user (id, tenant_id, email, name, status, is_firm_user, is_break_glass, mfa_type, failed_logins, version, created_at) "
                  "VALUES ('u1', 't1', 'asha@corvus.test', 'Asha Rao', 'active', true, false, 'totp', 0, 1, now())")
    run_migrations(owner)
    assert _version(owner) == _head(owner)  # 0002 and every later revision
    eng = create_engine(owner)
    try:
        col = {c["name"]: c for c in inspect(eng).get_columns("app_user")}["must_change_password"]
        assert col["nullable"] is False and str(col["default"]).lower() == "false"
        with eng.connect() as c:
            diffs = compare_metadata(MigrationContext.configure(c, opts={"compare_type": False}), Base.metadata)
    finally:
        eng.dispose()
    assert diffs == [], f"models and migrations differ on PostgreSQL: {diffs[:5]}"
    assert _owner(owner, "SELECT email, must_change_password FROM app_user") == [("asha@corvus.test", False)]
    # the runtime role reads and writes the column under row-level security
    app = create_engine(pg_0001["app"])
    try:
        with app.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', 't1', true), set_config('app.user_id', 'u1', true), set_config('app.scope', 'user', true)"))
            c.execute(text("UPDATE app_user SET must_change_password = true WHERE id = 'u1'"))
            assert c.execute(text("SELECT must_change_password FROM app_user")).scalars().all() == [True]
        with app.begin() as c:  # another firm's context sees nothing
            c.execute(text("SELECT set_config('app.tenant_id', 't2', true), set_config('app.scope', 'user', true)"))
            assert c.execute(text("SELECT count(*) FROM app_user")).scalar_one() == 0
    finally:
        app.dispose()
    # and back: the downgrade removes the column, the upgrade adds it again
    _downgrade(owner, "0001")
    assert "must_change_password" not in _user_columns(owner)
    run_migrations(owner)
    assert _version(owner) == _head(owner) and "must_change_password" in _user_columns(owner)


def test_first_run_on_postgres_as_the_runtime_role(pg_0001: dict[str, str], tmp_path: Path, totp_clock: FakeClock) -> None:
    run_migrations(pg_0001["owner"])
    s = make_settings(tmp_path, database_url=pg_0001["owner"], app_database_url=pg_0001["app"])
    st = build_state(s, engine=FakeEngine())
    try:
        c = TestClient(create_app(s, state=st), client=("127.0.0.1", 50000))
        assert c.get("/api/v1/auth/options").json()["setup_required"] is True  # RLS lets the sign-in scope see that no firm exists
        r = c.post("/api/v1/setup", json={"firm_name": "Corvus Advisors", "admin_name": "Asha Rao", "admin_email": "asha@corvus.test",
                                          "password": "Ledger-Lantern-Quay-58", "works_on_engagements": True})
        assert r.status_code == 201, r.text
        assert c.post("/api/v1/setup", json={"firm_name": "Again", "admin_name": "B C", "admin_email": "b@corvus.test",
                                             "password": "Ledger-Lantern-Quay-59"}).status_code == 409
        assert c.get("/api/v1/auth/options").json()["setup_required"] is False
        r = c.post("/api/v1/auth/login", json={"email": "asha@corvus.test", "password": "Ledger-Lantern-Quay-58"})
        assert r.json()["status"] == "mfa_enrollment_required"
        csrf = r.json()["csrf_token"]
        enrol = c.post("/api/v1/auth/mfa/enroll", headers={"X-CSRF-Token": csrf}).json()
        r = c.post("/api/v1/auth/mfa/enroll/confirm", json={"code": totp_clock.code(enrol["secret"])}, headers={"X-CSRF-Token": csrf})
        csrf = r.json()["csrf_token"]
        h = {"X-CSRF-Token": csrf}
        client = c.post("/api/v1/clients", json={"name": "Heron Textiles Private Limited"}, headers=h).json()
        me = c.get("/api/v1/auth/me").json()
        assert {a["role_code"] for a in me["assignments"] if a["client_id"] == client["id"]} == {"partner"}
        assert c.post("/api/v1/admin/access-reviews", json={"period": "2026Q4"}, headers=h).status_code == 201
        items = c.get("/api/v1/admin/access-reviews", params={"period": "2026Q4"}).json()
        assert [i["client_id"] for i in items if i["sod_warning"]] == [client["id"]]
        # tenant settings, the backup copy folder and an administrator's reset link, all under RLS
        hr = {**h, "X-Action-Reason": "desktop office set-up"}
        assert c.put("/api/v1/admin/tenant-settings", json={"works_on_engagements": False}, headers=hr).json() == {"works_on_engagements": False}
        r = c.put("/api/v1/admin/backup-settings", json={"backup_copy_dir": str(tmp_path)}, headers=hr)
        assert r.status_code == 200 and r.json()["backup_copy_dir"] == str(tmp_path)
        r = c.post("/api/v1/admin/users/invite", json={"email": "ravi@corvus.test", "name": "Ravi Iyer", "role_code": "staff", "client_id": client["id"]}, headers=h)
        assert r.status_code == 201 and r.json()["invite_link"]
        r = c.post(f"/api/v1/admin/users/{r.json()['user']['id']}/password-reset-link", headers=hr)
        assert r.status_code == 200 and "/reset-password?token=" in r.json()["link"]
        assert c.get("/api/v1/audit/verify").json()["ok"] is True
    finally:
        st.db.dispose()
