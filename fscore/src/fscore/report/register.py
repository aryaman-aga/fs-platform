"""Exceptions register workbook (02 §10.1).

A *new* file written with xlsxwriter (styling freedom; client workbooks are
never written this way). Sheets: Exceptions, Evidence, Formula changes,
Printed figures moved, PDF reconciliation, Cross-reference map, Key figures,
Run info. Every sheet has a frozen header, an autofilter and set widths.
Workbook text is always written as strings, never as formulas.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import xlsxwriter
from xlsxwriter.worksheet import Worksheet

from fscore.models import ApplyResult, CheckResult, FixPlan, Severity
from fscore.report.common import (
    clip,
    edit_status,
    edits_by_finding,
    finding_status,
    pdf_needs_reprint,
    printed_moves,
    sorted_findings,
    statement_label,
    to_float,
)

EXCEPTION_HEADERS = [
    "#", "Priority", "Type/Rule", "Statement/Note", "Sheet", "Cell(s)", "Issue", "As printed",
    "Visible sum / should be", "Diff", "Suggested fix", "Status", "Reviewer comment",
]
EVIDENCE_HEADERS = ["Finding #", "Rule", "Evidence", "Sheet", "Cell", "Printed", "Value", "Note"]
CHANGE_HEADERS = [
    "Edit", "Group", "Sheet", "Cell", "Caption", "Old formula / value", "New formula", "Old value", "New value",
    "Reason", "Findings", "Fix kind", "Status",
]
MOVE_HEADERS = ["Sheet", "Cell", "Caption", "Period", "Printed before", "Printed after", "PY figure", "Key figure"]
PDF_HEADERS = ["Page", "Sheet", "Cell", "Caption", "Workbook value", "Issue"]
XREF_HEADERS = ["#", "Cell A", "Caption A", "Printed A", "Cell B", "Caption B", "Printed B", "Status", "Source", "Confirmed"]
KEY_HEADERS = ["Key", "Label", "Period", "Printed", "Value", "Cell", "After fix", "Moved"]
INFO_HEADERS = ["Item", "Value"]

_SEVERITY_FILL = {
    Severity.HIGH: "#F8D7DA",
    Severity.MEDIUM: "#FFE8C2",
    Severity.LOW: "#FFF7CC",
    Severity.INFO: "#E9ECEF",
}
_DIFF_FORMAT = '#,##0.00;(#,##0.00);"-"'


class _Writer:
    def __init__(self, wb: xlsxwriter.Workbook) -> None:
        self.wb = wb
        base = {"font_name": "Arial", "font_size": 9, "valign": "top"}
        self.f_header = wb.add_format({**base, "bold": True, "bg_color": "#1F3A5F", "font_color": "#FFFFFF", "text_wrap": True, "border": 1})
        self.f_text = wb.add_format({**base, "text_wrap": True})
        self.f_num = wb.add_format({**base, "num_format": _DIFF_FORMAT})
        self.f_int = wb.add_format({**base, "num_format": "0"})
        self.f_title = wb.add_format({**base, "bold": True, "font_size": 11})
        self.f_bold = wb.add_format({**base, "bold": True, "text_wrap": True})
        self.f_flag = wb.add_format({**base, "text_wrap": True, "bg_color": "#FFF3CD"})
        self.f_flag_num = wb.add_format({**base, "bg_color": "#FFF3CD", "num_format": _DIFF_FORMAT})
        self.f_flag_int = wb.add_format({**base, "bg_color": "#FFF3CD", "num_format": "0"})
        self.f_sev = {s: wb.add_format({**base, "bold": True, "bg_color": c, "text_wrap": True}) for s, c in _SEVERITY_FILL.items()}

    def table(self, ws: Worksheet, headers: list[str], widths: list[float], rows: list[list[Any]], *, row_formats: list[Any] | None = None) -> int:
        """Write a header + rows; returns the number of data rows written."""
        for c, (h, w) in enumerate(zip(headers, widths, strict=True)):
            ws.write_string(0, c, h, self.f_header)
            ws.set_column(c, c, w)
        ws.freeze_panes(1, 0)
        for r, row in enumerate(rows, start=1):
            fmt_row = row_formats[r - 1] if row_formats else None
            for c, v in enumerate(row):
                self.cell(ws, r, c, v, fmt_row)
        last = max(len(rows), 1)
        ws.autofilter(0, 0, last, len(headers) - 1)
        return len(rows)

    def cell(self, ws: Worksheet, r: int, c: int, v: Any, fmt: Any = None) -> None:
        if v is None or v == "":
            ws.write_blank(r, c, None, fmt or self.f_text)
        elif isinstance(v, bool):
            ws.write_string(r, c, "Yes" if v else "", fmt or self.f_text)
        elif isinstance(v, int):
            ws.write_number(r, c, v, self.f_flag_int if fmt is self.f_flag else self.f_int)
        elif isinstance(v, float):
            ws.write_number(r, c, v, self.f_flag_num if fmt is self.f_flag else self.f_num)
        else:
            ws.write_string(r, c, clip(str(v)), fmt or self.f_text)


def write_register(
    result: CheckResult,
    out_path: Path,
    *,
    plan: FixPlan | None = None,
    applied: ApplyResult | None = None,
) -> Path:
    """Write the exceptions register to ``out_path`` (atomically) and return the path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".xlsx", prefix=".register-", dir=str(out_path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        wb = xlsxwriter.Workbook(str(tmp), {"strings_to_numbers": False, "strings_to_formulas": False, "strings_to_urls": False})
        wb.set_properties({"title": "FS-Check exceptions register", "subject": result.run.file_name, "comments": "Generated offline by fscore"})
        w = _Writer(wb)
        _exceptions(w, wb.add_worksheet("Exceptions"), result, plan, applied)
        _evidence(w, wb.add_worksheet("Evidence"), result)
        _changes(w, wb.add_worksheet("Formula changes"), plan, applied)
        _moves(w, wb.add_worksheet("Printed figures moved"), plan, applied)
        _pdf(w, wb.add_worksheet("PDF reconciliation"), result, plan, applied)
        _xref(w, wb.add_worksheet("Cross-reference map"), result)
        _key_figures(w, wb.add_worksheet("Key figures"), result, plan, applied)
        _run_info(w, wb.add_worksheet("Run info"), result, plan, applied)
        wb.close()
        os.replace(tmp, out_path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return out_path


def _exceptions(w: _Writer, ws: Worksheet, result: CheckResult, plan: FixPlan | None, applied: ApplyResult | None) -> None:
    by_finding = edits_by_finding(plan)
    rows: list[list[Any]] = []
    fmts: list[Any] = []
    for i, f in enumerate(sorted_findings(result.findings), start=1):
        rows.append([
            i,
            f"{f.severity.value} / {f.certainty.value}",
            f"{f.rule_id} {f.category}".strip(),
            statement_label(result, f.sheet),
            f.sheet or "",
            ", ".join(f.cells),
            f.explanation + (f" ({f.caption})" if f.caption and f.caption not in f.explanation else ""),
            f.printed or "",
            f.expected or "",
            to_float(f.difference),
            f.suggested_fix or "",
            finding_status(f, plan, applied, by_finding),
            "",
        ])
        fmts.append(None)
    w.table(ws, EXCEPTION_HEADERS, [5, 16, 30, 22, 16, 14, 60, 14, 18, 10, 40, 22, 30], rows, row_formats=fmts)
    # colour the priority cell by severity
    for r, f in enumerate(sorted_findings(result.findings), start=1):
        ws.write_string(r, 1, f"{f.severity.value} / {f.certainty.value}", w.f_sev.get(f.severity, w.f_text))
    if not rows:
        ws.write_string(1, 6, "No findings.", w.f_text)


def _evidence(w: _Writer, ws: Worksheet, result: CheckResult) -> None:
    rows: list[list[Any]] = []
    for i, f in enumerate(sorted_findings(result.findings), start=1):
        for ev in f.evidence:
            rows.append([
                i, f.rule_id, ev.label, ev.ref.sheet if ev.ref else "", ev.ref.addr if ev.ref else "",
                ev.printed or "", to_float(ev.value), ev.note or "",
            ])
    w.table(ws, EVIDENCE_HEADERS, [9, 10, 40, 18, 8, 14, 14, 40], rows)


def _changes(w: _Writer, ws: Worksheet, plan: FixPlan | None, applied: ApplyResult | None) -> None:
    rows: list[list[Any]] = []
    for e in plan.edits if plan is not None else []:
        old = f"={e.old_formula}" if e.old_formula else (e.old_value or "")
        new = f"={e.new_formula}" if e.new_formula else (e.new_value or "")
        rows.append([
            e.id, e.group.value, e.sheet, e.cell, e.caption or "", old, new, e.old_value or "", e.new_value or "",
            e.reason, ", ".join(e.finding_ids), e.fix_kind.value, edit_status(e, applied),
        ])
    w.table(ws, CHANGE_HEADERS, [8, 14, 16, 8, 30, 36, 36, 12, 12, 40, 14, 10, 18], rows)


def _moves(w: _Writer, ws: Worksheet, plan: FixPlan | None, applied: ApplyResult | None) -> None:
    moves = printed_moves(plan, applied)
    rows = [[m.sheet, m.cell, m.caption or "", m.period or "", m.before or "", m.after or "", m.is_py, m.is_key] for m in moves]
    fmts = [w.f_flag if (m.is_py or m.is_key) else None for m in moves]
    w.table(ws, MOVE_HEADERS, [16, 8, 40, 10, 16, 16, 10, 10], rows, row_formats=fmts)


def _pdf(w: _Writer, ws: Worksheet, result: CheckResult, plan: FixPlan | None, applied: ApplyResult | None) -> None:
    rows: list[list[Any]] = []
    pdf = result.pdf
    if pdf is None:
        rows.append(["", "", "", "", "", "No printed PDF was supplied for this run."])
    else:
        sheet_pages: dict[str, list[int]] = {}
        for pm in pdf.page_map:
            if pm.sheet:
                sheet_pages.setdefault(pm.sheet, []).append(pm.page)
        for m in pdf.misses:
            pages = m.pages or sheet_pages.get(m.sheet, [])
            rows.append([", ".join(str(p) for p in pages), m.sheet, m.cell, m.caption or "", m.printed, "Value not found on the mapped page(s)"])
        for tok in pdf.error_tokens:
            rows.append(["", "", "", "", "", f"Error value in PDF: {tok}"])
        for t in pdf.text_mismatches:
            rows.append(["", "", "", "", "", f"Text mismatch: {t}"])
        for p in pdf.blank_pages:
            rows.append([str(p), "", "", "", "", "Blank page"])
        if pdf.stale:
            rows.insert(0, ["", "", "", "", "", "The PDF appears to be printed from an older version of the workbook."])
        for p in pdf.reprint_pages:
            rows.append([str(p), next((pm.sheet or "" for pm in pdf.page_map if pm.page == p), ""), "", "", "", "Re-print this page"])
    for sheet in sorted({m.sheet for m in printed_moves(plan, applied)}):
        rows.append(["", sheet, "", "", "", "Printed figures move after the fix: re-print this sheet"])
    w.table(ws, PDF_HEADERS, [10, 18, 8, 36, 16, 60], rows)
    base = len(rows) + 3
    ws.write_string(base, 0, "Summary", w.f_title)
    summary: list[tuple[str, Any]] = [("PDF must be re-printed", "Yes" if pdf_needs_reprint(result, plan, applied) else "No")]
    if pdf is not None:
        summary += [
            ("PDF SHA-256", pdf.pdf_sha256),
            ("Pages", pdf.pages),
            ("Values checked", pdf.values_checked),
            ("Values missing", len(pdf.misses)),
            ("Stale", "Yes" if pdf.stale else "No"),
            ("Page map", "; ".join(f"p{pm.page}: {pm.sheet or '?'}" for pm in pdf.page_map)),
        ]
    for i, (k, v) in enumerate(summary, start=1):
        ws.write_string(base + i, 0, k, w.f_bold)
        w.cell(ws, base + i, 1, v)


def _xref(w: _Writer, ws: Worksheet, result: CheckResult) -> None:
    rows = [
        [i, str(p.a), p.caption_a, p.printed_a or "", str(p.b), p.caption_b, p.printed_b or "", p.status, p.source, p.confirmed]
        for i, p in enumerate(result.xref_map, start=1)
    ]
    w.table(ws, XREF_HEADERS, [5, 20, 32, 14, 20, 32, 14, 16, 10, 10], rows)


def _key_figures(w: _Writer, ws: Worksheet, result: CheckResult, plan: FixPlan | None, applied: ApplyResult | None) -> None:
    moved = {(m.sheet, m.cell): m for m in printed_moves(plan, applied)}
    rows: list[list[Any]] = []
    fmts: list[Any] = []
    for k in result.key_figures:
        m = moved.get((k.ref.sheet, k.ref.addr)) if k.ref else None
        rows.append([k.key, k.label, k.period, k.printed or "", to_float(k.value), str(k.ref) if k.ref else "", m.after if m else "", m is not None])
        fmts.append(w.f_flag if m is not None else None)
    w.table(ws, KEY_HEADERS, [16, 36, 10, 14, 14, 18, 14, 8], rows, row_formats=fmts)


def _run_info(w: _Writer, ws: Worksheet, result: CheckResult, plan: FixPlan | None, applied: ApplyResult | None) -> None:
    run = result.run
    st = run.selftest
    counts = result.counts()
    items: list[tuple[str, Any]] = [
        ("File", run.file_name),
        ("File SHA-256", run.file_sha256),
        ("Configuration hash", run.config_hash),
        ("Engine version", run.engine_version),
        ("Rule-set version", run.rule_set_version),
        ("Rule-set hash", run.rule_set_hash),
        ("Started", run.started_at.isoformat()),
        ("Finished", run.finished_at.isoformat() if run.finished_at else ""),
        ("Duration (s)", round(run.duration_s, 2)),
        ("Sheets", run.sheets),
        ("Cells read", run.cells_read),
        ("Print sheets", ", ".join(run.print_sheets)),
        ("Findings – High", counts.get("High", 0)),
        ("Findings – Medium", counts.get("Medium", 0)),
        ("Findings – Low", counts.get("Low", 0)),
        ("Findings – Info", counts.get("Info", 0)),
        ("Self-test: formulas", st.formulas),
        ("Self-test: evaluated", st.evaluated),
        ("Self-test: matched", st.matched),
        ("Self-test: match rate", f"{st.match_rate:.2%}"),
        ("Self-test: unsupported", st.unsupported),
        ("Self-test: missing cached values", st.missing_cache),
        ("Unsupported functions", ", ".join(st.unsupported_functions)),
    ]
    for warning in run.warnings:
        items.append(("Warning", warning))
    if plan is not None:
        items += [
            ("Fix plan", plan.id or ""),
            ("Fix plan SHA-256", plan.plan_sha256),
            ("Fix policy", plan.policy),
            ("Planned edits", len(plan.edits)),
            ("Fixed point reached", "Yes" if plan.converged else f"No ({plan.passes} passes)"),
        ]
        for b in plan.balancing:
            items.append((f"Balancing – {b.statement} {b.column}", f"gap {b.gap} → {b.status} {b.adjustment} {b.note}".strip()))
        for u in plan.unresolved:
            items.append(("Unresolved", u))
    if applied is not None:
        items += [
            ("Corrected copy", Path(applied.out_path).name if applied.out_path else ""),
            ("Corrected copy SHA-256", applied.sha256 or ""),
            ("Backup", Path(applied.backup_path).name if applied.backup_path else ""),
            ("Post-write verification", "Passed" if applied.verification.ok else "FAILED"),
        ]
        for step in applied.verification.steps:
            items.append((f"Verification – {step.name}", ("ok" if step.ok else "FAILED") + (f": {step.detail}" if step.detail else "")))
    rows = [[k, v] for k, v in items]
    w.table(ws, INFO_HEADERS, [34, 90], rows)
