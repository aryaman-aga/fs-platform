"""PDF stamping: dynamic watermark for viewed / downloaded copies (E-SEC-10),
DRAFT overlay for drafts produced by external engines, and bookmarks.

Everything is drawn in the reserved overlay colour so reconciliation ignores it.
"""

from __future__ import annotations

import importlib
import math
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fscore.io.pdf_reader import OVERLAY_RGB
from fscore.pdf.typefaces import font_path

_COLOR = tuple(c / 255 for c in OVERLAY_RGB)


def _font_alias() -> str:
    """A fresh resource name per stamp: re-using a subsetted font would lose new glyphs."""
    return "fswm" + uuid.uuid4().hex[:6]


def _diagonal(page: object, text: str, *, fontsize: float, opacity: float, alias: str) -> None:
    pymupdf: Any = importlib.import_module("pymupdf")

    rect = page.rect  # type: ignore[attr-defined]
    font = pymupdf.Font(fontfile=str(font_path()))
    width = font.text_length(text, fontsize=fontsize)
    angle = 35.0
    cx, cy = rect.width / 2, rect.height / 2
    rad = math.radians(angle)
    # baseline start so the text is centred on the page after rotation
    start = pymupdf.Point(cx - math.cos(rad) * width / 2, cy + math.sin(rad) * width / 2)
    page.insert_text(  # type: ignore[attr-defined]
        start, text, fontsize=fontsize, fontname=alias, fontfile=str(font_path()), color=_COLOR,
        morph=(start, pymupdf.Matrix(angle)), fill_opacity=opacity, overlay=True,
    )


def _footer(page: object, text: str, *, alias: str, fontsize: float = 7.0) -> None:
    pymupdf: Any = importlib.import_module("pymupdf")

    rect = page.rect  # type: ignore[attr-defined]
    font = pymupdf.Font(fontfile=str(font_path()))
    w = font.text_length(text, fontsize=fontsize)
    x = max(18.0, rect.width - 24.0 - w)
    page.insert_text(  # type: ignore[attr-defined]
        pymupdf.Point(x, rect.height - 6.0), text, fontsize=fontsize, fontname=alias, fontfile=str(font_path()), color=_COLOR, overlay=True,
    )


def _save(doc: object, src: Path, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.subset_fonts()  # type: ignore[attr-defined]
    if out.resolve() == src.resolve():
        fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=str(out.parent))
        os.close(fd)
        doc.save(tmp, garbage=3, deflate=True)  # type: ignore[attr-defined]
        doc.close()  # type: ignore[attr-defined]
        os.replace(tmp, out)
    else:
        doc.save(str(out), garbage=3, deflate=True)  # type: ignore[attr-defined]
        doc.close()  # type: ignore[attr-defined]
    return out


def stamp(src: Path, out: Path, text: str) -> Path:
    """Watermark every page with ``text`` (e.g. 'A. User · 21-Oct-2026 10:30 · DRAFT')."""
    pymupdf: Any = importlib.import_module("pymupdf")

    doc = pymupdf.open(str(src))
    alias = _font_alias()
    for page in doc:
        _diagonal(page, text, fontsize=max(14.0, min(40.0, page.rect.width / max(len(text), 1) * 1.6)), opacity=0.14, alias=alias)
        _footer(page, text, alias=alias)
    return _save(doc, Path(src), Path(out))


def stamp_draft(src: Path, out: Path, run_id: str) -> Path:
    """DRAFT overlay for PDFs produced by Excel or LibreOffice (the native engine draws its own)."""
    pymupdf: Any = importlib.import_module("pymupdf")

    doc = pymupdf.open(str(src))
    total = doc.page_count
    alias = _font_alias()
    for i, page in enumerate(doc, start=1):
        _diagonal(page, "DRAFT – for review", fontsize=64, opacity=0.13, alias=alias)
        _footer(page, f"DRAFT – for review · Run {run_id or '-'} · Page {i} of {total}", alias=alias)
    return _save(doc, Path(src), Path(out))


def set_bookmarks(pdf: Path, entries: list[tuple[int, str, int]]) -> list[str]:
    """Write an outline (level, title, 1-based page). Levels are normalised to start at 1."""
    pymupdf: Any = importlib.import_module("pymupdf")

    if not entries:
        return []
    toc: list[list[object]] = []
    prev = 0
    for lvl, title, page in entries:
        lvl = max(1, min(lvl, prev + 1))
        toc.append([lvl, title, page])
        prev = lvl
    doc = pymupdf.open(str(pdf))
    toc = [t for t in toc if 1 <= int(t[2]) <= doc.page_count]  # type: ignore[call-overload]
    doc.set_toc(toc)
    _save(doc, pdf, pdf)
    return [str(t[1]) for t in toc]
