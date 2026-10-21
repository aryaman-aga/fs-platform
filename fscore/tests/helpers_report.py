"""Builders for report / CLI / audit / diff tests (synthetic data only)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fscore.models import (
    ApplyResult,
    BalancingAdjustment,
    CellRef,
    Certainty,
    CheckResult,
    Edit,
    EditGroup,
    EditKind,
    Evidence,
    Finding,
    FixKind,
    FixPlan,
    KeyFigure,
    PdfMiss,
    PdfPageMap,
    PdfReconResult,
    PrintedMove,
    RunInfo,
    SelfTestReport,
    Severity,
    VerificationReport,
    VerificationStep,
    XrefPair,
)
from fscore.prepare.spec import SheetSpec, WorkbookSpec
from fscore.prepare.writer import write_workbook

XSS = '<script>alert("x")</script>'


def make_finding(fid: str, rule: str = "CAST-01", severity: Severity = Severity.HIGH, *, sheet: str = "BS", cells: list[str] | None = None,
                 explanation: str = "Total does not cast on printed values", fix_kind: FixKind = FixKind.AUTO,
                 caption: str | None = "Total current liabilities", diff: str | None = "0.01") -> Finding:
    f = Finding(
        id=fid, rule_id=rule, severity=severity, certainty=Certainty.CERTAIN, category="Vertical casting", sheet=sheet,
        cells=cells or ["C25"], caption=caption, printed="500.00", expected="500.01",
        difference=Decimal(diff) if diff is not None else None,
        evidence=[Evidence(label="Trade payables", ref=CellRef(sheet=sheet, addr="C20"), printed="300.00", value=Decimal("300.00")),
                  Evidence(label="Other liabilities", ref=CellRef(sheet=sheet, addr="C21"), printed="200.01", value=Decimal("200.01"))],
        explanation=explanation, suggested_fix="Rewrite the total so it adds the printed lines", fix_kind=fix_kind,
    )
    f.fingerprint = f.compute_fingerprint()
    return f


def make_result(findings: list[Finding] | None = None, *, pdf: bool = True) -> CheckResult:
    now = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    run = RunInfo(
        engine_version="1.0.0", rule_set_version="2026.10.1", rule_set_hash="ab" * 32, file_name="Synthetic FS FY26.xlsx",
        file_sha256="cd" * 32, config_hash="ef" * 32, started_at=now, finished_at=now, duration_s=1.5, cells_read=1234, sheets=12,
        print_sheets=["BS", "PL", "CFS"], selftest=SelfTestReport(formulas=100, evaluated=98, matched=98, unsupported=2),
        warnings=["2 formulas use unsupported functions"],
    )
    findings = findings if findings is not None else [
        make_finding("F-0001"),
        make_finding("F-0002", "PLUG-01", cells=["C30"], explanation="Typed plug -0.01 inside a total"),
        make_finding("F-0003", "TXT-01", Severity.HIGH, sheet="PL", cells=["B60"], explanation="Director B shows director A's DIN",
                     fix_kind=FixKind.APPROVAL, diff=None),
        make_finding("F-0004", "RPT-09", Severity.MEDIUM, sheet="RPT", cells=["D12"], fix_kind=FixKind.MANUAL),
        make_finding("F-0005", "TXT-07", Severity.LOW, sheet="Note 4-10", cells=["E8"], fix_kind=FixKind.MANUAL, diff=None),
    ]
    return CheckResult(
        run=run,
        findings=findings,
        key_figures=[
            KeyFigure(key="pat", label="Profit for the year", period="CY", value=Decimal("123.45"), printed="123.45", ref=CellRef(sheet="PL", addr="C30")),
            KeyFigure(key="total_assets", label="Total assets", period="CY", value=Decimal("1000.00"), printed="1,000.00", ref=CellRef(sheet="BS", addr="C40")),
        ],
        statements={"balance_sheet": "BS", "profit_loss": "PL", "cash_flow": "CFS"},
        xref_map=[XrefPair(a=CellRef(sheet="BS", addr="C10"), b=CellRef(sheet="Note 4-10", addr="C25"), caption_a="Trade receivables",
                           caption_b="Total trade receivables", printed_a="250.00", printed_b="250.01", status="rounding_mismatch", source="note_ref")],
        pdf=PdfReconResult(pdf_sha256="11" * 32, pages=12, page_map=[PdfPageMap(page=1, sheet="BS", score=0.9)], values_checked=400,
                           misses=[PdfMiss(sheet="BS", cell="C25", printed="500.01", caption="Total current liabilities", pages=[1])],
                           stale=True, reprint_pages=[1]) if pdf else None,
    )


def make_plan() -> FixPlan:
    return FixPlan(
        id="P-test", source_file="Synthetic FS FY26.xlsx", source_sha256="cd" * 32, policy="totals_only",
        edits=[
            Edit(id="E-0001", sheet="BS", cell="C25", kind=EditKind.FORMULA, group=EditGroup.TOTALS_REWRITE, old_formula="SUM(C20:C21)",
                 new_formula="SUMPRODUCT(ROUND(C20:C21,2))", old_value="500", new_value="500.01", reason="CAST-01", finding_ids=["F-0001"], fix_kind=FixKind.AUTO,
                 caption="Total current liabilities"),
            Edit(id="E-0002", sheet="BS", cell="C30", kind=EditKind.FORMULA, group=EditGroup.PLUG_REMOVAL, old_formula="SUM(C26:C29)-0.01",
                 new_formula="SUM(C26:C29)", reason="PLUG-01", finding_ids=["F-0002"], fix_kind=FixKind.AUTO),
            Edit(id="E-0003", sheet="PL", cell="B60", kind=EditKind.TEXT, group=EditGroup.TYPED_TEXT, old_value="DIN: 01234567",
                 new_value="DIN: 07654321", reason="TXT-01", finding_ids=["F-0003"], fix_kind=FixKind.APPROVAL),
        ],
        balancing=[BalancingAdjustment(statement="balance_sheet", column="C", period="CY", gap=Decimal("0.02"),
                                       target=CellRef(sheet="Note 20-27", addr="C40"), adjustment=Decimal("0.02"), status="applied")],
        printed_moves=[
            PrintedMove(sheet="BS", cell="C25", caption="Total current liabilities", before="500.00", after="500.01", period="CY"),
            PrintedMove(sheet="BS", cell="D25", caption="Total current liabilities", before="400.00", after="400.01", period="PY", is_py=True),
            PrintedMove(sheet="BS", cell="C40", caption="Total assets", before="1,000.00", after="1,000.01", period="CY", is_key=True),
        ],
        unresolved=["CF closing cash differs from the cash note by 0.37 (exchange difference?)"],
    )


def make_applied(ok: bool = True) -> ApplyResult:
    return ApplyResult(
        ok=ok, out_path="Synthetic FS FY26 - casting corrected.xlsx", sha256="99" * 32, backup_path=None,
        applied_edit_ids=["E-0001", "E-0002"], skipped_edit_ids=["E-0003"],
        verification=VerificationReport(ok=ok, steps=[VerificationStep(name="cell diff", ok=True), VerificationStep(name="xml", ok=ok, detail="" if ok else "bad part")]),
        printed_moves=make_plan().printed_moves,
    )


def small_workbook(path: Path, *, variant: str = "a") -> Path:
    """Two-sheet statements workbook; variant 'b' changes figures, a formula, a format and hides a row."""
    spec = WorkbookSpec()
    bs = spec.add(SheetSpec("BS", role="bs", selected=True))
    bs.set(1, 1, "Synthetic Company Private Limited", style="title")
    bs.set(2, 1, "Balance Sheet as at March 31, 2026", style="subtitle")
    bs.set(3, 3, "As at March 31, 2026", style="header")
    bs.set(3, 4, "As at March 31, 2025", style="header")
    rows = [("Property, plant and equipment", 120.25, 110.10), ("Trade receivables", 250.0, 230.55), ("Cash and cash equivalents", 75.5, 60.0)]
    if variant == "b":
        rows[1] = ("Trade receivables", 250.01, 230.56)
    for i, (cap, cy, py) in enumerate(rows):
        r = 5 + i
        bs.set(r, 1, cap, style="caption")
        bs.set(r, 3, cy, style="amount")
        bs.set(r, 4, py, style="amount")
    bs.set(8, 1, "Total assets", style="caption_bold")
    bs.set(8, 3, formula="SUM(C5:C7)" if variant == "a" else "SUMPRODUCT(ROUND(C5:C7,2))", style="amount_total")
    bs.set(8, 4, formula="SUM(D5:D7)", style="amount_total")
    bs.set(9, 1, "Working note (rupees)", style="caption")
    bs.set(9, 3, 12345678.0, style="amount" if variant == "a" else "amount_plain")
    if variant == "b":
        bs.hidden_rows.add(9)
    bs.print_area = (1, 1, 9, 4)
    tb = spec.add(SheetSpec("NS_TB_FT27", role="tb"))
    tb.set(1, 1, "Account")
    tb.set(2, 1, "1000")
    tb.set(2, 2, 100.0)
    if variant == "b":
        spec.add(SheetSpec("Notes", role="note", selected=True)).set(1, 1, "Note 3 Property, plant and equipment")
    return write_workbook(spec, path)
