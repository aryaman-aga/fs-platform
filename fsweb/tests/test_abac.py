"""ABAC policy suite (E-SEC-02): every role × engagement status × action, plus HTTP spot checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fsweb.authz import abac
from fsweb.authz.permissions import ACTION_CLASS, PERMISSION_CODES, ROLE_CODES, ROLES
from fsweb.services.workflow import STATES
from tests.conftest import FakeClock, World, build_world, set_status, upload_workbook

CLIENT_ROLES = {"client_uploader", "client_viewer"}
EARLY = {"DRAFT_SETUP", "DATA_RECEIVED", "CHECKED", "FIX_PLANNED", "FIX_APPROVED", "FIXED_VERIFIED", "REVIEW"}
CLIENT_EARLY_OK = {"file.upload", "engagement.view", "portal.view", "portal.respond"}
DOWNLOAD = {r.code: r.download_allowed for r in ROLES}


def attrs(role: str, status: str, perm: str, **over: Any) -> dict[str, Any]:
    a: dict[str, Any] = {
        "action": perm,
        "action_class": ACTION_CLASS[perm],
        "user_type": "client" if role in CLIENT_ROLES else "firm",
        "roles": [role],
        "network": "unknown",
        "office_networks_configured": False,
        "download_allowed": DOWNLOAD[role],
        "client_scoped": True,
        "system_admin_only": role == "system_admin",
        "client_assigned": True,
        "classification": "confidential",
        "engagement_status": status,
        "engagement_quarantine": False,
    }
    a.update(over)
    return a


def expected_rule(role: str, status: str, perm: str) -> str | None:
    cls = ACTION_CLASS[perm]
    if cls == "write" and status in {"FINAL_LOCKED", "ARCHIVED"}:
        return "locked-read-only"
    if role in CLIENT_ROLES and cls == "download" and not DOWNLOAD[role]:
        return "client-download-role-setting"
    if role in CLIENT_ROLES and cls in {"read", "download", "write"} and status in EARLY and perm not in CLIENT_EARLY_OK:
        return "client-sees-released-only"
    if role == "system_admin":
        return "system-admin-no-client-data"
    return None


@pytest.mark.parametrize("perm", PERMISSION_CODES)
@pytest.mark.parametrize("status", STATES)
@pytest.mark.parametrize("role", ROLE_CODES)
def test_policy_role_status_action(role: str, status: str, perm: str) -> None:
    d = abac.evaluate(abac.default_policy_text(), attrs(role, status, perm))
    want = expected_rule(role, status, perm)
    assert d.allowed is (want is None), (role, status, perm, d)
    if want:
        assert d.rule_id == want
    assert d.policy_version == 1


def test_quarantine_and_network_and_classification_rules() -> None:
    text = abac.default_policy_text()
    assert abac.evaluate(text, attrs("partner", "REVIEW", "run.check", engagement_quarantine=True)).rule_id == "quarantine-read-only"
    assert abac.evaluate(text, attrs("partner", "REVIEW", "engagement.promote", engagement_quarantine=True)).allowed
    assert abac.evaluate(text, attrs("firm_admin", "REVIEW", "users.manage", network="remote", office_networks_configured=True)).rule_id == "admin-from-office-network"
    assert abac.evaluate(text, attrs("firm_admin", "REVIEW", "users.manage", network="office", office_networks_configured=True)).allowed
    d = abac.evaluate(text, attrs("client_viewer", "DRAFT_RELEASED_TO_CLIENT", "finding.view", classification="restricted"))
    assert d.rule_id == "restricted-classification-firm-only"
    assert abac.evaluate(text, attrs("client_uploader", "REVIEW", "file.upload", client_assigned=False)).rule_id == "client-user-own-client"


def test_policy_documents_must_only_deny() -> None:
    with pytest.raises(ValueError, match="deny"):
        abac.PolicyDoc.parse("version: 2\nrules:\n  - id: x\n    effect: allow\n    when: {action: [a]}\n")
    with pytest.raises(ValueError):
        abac.PolicyDoc.parse("version: 2\nrules:\n  - id: x\n    effect: deny\n")


def test_network_classification() -> None:
    assert abac.network_of("10.1.2.3", ["10.0.0.0/8"]) == "office"
    assert abac.network_of("203.0.113.9", ["10.0.0.0/8"]) == "remote"
    assert abac.network_of("10.1.2.3", []) == "unknown"
    assert abac.network_of("not-an-ip", ["10.0.0.0/8"]) == "remote"


# ------------------------------------------------------------------ over HTTP


def test_locked_engagement_is_read_only(world: World, totp_clock: FakeClock) -> None:
    staff = world.login("staff", totp_clock)
    upload_workbook(staff, world.engagement_id)
    set_status(world, "FINAL_LOCKED")
    r = upload_workbook(staff, world.engagement_id)
    assert r.status_code == 403 and r.json()["error"]["details"]["rule"] == "locked-read-only"
    r = world.login("reviewer", totp_clock).post(f"/api/v1/engagements/{world.engagement_id}/review-notes", json={"text": "late note"})
    assert r.status_code == 403
    assert staff.get(f"/api/v1/engagements/{world.engagement_id}/files").status_code == 200  # reading still works


def test_client_user_waits_for_release(world: World, totp_clock: FakeClock) -> None:
    up = world.login("client_uploader", totp_clock)
    assert upload_workbook(up, world.engagement_id).status_code == 201  # uploads are allowed early
    r = up.get(f"/api/v1/engagements/{world.engagement_id}/files")
    assert r.status_code == 403 and r.json()["error"]["details"]["rule"] == "client-sees-released-only"
    set_status(world, "DRAFT_RELEASED_TO_CLIENT")
    assert up.get(f"/api/v1/engagements/{world.engagement_id}/files").status_code == 200


def test_admin_actions_only_from_office_network(tmp_path: Path, totp_clock: FakeClock) -> None:
    w = build_world(tmp_path, totp_clock, office_cidrs=["10.0.0.0/8"], trust_proxy=True)
    admin = w.login("firm_admin", totp_clock)
    r = admin.get("/api/v1/admin/users", headers={"X-Forwarded-For": "203.0.113.7"})
    assert r.status_code == 403 and r.json()["error"]["details"]["rule"] == "admin-from-office-network"
    assert admin.get("/api/v1/admin/users", headers={"X-Forwarded-For": "10.20.30.40"}).status_code == 200


def test_policy_versions_are_data(world: World, totp_clock: FakeClock) -> None:
    admin = world.login("firm_admin", totp_clock)
    admin.step_up(totp_clock)
    body = abac.default_policy_text().replace("version: 1", "version: 2") + (
        "\n  - id: no-ai-on-restricted\n    description: test rule\n    effect: deny\n    when:\n      action: [ai.request]\n      classification: [restricted]\n")
    r = admin.put("/api/v1/admin/policies/abac", json={"body": body}, headers={"X-Action-Reason": "add AI restriction"})
    assert r.status_code == 200 and r.json()["version"] == 2
    bad = admin.put("/api/v1/admin/policies/abac", json={"body": "rules:\n  - id: x\n    effect: allow\n    when: {a: [1]}\n"}, headers={"X-Action-Reason": "bad policy"})
    assert bad.status_code == 400
    versions = admin.get("/api/v1/admin/policies/abac").json()
    assert [v["version"] for v in versions] == [2, 1] and versions[0]["active"] and not versions[1]["active"]
