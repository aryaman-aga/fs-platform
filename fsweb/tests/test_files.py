"""Uploads (05 §5), immutable versions (C1), check-out (E-DATA-03), watermarking and share links (E-SEC-10)."""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
from freezegun import freeze_time
from pypdf import PdfReader

from fsweb.db import utcnow
from fsweb.storage import LocalStorage, StorageError
from tests.conftest import FakeClock, World, drain, pdf_bytes, set_status, upload_workbook, xlsx_bytes

EXT_LINK_RELS = b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/externalLink" Target="externalLinks/externalLink1.xml"/></Relationships>'


@pytest.mark.parametrize(
    ("name", "data", "status", "code"),
    [
        ("evil.exe", b"MZ....", 415, "file_type"),
        ("fake.xlsx", b"not a zip at all", 415, "bad_magic"),
        ("fake.pdf", b"%NOTPDF", 415, "bad_magic"),
        ("links.xlsx", None, 422, "external_links"),
        ("dde.xlsx", None, 422, "dde"),
        ("macro.xlsx", None, 422, "macro_in_xlsx"),
        ("aadhaar.csv", b"name,id\nA Person,2345 6789 0124\n", 422, "dlp_blocked"),
        ("active.pdf", b"%PDF-1.7\n1 0 obj << /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>", 422, "active_content"),
    ],
)
def test_upload_validation(world: World, totp_clock: FakeClock, name: str, data: bytes | None, status: int, code: str) -> None:
    from fsweb.services.uploads import verhoeff_ok

    staff = world.login("staff", totp_clock)
    if name == "links.xlsx":
        data = xlsx_bytes({"xl/externalLinks/externalLink1.xml": b"<externalLink/>", "xl/_rels/extra.rels": EXT_LINK_RELS})
    elif name == "dde.xlsx":
        data = xlsx_bytes({"xl/dde.xml": b"<ddeLink ddeService='Excel'/>"})
    elif name == "macro.xlsx":
        data = xlsx_bytes({"xl/vbaProject.bin": b"\x00\x01"})
    if name == "aadhaar.csv":
        assert verhoeff_ok("234567890124")
    kind = "workbook" if name.endswith((".xlsx", ".exe")) else ("printed_pdf" if name.endswith(".pdf") else "tb")
    r = staff.post(f"/api/v1/engagements/{world.engagement_id}/files", files={"file": (name, data, "application/octet-stream")}, data={"kind": kind})
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code


def test_xlsm_macros_are_stored_never_run(world: World, totp_clock: FakeClock) -> None:
    staff = world.login("staff", totp_clock)
    data = xlsx_bytes({"xl/vbaProject.bin": b"\x00\x01"})
    r = staff.post(f"/api/v1/engagements/{world.engagement_id}/files", files={"file": ("book.xlsm", data, "application/vnd.ms-excel.sheet.macroEnabled.12")}, data={"kind": "workbook"})
    assert r.status_code == 201 and r.json()["version"]["has_macros"] is True


def test_versions_are_immutable(world: World, totp_clock: FakeClock) -> None:
    staff = world.login("staff", totp_clock)
    v1 = upload_workbook(staff, world.engagement_id, xlsx_bytes(title="one")).json()["version"]
    v2 = upload_workbook(staff, world.engagement_id, xlsx_bytes(title="two")).json()["version"]
    assert (v1["version_no"], v2["version_no"]) == (1, 2) and v2["parent_version_id"] == v1["id"] and v1["sha256"] != v2["sha256"]
    storage = world.state.storage
    assert isinstance(storage, LocalStorage)
    from fsweb.models import FileVersion

    with world.db() as db:
        fv = db.get(FileVersion, v1["id"])
        assert fv is not None
        with pytest.raises(StorageError):
            storage.put_bytes(fv.object_key, b"overwrite attempt")
    restored = staff.post(f"/api/v1/versions/{v1['id']}/restore").json()
    assert restored["version_no"] == 3 and restored["sha256"] == v1["sha256"] and restored["change_summary_json"]["restored_from"] == v1["id"]


def test_checkout_blocks_other_editors(world: World, totp_clock: FakeClock) -> None:
    staff = world.login("staff", totp_clock)
    manager = world.login("manager", totp_clock)
    up = upload_workbook(staff, world.engagement_id).json()
    fid = up["file"]["id"]
    assert staff.post(f"/api/v1/files/{fid}/checkout").status_code == 200
    r = upload_workbook(manager, world.engagement_id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "checked_out"
    assert manager.post(f"/api/v1/files/{fid}/checkout").status_code == 409
    # The holder checks in by uploading; the lock is released.
    assert upload_workbook(staff, world.engagement_id).status_code == 201
    assert upload_workbook(manager, world.engagement_id).status_code == 201
    # A stale base version is refused (no lost update).
    latest = staff.get(f"/api/v1/files/{fid}/versions").json()[0]
    older = staff.get(f"/api/v1/files/{fid}/versions").json()[1]
    r = upload_workbook(staff, world.engagement_id, base_version_id=older["id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_base"
    assert upload_workbook(staff, world.engagement_id, base_version_id=latest["id"]).status_code == 201


def test_checkout_expires(world: World, totp_clock: FakeClock) -> None:
    staff = world.login("staff", totp_clock)
    manager = world.login("manager", totp_clock)
    fid = upload_workbook(staff, world.engagement_id).json()["file"]["id"]
    staff.post(f"/api/v1/files/{fid}/checkout?minutes=10")
    with freeze_time(utcnow() + timedelta(minutes=11)):
        assert upload_workbook(manager, world.engagement_id).status_code == 201


def _render_pdf_version(world: World, clock: FakeClock) -> str:
    staff = world.login("staff", clock)
    upload_workbook(staff, world.engagement_id)
    run = staff.post(f"/api/v1/engagements/{world.engagement_id}/render", json={"mode": "draft"}).json()
    drain(world)
    return str(staff.get(f"/api/v1/runs/{run['id']}").json()["output_version_id"])


def test_pdf_downloads_are_watermarked(world: World, totp_clock: FakeClock) -> None:
    vid = _render_pdf_version(world, totp_clock)
    staff = world.login("staff", totp_clock)
    r = staff.get(f"/api/v1/versions/{vid}/download")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    text = PdfReader(io.BytesIO(r.content)).pages[0].extract_text()
    assert "staff@test.fs" in text and "DRAFT" in text


def test_share_links_are_single_use_expiring_and_bound(world: World, totp_clock: FakeClock) -> None:
    vid = _render_pdf_version(world, totp_clock)
    set_status(world, "DRAFT_RELEASED_TO_CLIENT")
    manager = world.login("manager", totp_clock)
    cfo_id = world.users["client_viewer"]["id"]
    link = manager.post(f"/api/v1/versions/{vid}/share-links", json={"recipient_user_id": cfo_id, "hours": 2}).json()
    token_path = link["url"].replace("http://testserver", "")
    uploader = world.login("client_uploader", totp_clock)
    assert uploader.get(token_path).status_code == 403  # bound to the recipient
    cfo = world.login("client_viewer", totp_clock)
    r = cfo.get(token_path)
    assert r.status_code == 200 and "client_viewer@test.fs" in PdfReader(io.BytesIO(r.content)).pages[0].extract_text()
    assert cfo.get(token_path).status_code == 410  # single use
    link2 = manager.post(f"/api/v1/versions/{vid}/share-links", json={"hours": 1}).json()
    with freeze_time(utcnow() + timedelta(hours=2)):
        cfo2 = world.login("client_viewer", totp_clock)
        assert cfo2.get(link2["url"].replace("http://testserver", "")).status_code == 410  # expired


def test_diff_between_versions(world: World, totp_clock: FakeClock) -> None:
    staff = world.login("staff", totp_clock)
    a = upload_workbook(staff, world.engagement_id, xlsx_bytes(title="a")).json()["version"]["id"]
    b = upload_workbook(staff, world.engagement_id, xlsx_bytes(title="b")).json()["version"]["id"]
    d = staff.get(f"/api/v1/engagements/{world.engagement_id}/diff?a={a}&b={b}").json()
    assert d["diff"]["a_sha256"] != d["diff"]["b_sha256"]


def test_tb_and_pdf_uploads(world: World, totp_clock: FakeClock) -> None:
    up = world.login("client_uploader", totp_clock)
    r = up.post(f"/api/v1/engagements/{world.engagement_id}/files", files={"file": ("tb.csv", b"account,debit,credit\n1000,10,0\n", "text/csv")}, data={"kind": "tb"})
    assert r.status_code == 201 and r.json()["engagement_status"] == "DATA_RECEIVED"
    r = up.post(f"/api/v1/engagements/{world.engagement_id}/files", files={"file": ("signed.pdf", pdf_bytes(), "application/pdf")}, data={"kind": "prior_year"})
    assert r.status_code == 201
