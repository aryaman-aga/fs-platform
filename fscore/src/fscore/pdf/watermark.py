"""PDF stamping: dynamic watermark for viewed / downloaded copies (E-SEC-10),
DRAFT overlay for drafts produced by external engines, and bookmarks.

Everything is drawn in the reserved overlay colour so reconciliation ignores it.
Overlays are drawn with ReportLab (DejaVu, subset-embedded), marked as artifacts and
merged onto the pages with pypdf; the document itself is cloned, so tagged structure,
metadata and links from Excel / LibreOffice survive. Files are read into memory and
written through a temporary file + atomic replace, so the same path can be stamped in
place and no handle stays open (Windows).
"""

from __future__ import annotations

import io
import os
import re
import tempfile
import uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from pypdf.generic import ContentStream, NameObject

from fscore.io.pdf_reader import OVERLAY_RGB
from fscore.pdf.typefaces import face

_COLOR = tuple(c / 255 for c in OVERLAY_RGB)
_SUBSET_TAG = re.compile(r"^[A-Z]{6}\+")
_ANGLE = 35.0

Draw = Callable[[Any, int, int, float, float], None]  # (canvas, page number (1-based), page count, width, height)


def _load(src: Path) -> PdfReader:
    reader = PdfReader(io.BytesIO(Path(src).read_bytes()))
    if reader.is_encrypted and not reader.decrypt(""):  # owner-password-only PDFs open with an empty user password
        raise ValueError(f"{Path(src).name} is password-protected")
    return reader


def _frame(page: PageObject) -> tuple[float, float, Transformation]:
    """Visible size (crop box ∩ media box, /Rotate applied) and the map from that upright frame,
    origin bottom-left, to the page's own coordinates."""
    media, crop = page.mediabox, page.cropbox
    left, bottom = max(float(media.left), float(crop.left)), max(float(media.bottom), float(crop.bottom))
    right, top = min(float(media.right), float(crop.right)), min(float(media.top), float(crop.top))
    if right <= left or top <= bottom:
        left, bottom, right, top = float(media.left), float(media.bottom), float(media.right), float(media.top)
    rot = int(page.rotation or 0) % 360
    if rot == 90:
        return top - bottom, right - left, Transformation((0, 1, -1, 0, right, bottom))
    if rot == 180:
        return right - left, top - bottom, Transformation((-1, 0, 0, -1, right, top))
    if rot == 270:
        return top - bottom, right - left, Transformation((0, -1, 1, 0, left, top))
    return right - left, top - bottom, Transformation((1, 0, 0, 1, left, bottom))


def _overlays(sizes: list[tuple[float, float]], draw: Draw) -> list[PageObject]:
    """One overlay page per size, drawn by ``draw`` and marked as an artifact (not content)."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    # the initial font is an embedded face too: ReportLab would otherwise reference (unembedded) Helvetica
    c = canvas.Canvas(buf, pagesize=sizes[0], pageCompression=0, initialFontName=face(), initialFontSize=7, initialLeading=8)
    for number, (w, h) in enumerate(sizes, start=1):
        c.setPageSize((w, h))
        draw(c, number, len(sizes), w, h)
        c.showPage()
    c.save()
    pages = list(PdfReader(io.BytesIO(buf.getvalue())).pages)
    # ReportLab names every subset 'AAAAAA+<font>'; a fresh tag per stamp keeps the overlay's subset
    # distinct from the page's own (and an earlier stamp's) subsets of the same face
    tag = "".join(chr(ord("A") + int(ch, 16)) for ch in uuid.uuid4().hex[:6])
    for page in pages:
        content = page.get_contents()
        if isinstance(content, ContentStream):
            content.set_data(b"/Artifact BMC\n" + content.get_data() + b"\nEMC\n")
            page[NameObject("/Contents")] = content
        fonts = page.get("/Resources", {}).get("/Font", {})
        for ref in fonts.values():
            font = ref.get_object()
            base = _SUBSET_TAG.sub("", str(font.get("/BaseFont", "")).lstrip("/"))
            if base:
                font[NameObject("/BaseFont")] = NameObject(f"/{tag}+{base}")
                descriptor = font.get("/FontDescriptor")
                if descriptor is not None:
                    descriptor.get_object()[NameObject("/FontName")] = NameObject(f"/{tag}+{base}")
    return pages


def _stamped(src: Path, draw: Draw) -> PdfWriter:
    writer = PdfWriter(clone_from=_load(src))
    frames = [_frame(page) for page in writer.pages]
    if frames:
        overlays = _overlays([(w, h) for w, h, _m in frames], draw)
        for page, overlay, (_w, _h, to_page) in zip(writer.pages, overlays, frames, strict=True):
            page.merge_transformed_page(overlay, to_page, over=True, expand=False)
    if writer.pdf_header < "%PDF-1.4":
        writer.pdf_header = "%PDF-1.4"  # transparency (watermark opacity)
    return writer


def _save(writer: PdfWriter, out: Path) -> Path:
    """Compress and write atomically (``out`` may be the source file)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    for page in writer.pages:
        page.compress_content_streams()
    writer.compress_identical_objects()  # duplicates and unreferenced objects (defaults)
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=str(out.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            writer.write(fh)
        os.replace(tmp, out)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp)
        raise
    return out


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()] or [text.strip() or "-"]


def _diagonal(c: Any, lines: list[str], w: float, h: float, *, fontsize: float, opacity: float) -> None:
    """``lines`` centred on the page, rotated 35°, translucent."""
    lead = fontsize * 1.25
    c.saveState()
    c.setFillColorRGB(*_COLOR)
    c.setFillAlpha(opacity)
    c.setFont(face(), fontsize)
    c.translate(w / 2, h / 2)
    c.rotate(_ANGLE)
    top = (len(lines) - 1) * lead / 2 - fontsize * 0.35  # first baseline: the block is centred on the page
    for k, line in enumerate(lines):
        c.drawCentredString(0, top - k * lead, line)
    c.restoreState()


def _footer(c: Any, text: str, w: float, *, fontsize: float = 7.0) -> None:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    c.saveState()
    c.setFillColorRGB(*_COLOR)
    c.setFont(face(), fontsize)
    c.drawString(max(18.0, w - 24.0 - stringWidth(text, face(), fontsize)), 6.0, text)
    c.restoreState()


def stamp(src: Path, out: Path, text: str) -> Path:
    """Watermark every page with ``text`` (e.g. 'A. User · 21-Oct-2026 10:30 · DRAFT');
    a multi-line text is set as a block on the diagonal and joined with ' · ' in the footer."""
    lines = _lines(text)
    longest = max(len(ln) for ln in lines)

    def draw(c: Any, _number: int, _total: int, w: float, h: float) -> None:
        fontsize = max(14.0, min(40.0, w / max(longest, 1) * 1.6))
        _diagonal(c, lines, w, h, fontsize=fontsize, opacity=0.14)
        _footer(c, " · ".join(lines), w)

    return _save(_stamped(Path(src), draw), Path(out))


def stamp_draft(src: Path, out: Path, run_id: str) -> Path:
    """DRAFT overlay for PDFs produced by Excel or LibreOffice: the same overlay the built-in
    renderer draws (diagonal 'DRAFT – for review', run ID and page numbering)."""
    from fscore.pdf.render_native import draw_overlay

    def draw(c: Any, number: int, total: int, w: float, h: float) -> None:
        draw_overlay(c, w, h, page=number, total=total, draft=True, run_id=run_id)

    return _save(_stamped(Path(src), draw), Path(out))


def set_bookmarks(pdf: Path, entries: list[tuple[int, str, int]]) -> list[str]:
    """Write an outline (level, title, 1-based page), replacing any existing one. Levels are
    normalised to start at 1 and never skip a level; entries for missing pages are dropped."""
    if not entries:
        return []
    reader = _load(Path(pdf))
    pages = len(reader.pages)
    toc: list[tuple[int, str, int]] = []
    prev = 0
    for lvl, title, page in entries:
        lvl = max(1, min(lvl, prev + 1))
        toc.append((lvl, title, page))
        prev = lvl
    kept: list[tuple[int, str, int]] = []
    prev = 0
    for lvl, title, page in toc:
        if 1 <= int(page) <= pages:
            lvl = min(lvl, prev + 1)
            kept.append((lvl, str(title), int(page)))
            prev = lvl
    writer = PdfWriter(clone_from=reader)
    root = writer.root_object
    if NameObject("/Outlines") in root:
        del root[NameObject("/Outlines")]
    parents: dict[int, Any] = {}
    for lvl, title, page in kept:
        item = writer.add_outline_item(title, page - 1, parent=parents.get(lvl - 1))
        parents[lvl] = item
        for deeper in [k for k in parents if k > lvl]:
            del parents[deeper]
    _save(writer, Path(pdf))
    return [title for _lvl, title, _page in kept]


__all__ = ["set_bookmarks", "stamp", "stamp_draft"]
