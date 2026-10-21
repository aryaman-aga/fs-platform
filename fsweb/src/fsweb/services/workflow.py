"""Engagement lifecycle (06 §1): the state machine, the FINAL_LOCKED gates and the
"data change after FIXED_VERIFIED → CHECKED" rule."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsweb import audit
from fsweb.db import utcnow
from fsweb.models import (
    DisclosureChecklistItem,
    Engagement,
    File,
    FileVersion,
    FindingRow,
    MgmtItem,
    ReviewNote,
    Run,
)

STATES = [
    "DRAFT_SETUP",
    "DATA_RECEIVED",
    "CHECKED",
    "FIX_PLANNED",
    "FIX_APPROVED",
    "FIXED_VERIFIED",
    "REVIEW",
    "DRAFT_RELEASED_TO_CLIENT",
    "MGMT_CONFIRMED",
    "FINAL_LOCKED",
    "ARCHIVED",
]
POST_VERIFY = {"FIXED_VERIFIED", "REVIEW", "DRAFT_RELEASED_TO_CLIENT", "MGMT_CONFIRMED"}
LOCKED = {"FINAL_LOCKED", "ARCHIVED"}

# Manual transitions exposed through POST /engagements/{id}/transition: (from, to) -> permission.
MANUAL: dict[tuple[str, str], str] = {
    ("DRAFT_SETUP", "DATA_RECEIVED"): "engagement.transition",
    ("CHECKED", "REVIEW"): "engagement.transition",
    ("FIXED_VERIFIED", "REVIEW"): "engagement.transition",
    ("REVIEW", "CHECKED"): "engagement.transition",
    ("DRAFT_RELEASED_TO_CLIENT", "MGMT_CONFIRMED"): "release.client",
    ("DRAFT_RELEASED_TO_CLIENT", "REVIEW"): "release.client",
    ("MGMT_CONFIRMED", "FINAL_LOCKED"): "engagement.lock",
    ("FINAL_LOCKED", "ARCHIVED"): "engagement.lock",
}
# Transitions made by the platform itself when an event happens.
AUTOMATIC: set[tuple[str, str]] = {
    ("DRAFT_SETUP", "DATA_RECEIVED"),
    ("DATA_RECEIVED", "CHECKED"),
    ("CHECKED", "FIX_PLANNED"),
    ("FIX_PLANNED", "FIX_APPROVED"),
    ("FIX_APPROVED", "FIXED_VERIFIED"),
    ("FIX_PLANNED", "CHECKED"),
    ("FIX_APPROVED", "CHECKED"),
    ("REVIEW", "DRAFT_RELEASED_TO_CLIENT"),
    ("DRAFT_RELEASED_TO_CLIENT", "DRAFT_RELEASED_TO_CLIENT"),
    ("DRAFT_RELEASED_TO_CLIENT", "MGMT_CONFIRMED"),
    ("FINAL_LOCKED", "REVIEW"),
} | {(s, "CHECKED") for s in POST_VERIFY}


class TransitionError(ValueError):
    def __init__(self, message: str, gates: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.gates = gates or []


def set_status(db: Session, eng: Engagement, to: str, actor: audit.Actor, *, reason: str = "", automatic: bool = True, details: dict[str, Any] | None = None) -> None:
    frm = eng.status
    if frm == to:
        return
    allowed = (frm, to) in AUTOMATIC if automatic else (frm, to) in MANUAL
    if not allowed:
        raise TransitionError(f"transition {frm} → {to} is not allowed")
    eng.status = to
    audit.append(db, actor, "engagement.transition", client_id=eng.client_id, engagement_id=eng.id, object_type="engagement",
                 object_id=eng.id, details={"from": frm, "to": to, "reason": reason, "automatic": automatic, **(details or {})})


def advance(db: Session, eng: Engagement, frm: set[str], to: str, actor: audit.Actor, reason: str) -> bool:
    if eng.status in frm:
        set_status(db, eng, to, actor, reason=reason)
        return True
    return False


# ------------------------------------------------------------------ queries


def workbook_file(db: Session, eng: Engagement) -> File | None:
    return db.execute(
        select(File).where(File.engagement_id == eng.id, File.kind == "workbook").order_by(File.created_at)
    ).scalars().first()


def latest_version(db: Session, file_id: str) -> FileVersion | None:
    return db.execute(
        select(FileVersion).where(FileVersion.file_id == file_id).order_by(FileVersion.version_no.desc())
    ).scalars().first()


def latest_workbook_version(db: Session, eng: Engagement) -> FileVersion | None:
    f = workbook_file(db, eng)
    return latest_version(db, f.id) if f else None


def latest_run(db: Session, eng: Engagement, kind: str, *, version_id: str | None = None, mode: str | None = None, status: str = "succeeded") -> Run | None:
    stmt = select(Run).where(Run.engagement_id == eng.id, Run.kind == kind, Run.status == status).order_by(Run.created_at.desc())
    for run in db.execute(stmt).scalars():
        if version_id is not None and version_id not in (run.input_version_ids or []):
            continue
        if mode is not None and (run.params_json or {}).get("mode") != mode:
            continue
        return run
    return None


def high_findings(db: Session, run: Run) -> int:
    return int(
        db.execute(
            select(func.count()).select_from(FindingRow).where(
                FindingRow.run_id == run.id, FindingRow.severity == "High", FindingRow.status.not_in(["Not an issue", "Resolved"])
            )
        ).scalar_one()
    )


@dataclass
class Gate:
    code: str
    label: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "label": self.label, "ok": self.ok, "detail": self.detail}


def final_pdf_gates(db: Session, eng: Engagement) -> list[Gate]:
    """'Final' PDF only with zero High findings and passing signatory checks (02 §10A)."""
    gates: list[Gate] = []
    wb = latest_workbook_version(db, eng)
    check = latest_run(db, eng, "check", version_id=wb.id) if wb else None
    if wb is None or check is None:
        gates.append(Gate("checked", "Latest workbook version has been checked", False, "run checks on the latest version"))
        return gates
    highs = high_findings(db, check)
    gates.append(Gate("zero_high", "Zero High findings", highs == 0, f"{highs} High finding(s) open"))
    sig = int(db.execute(select(func.count()).select_from(FindingRow).where(
        FindingRow.run_id == check.id, FindingRow.rule_id.like("TXT-01%"), FindingRow.severity == "High",
        FindingRow.status.not_in(["Not an issue", "Resolved"]))).scalar_one())
    gates.append(Gate("signatories", "Signatory checks passed (DIN / FRN / membership no. / date / place)", sig == 0, f"{sig} signatory finding(s)"))
    return gates


def final_lock_gates(db: Session, eng: Engagement) -> list[Gate]:
    gates = final_pdf_gates(db, eng)
    open_notes = int(db.execute(select(func.count()).select_from(ReviewNote).where(
        ReviewNote.engagement_id == eng.id, ReviewNote.audience == "team", ReviewNote.status != "Cleared")).scalar_one())
    gates.append(Gate("review_notes", "All review notes cleared", open_notes == 0, f"{open_notes} open review note(s)"))
    wb = latest_workbook_version(db, eng)
    render = latest_run(db, eng, "render", version_id=wb.id, mode="final") if wb else None
    misses = None if render is None else int((render.result_json or {}).get("misses", -1))
    gates.append(Gate("final_pdf", "Final PDF rendered from the latest version and verified (PDF-01 zero misses)",
                      render is not None and misses == 0, "no verified final render" if render is None else f"{misses} miss(es)"))
    unanswered = int(db.execute(select(func.count()).select_from(DisclosureChecklistItem).where(
        DisclosureChecklistItem.engagement_id == eng.id, DisclosureChecklistItem.answer.is_(None))).scalar_one())
    total = int(db.execute(select(func.count()).select_from(DisclosureChecklistItem).where(
        DisclosureChecklistItem.engagement_id == eng.id)).scalar_one())
    gates.append(Gate("disclosure_checklist", "Disclosure checklist complete (E-PREP-02)", total > 0 and unanswered == 0,
                      "checklist not started" if total == 0 else f"{unanswered} unanswered item(s)"))
    pending = int(db.execute(select(func.count()).select_from(MgmtItem).where(
        MgmtItem.engagement_id == eng.id, MgmtItem.status != "confirmed")).scalar_one())
    gates.append(Gate("mgmt_confirmed", "Management representations confirmed", eng.status in {"MGMT_CONFIRMED", "FINAL_LOCKED"} and pending == 0,
                      f"status {eng.status}; {pending} unconfirmed item(s)"))
    return gates


def mark_locked(eng: Engagement, user_id: str) -> None:
    eng.locked_at = utcnow()
    eng.locked_by = user_id
