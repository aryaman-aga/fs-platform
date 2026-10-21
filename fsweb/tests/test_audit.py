"""Hash-chained, append-only audit trail (06 §4)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from fsweb import audit
from fsweb.db import SQLITE_AUDIT_TRIGGERS
from tests.conftest import FakeClock, World, upload_workbook


def _activity(world: World, clock: FakeClock) -> None:
    staff = world.login("staff", clock)
    upload_workbook(staff, world.engagement_id)
    staff.get("/api/v1/admin/users")  # denied → audited


def test_chain_verifies_and_links(world: World, totp_clock: FakeClock) -> None:
    _activity(world, totp_clock)
    with world.db() as db:
        rep = audit.verify_chain(db, world.tenant_id)
    assert rep.ok and rep.events >= 5 and rep.first_bad_seq is None


def test_update_and_delete_are_blocked(world: World, totp_clock: FakeClock) -> None:
    _activity(world, totp_clock)
    with world.db() as db:
        with pytest.raises((IntegrityError, OperationalError)):
            db.execute(text("UPDATE audit_event SET action = 'tampered' WHERE seq = 1"))
        db.rollback()
        with pytest.raises((IntegrityError, OperationalError)):
            db.execute(text("DELETE FROM audit_event WHERE seq = 1"))
        db.rollback()


def test_tampering_is_detected(world: World, totp_clock: FakeClock) -> None:
    _activity(world, totp_clock)
    with world.state.db.owner_engine.begin() as conn:  # an attacker with owner rights drops the guard...
        conn.exec_driver_sql("DROP TRIGGER audit_event_no_update")
        conn.exec_driver_sql("UPDATE audit_event SET details = '{\"forged\": true}' WHERE seq = 3")
        for stmt in SQLITE_AUDIT_TRIGGERS:
            conn.exec_driver_sql(stmt)
    with world.db() as db:
        rep = audit.verify_chain(db, world.tenant_id)
    assert not rep.ok and rep.first_bad_seq == 3 and "tampered" in rep.problems[0]
    admin = world.login("firm_admin", totp_clock)
    assert admin.get("/api/v1/audit/verify").json()["ok"] is False


def test_deleted_tail_is_detected(world: World, totp_clock: FakeClock) -> None:
    _activity(world, totp_clock)
    with world.state.db.owner_engine.begin() as conn:
        conn.exec_driver_sql("DROP TRIGGER audit_event_no_delete")
        conn.exec_driver_sql("DELETE FROM audit_event WHERE seq = (SELECT max(seq) FROM audit_event)")
    with world.db() as db:
        rep = audit.verify_chain(db, world.tenant_id)
    assert not rep.ok and any("audit_head" in p for p in rep.problems)


def test_head_file_is_write_once(world: World, totp_clock: FakeClock, tmp_path: Path) -> None:
    _activity(world, totp_clock)
    with world.db() as db:
        rep = audit.verify_chain(db, world.tenant_id)
    p = audit.write_head_file(tmp_path, rep, "2026-10-21")
    body = json.loads(p.read_text())
    assert body["head_hash"] == rep.head_hash and body["ok"]
    assert not os.access(p, os.W_OK)
    with pytest.raises(FileExistsError):
        audit.write_head_file(tmp_path, rep, "2026-10-21")


def test_search_scoping_and_export(world: World, totp_clock: FakeClock) -> None:
    _activity(world, totp_clock)
    admin = world.login("firm_admin", totp_clock)
    page = admin.get("/api/v1/audit?action=authz.denied").json()
    assert page["total"] >= 1 and page["items"][0]["outcome"] == "denied"
    partner = world.login("partner", totp_clock)
    mine = partner.get("/api/v1/audit").json()
    assert all(i["client_id"] in (None, world.client_id) for i in mine["items"])
    assert world.login("staff", totp_clock).get("/api/v1/audit").status_code == 403
    csv_text = admin.get("/api/v1/audit/export.csv").text
    assert csv_text.splitlines()[0].startswith("seq,ts,user_id")
    tl = partner.get(f"/api/v1/engagements/{world.engagement_id}/timeline").json()
    assert any(e["action"] == "file.uploaded" for e in tl)


def test_hash_is_reproducible() -> None:
    payload = {"a": 1, "details": {"z": "₹ 1,234.56", "b": [1, 2]}}
    assert audit.compute_hash(audit.GENESIS, payload) == audit.compute_hash(audit.GENESIS, json.loads(json.dumps(payload)))
    assert audit._sanitise({"x": 1.5, "y": {2}}) == {"x": "1.5", "y": [2]}
