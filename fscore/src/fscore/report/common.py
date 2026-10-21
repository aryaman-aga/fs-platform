"""Helpers shared by the exceptions register and the HTML report."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from fscore.models import (
    SEVERITY_ORDER,
    ApplyResult,
    CheckResult,
    Edit,
    Finding,
    FixKind,
    FixPlan,
    PrintedMove,
)

ROLE_LABELS: dict[str, str] = {
    "balance_sheet": "Balance Sheet",
    "bs": "Balance Sheet",
    "profit_loss": "Statement of Profit and Loss",
    "pl": "Statement of Profit and Loss",
    "cash_flow": "Cash Flow Statement",
    "cf": "Cash Flow Statement",
    "soce": "Statement of Changes in Equity",
    "rpt": "Related party transactions",
    "going_concern": "Going concern",
    "gc": "Going concern",
    "ratios": "Ratios",
    "share_capital": "Share capital",
    "trial_balance": "Trial balance",
    "tb": "Trial balance",
}

_FILENAME_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def register_filename(client: str, when: date | None = None) -> str:
    """``<client> FS-Check <date>.xlsx`` with characters that are illegal in file names removed."""
    name = _FILENAME_BAD.sub(" ", client or "Client")
    name = re.sub(r"\s+", " ", name).strip(" .") or "Client"
    day = when or date.today()
    return f"{name[:80]} FS-Check {day.isoformat()}.xlsx"


def statement_label(result: CheckResult, sheet: str | None) -> str:
    """Which statement / note a finding belongs to (falls back to the sheet name)."""
    if not sheet:
        return "Workbook"
    for role, name in result.statements.items():
        if name == sheet:
            return ROLE_LABELS.get(role, role.replace("_", " ").title())
    return sheet


def sorted_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.rule_id, f.sheet or "", f.cells[:1], f.id))


def edits_by_finding(plan: FixPlan | None) -> dict[str, list[Edit]]:
    out: dict[str, list[Edit]] = {}
    if plan is None:
        return out
    for e in plan.edits:
        for fid in e.finding_ids:
            out.setdefault(fid, []).append(e)
    return out


def finding_status(f: Finding, plan: FixPlan | None, applied: ApplyResult | None, by_finding: dict[str, list[Edit]] | None = None) -> str:
    """Register status of a finding given the plan and what was applied."""
    edits = (by_finding if by_finding is not None else edits_by_finding(plan)).get(f.id, [])
    applied_ids = set(applied.applied_edit_ids) if applied is not None and applied.ok else set()
    if edits:
        if applied_ids and all(e.id in applied_ids for e in edits):
            return "Fixed"
        if any(e.id in applied_ids for e in edits):
            return "Partly fixed"
        if any(e.fix_kind == FixKind.APPROVAL and not e.approved for e in edits):
            return "Proposed – needs approval"
        return "Proposed"
    if f.fix_kind == FixKind.APPROVAL:
        return "Open – needs approval"
    if f.fix_kind == FixKind.MANUAL:
        return "Open – manual"
    if f.fix_kind == FixKind.NONE:
        return "Information"
    return "Open"


def printed_moves(plan: FixPlan | None, applied: ApplyResult | None) -> list[PrintedMove]:
    if applied is not None and applied.printed_moves:
        return applied.printed_moves
    return plan.printed_moves if plan is not None else []


def edit_status(e: Edit, applied: ApplyResult | None) -> str:
    if applied is None:
        return "Proposed"
    if e.id in applied.applied_edit_ids:
        return "Applied" if applied.ok else "Applied (verification failed)"
    if e.id in applied.skipped_edit_ids:
        return "Skipped"
    return "Not applied"


def reprint_sheets(plan: FixPlan | None, applied: ApplyResult | None) -> list[str]:
    seen: list[str] = []
    for m in printed_moves(plan, applied):
        if m.sheet not in seen:
            seen.append(m.sheet)
    return seen


def pdf_needs_reprint(result: CheckResult, plan: FixPlan | None = None, applied: ApplyResult | None = None) -> bool:
    if result.pdf is not None and (result.pdf.stale or result.pdf.misses or result.pdf.reprint_pages):
        return True
    return bool(printed_moves(plan, applied))


def to_float(d: Decimal | None) -> float | None:
    return None if d is None else float(d)


def clip(text: str | None, limit: int = 32_000) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[: limit - 1] + "…"
