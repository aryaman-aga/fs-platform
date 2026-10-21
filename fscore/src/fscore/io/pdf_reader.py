"""PDF text model for reconciliation (02 §6.7): page → lines → words / numeric tokens.

Words are rebuilt from pdfium's per-character output (pypdfium2) so each word keeps
its colour and font. Text drawn in the reserved *overlay* colour (draft watermark,
run-ID footer, page numbering added by FS-Platform) is excluded from the content
tokens, so stamping a PDF never changes what reconciliation sees.

Coordinates are points with the origin at the top-left corner of the visible page
(crop box, ``/Rotate`` applied). pdfium is not thread-safe: every call into it holds
:data:`PDFIUM_LOCK`, and documents are loaded from bytes so no file stays open
(Windows file locks).
"""

from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import io
import math
import re
import threading
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

PDFIUM_LOCK = threading.RLock()
"""pdfium is not thread-safe: hold this lock around every pypdfium2 call (any document)."""

_SUBSET_TAG = re.compile(r"^[A-Z]{6}\+")
_FORCE_BOLD = 1 << 18  # font descriptor flag
_SPACE_GAP = 0.15  # a gap wider than 0.15 em along the baseline separates words
_LINE_SHIFT = 0.8  # a baseline shift of 0.8 em starts a new line (and a new word)
_MAX_DEPTH = 16  # nesting of form XObjects / outline levels that is followed


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


def is_numeric_word(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > 32:
        return False
    if not _NUMERIC_WORD.match(t):
        return False
    return parse_printed_number(t) is not None


# ------------------------------------------------------------------------------- characters → words


@dataclass(slots=True)
class _Char:
    """One printed character; ``sep`` marks whitespace and pdfium-generated characters."""

    text: str
    sep: bool = False
    box: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # top-left page coordinates
    style: tuple[int, str, float, bool] = (0, "", 0.0, False)  # colour, font, size, bold
    origin: tuple[float, float] = (0.0, 0.0)  # baseline start (PDF user space)
    pen: tuple[float, float] = (0.0, 0.0)  # baseline end = origin + advance (PDF user space)
    ux: float = 1.0  # text direction (unit vector, PDF user space)
    uy: float = 0.0


_SEP = _Char("", sep=True)


@dataclass(slots=True)
class _ObjStyle:
    """Per text object: colour, font, nominal size, the font's ascent / descent (1/1000 em) and
    the character matrix (text matrix × enclosing forms; the same for every character of the object)."""

    color: int
    font: str
    bold: bool
    tf: float
    ascent: float
    descent: float
    matrix: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 1.0)  # text space → page, per object


class _Geometry:
    """PDF user space → top-left page coordinates (crop box ∩ media box, /Rotate applied)."""

    def __init__(self, bbox: tuple[float, float, float, float], rotation: int) -> None:
        self.l, self.b, self.r, self.t = (float(v) for v in bbox)
        self.rot = int(rotation) % 360
        if self.rot in (90, 270):
            self.width, self.height = self.t - self.b, self.r - self.l
        else:
            self.width, self.height = self.r - self.l, self.t - self.b

    def point(self, x: float, y: float) -> tuple[float, float]:
        if self.rot == 90:
            return y - self.b, x - self.l
        if self.rot == 180:
            return self.r - x, y - self.b
        if self.rot == 270:
            return self.t - y, self.r - x
        return x - self.l, self.t - y

    def rect(self, x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
        ax, ay = self.point(x0, y0)
        bx, by = self.point(x1, y1)
        return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)


# Glyph-box ascent / descent (1/1000 em) that PyMuPDF uses for the standard fonts when a PDF does
# not embed them (its URW base-14 faces). pdfium substitutes other faces with smaller boxes.
_BASE14_METRICS = {
    "Helvetica": (1075.0, -299.0), "Helvetica-Bold": (1070.0, -307.0), "Helvetica-Oblique": (1070.0, -284.0),
    "Helvetica-BoldOblique": (1073.0, -309.0), "Times-Roman": (1053.0, -281.0), "Times-Bold": (1044.0, -341.0),
    "Times-Italic": (951.0, -270.0), "Times-BoldItalic": (972.0, -324.0), "Courier": (932.0, -317.0),
    "Courier-Bold": (1007.0, -393.0), "Courier-Oblique": (920.0, -317.0), "Courier-BoldOblique": (997.0, -393.0),
}


def _metrics(ascent: float, descent: float) -> tuple[float, float]:
    """Glyph box height in em, as PyMuPDF draws it: the font's ascent / descent, scaled up
    to at least one em (small or missing font metrics, e.g. OCR glyph-less fonts)."""
    asc, dsc = ascent / 1000.0, descent / 1000.0
    if asc - dsc + 1e-6 >= 1.0:
        return asc, dsc
    if asc < 1e-3:
        return 0.9, -0.1
    height = asc - dsc
    return asc / height, dsc / height


def _obj_style(R: Any, tp: Any, index: int, obj: Any) -> _ObjStyle:
    r, g, b, a = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
    color = 0
    if R.FPDFText_GetFillColor(tp, index, r, g, b, a):
        color = ((r.value & 255) << 16) | ((g.value & 255) << 8) | (b.value & 255)  # alpha is ignored
    flags = ctypes.c_int(0)
    n = int(R.FPDFText_GetFontInfo(tp, index, None, 0, flags))
    name = ""
    if n > 0:
        buf = ctypes.create_string_buffer(n)
        R.FPDFText_GetFontInfo(tp, index, buf, n, flags)
        name = _SUBSET_TAG.sub("", buf.value.decode("utf-8", "replace"))
    asc, dsc = ctypes.c_float(0.0), ctypes.c_float(0.0)
    font = R.FPDFTextObj_GetFont(obj)
    if font:
        R.FPDFFont_GetAscent(font, 1000.0, asc)
        R.FPDFFont_GetDescent(font, 1000.0, dsc)
    ascent, descent = float(asc.value), float(dsc.value)
    if font and name in _BASE14_METRICS and R.FPDFFont_GetIsEmbedded(font) != 1:
        ascent, descent = _BASE14_METRICS[name]
    bold = "bold" in name.lower() or bool(flags.value & _FORCE_BOLD)
    mat = R.FS_MATRIX()
    matrix = (1.0, 0.0, 0.0, 1.0)
    if R.FPDFText_GetMatrix(tp, index, mat):
        matrix = (float(mat.a), float(mat.b), float(mat.c), float(mat.d))
    return _ObjStyle(color, name, bold, float(R.FPDFText_GetFontSize(tp, index)), ascent, descent, matrix)


def _unicode(R: Any, tp: Any, i: int, n: int) -> tuple[str, int]:
    """Text of character ``i`` and how many character slots it used (surrogate pairs use two)."""
    u = int(R.FPDFText_GetUnicode(tp, i))
    if R.FPDFText_HasUnicodeMapError(tp, i) == 1:
        return "\ufffd", 1  # no Unicode mapping: a replacement character, as other extractors print it
    if 0xD800 <= u <= 0xDBFF and i + 1 < n:
        lo = int(R.FPDFText_GetUnicode(tp, i + 1))
        if 0xDC00 <= lo <= 0xDFFF:
            return chr(0x10000 + ((u - 0xD800) << 10) + (lo - 0xDC00)), 2
    if u == 0 or 0xD800 <= u <= 0xDFFF or u > 0x10FFFF:
        return "", 1
    return chr(u), 1


_Rect = tuple[float, float, float, float]  # left, bottom, right, top (PDF user space)
_Matrix = tuple[float, float, float, float, float, float]
_IDENTITY: _Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _concat(m1: _Matrix, m2: _Matrix) -> _Matrix:
    """``m1`` followed by ``m2`` (PDF row-vector convention)."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (a1 * a2 + b1 * c2, a1 * b2 + b1 * d2, c1 * a2 + d1 * c2, c1 * b2 + d1 * d2,
            e1 * a2 + f1 * c2 + e2, e1 * b2 + f1 * d2 + f2)


def _intersect(r1: _Rect | None, r2: _Rect) -> _Rect:
    if r1 is None:
        return r2
    return max(r1[0], r2[0]), max(r1[1], r2[1]), min(r1[2], r2[2]), min(r1[3], r2[3])


def _outside(box: _Rect, clip: _Rect) -> bool:
    return box[2] < clip[0] or box[0] > clip[2] or box[3] < clip[1] or box[1] > clip[3]


@dataclass
class _PageObjects:
    """What the page objects tell (forms followed): text clip rectangles, graphics, images, fonts."""

    clips: dict[int, _Rect | None] = field(default_factory=dict)  # text object → clip box (page space)
    has_graphics: bool = False
    has_images: bool = False
    fonts: dict[str, FontInfo] = field(default_factory=dict)  # fallback list (see _resource_fonts)


def _address(handle: Any) -> int:
    return int(ctypes.cast(handle, ctypes.c_void_p).value or 0)


def _clip_box(R: Any, obj: Any, mat: _Matrix, parent: _Rect | None) -> _Rect | None:
    """Bounding box of the object's clip path in page space, intersected with the enclosing clip."""
    clip = R.FPDFPageObj_GetClipPath(obj)
    box = parent
    if not clip:
        return box
    x, y = ctypes.c_float(), ctypes.c_float()
    a, b, c, d, e, f = mat
    for p in range(max(int(R.FPDFClipPath_CountPaths(clip)), 0)):
        xs: list[float] = []
        ys: list[float] = []
        for s in range(max(int(R.FPDFClipPath_CountPathSegments(clip, p)), 0)):
            seg = R.FPDFClipPath_GetPathSegment(clip, p, s)
            if seg and R.FPDFPathSegment_GetPoint(seg, x, y):
                px, py = float(x.value), float(y.value)
                xs.append(a * px + c * py + e)
                ys.append(b * px + d * py + f)
        if xs:
            box = _intersect(box, (min(xs), min(ys), max(xs), max(ys)))
    return box


def _font_name(R: Any, font: Any) -> str:
    getter = getattr(R, "FPDFFont_GetBaseFontName", None) or getattr(R, "FPDFFont_GetFontName", None)
    if getter is None:
        return ""
    n = int(getter(font, None, 0))
    if n <= 0:
        return ""
    buf = ctypes.create_string_buffer(n)
    getter(font, buf, n)
    return buf.value.decode("utf-8", "replace")


def _page_objects(R: Any, page: Any) -> _PageObjects:
    out = _PageObjects()

    def walk(parent: Any, count: Any, get: Any, mat: _Matrix, clip: _Rect | None, depth: int) -> None:
        for k in range(max(int(count(parent)), 0)):
            obj = get(parent, k)
            if not obj:
                continue
            kind = int(R.FPDFPageObj_GetType(obj))
            if kind == R.FPDF_PAGEOBJ_PATH:
                out.has_graphics = True
            elif kind == R.FPDF_PAGEOBJ_IMAGE:
                out.has_images = True
            elif kind == R.FPDF_PAGEOBJ_TEXT:
                out.clips[_address(obj)] = _clip_box(R, obj, mat, clip)
                font = R.FPDFTextObj_GetFont(obj)
                if font:
                    name = _font_name(R, font)
                    out.fonts.setdefault(name, FontInfo(name=name, embedded=R.FPDFFont_GetIsEmbedded(font) == 1, kind=""))
            elif kind == R.FPDF_PAGEOBJ_FORM and depth < _MAX_DEPTH:
                fm = R.FS_MATRIX()
                inner = mat
                if R.FPDFPageObj_GetMatrix(obj, fm):
                    inner = _concat((float(fm.a), float(fm.b), float(fm.c), float(fm.d), float(fm.e), float(fm.f)), mat)
                walk(obj, R.FPDFFormObj_CountObjects, R.FPDFFormObj_GetObject, inner, _clip_box(R, obj, mat, clip), depth + 1)

    walk(page, R.FPDFPage_CountObjects, R.FPDFPage_GetObject, _IDENTITY, None, 0)
    return out


@dataclass(slots=True)
class _RawChar:
    text: str
    key: int  # text object
    origin: tuple[float, float]
    matrix: tuple[float, float, float, float]
    loose: _Rect
    tight: _Rect
    sep: bool  # whitespace or clipped away: separates words, but still positions its neighbour


def _advance(ch: _RawChar, nxt: _RawChar | None, cell: float) -> float:
    """Advance width in text space (font size applied, character matrix not).

    pdfium's loose box is the union of the glyph cell (origin + advance, ``cell`` high) and the ink
    box; where the ink reaches past the advance (italics) the next character's origin tells it."""
    a, b, c, d = ch.matrix
    x, y = ch.origin
    following = nxt if nxt is not None and nxt.key == ch.key and nxt.matrix == ch.matrix else None
    if abs(b) < 1e-6 and abs(c) < 1e-6:
        if a > 0:
            width, inked = ch.loose[2] - x, ch.loose[2] - ch.tight[2] <= 0.01
        else:
            width, inked = x - ch.loose[0], ch.tight[0] - ch.loose[0] <= 0.01
        if inked and following is not None:
            step = (following.origin[0] - x) * (1.0 if a > 0 else -1.0)
            if 0.0 <= step <= width:
                width = step
        return max(width, 0.0) / abs(a)
    if following is not None:
        step = (following.origin[0] - x) * a + (following.origin[1] - y) * b  # along the baseline
        if step >= 0.0:
            return step / (a * a + b * b)
    lw, lh = ch.loose[2] - ch.loose[0], ch.loose[3] - ch.loose[1]
    if abs(a) >= abs(b):
        return max((lw - abs(c) * cell) / abs(a), 0.0)
    return max((lh - abs(d) * cell) / abs(b), 0.0)


def _page_chars(R: Any, tp: Any, geo: _Geometry, objects: _PageObjects) -> list[_Char]:
    """Characters of a text page in content order, with PyMuPDF-style glyph boxes; characters
    that are clipped away (cell clips, page edge) separate words like whitespace."""
    n = int(R.FPDFText_CountChars(tp))
    styles: dict[int, _ObjStyle] = {}
    page_box: _Rect = (geo.l, geo.b, geo.r, geo.t)
    ox, oy = ctypes.c_double(), ctypes.c_double()
    l_, r_, b_, t_ = ctypes.c_double(), ctypes.c_double(), ctypes.c_double(), ctypes.c_double()
    loose = R.FS_RECTF()
    is_generated, get_object = R.FPDFText_IsGenerated, R.FPDFText_GetTextObject
    get_origin, get_loose, get_tight = R.FPDFText_GetCharOrigin, R.FPDFText_GetLooseCharBox, R.FPDFText_GetCharBox
    clips = objects.clips
    raw: list[_RawChar | None] = []  # None: generated by pdfium or unusable
    i = 0
    while i < n:
        idx = i
        if is_generated(tp, idx) == 1:  # pdfium's own spaces / line breaks (no colour, no font)
            raw.append(None)
            i += 1
            continue
        text, used = _unicode(R, tp, idx, n)
        i += used
        if not text:
            continue
        obj = get_object(tp, idx)
        if not obj or not (get_origin(tp, idx, ox, oy) and get_loose(tp, idx, loose) and get_tight(tp, idx, l_, r_, b_, t_)):
            raw.append(None)
            continue
        key = _address(obj)
        st = styles.get(key)
        if st is None:
            st = styles[key] = _obj_style(R, tp, idx, obj)
        tight = (l_.value, b_.value, r_.value, t_.value)
        clip = clips.get(key)
        sep = text.isspace() or _outside(tight, page_box) or (clip is not None and _outside(tight, clip))
        raw.append(_RawChar(text, key, (ox.value, oy.value), st.matrix,
                            (loose.left, loose.bottom, loose.right, loose.top), tight, sep))
    out: list[_Char] = []
    last = len(raw) - 1
    for pos, ch in enumerate(raw):
        if ch is None or ch.sep:
            out.append(_SEP)
            continue
        st = styles[ch.key]
        a, b, c, d = ch.matrix
        size = st.tf * math.sqrt(abs(a * d - b * c))
        norm = math.hypot(a, b)
        if size <= 0.0 or norm <= 1e-9:
            out.append(_SEP)
            continue
        adv = _advance(ch, raw[pos + 1] if pos < last else None, (st.ascent - st.descent) * st.tf / 1000.0)
        asc, dsc = _metrics(st.ascent, st.descent)
        x, y = ch.origin
        if b == 0.0 and c == 0.0:  # upright (possibly scaled or mirrored) text: the usual case
            x0, x1 = sorted((x, x + a * adv))
            y0, y1 = sorted((y + d * dsc * st.tf, y + d * asc * st.tf))
        else:
            xs = [x + a * tx + c * ty for tx in (0.0, adv) for ty in (dsc * st.tf, asc * st.tf)]
            ys = [y + b * tx + d * ty for tx in (0.0, adv) for ty in (dsc * st.tf, asc * st.tf)]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        out.append(_Char(ch.text, False, geo.rect(x0, y0, x1, y1), (st.color, st.font, size, st.bold), (x, y),
                         (x + a * adv, y + b * adv), a / norm, b / norm))
    return out


def _breaks(prev: _Char, ch: _Char) -> bool:
    """Does ``ch`` start a new word after ``prev`` (other direction, new line or a gap)?"""
    if prev.ux * ch.ux + prev.uy * ch.uy < 0.999:
        return True
    size = ch.style[2]
    dx, dy = ch.origin[0] - prev.pen[0], ch.origin[1] - prev.pen[1]
    along = (dx * ch.ux + dy * ch.uy) / size
    across = (dy * ch.ux - dx * ch.uy) / size  # measured across the baseline: diagonal text stays one line
    return abs(across) >= _LINE_SHIFT or abs(along) > _SPACE_GAP


def _restyled(a: tuple[int, str, float, bool], b: tuple[int, str, float, bool]) -> bool:
    return a[0] != b[0] or a[1] != b[1] or a[3] != b[3] or abs(a[2] - b[2]) > 1e-3 * max(a[2], b[2])


def _words(chars: list[_Char], *, by_style: bool = True) -> list[Word]:
    """Rebuild words: split on whitespace, generated characters, new lines and gaps,
    and (``by_style``) wherever colour, font, size or weight change."""
    out: list[Word] = []
    cur: list[_Char] = []
    for ch in chars:
        if ch.sep:
            _flush(cur, out)
            continue
        if cur and ((by_style and _restyled(cur[-1].style, ch.style)) or _breaks(cur[-1], ch)):
            _flush(cur, out)
        cur.append(ch)
    _flush(cur, out)
    return out


def _flush(cur: list[_Char], out: list[Word]) -> None:
    if not cur:
        return
    text = "".join(c.text for c in cur)
    x0 = min(c.box[0] for c in cur)
    y0 = min(c.box[1] for c in cur)
    x1 = max(c.box[2] for c in cur)
    y1 = max(c.box[3] for c in cur)
    color, font, size, bold = cur[0].style
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


# ------------------------------------------------------------------------------- document level (pdfium)


@dataclass
class _RawPage:
    width: float
    height: float
    words: list[Word]
    has_graphics: bool = False
    has_images: bool = False
    fonts: list[FontInfo] = field(default_factory=list)  # used only when the resource scan fails
    image: Any = None  # rendered page for the OCR fallback


def _page_geometry(page: Any) -> _Geometry:
    return _Geometry(tuple(page.get_bbox()), int(page.get_rotation() or 0))


def _utf16(getter: Any, *args: Any) -> str:
    """Call a pdfium getter that fills a UTF-16LE buffer (size query first)."""
    n = int(getter(*args, None, 0))
    if n <= 2:
        return ""
    buf = ctypes.create_string_buffer(n)
    getter(*args, buf, n)
    return buf.raw[: n - 2].decode("utf-16-le", "replace")


def _toc(R: Any, doc: Any) -> list[tuple[int, str, int]]:
    """Outline as (level, title, 1-based page); -1 when an entry points nowhere in the document."""
    out: list[tuple[int, str, int]] = []
    seen: set[int] = set()

    def walk(parent: Any, level: int) -> None:
        bm = R.FPDFBookmark_GetFirstChild(doc, parent)
        while bm:
            addr = int(ctypes.cast(bm, ctypes.c_void_p).value or 0)
            if addr in seen:
                return
            seen.add(addr)
            title = _utf16(R.FPDFBookmark_GetTitle, bm)
            dest = R.FPDFBookmark_GetDest(doc, bm)
            index = int(R.FPDFDest_GetDestPageIndex(doc, dest)) if dest else -1
            out.append((level, title, index + 1 if index >= 0 else -1))
            if level < _MAX_DEPTH:
                walk(bm, level + 1)
            bm = R.FPDFBookmark_GetNextSibling(doc, bm)

    walk(None, 1)
    return out


_META_KEYS = (("Title", "title"), ("Author", "author"), ("Subject", "subject"), ("Keywords", "keywords"),
              ("Creator", "creator"), ("Producer", "producer"), ("CreationDate", "creationDate"),
              ("ModDate", "modDate"))


def _metadata(R: Any, pdf: Any) -> dict[str, str]:
    """Document information, keyed like PyMuPDF's ``Document.metadata`` (empty values dropped)."""
    meta: dict[str, str] = {}
    version = pdf.get_version()
    if version:
        meta["format"] = f"PDF {int(version) // 10}.{int(version) % 10}"
    for key, name in _META_KEYS:
        value = _utf16(R.FPDF_GetMetaText, pdf.raw, key.encode("ascii") + b"\x00")
        if value:
            meta[name] = value
    revision = int(R.FPDF_GetSecurityHandlerRevision(pdf.raw))
    if revision >= 0:
        meta["encryption"] = f"Standard R{revision}"
    return meta


def _extract(data: bytes, *, by_style: bool, full: bool, render_dpi: int = 0) -> tuple[list[_RawPage], list[tuple[int, str, int]], dict[str, str]]:
    """Read every page with pdfium (under :data:`PDFIUM_LOCK`). ``full`` adds the outline and the
    metadata; ``render_dpi`` renders pages without text for the OCR fallback."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as R

    pages: list[_RawPage] = []
    toc: list[tuple[int, str, int]] = []
    meta: dict[str, str] = {}
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            for index in range(len(pdf)):
                page = pdf.get_page(index)
                try:
                    geo = _page_geometry(page)
                    objects = _page_objects(R, page.raw)
                    tp = page.get_textpage()
                    try:
                        words = _words(_page_chars(R, tp.raw, geo, objects), by_style=by_style)
                    finally:
                        tp.close()
                    raw = _RawPage(geo.width, geo.height, words, objects.has_graphics, objects.has_images,
                                   list(objects.fonts.values()))
                    if render_dpi and not words:
                        bitmap = page.render(scale=render_dpi / 72.0)
                        try:
                            raw.image = bitmap.to_pil()
                        finally:
                            bitmap.close()
                    pages.append(raw)
                finally:
                    page.close()
            if full:
                toc = _toc(R, pdf.raw)
                meta = _metadata(R, pdf)
        finally:
            pdf.close()
    return pages, toc, meta


# ------------------------------------------------------------------------------- fonts (resources, pypdf)


def _font_info(font: Any) -> FontInfo:
    subtype = str(font.get("/Subtype", "") or "").lstrip("/")
    name = font.get("/BaseFont") or font.get("/Name") or ""
    descriptor_owner = font
    descendants = font.get("/DescendantFonts")
    if descendants is not None:
        descendants = descendants.get_object()
        if len(descendants):
            descriptor_owner = descendants[0].get_object()
    descriptor = descriptor_owner.get("/FontDescriptor")
    descriptor = descriptor.get_object() if descriptor is not None else None
    embedded = subtype == "Type3" or (
        descriptor is not None and any(k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3"))
    )
    return FontInfo(name=str(name).lstrip("/"), embedded=embedded, kind=subtype)


def _ref_key(kind: str, ref: Any) -> tuple[str, int, int]:
    from pypdf.generic import IndirectObject

    if isinstance(ref, IndirectObject):
        return kind, int(ref.idnum), int(ref.generation)
    return kind, id(ref), -1


def _scan_fonts(resources: Any, out: list[FontInfo], seen: set[tuple[str, int, int]], depth: int) -> None:
    """Fonts of a resource dictionary and, recursively, of the form XObjects it uses (each font once)."""
    from pypdf.generic import DictionaryObject

    res = resources.get_object() if resources is not None else None
    if not isinstance(res, DictionaryObject) or depth > _MAX_DEPTH:
        return
    fonts = res.get("/Font")
    fonts = fonts.get_object() if fonts is not None else None
    if isinstance(fonts, DictionaryObject):
        for ref in fonts.values():
            key = _ref_key("font", ref)
            if key in seen:
                continue
            seen.add(key)
            font = ref.get_object()
            if isinstance(font, DictionaryObject):
                out.append(_font_info(font))
    xobjects = res.get("/XObject")
    xobjects = xobjects.get_object() if xobjects is not None else None
    if isinstance(xobjects, DictionaryObject):
        for ref in xobjects.values():
            key = _ref_key("xobject", ref)
            if key in seen:
                continue
            seen.add(key)
            xobj = ref.get_object()
            if isinstance(xobj, DictionaryObject) and xobj.get("/Subtype") == "/Form":
                _scan_fonts(xobj.get("/Resources"), out, seen, depth + 1)


def _resource_fonts(data: bytes) -> list[list[FontInfo]] | None:
    """Per page, the fonts its resources reference (form XObjects included); None if pypdf cannot parse."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            return None
        out: list[list[FontInfo]] = []
        for page in reader.pages:
            fonts: list[FontInfo] = []
            _scan_fonts(page.get("/Resources"), fonts, set(), 0)
            out.append(fonts)
        return out
    except Exception:
        return None


def page_count(path: str | Path) -> int:
    """Number of pages (pypdf; the file is read into memory first, so no handle stays open)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(Path(path).read_bytes()), strict=False)
    if reader.is_encrypted:
        reader.decrypt("")
    return len(reader.pages)


# ------------------------------------------------------------------------------- public readers


def read_pdf(path: str | Path, *, ocr: bool = False) -> PdfDocument:
    p = Path(path)
    data = p.read_bytes()
    want_ocr = ocr and _ocr_available()
    raw_pages, toc, meta = _extract(data, by_style=True, full=True, render_dpi=300 if want_ocr else 0)
    resource_fonts = _resource_fonts(data)
    pages: list[PdfPage] = []
    ocr_used = False
    for i, raw in enumerate(raw_pages, start=1):
        all_words = raw.words
        if not all_words and raw.image is not None:
            all_words = _ocr_words(raw.image)
            ocr_used = True
        all_words = _merge_numeric_fragments(sorted(all_words, key=lambda w: (round(w.yc), w.x0)))
        content = [w for w in all_words if not w.overlay]
        overlay = [w for w in all_words if w.overlay]
        pg = PdfPage(number=i, width=raw.width, height=raw.height, words=content, overlay_words=overlay)
        pg.lines = _group_lines(content, i)
        pg.numbers = _tokens(content, i)
        pg.fonts = resource_fonts[i - 1] if resource_fonts is not None and len(resource_fonts) == len(raw_pages) else raw.fonts
        pg.has_graphics = raw.has_graphics
        pg.has_images = raw.has_images
        pages.append(pg)
    return PdfDocument(path=p, sha256=hashlib.sha256(data).hexdigest(), pages=pages, toc=toc, metadata=meta, ocr_used=ocr_used)


def read_pdf_words(path: str | Path) -> list[list[Word]]:
    """Plain words per page, in content order: split on whitespace, new lines and gaps only.

    Unlike :func:`read_pdf` there is no splitting on style changes, no merging of numeric
    fragments and overlay / invisible text is included (PyMuPDF's ``get_text("words")``).
    """
    raw_pages, _toc_unused, _meta_unused = _extract(Path(path).read_bytes(), by_style=False, full=False)
    return [raw.words for raw in raw_pages]


def _ocr_available() -> bool:
    return importlib.util.find_spec("pytesseract") is not None


def _ocr_words(image: Any) -> list[Word]:  # pragma: no cover - requires Tesseract
    """OCR fallback for scanned PDFs (local Tesseract only; lower certainty). ``image`` is the
    page rendered at 300 dpi."""
    import pytesseract

    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
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
