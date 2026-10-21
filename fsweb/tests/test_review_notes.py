"""Review notes (06 §3): who raises, responds and clears; client queries; export with clearance evidence."""

from __future__ import annotations

from tests.conftest import FakeClock, World, set_status, upload_workbook


def test_review_note_rules_and_export(world: World, totp_clock: FakeClock) -> None:
    staff = world.login("staff", totp_clock)
    upload_workbook(staff, world.engagement_id)
    assert staff.post(f"/api/v1/engagements/{world.engagement_id}/review-notes", json={"text": "staff cannot raise"}).status_code == 403
    reviewer = world.login("reviewer", totp_clock)
    note = reviewer.post(f"/api/v1/engagements/{world.engagement_id}/review-notes",
                         json={"target_type": "cell", "target_ref": "BS!D20", "text": "Total does not cast", "assigned_to": world.users["staff"]["id"]}).json()
    assert note["status"] == "Open" and note["audience"] == "team"
    assert staff.post(f"/api/v1/review-notes/{note['id']}/messages", json={"text": "Rewritten as SUMPRODUCT(ROUND())."}).json()["status"] == "Responded"
    manager = world.login("manager", totp_clock)
    assert manager.post(f"/api/v1/review-notes/{note['id']}/clear").status_code == 403  # Partner / Reviewer clear
    cleared = reviewer.post(f"/api/v1/review-notes/{note['id']}/clear", headers={"If-Match": 'W/"2"'})
    assert cleared.status_code == 200 and cleared.json()["cleared_by"] == world.users["reviewer"]["id"]
    assert staff.post(f"/api/v1/review-notes/{note['id']}/messages", json={"text": "late"}).status_code == 409
    csv_text = manager.get(f"/api/v1/engagements/{world.engagement_id}/review-notes/export").text
    assert "reviewer@test.fs" in csv_text and "Cleared" in csv_text and "Rewritten as SUMPRODUCT" in csv_text


def test_client_queries_via_portal(world: World, totp_clock: FakeClock) -> None:
    manager = world.login("manager", totp_clock)
    upload_workbook(manager, world.engagement_id)
    q = manager.post(f"/api/v1/engagements/{world.engagement_id}/review-notes", json={"target_type": "client_query", "text": "Please confirm the loan balance."}).json()
    assert q["audience"] == "client"
    set_status(world, "DRAFT_RELEASED_TO_CLIENT")
    up = world.login("client_uploader", totp_clock)
    queries = up.get(f"/api/v1/portal/engagements/{world.engagement_id}/queries").json()
    assert [x["id"] for x in queries] == [q["id"]]
    assert up.post(f"/api/v1/portal/queries/{q['id']}/respond", json={"text": "Confirmed per bank statement."}).json()["status"] == "Responded"
    # Team notes stay invisible to the client.
    world.login("reviewer", totp_clock).post(f"/api/v1/engagements/{world.engagement_id}/review-notes", json={"text": "internal"})
    assert len(up.get(f"/api/v1/engagements/{world.engagement_id}/review-notes").json()) == 1
    assert world.login("staff", totp_clock).get("/api/v1/portal/engagements").status_code == 403
