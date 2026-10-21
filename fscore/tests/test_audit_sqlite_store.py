from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from helpers_report import make_applied, make_plan, make_result

from fscore.audit.store import GENESIS, AuditStore, default_path


@pytest.fixture
def store(tmp_path: Path) -> AuditStore:
    s = AuditStore(tmp_path / "audit.db", actor="tester")
    yield s
    s.close()


def _populate(store: AuditStore) -> tuple[str, str]:
    result = make_result()
    run_id = store.record_check(result, params={"x": 1})
    plan = make_plan()
    plan_id = store.record_plan(plan, run_id=run_id)
    store.record_approval(plan_id, "manager-1", plan.plan_sha256, ["E-0003"])
    store.record_apply(plan_id, make_applied(), run_id=run_id)
    store.record_ai_call(payload={"findings": ["F-0001"]}, provider="mock", model="claude-opus-5-5", region="ap-south-1", decision="sent",
                         retain="hash_only", approved_by="manager-1", stop_reason="end_turn", response={"items": []})
    return run_id, plan_id


def test_chain_verifies_and_content_roundtrips(store: AuditStore) -> None:
    run_id, plan_id = _populate(store)
    v = store.verify_chain()
    assert v.ok, v.reason
    assert v.events >= 6
    assert v.head == store.head() != GENESIS
    assert len(store.findings(run_id)) == 5
    stored = store.check_result(run_id)
    assert stored is not None
    assert stored.run.file_sha256 == "cd" * 32
    assert store.latest_check_run() == run_id
    actions = [e["action"] for e in store.events()]
    assert actions[:2] == ["run.started", "check.recorded"]
    assert "plan.approved" in actions
    assert "plan.applied" in actions
    assert "ai.call" in actions
    applied = store.conn.execute("SELECT edit_id FROM edits WHERE plan_id=? AND applied=1 ORDER BY edit_id", (plan_id,)).fetchall()
    assert [r[0] for r in applied] == ["E-0001", "E-0002"]


def test_ai_payload_retention(store: AuditStore) -> None:
    store.record_ai_call(payload="secret payload", provider="mock", model=None, region=None, decision="rejected", retain="hash_only")
    store.record_ai_call(payload="kept payload", provider="mock", model=None, region=None, decision="sent", retain="full", response="ok")
    rows = store.conn.execute("SELECT payload, payload_sha256, response FROM ai_calls ORDER BY ts").fetchall()
    assert rows[0][0] is None
    assert len(rows[0][1]) == 64
    assert rows[1][0] == "kept payload"
    assert rows[1][2] == "ok"
    assert store.verify_chain().ok


def test_tampered_event_is_detected(store: AuditStore) -> None:
    _populate(store)
    store.conn.execute("UPDATE events SET details_json = replace(details_json, '\"findings\":5', '\"findings\":4') WHERE action='check.recorded'")
    v = store.verify_chain()
    assert not v.ok
    assert v.broken_at == 2
    assert "modified" in v.reason


def test_tampered_finding_row_is_detected(store: AuditStore) -> None:
    run_id, _ = _populate(store)
    store.conn.execute("UPDATE findings SET detail_json = replace(detail_json, '500.01', '500.00') WHERE run_id=?", (run_id,))
    v = store.verify_chain()
    assert not v.ok
    assert "findings" in v.reason


def test_tampered_plan_edit_is_detected(store: AuditStore) -> None:
    _, plan_id = _populate(store)
    store.conn.execute("UPDATE edits SET new_formula='SUM(C20:C21)+0.01' WHERE plan_id=? AND edit_id='E-0001'", (plan_id,))
    v = store.verify_chain()
    assert not v.ok
    assert "edits" in v.reason


def test_deleted_event_is_detected(store: AuditStore) -> None:
    _populate(store)
    store.conn.execute("DELETE FROM events WHERE seq = 3")
    v = store.verify_chain()
    assert not v.ok
    assert ("gap" in v.reason or "link" in v.reason)


def test_approval_bound_to_plan_hash(store: AuditStore) -> None:
    plan = make_plan()
    plan_id = store.record_plan(plan)
    with pytest.raises(ValueError):
        store.record_approval(plan_id, "manager-1", "0" * 64, ["E-0003"])
    with pytest.raises(KeyError):
        store.record_approval("P-missing", "manager-1", plan.plan_sha256, [])


def test_events_hold_no_cell_contents(store: AuditStore) -> None:
    _populate(store)
    blob = " ".join(str(e["details"]) for e in store.events())
    for content in ("Total current liabilities", "500.01", "SUM(C20:C21)", "DIN: 01234567"):
        assert content not in blob


def test_default_path_respects_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FSCHECK_AUDIT_DB", str(tmp_path / "x.db"))
    assert default_path() == tmp_path / "x.db"


def test_reopen_and_continue_chain(tmp_path: Path) -> None:
    p = tmp_path / "a.db"
    with AuditStore(p, actor="a") as s1:
        s1.append_event("one", details={"n": 1})
    with AuditStore(p, actor="b") as s2:
        s2.append_event("two", details={"n": 2})
        assert s2.verify_chain().ok
        assert [e["actor"] for e in s2.events()] == ["a", "b"]
    con = sqlite3.connect(p)
    assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "1"
    con.close()
