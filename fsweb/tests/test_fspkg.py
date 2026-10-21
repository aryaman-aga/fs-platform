"""Engagement packages (.fspkg, 07 §4 / E-DATA-05): signed + encrypted, tamper-evident,
imported into quarantine; conflicting changes need a Manager merge; restore drill (C13)."""

from __future__ import annotations

from pathlib import Path

import pytest

from fsweb.services import fspkg
from tests.conftest import FakeClock, World, build_world, drain, upload_workbook


def _package(world: World, clock: FakeClock) -> bytes:
    staff = world.login("staff", clock)
    upload_workbook(staff, world.engagement_id)
    staff.post(f"/api/v1/engagements/{world.engagement_id}/runs", json={"kind": "check"})
    drain(world)
    world.login("reviewer", clock).post(f"/api/v1/engagements/{world.engagement_id}/review-notes", json={"text": "Check the RPT note."})
    manager = world.login("manager", clock)
    r = manager.post(f"/api/v1/engagements/{world.engagement_id}/export")
    assert r.status_code == 200 and r.content.startswith(b"FSPKG1")
    return r.content


def test_round_trip_into_quarantine(world: World, totp_clock: FakeClock) -> None:
    data = _package(world, totp_clock)
    manager = world.login("manager", totp_clock)
    r = manager.post("/api/v1/engagements/import", files={"file": ("e.fspkg", data, "application/octet-stream")})
    assert r.status_code == 201, r.text
    eng = r.json()
    assert eng["is_quarantine"] and not eng["merge_required"]
    with world.db() as db:
        assert fspkg.version_hashes(db, eng["id"]) == fspkg.version_hashes(db, world.engagement_id)
    # Quarantine is read-only until a Partner promotes it.
    assert upload_workbook(manager, eng["id"]).status_code == 403
    assert manager.post(f"/api/v1/engagements/{eng['id']}/promote").status_code == 403
    promoted = world.login("partner", totp_clock).post(f"/api/v1/engagements/{eng['id']}/promote")
    assert promoted.status_code == 200 and not promoted.json()["is_quarantine"]


@pytest.mark.parametrize("where", ["ciphertext", "header", "signature"])
def test_tampered_package_is_rejected(world: World, totp_clock: FakeClock, where: str) -> None:
    data = bytearray(_package(world, totp_clock))
    if where == "ciphertext":
        data[-10] ^= 0x01
    elif where == "header":
        i = data.index(b'"engagement_id"')
        data[i + 20] ^= 0x01
    else:
        hlen = int.from_bytes(data[6:10], "big")
        data[10 + hlen + 2 + 5] ^= 0x01
    manager = world.login("manager", totp_clock)
    r = manager.post("/api/v1/engagements/import", files={"file": ("e.fspkg", bytes(data), "application/octet-stream")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "package_rejected"


def test_untrusted_signer_is_rejected(world: World, totp_clock: FakeClock, tmp_path: Path) -> None:
    data = _package(world, totp_clock)
    other = build_world(tmp_path / "other", totp_clock, secret_key="another-firm-secret-0123456789abcdef-xyz")
    with pytest.raises(fspkg.PackageError, match="untrusted"):
        fspkg.read_package(data, other.state.settings)


def test_trusted_key_with_shared_kek_is_accepted(world: World, totp_clock: FakeClock) -> None:
    """An offline edition trusts the firm's signing key and holds the same package KEK."""
    data = _package(world, totp_clock)
    other = world.state.settings.model_copy(update={"secret_key": "offline-node-secret-0123456789abcdef-xyz",
                                                     "pkg_trusted_keys": [fspkg.public_key_b64(world.state.settings)]})
    with pytest.raises(fspkg.PackageError, match="decryption failed"):
        # Same key id ("k-dev") but a different key: decryption must fail closed.
        fspkg.read_package(data, other.model_copy(update={"kms_master_key": None}))
    import base64

    shared = "k-dev:" + base64.b64encode(world.state.settings.master_keys()["k-dev"]).decode()
    pkg = fspkg.read_package(data, other.model_copy(update={"kms_master_key": shared}))
    assert pkg.manifest["engagement"]["id"] == world.engagement_id


def test_conflicting_changes_need_a_manager_merge(world: World, totp_clock: FakeClock) -> None:
    data = _package(world, totp_clock)
    staff = world.login("staff", totp_clock)
    upload_workbook(staff, world.engagement_id)  # the live engagement moved on after the export
    manager = world.login("manager", totp_clock)
    eng = manager.post("/api/v1/engagements/import", files={"file": ("e.fspkg", data, "application/octet-stream")}).json()
    assert eng["merge_required"]
    partner = world.login("partner", totp_clock)
    r = partner.post(f"/api/v1/engagements/{eng['id']}/promote")
    assert r.status_code == 409 and r.json()["error"]["code"] == "merge_required"
    assert manager.post(f"/api/v1/engagements/{eng['id']}/merge").status_code == 200
    assert partner.post(f"/api/v1/engagements/{eng['id']}/promote").status_code == 200


def test_restore_drill(world: World, totp_clock: FakeClock) -> None:
    from fsweb.manage import restore_drill

    _package(world, totp_clock)
    report = restore_drill(world.state, engagement_id=world.engagement_id, seed=1)
    assert report["ok"], report
    assert [s["step"] for s in report["steps"]] == ["export", "import_quarantine", "compare_hashes", "recast"]
    admin = world.login("firm_admin", totp_clock)
    drills = [b for b in admin.get("/api/v1/admin/backups").json() if b["kind"] == "restore_drill"]
    assert drills and drills[0]["verified"]
