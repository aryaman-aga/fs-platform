"""XREF-01 / XREF-02 / XREF-04 and the cross-reference map; clean-workbook false-positive guard."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers_checks_x import config_for, found, run_rules, statement_sheet, summary, write_small

from fscore.checks.xref import dump_xref_map, load_xref_map
from fscore.config import EngagementConfig
from fscore.models import CellRef, XrefPair
from fscore.prepare.spec import WorkbookSpec
from fscore.synthetic.generator import make_workbook


@pytest.mark.parametrize(("layout", "exact", "seed"), [("annual", False, 7), ("annual", True, 11), ("spfs", False, 7), ("spfs", True, 23)])
def test_clean_synthetic_workbooks_have_no_findings(tmp_path: Path, layout: str, exact: bool, seed: int) -> None:
    res = make_workbook(tmp_path / "clean.xlsx", seed=seed, layout=layout, exact=exact)
    findings = run_rules(res.path, config_for(layout))
    assert findings == [], summary(findings)


def _link_book(tmp_path: Path, note_style: str) -> Path:
    spec = WorkbookSpec()
    bs = statement_sheet(spec, "BS", "Balance Sheet as at March 31, 2026", role="bs")
    notes = statement_sheet(spec, "Notes", "Notes to the financial statements")
    notes.set(6, 1, "10. Trade receivables", style="section")
    notes.set(7, 1, "Unsecured, considered good", style="caption")
    notes.set(7, 2, 123.456, style=note_style)
    notes.set(7, 3, 100.0, style="amount")
    bs.set(7, 1, "Trade receivables", style="caption")
    bs.set(7, 2, formula="Notes!B7", style="amount")
    bs.set(7, 3, formula="Notes!C7", style="amount")
    return write_small(spec, tmp_path / f"links_{note_style}.xlsx")


def test_xref01_link_prints_a_different_figure(tmp_path: Path) -> None:
    findings = run_rules(_link_book(tmp_path, "amount_0dp"))
    assert found(findings, "XREF-01", "BS", "B7")
    f = next(f for f in findings if f.rule_id == "XREF-01")
    assert f.printed == "123.46" and f.expected == "123" and len(f.evidence) == 2
    assert not found(findings, "XREF-01", "BS", "C7")


def test_xref01_same_format_links_are_clean(tmp_path: Path) -> None:
    assert [f for f in run_rules(_link_book(tmp_path, "amount")) if f.rule_id == "XREF-01"] == []


def _twin_book(tmp_path: Path, second: float, *, xref_map: Path | None = None) -> tuple[Path, EngagementConfig]:
    spec = WorkbookSpec()
    a = statement_sheet(spec, "Note 7", "Notes to the financial statements")
    a.set(6, 1, "7. Other financial assets", style="section")
    a.set(7, 1, "Security deposits for office premises", style="caption")
    a.set(7, 2, 250.40, style="amount")
    a.set(7, 3, 240.00, style="amount")
    b = statement_sheet(spec, "Note 32", "Notes to the financial statements")
    b.set(6, 1, "32. Financial instruments by category", style="section")
    b.set(7, 1, "Security deposits for office premises", style="caption")
    b.set(7, 2, second, style="amount")
    b.set(7, 3, 240.00, style="amount")
    cfg = EngagementConfig(xref_map=str(xref_map)) if xref_map else EngagementConfig()
    return write_small(spec, tmp_path / f"twins_{second}.xlsx"), cfg


def test_xref02_rounding_twin_between_notes(tmp_path: Path) -> None:
    path, cfg = _twin_book(tmp_path, 250.41)
    findings = run_rules(path, cfg)
    twin = [f for f in findings if f.rule_id == "XREF-02"]
    assert len(twin) == 1 and twin[0].severity.value == "High"
    assert str(abs(twin[0].difference or 0)) == "0.01"


def test_xref02_equal_figures_are_recorded_not_reported(tmp_path: Path) -> None:
    from helpers_checks_x import context

    from fscore.checks.xref import check_duplicates

    path, cfg = _twin_book(tmp_path, 250.40)
    ctx = context(path, cfg)
    check_duplicates(ctx)
    assert [f for f in ctx.findings if f.rule_id == "XREF-02"] == []
    assert any(p.status == "ok" and p.source == "caption" for p in ctx.shared["xref_map"])


def test_xref_map_round_trip_and_confirmed_mismatch(tmp_path: Path) -> None:
    pairs = [XrefPair(a=CellRef(sheet="Note 7", addr="B7"), b=CellRef(sheet="Note 32", addr="B7"),
                      caption_a="Security deposits for office premises", caption_b="Security deposits for office premises",
                      status="ok", source="caption", confirmed=True)]
    text = dump_xref_map(pairs)
    map_path = tmp_path / "xref_map.yaml"
    map_path.write_text(text, encoding="utf-8")
    loaded = load_xref_map(map_path)
    assert loaded[0].a == pairs[0].a and loaded[0].confirmed and loaded[0].source == "map"
    # a confirmed pair that no longer agrees is reported even when the difference is large
    path, cfg = _twin_book(tmp_path, 255.40, xref_map=map_path)
    findings = run_rules(path, cfg)
    assert found(findings, "XREF-02", "Note 7", "B7")


def _pl(spec: WorkbookSpec, name: str, cy_label: str, py_label: str, rows: list[tuple[str, float, float]]) -> None:
    s = statement_sheet(spec, name, f"Statement of Profit and Loss for the {cy_label.lower()}", cy_label, py_label, role="pl")
    for i, (cap, cy, py) in enumerate(rows):
        s.set(7 + i, 1, cap, style="caption")
        s.set(7 + i, 2, cy, style="amount")
        s.set(7 + i, 3, py, style="amount")


def test_xref04_comparatives_against_signed_prior_year_workbook(tmp_path: Path) -> None:
    cur = WorkbookSpec()
    _pl(cur, "PL", "Year ended March 31, 2026", "Year ended March 31, 2025",
        [("Revenue from operations", 620.0, 500.0), ("Other income", 12.0, 10.0), ("Total expenses", 560.0, 470.0)])
    current = write_small(cur, tmp_path / "current.xlsx")
    signed = WorkbookSpec()
    _pl(signed, "PL", "Year ended March 31, 2025", "Year ended March 31, 2024",
        [("Revenue from operations", 510.0, 430.0), ("Other income", 10.0, 9.0), ("Total expenses", 470.0, 400.0)])
    prior = write_small(signed, tmp_path / "signed_py.xlsx")
    findings = run_rules(current, prior_year=prior)
    xr = [f for f in findings if f.rule_id == "XREF-04"]
    assert [(f.sheet, f.cells) for f in xr] == [("PL", ["C7"])]
    assert xr[0].expected == "510.00" and str(xr[0].difference) == "-10.00"


def test_xref04_comparatives_against_signed_prior_year_pdf(tmp_path: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "signed_py.pdf"
    c = canvas.Canvas(str(pdf), pagesize=A4)
    y = 780
    for cap, note, cy, py in (("Revenue from operations", "20", "510.00", "430.00"), ("Other income", "21", "10.00", "9.00"),
                              ("Total expenses", "", "470.00", "400.00")):
        c.drawString(50, y, cap)
        if note:
            c.drawString(300, y, note)
        c.drawRightString(430, y, cy)
        c.drawRightString(520, y, py)
        y -= 18
    c.save()
    cur = WorkbookSpec()
    _pl(cur, "PL", "Year ended March 31, 2026", "Year ended March 31, 2025",
        [("Revenue from operations", 620.0, 500.0), ("Other income", 12.0, 10.0), ("Total expenses", 560.0, 470.0)])
    current = write_small(cur, tmp_path / "current_pdf.xlsx")
    findings = run_rules(current, prior_year=pdf)
    assert [(f.rule_id, f.cells) for f in findings if f.rule_id == "XREF-04"] == [("XREF-04", ["C7"])]


def test_xref_map_published_on_the_context(tmp_path: Path) -> None:
    from helpers_checks_x import context

    from fscore.registry import load_all_checks

    res = make_workbook(tmp_path / "map.xlsx", seed=7)
    ctx = context(res.path)
    for impl in load_all_checks():
        if impl.name.startswith(("fscore.checks.xref", "fscore.checks.tie_outs")):
            impl.fn(ctx)
    pairs = ctx.shared["xref_map"]
    sources = {p.source for p in pairs}
    assert {"link", "note_ref"} <= sources
    assert all(p.status == "ok" for p in pairs if p.source in ("link", "note_ref"))
