"""Runs and jobs: idempotency (E-ARCH-05), outbox atomicity, retries, SSE progress,
findings workflow (E-RULE-05), optimistic locking (E-DATA-03), PDF-only checks."""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import func, select

from fsweb.jobs.queue import claim, finish, reap_stale
from fsweb.models import FindingRow, Job, Run
from tests.conftest import FakeClock, World, drain, pdf_bytes, upload_workbook


def _checked(world: World, clock: FakeClock) -> tuple[Any, str]:
    staff = world.login("staff", clock)
    upload_workbook(staff, world.engagement_id)
    run = staff.post(f"/api/v1/engagements/{world.engagement_id}/runs", json={"kind": "check"}).json()
    drain(world)
    return staff, run["id"]


def test_run_and_job_are_created_atomically_and_idempotently(world: World, totp_clock: FakeClock) -> None:
    staff = world.login("staff", totp_clock)
    upload_workbook(staff, world.engagement_id)
    a = staff.post(f"/api/v1/engagements/{world.engagement_id}/runs", json={"kind": "check"}, headers={"Idempotency-Key": "same-key"})
    b = staff.post(f"/api/v1/engagements/{world.engagement_id}/runs", json={"kind": "check"}, headers={"Idempotency-Key": "same-key"})
    assert a.json()["id"] == b.json()["id"] and (a.status_code, b.status_code) == (202, 200)
    with world.db() as db:
        assert db.execute(select(func.count()).select_from(Run).where(Run.kind == "check")).scalar_one() == 1
        jobs = db.execute(select(Job)).scalars().all()
        assert len(jobs) == 1 and jobs[0].run_id == a.json()["id"] and jobs[0].status == "queued"


def test_default_worker_never_takes_ai_or_windows_jobs(world: World, totp_clock: FakeClock) -> None:
    _checked(world, totp_clock)
    with world.db() as db:
        run = db.execute(select(Run)).scalars().first()
        assert run is not None
        for q in ("ai-gateway", "render-win"):
            db.add(Job(tenant_id=run.tenant_id, client_id=run.client_id, run_id=run.id, queue=q))
        db.commit()
    assert drain(world) == 0
    assert claim(world.state.db, ["render-win"], "w1") is not None


def test_retry_with_backoff_then_dead(world: World, totp_clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    staff = world.login("staff", totp_clock)
    upload_workbook(staff, world.engagement_id)
    run = staff.post(f"/api/v1/engagements/{world.engagement_id}/runs", json={"kind": "check"}).json()

    def boom(*a: Any, **k: Any) -> Any:
        raise OSError("disk hiccup")

    monkeypatch.setattr(world.state.engine, "check_workbook", boom)
    assert drain(world) == 1
    with world.db() as db:
        job = db.execute(select(Job)).scalars().one()
        assert job.status == "queued" and job.attempts == 1 and "disk hiccup" in (job.error or "")
        r = db.get(Run, run["id"])
        assert r is not None and r.status == "queued"
    for _ in range(3):
        with world.db() as db:  # skip the backoff delay
            j = db.execute(select(Job)).scalars().one()
            j.available_at = j.created_at
            db.commit()
        drain(world)
    with world.db() as db:
        j = db.execute(select(Job)).scalars().one()
        assert j.status == "dead" and j.attempts == 3
        r = db.get(Run, run["id"])
        assert r is not None and r.status == "failed" and r.error_code == "engine_error"


def test_stale_claims_are_reaped(world: World, totp_clock: FakeClock) -> None:
    from datetime import timedelta

    staff = world.login("staff", totp_clock)
    upload_workbook(staff, world.engagement_id)
    staff.post(f"/api/v1/engagements/{world.engagement_id}/runs", json={"kind": "check"})
    job = claim(world.state.db, ["default"], "crashed-worker")
    assert job is not None
    assert reap_stale(world.state.db, timedelta(seconds=-1)) == 1
    with world.db() as db:
        assert db.get(Job, job.id).status == "queued"  # type: ignore[union-attr]
    assert finish(world.state.db, "missing", ok=True) == "missing"


def test_sse_progress_stream(world: World, totp_clock: FakeClock) -> None:
    staff, run_id = _checked(world, totp_clock)
    with staff.client.stream("GET", f"/api/v1/runs/{run_id}/events") as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    events = [line[6:] for line in body.splitlines() if line.startswith("data: ")]
    kinds = [json.loads(e).get("message") or json.loads(e).get("status") for e in events]
    assert "started" in kinds and kinds[-1] == "succeeded"
    assert "event: end" in body


def test_run_result_and_artefacts(world: World, totp_clock: FakeClock) -> None:
    staff, run_id = _checked(world, totp_clock)
    res = staff.get(f"/api/v1/runs/{run_id}/result").json()
    assert res["key_figures"][0]["key"] == "pat" and res["summary"]["counts"]["High"] == 3
    assert res["text_proposals"] and res["text_proposals"][0]["cell"] == "B12"
    arts = staff.get(f"/api/v1/runs/{run_id}/artefacts").json()
    reg = next(a for a in arts if a["kind"] == "register")
    dl = staff.get(f"/api/v1/artefacts/{reg['id']}/download")
    assert dl.status_code == 200 and dl.content[:2] == b"PK"
    assert world.login("client_viewer", totp_clock).get(f"/api/v1/runs/{run_id}/artefacts").status_code in (403, 404)


def test_sheet_states_are_passed_to_the_next_run(world: World, totp_clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    staff, _ = _checked(world, totp_clock)
    seen: dict[str, Any] = {}
    original = world.state.engine.check_workbook

    def spy(*a: Any, **k: Any) -> Any:
        seen.update(k)
        return original(*a, **k)

    monkeypatch.setattr(world.state.engine, "check_workbook", spy)
    staff.post(f"/api/v1/engagements/{world.engagement_id}/runs", json={"kind": "check"})
    drain(world)
    assert seen["previous_sheet_states"] == {"BS": {"state": "visible"}, "Notes": {"state": "visible"}}


def test_findings_workflow_and_optimistic_locking(world: World, totp_clock: FakeClock) -> None:
    staff, run_id = _checked(world, totp_clock)
    f = staff.get(f"/api/v1/runs/{run_id}/findings?severity=Medium").json()[0]
    r = staff.patch(f"/api/v1/findings/{f['id']}", json={"status": "Not an issue"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "reason_required"
    r = staff.patch(f"/api/v1/findings/{f['id']}", json={"status": "Not an issue", "status_reason": "narrative already updated"},
                    headers={"If-Match": 'W/"1"'})
    assert r.status_code == 200 and r.headers["etag"] == 'W/"2"'
    stale = staff.patch(f"/api/v1/findings/{f['id']}", json={"comment": "late"}, headers={"If-Match": 'W/"1"'})
    assert stale.status_code == 412
    assert staff.patch(f"/api/v1/findings/{f['id']}", json={"shared_with_client": True}).status_code == 403  # staff cannot share
    manager = world.login("manager", totp_clock)
    assert manager.patch(f"/api/v1/findings/{f['id']}", json={"shared_with_client": True}).status_code == 200
    # Decisions carry forward to the same finding in the next run.
    staff.post(f"/api/v1/engagements/{world.engagement_id}/runs", json={"kind": "check"})
    drain(world)
    with world.db() as db:
        same = db.execute(select(FindingRow).where(FindingRow.fingerprint == f["fingerprint"]).order_by(FindingRow.created_at.desc())).scalars().first()
        assert same is not None and same.status == "Not an issue" and same.shared_with_client
    analytics = staff.get("/api/v1/analytics/rule-quality").json()
    gc = next(a for a in analytics if a["rule_id"] == "GC-01")
    assert gc["not_an_issue"] == 2 and gc["flag_for_tuning"] is True


def test_pdf_only_check(world: World, totp_clock: FakeClock) -> None:
    staff = world.login("staff", totp_clock)
    staff.post(f"/api/v1/engagements/{world.engagement_id}/files", files={"file": ("printed.pdf", pdf_bytes(), "application/pdf")}, data={"kind": "printed_pdf"})
    run = staff.post(f"/api/v1/engagements/{world.engagement_id}/runs", json={"kind": "pdf_check"}).json()
    drain(world)
    done = staff.get(f"/api/v1/runs/{run['id']}").json()
    assert done["status"] == "succeeded" and done["result_json"]["mode"] == "pdf_only"
    assert staff.get(f"/api/v1/runs/{run['id']}/findings").json()[0]["rule_id"] == "PDF-04"


def test_run_records_reproducibility_metadata(world: World, totp_clock: FakeClock) -> None:
    staff, run_id = _checked(world, totp_clock)
    run = staff.get(f"/api/v1/runs/{run_id}").json()
    assert run["engine_version"] and run["rule_set_version"] and run["config_hash"] and run["input_hashes"]


def test_rules_endpoint(world: World, totp_clock: FakeClock) -> None:
    rules = world.login("reviewer", totp_clock).get("/api/v1/rules").json()
    assert rules["rule_set_version"] == "fake-rules-1" and rules["rules"]
    assert world.login("client_viewer", totp_clock).get("/api/v1/rules").status_code == 403


def test_signatory_master_reaches_the_engine(world: World, totp_clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    """C8: the signatories kept on the platform are the engine's master file for TXT-01 (former ones left out)."""
    from datetime import date
    from pathlib import Path

    import yaml

    from fsweb.models import Signatory

    with world.db() as db:
        db.add(Signatory(tenant_id=world.tenant_id, client_id=world.client_id, entity_id=world.entity_id, name="Former Director",
                         role="Director", valid_to=date(2025, 12, 31), created_by="test"))
        db.commit()
    seen: dict[str, Any] = {}
    real = world.state.engine.check_workbook

    def spy(path: Any, config: Any, **kw: Any) -> Any:
        seen["master"] = yaml.safe_load(Path(config.signatories).read_text(encoding="utf-8")) if config.signatories else None
        return real(path, config, **kw)

    monkeypatch.setattr(world.state.engine, "check_workbook", spy)
    _checked(world, totp_clock)
    assert seen["master"] == {"signatories": [{"name": "Director Alpha", "role": "Director", "din": "01234567"}]}


def test_rule_versions_in_use_are_recorded(world: World, totp_clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    """E-ITGC-02: the first run using a rule version registers it; a changed rule closes its previous version
    and links the approved change record that carried the regression evidence."""
    from fsweb.models import Release, RuleRow, RuleVersion

    def versions() -> dict[str, list[tuple[str, bool, str | None]]]:
        with world.db() as db:
            codes = {r.id: r.code for r in db.execute(select(RuleRow)).scalars()}
            out: dict[str, list[tuple[str, bool, str | None]]] = {}
            for v in db.execute(select(RuleVersion).order_by(RuleVersion.created_at)).scalars():
                out.setdefault(codes[v.rule_id], []).append((v.version, v.effective_to is None, v.regression_run_id))
            return out

    staff, _ = _checked(world, totp_clock)
    assert versions() == {"CAST-01": [("1", True, None)], "TXT-01": [("1", True, None)]}
    staff.post(f"/api/v1/engagements/{world.engagement_id}/runs", json={"kind": "check"})
    drain(world)
    assert versions() == {"CAST-01": [("1", True, None)], "TXT-01": [("1", True, None)]}  # nothing new
    with world.db() as db:
        rel = Release(tenant_id=world.tenant_id, version="1.1.0", ticket_ref="REL-2026-007", rule_change=True,
                      regression_evidence="golden suite passed", approver_id=world.users["firm_admin"]["id"], created_by="dev")
        db.add(rel)
        db.commit()
        rel_id = rel.id
    rules = world.state.engine.list_rules()
    monkeypatch.setattr(world.state.engine, "list_rules", lambda: [dict(r, version="2") if r["id"] == "CAST-01" else r for r in rules])
    staff.post(f"/api/v1/engagements/{world.engagement_id}/runs", json={"kind": "check"})
    drain(world)
    assert versions() == {"CAST-01": [("1", False, None), ("2", True, rel_id)], "TXT-01": [("1", True, None)]}
