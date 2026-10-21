"""PDF text model for reconciliation (02 §6.7): page → lines → words / numeric tokens.

Words are rebuilt from PyMuPDF's per-character output so each word keeps its
colour and font. Text drawn in the reserved *overlay* colour (draft watermark,
run-ID footer, page numbering added by FS-Platform) is excluded from the
content tokens, so stamping a PDF never changes what reconciliation sees.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from fscore.engine.display import numeric_signature, parse_printed_number

# Reserved sRGB colour for everything FS-Platform adds on top of a rendered page.
OVERLAY_RGB = (0x5A, 0x5B, 0x5C)
OVERLAY_INT = (OVERLAY_RGB[0] << 16) | (OVERLAY_RGB[1] << 8) | OVERLAY_RGB[2]

ERROR_TOKENS = ("#REF!", "#VALUE!", "#DIV/0!", "#N/A", "#NAME?", "#NUM!", "#NULL!", "#SPILL!", "#CALC!")
_NUMERIC_WORD = re.compile(r"^\(?-?[₹$€£]?\(?[\d,]*\.?\d+\)?%?\)?$|^[-–—]$")


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    color: int = 0
    font: str = ""
    size: float = 0.0
    bold: bool = False

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def invisible(self) -> bool:
        """White (or near-white) text on the default white page."""
        r, g, b = (self.color >> 16) & 255, (self.color >> 8) & 255, self.color & 255
        return min(r, g, b) >= 0xF0

    @property
    def overlay(self) -> bool:
        return self.color == OVERLAY_INT


@dataclass
class NumToken:
    text: str
    value: Decimal
    signature: str
    page: int
    x0: float
    x1: float
    y: float
    invisible: bool = False


@dataclass
class Line:
    page: int
    y: float
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def x0(self) -> float:
        return min((w.x0 for w in self.words), default=0.0)

    @property
    def x1(self) -> float:
        return max((w.x1 for w in self.words), default=0.0)


@dataclass
class FontInfo:
    name: str
    embedded: bool
    kind: str


@dataclass
class PdfPage:
    number: int  # 1-based
    width: float
    height: float
    words: list[Word] = field(default_factory=list)  # content words (overlay text excluded)
    overlay_words: list[Word] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    numbers: list[NumToken] = field(default_factory=list)
    fonts: list[FontInfo] = field(default_factory=list)
    has_graphics: bool = False
    has_images: bool = False

    @property
    def orientation(self) -> str:
        return "landscape" if self.width > self.height else "portrait"

    @property
    def text(self) -> str:
        return "\n".join(ln.text for ln in self.lines)

    @property
    def visible_text(self) -> str:
        return "\n".join(" ".join(w.text for w in ln.words if not w.invisible) for ln in self.lines)

    @property
    def is_blank(self) -> bool:
        return not any(not w.invisible for w in self.words) and not self.has_images

    def text_extent(self) -> tuple[float, float, float, float] | None:
        vis = [w for w in self.words if not w.invisible]
        if not vis:
            return None
        return (min(w.x0 for w in vis), min(w.y0 for w in vis), max(w.x1 for w in vis), max(w.y1 for w in vis))


@dataclass
class PdfDocument:
    path: Path
    sha256: str
    pages: list[PdfPage]
    toc: list[tuple[int, str, int]] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    ocr_used: bool = False

    def page(self, number: int) -> PdfPage:
        return self.pages[number - 1]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_numeric_word(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > 32:
        return False
    if not _NUMERIC_WORD.match(t):
        return False
    return parse_printed_number(t) is not None


def _words_from_rawdict(page: object) -> list[Word]:
    """Rebuild words from per-character boxes, splitting on spaces and span changes."""
    raw = page.get_text("rawdict")  # type: ignore[attr-defined]
    out: list[Word] = []
    for block in raw.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                color = int(span.get("color", 0))
                font = str(span.get("font", ""))
                size = float(span.get("size", 0.0))
                bold = bool(int(span.get("flags", 0)) & 16) or "bold" in font.lower()
                cur: list[tuple[str, tuple[float, float, float, float]]] = []
                style = (color, font, size, bold)
                prev_x1: float | None = None
                for ch in span.get("chars", []):
                    c = ch.get("c", "")
                    bbox = tuple(ch.get("bbox", (0, 0, 0, 0)))
                    if c.isspace():
                        _flush(cur, style, out)
                        prev_x1 = None
                        continue
                    # a large horizontal jump inside one span (fill characters) also separates words
                    if prev_x1 is not None and bbox[0] - prev_x1 > max(size * 0.9, 2.5):
                        _flush(cur, style, out)
                    cur.append((c, (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))))
                    prev_x1 = bbox[2]
                _flush(cur, style, out)
    return out


def _flush(cur: list[tuple[str, tuple[float, float, float, float]]], style: tuple[int, str, float, bool], out: list[Word]) -> None:
    if not cur:
        return
    text = "".join(c for c, _b in cur)
    x0 = min(b[0] for _c, b in cur)
    y0 = min(b[1] for _c, b in cur)
    x1 = max(b[2] for _c, b in cur)
    y1 = max(b[3] for _c, b in cur)
    color, font, size, bold = style
    out.append(Word(text, x0, y0, x1, y1, color, font, size, bold))
    cur.clear()


def _merge_numeric_fragments(words: list[Word]) -> list[Word]:
    """Join '(', '1,234.56', ')' and '₹' '1,000' fragments that renderers split."""
    out: list[Word] = []
    i = 0
    while i < len(words):
        w = words[i]
        if w.text in ("(", "₹", "Rs.", "INR", "$") and i + 1 < len(words):
            nxt = words[i + 1]
            if abs(nxt.yc - w.yc) < max(w.y1 - w.y0, 1) * 0.6 and nxt.x0 - w.x1 < max(w.size, 6) * 1.5 and is_numeric_word(nxt.text.rstrip(")")):
                text = (w.text if w.text == "(" else "") + nxt.text
                merged = Word(text, w.x0, min(w.y0, nxt.y0), nxt.x1, max(w.y1, nxt.y1), nxt.color, nxt.font, nxt.size, nxt.bold)
                if text.startswith("(") and not text.endswith(")") and i + 2 < len(words) and words[i + 2].text == ")":
                    merged.text += ")"
                    merged.x1 = words[i + 2].x1
                    i += 1
                out.append(merged)
                i += 2
                continue
        if w.text == ")" and out and is_numeric_word(out[-1].text + ")") and abs(out[-1].yc - w.yc) < 3:
            out[-1].text += ")"
            out[-1].x1 = w.x1
            i += 1
            continue
        out.append(w)
        i += 1
    return out


def _group_lines(words: list[Word], page_no: int) -> list[Line]:
    """Cluster words into visual lines by vertical centre (tables span blocks)."""
    lines: list[Line] = []
    for w in sorted(words, key=lambda w: (round(w.yc, 1), w.x0)):
        tol = max((w.y1 - w.y0) * 0.45, 1.5)
        target = None
        for ln in reversed(lines[-6:]):
            if abs(ln.y - w.yc) <= tol:
                target = ln
                break
        if target is None:
            lines.append(Line(page_no, w.yc, [w]))
        else:
            target.words.append(w)
    for ln in lines:
        ln.words.sort(key=lambda w: w.x0)
    lines.sort(key=lambda ln: ln.y)
    return lines


def _tokens(words: list[Word], page_no: int) -> list[NumToken]:
    out: list[NumToken] = []
    for w in words:
        t = w.text.strip()
        if not is_numeric_word(t):
            continue
        v = parse_printed_number(t)
        sig = numeric_signature(t)
        if v is None or sig is None:
            continue
        out.append(NumToken(t, v, sig, page_no, w.x0, w.x1, w.yc, w.invisible))
    return out


def read_pdf(path: str | Path, *, ocr: bool = False) -> PdfDocument:
    pymupdf: Any = importlib.import_module("pymupdf")

    p = Path(path)
    doc = pymupdf.open(str(p))
    pages: list[PdfPage] = []
    ocr_used = False
    try:
        for i, page in enumerate(doc, start=1):
            rect = page.rect
            all_words = _words_from_rawdict(page)
            if not all_words and ocr and _ocr_available():
                all_words = _ocr_words(page)
                ocr_used = True
            all_words = _merge_numeric_fragments(sorted(all_words, key=lambda w: (round(w.yc), w.x0)))
            content = [w for w in all_words if not w.overlay]
            overlay = [w for w in all_words if w.overlay]
            pg = PdfPage(number=i, width=float(rect.width), height=float(rect.height), words=content, overlay_words=overlay)
            pg.lines = _group_lines(content, i)
            pg.numbers = _tokens(content, i)
            fonts = []
            for f in page.get_fonts(full=False):
                # (xref, ext, type, basefont, name, encoding)
                ext, ftype, base = f[1], f[2], f[3]
                fonts.append(FontInfo(name=str(base), embedded=ext not in ("n/a", ""), kind=str(ftype)))
            pg.fonts = fonts
            try:
                pg.has_graphics = bool(page.get_drawings())
            except Exception:
                pg.has_graphics = False
            pg.has_images = bool(page.get_images(full=False))
            pages.append(pg)
        toc = [(int(lvl), str(title), int(pno)) for lvl, title, pno, *_ in doc.get_toc(simple=True)]
        meta = {k: str(v) for k, v in (doc.metadata or {}).items() if v}
    finally:
        doc.close()
    return PdfDocument(path=p, sha256=_sha256(p), pages=pages, toc=toc, metadata=meta, ocr_used=ocr_used)


def _ocr_available() -> bool:
    return importlib.util.find_spec("pytesseract") is not None


def _ocr_words(page: object) -> list[Word]:  # pragma: no cover - requires Tesseract
    """OCR fallback for scanned PDFs (local Tesseract only; lower certainty)."""
    import io

    import pytesseract
    from PIL import Image

    pix = page.get_pixmap(dpi=300)  # type: ignore[attr-defined]
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    scale = 72 / 300
    out: list[Word] = []
    for i, text in enumerate(data["text"]):
        if not text or not text.strip():
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        out.append(Word(text.strip(), x * scale, y * scale, (x + w) * scale, (y + h) * scale))
    return out


def fonts_embedded(doc: PdfDocument) -> list[str]:
    """Names of fonts that are NOT embedded (empty list = all embedded)."""
    missing: set[str] = set()
    for p in doc.pages:
        for f in p.fonts:
            if not f.embedded:
                missing.add(f.name)
    return sorted(missing)


def error_tokens(doc: PdfDocument) -> list[str]:
    out = []
    for p in doc.pages:
        for w in p.words:
            for e in ERROR_TOKENS:
                if e in w.text.upper():
                    out.append(f"{e} on page {p.number}")
    return out
