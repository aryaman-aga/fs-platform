"""The permission matrix of 05 §3, row by row and role by role, enforced server-side.

Every cell is tested twice: as data (the seeded matrix and the role resolution)
and over HTTP against a representative endpoint, as the role's own signed-in user.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from fsweb.authz.deps import Principal, load_roles, permission_value
from fsweb.authz.permissions import ALLOW, DENY, MATRIX_ROWS, ROLE_CODES
from fsweb.models import BackupLog, DraftRelease, Engagement, Run
from fsweb.services.engagements import seed_engagement
from tests.conftest import Api, FakeClock, World, build_world, drain, upload_workbook

ROW_PERMS = [code for _, code, _ in MATRIX_ROWS]


@pytest.fixture(scope="module")
def mw(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    from tests.conftest import totp_mod

    mp = pytest.MonkeyPatch()
    clock = FakeClock()
    mp.setattr(totp_mod, "time", clock)
    w = build_world(Path(tmp_path_factory.mktemp("matrix")), clock)
    apis = {role: w.login(role, clock) for role in ROLE_CODES}
    for api in apis.values():
        assert api.post("/api/v1/ai/policy/accept").status_code == 200
    eid = w.engagement_id
    staff = apis["staff"]
    v1 = upload_workbook(staff, eid).json()["version"]["id"]
    staff.post(f"/api/v1/engagements/{eid}/runs", json={"kind": "check"})
    drain(w)
    check = staff.get(f"/api/v1/engagements/{eid}/runs?kind=check").json()[0]["id"]
    plan = staff.post(f"/api/v1/runs/{check}/fix-plan").json()["id"]
    drain(w)
    render = staff.post(f"/api/v1/engagements/{eid}/render", json={"mode": "draft"}).json()["id"]
    drain(w)
    ai_req = staff.post(f"/api/v1/engagements/{eid}/ai/requests", json={"kind": "triage", "reason": "triage the High findings", "provider": "mock"}).json()
    with w.db() as db:
        run = db.get(Run, render)
        assert run is not None and run.output_version_id
        pdf_v = run.output_version_id
        db.add(DraftRelease(tenant_id=w.tenant_id, client_id=w.client_id, engagement_id=eid, render_run_id=render, pdf_version_id=pdf_v,
                            workbook_version_id=v1, released_by=w.users["manager"]["id"]))
        e = db.get(Engagement, eid)
        assert e is not None
        e.status = "DRAFT_RELEASED_TO_CLIENT"
        extra = {}
        for status in ("MGMT_CONFIRMED", "FINAL_LOCKED", "DATA_RECEIVED"):
            x = Engagement(tenant_id=w.tenant_id, client_id=w.client_id, entity_id=w.entity_id, name=f"{status} engagement", type="annual",
                           period_end=date(2026, 3, 31), status=status, created_by="test")
            db.add(x)
            db.flush()
            seed_engagement(db, x, "test")
            extra[status] = x.id
        restore = BackupLog(tenant_id=w.tenant_id, kind="restore_request", status="pending_approval", created_by=w.users["system_admin"]["id"])
        db.add(restore)
        db.commit()
        ids = {"engagement": eid, "check": check, "plan": plan, "render": render, "pdf": pdf_v, "ai": ai_req["id"], "ai_sha": ai_req["payload_sha256"],
               "mgmt": extra["MGMT_CONFIRMED"], "locked": extra["FINAL_LOCKED"], "upload": extra["DATA_RECEIVED"], "restore": restore.id}
    yield {"w": w, "apis": apis, "ids": ids, "clock": clock}
    mp.undo()
    w.state.db.dispose()


def call(perm: str, role: str, api: Api, ids: dict[str, Any], clock: FakeClock) -> Any:
    e = ids["engagement"]
    if perm == "users.manage":
        return api.get("/api/v1/admin/users")
    if perm == "client.manage":
        return api.post("/api/v1/clients", json={"name": f"Matrix client by {role}"})
    if perm == "engagement.assign":
        return api.get("/api/v1/admin/assignments")
    if perm == "file.upload":  # a separate engagement, so uploads do not move the main one back to CHECKED
        return upload_workbook(api, ids["upload"])
    if perm == "run.check":
        return api.post(f"/api/v1/engagements/{e}/runs", json={"kind": "check"})
    if perm == "finding.view":
        return api.get(f"/api/v1/runs/{ids['check']}/findings")
    if perm == "fix.propose":
        return api.post(f"/api/v1/runs/{ids['check']}/fix-plan")
    if perm == "fix.approve":
        return api.post(f"/api/v1/fix-plans/{ids['plan']}/approve", json={"plan_sha256": "x" * 64})
    if perm == "narrative.edit":
        return api.post(f"/api/v1/engagements/{e}/narrative-edits", json={"sheet": "Notes", "cell": "B12", "new_text": "The Company has positive net worth."})
    if perm == "balancing.approve":
        return api.post(f"/api/v1/fix-plans/{ids['plan']}/approve-balancing", json={"plan_sha256": "x" * 64})
    if perm == "ai.request":
        return api.post(f"/api/v1/engagements/{e}/ai/requests", json={"kind": "triage", "reason": "matrix test", "provider": "mock"})
    if perm == "ai.approve":
        return api.post(f"/api/v1/ai/requests/{ids['ai']}/approve", json={"payload_sha256": ids["ai_sha"]})
    if perm == "render.draft":
        return api.post(f"/api/v1/engagements/{e}/render", json={"mode": "draft"})
    if perm == "release.client":
        return api.post(f"/api/v1/engagements/{e}/release", json={"render_run_id": ids["render"]})
    if perm == "engagement.lock":
        return api.post(f"/api/v1/engagements/{ids['mgmt']}/transition", json={"to": "FINAL_LOCKED"})
    if perm == "engagement.unlock":
        return api.post(f"/api/v1/engagements/{ids['locked']}/unlock-requests", json={"reason": "matrix test unlock request"})
    if perm == "file.download":
        return api.get(f"/api/v1/versions/{ids['pdf']}/download")
    if perm == "audit.view":
        return api.get("/api/v1/audit")
    if perm == "backup.manage":
        if role == "firm_admin":  # 'approve restore of client data'
            api.step_up(clock)
            return api.post(f"/api/v1/admin/backups/{ids['restore']}/approve-restore", headers={"X-Action-Reason": "drill restore approved"})
        return api.post("/api/v1/admin/backups", json={"kind": "pg_full", "size": 1})
    raise AssertionError(perm)


# Expected HTTP outcome for cells whose value is conditional.
SPECIAL: dict[tuple[str, str], str] = {
    ("balancing.approve", "manager"): "denied",  # M: a Manager's approval needs a Partner as checker
    ("narrative.edit", "staff"): "allowed",  # M: accepted as a pending edit awaiting approval
    ("finding.view", "client_uploader"): "allowed",  # shared items only
    ("finding.view", "client_viewer"): "allowed",
    ("file.download", "client_uploader"): "allowed",  # role setting: uploader may download
    ("file.download", "client_viewer"): "denied",  # role setting: viewer may not
    ("fix.approve", "manager"): "allowed",  # not own plan (the plan was proposed by Staff)
    ("ai.approve", "manager"): "allowed",  # not own request (requested by Staff)
    ("ai.approve", "firm_admin"): "allowed",  # per the firm's AI policy
    ("engagement.unlock", "partner"): "allowed",  # dual: the request needs a Firm Admin co-approval
    ("backup.manage", "firm_admin"): "allowed",  # approve restore of client data
}


def expected(perm: str, role: str) -> str:
    if (perm, role) in SPECIAL:
        return SPECIAL[(perm, role)]
    cells = dict(next(c for _, code, c in MATRIX_ROWS if code == perm))
    return "allowed" if cells.get(role, DENY) != DENY else "denied"


@pytest.mark.parametrize("role", ROLE_CODES)
@pytest.mark.parametrize("perm", ROW_PERMS)
def test_matrix_http(mw: dict[str, Any], perm: str, role: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.conftest import totp_mod

    monkeypatch.setattr(totp_mod, "time", mw["clock"])
    r = call(perm, role, mw["apis"][role], mw["ids"], mw["clock"])
    want = expected(perm, role)
    if want == "allowed":
        assert r.status_code not in (401, 403, 404), f"{role} should be allowed {perm}: {r.status_code} {r.text[:300]}"
    else:
        assert r.status_code in (403, 404), f"{role} should be denied {perm}: {r.status_code} {r.text[:300]}"


@pytest.mark.parametrize("role", ROLE_CODES)
@pytest.mark.parametrize("perm", ROW_PERMS)
def test_matrix_data(mw: dict[str, Any], perm: str, role: str) -> None:
    w: World = mw["w"]
    with w.db() as db:
        roles = load_roles(db, w.users[role]["id"])
        p = Principal(tenant_id=w.tenant_id, user_id=w.users[role]["id"], session_id="", email="", name="", is_firm_user=True,
                      is_break_glass=False, mfa_verified=True, stepup_at=None, roles=roles)
        got = permission_value(db, p, perm, w.client_id)
    cells = dict(next(c for _, code, c in MATRIX_ROWS if code == perm))
    assert got == cells.get(role, DENY)


def test_denials_are_audited(mw: dict[str, Any]) -> None:
    """Every refusal is in the audit trail with the permission refused (06 §4), however the tests are scheduled."""
    from fsweb.models import AuditEvent

    w: World = mw["w"]

    def denials() -> list[AuditEvent]:
        with w.db() as db:
            return list(db.execute(select(AuditEvent).where(AuditEvent.action == "authz.denied").order_by(AuditEvent.seq)).scalars())

    before = len(denials())
    assert mw["apis"]["staff"].get("/api/v1/admin/users").status_code == 403
    assert mw["apis"]["reviewer"].post(f"/api/v1/runs/{mw['ids']['check']}/fix-plan").status_code == 403
    after = denials()
    assert len(after) == before + 2
    assert [e.details["permission"] for e in after[-2:]] == ["users.manage", "fix.propose"]
    assert all(e.outcome == "denied" for e in after[-2:])


def test_unassigned_client_is_invisible(mw: dict[str, Any]) -> None:
    """Client scoping: a Partner of client A sees nothing of client B."""
    w: World = mw["w"]
    with w.db() as db:
        other = Engagement(tenant_id=w.tenant_id, client_id=w.other_client_id, entity_id=w.entity_id, name="Other client engagement",
                           type="annual", period_end=date(2026, 3, 31), created_by="test")
        db.add(other)
        db.commit()
        oid = other.id
    partner: Api = mw["apis"]["partner"]
    assert partner.get(f"/api/v1/engagements/{oid}").status_code == 404
    assert upload_workbook(partner, oid).status_code == 404
    assert partner.get(f"/api/v1/engagements/{oid}/runs").status_code == 404
    assert all(e["client_id"] != w.other_client_id for e in partner.get("/api/v1/engagements").json())
    assert partner.get(f"/api/v1/clients/{w.other_client_id}").status_code == 404
    admin: Api = mw["apis"]["firm_admin"]
    assert admin.get(f"/api/v1/clients/{w.other_client_id}").status_code == 200  # Firm Admin manages all clients
    assert admin.get(f"/api/v1/runs/{mw['ids']['check']}/findings").status_code == 403  # ...but sees no findings


def test_firm_admin_sees_the_engagement_header_only(mw: dict[str, Any]) -> None:
    """05 §2: the Firm Admin assigns engagement teams, so reads and edits the header, never the engagement's contents."""
    admin: Api = mw["apis"]["firm_admin"]
    eid = mw["ids"]["upload"]
    r = admin.get(f"/api/v1/engagements/{eid}")
    assert r.status_code == 200, r.text
    r = admin.patch(f"/api/v1/engagements/{eid}", json={"due_date": "2026-06-30"}, headers={"If-Match": r.headers["ETag"]})
    assert r.status_code == 200 and r.json()["due_date"] == "2026-06-30", r.text
    assert admin.get(f"/api/v1/engagements/{eid}/runs").status_code == 403
    assert admin.get(f"/api/v1/engagements/{eid}/gates").status_code == 403
    assert mw["apis"]["system_admin"].get(f"/api/v1/engagements/{eid}").status_code in (403, 404)


def test_matrix_values_are_known() -> None:
    from fsweb.authz.permissions import STRENGTH

    for _, _, cells in MATRIX_ROWS:
        for role, value in cells.items():
            assert role in ROLE_CODES and value in STRENGTH and value != DENY
    assert ALLOW in STRENGTH
