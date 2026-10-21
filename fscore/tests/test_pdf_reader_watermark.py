"""PDF reader tokens, overlay exclusion and the E-SEC-10 watermark stamp."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from helpers_pdf import make_workbook

from fscore.checks.pdf_recon import reconcile
from fscore.config import EngagementConfig
from fscore.io.pdf_reader import is_numeric_word, read_pdf
from fscore.pdf.render_native import render_native
from fscore.pdf.watermark import set_bookmarks, stamp


def test_numeric_word_classification() -> None:
    for t in ("(1,253.04)", "12,34,567.89", "-", "5.00%", "0.50", "1,000"):
        assert is_numeric_word(t), t
    for t in ("Note", "4-10", "31,", "#REF!", "U72900KA2015PTC081234", ""):
        assert not is_numeric_word(t), t


def test_reader_tokens_and_lines(tmp_path: Path) -> None:
    wb = make_workbook(tmp_path / "r.xlsx", hidden_value=0.0)
    pdf = render_native(wb, tmp_path / "r.pdf", ["BS"], draft=True, run_id="R-9")
    assert pdf.pages == 1
    doc = read_pdf(tmp_path / "r.pdf")
    page = doc.pages[0]
    values = {t.text: t.value for t in page.numbers}
    assert values["(12.34)"] == Decimal("-12.34")
    assert values["1,234,567.89"] == Decimal("1234567.89")
    assert values["-"] == Decimal(0)
    line = next(ln for ln in page.lines if ln.text.startswith("Trade receivables"))
    assert line.text.endswith("1,234,567.89 987,654.32")
    # the overlay (watermark / footer) never mixes with content
    assert all(w.text != "R-9" for w in page.words)
    assert any(w.text == "R-9" for w in page.overlay_words)
    assert page.orientation == "portrait"
    assert not page.is_blank


def test_stamp_marks_every_page_and_keeps_reconciliation(tmp_path: Path) -> None:
    wb = make_workbook(tmp_path / "s.xlsx", hidden_value=0.0)
    render_native(wb, tmp_path / "s.pdf", ["BS", "Note 3-5"], draft=False)
    out = stamp(tmp_path / "s.pdf", tmp_path / "s_wm.pdf", "Asha Raman · 21-Oct-2026 10:30 · DRAFT")
    doc = read_pdf(out)
    assert len(doc.pages) == 4
    for p in doc.pages:
        overlay = " ".join(w.text for w in p.overlay_words)
        assert "Asha Raman" in overlay
        assert "DRAFT" in overlay
    cfg = EngagementConfig(client="Corvus")
    assert reconcile(wb, out, cfg).misses == []
    # stamping in place works too
    stamp(out, out, "second stamp")
    assert "second" in " ".join(w.text for w in read_pdf(out).pages[0].overlay_words)


def test_bookmarks_are_normalised(tmp_path: Path) -> None:
    wb = make_workbook(tmp_path / "b.xlsx", hidden_value=0.0)
    render_native(wb, tmp_path / "b.pdf", ["BS", "Note 3-5"], draft=False)
    titles = set_bookmarks(tmp_path / "b.pdf", [(2, "Orphan level 2", 1), (1, "Notes", 2), (3, "Too deep", 2), (1, "Out of range", 99)])
    assert titles == ["Orphan level 2", "Notes", "Too deep"]
    toc = read_pdf(tmp_path / "b.pdf").toc
    assert toc == [(1, "Orphan level 2", 1), (1, "Notes", 2), (2, "Too deep", 2)]
