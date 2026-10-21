"""PDF reader tokens, overlay exclusion and the E-SEC-10 watermark stamp."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from helpers_pdf import make_workbook

from fscore.checks.pdf_recon import reconcile
from fscore.config import EngagementConfig
from fscore.io.pdf_reader import fonts_embedded, is_numeric_word, read_pdf
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


def test_multiline_download_watermark(tmp_path: Path) -> None:
    """fsweb's download watermark has two lines; both go on the diagonal and, joined, in the footer."""
    import io

    from pypdf import PdfReader

    wb = make_workbook(tmp_path / "m.xlsx", hidden_value=0.0)
    src = tmp_path / "m.pdf"
    render_native(wb, src, ["BS", "Note 3-5"], draft=False)
    out = stamp(src, tmp_path / "m_wm.pdf", "DRAFT – for review\nstaff@test.fs · 21-Oct-2026 10:30 UTC · v1")
    before, after = read_pdf(src), read_pdf(out)
    for p0, p1 in zip(before.pages, after.pages, strict=True):
        assert [w.text for w in p1.words] == [w.text for w in p0.words]  # content untouched
        overlay = " ".join(w.text for w in p1.overlay_words)
        assert "staff@test.fs" in overlay and "DRAFT" in overlay and "v1" in overlay
        footer = [w for w in p1.overlay_words if w.size < 8]
        assert footer and max(w.y1 for w in footer) < p1.height and min(w.x0 for w in footer) > 0
        assert any(w.text == "DRAFT" and w.size >= 14 for w in p1.overlay_words)  # translucent diagonal: still overlay
    text = PdfReader(io.BytesIO(out.read_bytes())).pages[0].extract_text()
    assert "staff@test.fs" in text and "DRAFT" in text
    assert fonts_embedded(after) == []


def _rotated_pdf(path: Path) -> Path:
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas

    src = path.with_name("plain.pdf")
    c = canvas.Canvas(str(src), pagesize=(595, 842))
    for rot in (0, 90, 180, 270):
        c.setFont("Helvetica", 12)
        c.drawString(72, 700, f"Page rotated {rot} shows 1,234.56")
        c.showPage()
    c.save()
    w = PdfWriter(clone_from=PdfReader(str(src)))
    for page, rot in zip(w.pages, (0, 90, 180, 270), strict=True):
        if rot:
            page.rotate(rot)
    w.pages[0].cropbox.lower_left = (50, 60)
    w.pages[0].cropbox.upper_right = (545, 800)
    with path.open("wb") as fh:
        w.write(fh)
    return path


def test_reader_uses_the_visible_page_and_stamps_follow_rotation(tmp_path: Path) -> None:
    pdf = _rotated_pdf(tmp_path / "rot.pdf")
    doc = read_pdf(pdf)
    assert [(round(p.width), round(p.height)) for p in doc.pages] == [(495, 740), (842, 595), (595, 842), (842, 595)]
    first = doc.pages[0].words[0]
    assert (round(first.x0), round(first.y1)) == (22, 104)  # crop box offset applied, top-left origin (baseline 800 - 700)
    for p in doc.pages:
        assert any(t.text == "1,234.56" for t in p.numbers)
        assert all(0 <= w.x0 < w.x1 <= p.width and 0 <= w.y0 < w.y1 <= p.height for w in p.words)
    out = stamp(pdf, tmp_path / "rot_wm.pdf", "Viewer · 21-Oct-2026")
    for p in read_pdf(out).pages:
        footer = [w for w in p.overlay_words if w.size < 8]
        diagonal = [w for w in p.overlay_words if w.size >= 8]
        assert " ".join(w.text for w in footer) == "Viewer · 21-Oct-2026"
        assert p.height - 15 < min(w.y0 for w in footer) and max(w.x1 for w in footer) <= p.width - 23  # bottom right
        cx = (min(w.x0 for w in diagonal) + max(w.x1 for w in diagonal)) / 2
        cy = (min(w.y0 for w in diagonal) + max(w.y1 for w in diagonal)) / 2
        assert abs(cx - p.width / 2) < 5 and abs(cy - p.height / 2) < 10  # centred on the visible page


def test_owner_password_pdf_can_be_stamped(tmp_path: Path) -> None:
    from pypdf import PdfReader, PdfWriter

    wb = make_workbook(tmp_path / "e.xlsx", hidden_value=0.0)
    render_native(wb, tmp_path / "e.pdf", ["BS"], draft=False)
    w = PdfWriter(clone_from=PdfReader(str(tmp_path / "e.pdf")))
    w.encrypt(user_password="", owner_password="owner-only", permissions_flag=0)
    with (tmp_path / "enc.pdf").open("wb") as fh:
        w.write(fh)
    out = stamp(tmp_path / "enc.pdf", tmp_path / "enc_wm.pdf", "Copy for A. User")
    assert "Copy" in " ".join(w.text for w in read_pdf(out).pages[0].overlay_words)


def test_bookmarks_replace_an_existing_outline(tmp_path: Path) -> None:
    from pypdf import PdfReader, PdfWriter

    wb = make_workbook(tmp_path / "o.xlsx", hidden_value=0.0)
    render_native(wb, tmp_path / "o.pdf", ["BS", "Note 3-5"], draft=False)
    w = PdfWriter(clone_from=PdfReader(str(tmp_path / "o.pdf")))
    w.add_outline_item("Old entry", 0)
    with (tmp_path / "old.pdf").open("wb") as fh:
        w.write(fh)
    assert read_pdf(tmp_path / "old.pdf").toc == [(1, "Old entry", 1)]
    set_bookmarks(tmp_path / "old.pdf", [(1, "Balance Sheet", 1), (2, "Note 3 Property", 2), (2, "Note 4 Leases", 3), (1, "End", 4)])
    assert read_pdf(tmp_path / "old.pdf").toc == [(1, "Balance Sheet", 1), (2, "Note 3 Property", 2), (2, "Note 4 Leases", 3), (1, "End", 4)]


def test_clipped_text_is_not_read(tmp_path: Path) -> None:
    """Text cut off by a clip (a cell that overflows into a filled neighbour) is not on paper."""
    from reportlab.pdfgen import canvas

    from fscore.io.pdf_reader import read_pdf_words

    pdf = tmp_path / "clip.pdf"
    c = canvas.Canvas(str(pdf), pagesize=(400, 300))
    c.setFont("Helvetica", 10)
    c.saveState()
    path = c.beginPath()
    path.rect(50, 190, 120, 30)  # the cell: 50..170 wide
    c.clipPath(path, stroke=0, fill=0)
    c.drawString(55, 200, "Visible caption 1,234.56 clipped tail 9,876.54")
    c.restoreState()
    c.drawString(55, 100, "Outside the clip 42.00")
    c.save()
    doc = read_pdf(pdf)
    text = doc.pages[0].visible_text
    assert "Visible caption 1,234.56" in text and "9,876.54" not in text
    assert "Outside the clip 42.00" in text
    assert "9,876.54" not in [w.text for w in read_pdf_words(pdf)[0]]


def test_concurrent_reads_and_stamps(tmp_path: Path) -> None:
    """pdfium is not thread-safe; the reader serialises every call into it."""
    import threading

    wb = make_workbook(tmp_path / "t.xlsx", hidden_value=0.0)
    src = tmp_path / "t.pdf"
    render_native(wb, src, ["BS", "Note 3-5"], draft=True, run_id="R-T")
    expected = [w.text for w in read_pdf(src).pages[0].words]
    errors: list[BaseException] = []

    def work(n: int) -> None:
        try:
            for i in range(3):
                assert [w.text for w in read_pdf(src).pages[0].words] == expected
                stamp(src, tmp_path / f"t{n}-{i}.pdf", f"thread {n}")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_page_count_and_closed_files(tmp_path: Path) -> None:
    from fscore.io.pdf_reader import page_count

    wb = make_workbook(tmp_path / "c.xlsx", hidden_value=0.0)
    pdf = tmp_path / "c.pdf"
    render_native(wb, pdf, ["BS", "Note 3-5"], draft=True)
    assert page_count(pdf) == 4
    read_pdf(pdf)
    stamp(pdf, pdf, "in place")
    pdf.unlink()  # nothing keeps the file open
    assert not pdf.exists()


def test_words_mode_splits_on_spaces_only(tmp_path: Path) -> None:
    """xref reads signed PDFs as plain words: a style change inside a word does not split it."""
    from reportlab.pdfgen import canvas

    from fscore.io.pdf_reader import read_pdf_words

    pdf = tmp_path / "w.pdf"
    c = canvas.Canvas(str(pdf), pagesize=(400, 300))
    t = c.beginText(50, 200)
    t.setFont("Helvetica-Bold", 10)
    t.textOut("Total")
    t.setFont("Helvetica", 10)
    t.textOut("Assets 1,234.56")
    c.drawText(t)
    c.save()
    assert [w.text for w in read_pdf(pdf).pages[0].words] == ["Total", "Assets", "1,234.56"]
    assert [w.text for w in read_pdf_words(pdf)[0]] == ["TotalAssets", "1,234.56"]
