"""PDF reconciliation: PDF-01 staleness, PDF-02 text, PDF-03 invisible values, PDF-04 PDF-only."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from helpers_pdf import make_workbook

from fscore.checks.base import CheckContext
from fscore.checks.pdf_recon import check_pdf_only, pdf_checks, reconcile, reprint_list
from fscore.config import EngagementConfig
from fscore.io.xlsx_reader import Workbook
from fscore.pdf.render_native import render_native

CFG = EngagementConfig(client="Corvus")
SHEETS = ["BS", "Note 3-5"]


def _pdf(xlsx: Path, out: Path, **kw: object) -> Path:
    render_native(xlsx, out, SHEETS, draft=True, run_id="R-T")
    return out


@pytest.fixture
def base(tmp_path: Path) -> tuple[Path, Path]:
    wb = make_workbook(tmp_path / "v1.xlsx", hidden_value=0.0)
    return wb, _pdf(wb, tmp_path / "v1.pdf")


def test_current_pdf_reconciles(base: tuple[Path, Path]) -> None:
    wb, pdf = base
    r = reconcile(wb, pdf, CFG)
    assert r.misses == []
    assert not r.stale
    assert r.values_checked > 200
    assert [m.sheet for m in r.page_map] == ["BS", "Note 3-5", "Note 3-5", "Note 3-5"]
    assert r.error_tokens == []
    assert r.blank_pages == []
    assert r.text_mismatches == []


def test_stale_pdf_reports_misses_and_reprint_pages(base: tuple[Path, Path], tmp_path: Path) -> None:
    _wb, old_pdf = base
    newer = make_workbook(tmp_path / "v2.xlsx", hidden_value=0.0, bad_total_delta=0.01)
    r = reconcile(newer, old_pdf, CFG)
    assert r.stale
    assert [(m.sheet, m.cell, m.printed) for m in r.misses] == [("BS", "C14", "1,236,691.27")]
    assert r.reprint_pages == [1]
    assert reprint_list(r, [("Note 3-5", "B10")]) == [2, 3, 4]


def test_registered_check_emits_pdf01(base: tuple[Path, Path], tmp_path: Path) -> None:
    _wb, old_pdf = base
    newer = make_workbook(tmp_path / "v2.xlsx", hidden_value=0.0, bad_total_delta=0.01)
    with Workbook.open(newer) as wb:
        ctx = CheckContext(wb, CFG, pdf_path=old_pdf)
        pdf_checks(ctx)
        rules = [f.rule_id for f in ctx.findings]
        assert rules.count("PDF-01") == 2  # overall staleness + the sheet
        sheet_f = next(f for f in ctx.findings if f.rule_id == "PDF-01" and f.sheet == "BS")
        assert sheet_f.cells == ["C14"]
        assert sheet_f.evidence[0].printed == "1,236,691.27"
        assert "pdf_recon" in ctx.shared


def test_no_pdf_means_no_pdf_findings(base: tuple[Path, Path]) -> None:
    wb, _pdf_path = base
    with Workbook.open(wb) as book:
        ctx = CheckContext(book, CFG)
        pdf_checks(ctx)
        assert ctx.findings == []


def test_pdf02_error_token_and_din_mismatch(tmp_path: Path) -> None:
    printed = make_workbook(tmp_path / "printed.xlsx", error_cell=True, din_second="07654321")
    pdf = _pdf(printed, tmp_path / "p.pdf")
    current = make_workbook(tmp_path / "current.xlsx", error_cell=True, din_second="07654322")
    r = reconcile(current, pdf, CFG)
    assert any("#REF!" in e for e in r.error_tokens)
    assert any("07654322" in m and "07654321" in m for m in r.text_mismatches)
    with Workbook.open(current) as wb:
        ctx = CheckContext(wb, CFG, pdf_path=pdf)
        pdf_checks(ctx)
        p2 = [f for f in ctx.findings if f.rule_id == "PDF-02"]
        assert any("#REF!" in f.explanation for f in p2)
        din = next(f for f in p2 if f.expected == "07654322")
        assert din.printed == "07654321"
        assert din.sheet == "BS"


def test_pdf02_placeholder_printed(tmp_path: Path) -> None:
    wb = make_workbook(tmp_path / "ph.xlsx", din_second="XXX")
    pdf = _pdf(wb, tmp_path / "ph.pdf")
    r = reconcile(wb, pdf, CFG)
    assert any(m.startswith("placeholder") and "XXX" in m for m in r.text_mismatches)


def test_pdf03_white_font_value(tmp_path: Path) -> None:
    wb = make_workbook(tmp_path / "w.xlsx", hidden_value=0.0, white_value=123456.78)
    pdf = _pdf(wb, tmp_path / "w.pdf")
    r = reconcile(wb, pdf, CFG)
    assert r.misses == []  # invisible cells are not expected on paper
    with Workbook.open(wb) as book:
        ctx = CheckContext(book, CFG, pdf_path=pdf)
        pdf_checks(ctx)
        p3 = [f for f in ctx.findings if f.rule_id == "PDF-03"]
        assert len(p3) == 1
        assert p3[0].printed == "123,456.78"
        assert "invisible" in p3[0].explanation


def test_pdf04_pdf_only_casting(tmp_path: Path) -> None:
    clean = _pdf(make_workbook(tmp_path / "c.xlsx", hidden_value=0.0), tmp_path / "c.pdf")
    findings, res = check_pdf_only(clean, CFG)
    assert findings == []
    assert res.pages == 4
    bad = _pdf(make_workbook(tmp_path / "b.xlsx", hidden_value=0.0, bad_total_delta=0.01), tmp_path / "b.pdf")
    findings, _ = check_pdf_only(bad, CFG)
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "PDF-04"
    assert f.caption == "Total assets"
    assert f.difference == Decimal("0.01")
    assert f.certainty.value == "Review"
    assert f.fix_kind.value == "none"
    assert f.pages_in_pdf == [1]
    hidden = _pdf(make_workbook(tmp_path / "h.xlsx", hidden_value=777.77), tmp_path / "h.pdf")
    findings, _ = check_pdf_only(hidden, CFG)
    assert {str(f.difference) for f in findings} == {"777.77"}  # hidden contribution does not cast on paper


def test_unreadable_pdf_is_a_warning_not_a_crash(base: tuple[Path, Path], tmp_path: Path) -> None:
    wb, _ = base
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"not a pdf")
    with Workbook.open(wb) as book:
        ctx = CheckContext(book, CFG, pdf_path=junk)
        pdf_checks(ctx)
        assert ctx.findings == []
        assert any("PDF" in w for w in ctx.warnings)


def test_text_beyond_the_page_edge_is_reported(tmp_path: Path) -> None:
    """Post-render verification: a renderer that prints past the paper (e.g. an over-wide column) is refused."""
    from reportlab.pdfgen import canvas

    from fscore.checks.pdf_recon import cutoff_problems
    from fscore.io.pdf_reader import read_pdf

    pdfs = {}
    for name, x in (("inside", 72.0), ("cut", 560.0)):
        pdfs[name] = tmp_path / f"{name}.pdf"
        c = canvas.Canvas(str(pdfs[name]), pagesize=(595.3, 841.9))
        c.setFont("Helvetica", 12)
        c.drawString(x, 700, "Corporate information of the Company")
        c.save()
    assert cutoff_problems(read_pdf(pdfs["inside"])) == []
    problems = cutoff_problems(read_pdf(pdfs["cut"]))
    assert len(problems) == 1 and "page 1" in problems[0]


def test_rupee_glyph_problems_are_reported(tmp_path: Path) -> None:
    """The unit line's '₹' must render on the sheet's pages, never as '?' or a replacement character."""
    from types import SimpleNamespace

    from fscore.checks.pdf_recon import rupee_problems
    from fscore.synthetic.generator import make_workbook as make_synthetic

    path = Path(make_synthetic(tmp_path / "s.xlsx", seed=7).path)
    with Workbook.open(path) as wb:
        ctx = CheckContext(wb, EngagementConfig())
        sheet = ctx.print_sheets[0].name

        def state(text: str) -> SimpleNamespace:
            page = SimpleNamespace(number=1, visible_text=text)
            return SimpleNamespace(sheet_pages={sheet: [1]}, doc=SimpleNamespace(pages=[page], page=lambda _n: page))

        assert rupee_problems(ctx, state("(All amounts in ₹ lakhs, unless otherwise stated)")) == []  # type: ignore[arg-type]
        assert any("printed as '?'" in p for p in rupee_problems(ctx, state("(All amounts in ? lakhs)")))  # type: ignore[arg-type]
        assert any("does not render" in p for p in rupee_problems(ctx, state("(All amounts in lakhs)")))  # type: ignore[arg-type]
        assert any("replacement characters" in p for p in rupee_problems(ctx, state("₹ lakhs �")))  # type: ignore[arg-type]
