"""Build the illustrated FS-Platform user guide (A4 PDF) from real screenshots of the desktop edition.

The guide is for the staff of a small accounting firm: they know Ind AS statements well but are not technical.
It starts where the one-page installation guide (install_guide.py) ends — "open FS-Platform" — and teaches
day-to-day use. First capture the screens by driving a throw-away copy of the app with synthetic data, then
build the PDF:

    node docs/client-guides/capture_screens.mjs --data-dir /tmp/fs-guide-data
    .venv/bin/python docs/client-guides/user_guide.py --contact "Name · phone · e-mail"

The pictures come from build/client-guides/screens (PNG files and manifest.json, which says where the buttons
are on each picture); the PDF goes to dist/. Both folders are git-ignored. Never commit the PDF: the
confidentiality scan rejects PDF files outside fixtures/synthetic.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image
from pypdf import PdfReader
from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

REPO = Path(__file__).resolve().parents[2]
FONT_DIR = REPO / "fscore" / "src" / "fscore" / "pdf" / "fonts"  # DejaVu: has the → and ₹ glyphs
VERSION_FILE = REPO / "fsoffline" / "src" / "fsoffline" / "__init__.py"
DEFAULT_SCREENS = REPO / "build" / "client-guides" / "screens"
DEFAULT_OUT = REPO / "dist" / "client-guides" / "FS-Platform-User-Guide.pdf"
NO_CONTACT = "the person who sent you FS-Platform"

# The install guide's palette: one accent colour plus greys (every text colour has at least 7:1 contrast on white,
# tints print as light grey). One warm colour frames, on the pictures, the button or field a step talks about.
INK = HexColor("#1F2933")
MUTED = HexColor("#4B5563")
ACCENT = HexColor("#1B5E7A")
TINT = HexColor("#EAF2F6")
NOTE_FILL = HexColor("#F1F6F9")
RULE = HexColor("#A3BBC7")
FRAME = HexColor("#D9480F")
PICTURE_EDGE = HexColor("#B9C3CE")

PAGE_W, PAGE_H = A4
MARGIN_X = 18 * mm
MARGIN_TOP = 16 * mm
MARGIN_BOTTOM = 19 * mm
CONTENT_W = PAGE_W - 2 * MARGIN_X

REGULAR, BOLD, OBLIQUE, BOLD_OBLIQUE = "Guide", "Guide-Bold", "Guide-Oblique", "Guide-BoldOblique"
CAP_HEIGHT = 0.73  # DejaVu Sans, as a fraction of the font size
DESCENT = 0.24

DISC_R = 11.5  # step-number disc, as in the install guide
STEP_INDENT = 2 * DISC_R + 7
MARK_R = 8.5  # the same disc, smaller, on the pictures
DPI = 250  # pictures are scaled to this resolution at their printed size
MIN_TEXT_PT = 10.5  # nothing in the guide's own text is smaller

# Words a non-technical reader should never meet in the guide's text (checked in the finished PDF).
BANNED = ("MFA", "TOTP", "API", "loopback", "port", "SHA", "JSON", "endpoint")

WARNINGS: set[str] = set()  # layout problems worth a look, printed after the build


# ---------------------------------------------------------------------------------------------- fonts and styles


def register_fonts() -> None:
    faces = {
        REGULAR: "DejaVuSansCondensed.ttf",
        BOLD: "DejaVuSansCondensed-Bold.ttf",
        OBLIQUE: "DejaVuSansCondensed-Oblique.ttf",
        BOLD_OBLIQUE: "DejaVuSansCondensed-BoldOblique.ttf",
    }
    for name, filename in faces.items():
        pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / filename)))
    pdfmetrics.registerFontFamily(REGULAR, normal=REGULAR, bold=BOLD, italic=OBLIQUE, boldItalic=BOLD_OBLIQUE)


def style(
    name: str,
    size: float,
    leading: float,
    *,
    font: str = REGULAR,
    color: Color = INK,
    before: float = 0.0,
    after: float = 0.0,
    keep: bool = False,
) -> ParagraphStyle:
    if size < MIN_TEXT_PT:
        raise ValueError(f"style {name}: {size} pt is below the guide's minimum of {MIN_TEXT_PT} pt")
    # No hyphenation and no splitting inside words, so file names and button names stay whole.
    return ParagraphStyle(
        name,
        fontName=font,
        fontSize=size,
        leading=leading,
        textColor=color,
        spaceBefore=before,
        spaceAfter=after,
        keepWithNext=int(keep),
        splitLongWords=0,
        hyphenationLang="",
        embeddedHyphenation=0,
        allowWidows=0,
        allowOrphans=0,
    )


BODY = style("body", 11, 15.5, after=7)
INTRO = style("intro", 12, 16.5, color=MUTED, after=6)
STEP = style("step", 11, 15)
NOTE = style("note", 10.5, 14)
CAPTION = style("caption", 10.5, 13.5, font=OBLIQUE, color=MUTED)
CELL = style("cell", 10.5, 13.5)
CELL_HEAD = style("cell-head", 10.5, 13.5, font=BOLD)
QUESTION = style("question", 11.5, 15.5, font=BOLD, color=ACCENT, before=8, after=2, keep=True)
TOC_ENTRY = style("toc", 12.5, 23)
COVER_TITLE = style("cover-title", 30, 36, font=BOLD)
COVER_SUB = style("cover-sub", 14, 19, color=MUTED)
COVER_TEXT = style("cover-text", 12, 17)
TITLE = style("title", 21, 26, font=BOLD, after=12)
BOX_TITLE = style("box-title", 12.5, 16, font=BOLD, color=ACCENT)
FOOTER_SIZE = 10.5


# ---------------------------------------------------------------------------------------------- markup


def nb(text: str) -> str:
    """Keep a phrase on one line."""
    return text.replace(" ", "&nbsp;")


def b(text: str) -> str:
    """A button, field or menu, exactly as written on the screen (kept on one line unless it is long)."""
    escaped = html.escape(text, quote=False)
    return f"<b>{nb(escaped) if len(text) <= 24 else escaped}</b>"


def menu(*names: str) -> str:
    """A menu path, e.g. Administration → Users."""
    return "&nbsp;→ ".join(b(n) for n in names)


def i(text: str) -> str:
    """Text the screen shows (a message, a value)."""
    return f"<i>{html.escape(text, quote=False)}</i>"


# ---------------------------------------------------------------------------------------------- text blocks


def para(text: str, st: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(text, st)


class ChapterTitle(Flowable):
    """A numbered chapter heading; it registers itself in the contents and in the PDF's bookmarks."""

    SIZE = 21.0
    BOX = 30.0

    def __init__(self, number: int, title: str) -> None:
        super().__init__()
        self.number, self.title = number, title
        self.key = f"chapter-{number}"

    def wrap(self, aw: float, ah: float) -> tuple[float, float]:
        self.width = aw
        return aw, self.BOX

    def getKeepWithNext(self) -> bool:
        return True

    def draw(self) -> None:
        c = self.canv
        box = self.BOX
        c.setFillColor(ACCENT)
        c.roundRect(0, 0, box, box, 5, stroke=0, fill=1)
        c.setFillColor(white)
        c.setFont(BOLD, 17)
        c.drawCentredString(box / 2, box / 2 - 17 * CAP_HEIGHT / 2, str(self.number))
        c.setFillColor(INK)
        c.setFont(BOLD, self.SIZE)
        c.drawString(box + 11, box / 2 - self.SIZE * CAP_HEIGHT / 2, self.title)
        c.bookmarkPage(self.key)
        c.addOutlineEntry(f"{self.number}  {self.title}", self.key, level=0)


class Heading(Flowable):
    """Section heading in the accent colour with a hairline to the right edge (as in the install guide)."""

    SIZE = 13.0

    def __init__(self, text: str, space_before: float = 13.0) -> None:
        super().__init__()
        self.text = text
        self.space_before = space_before

    def wrap(self, aw: float, ah: float) -> tuple[float, float]:
        self.width = aw
        return aw, self.SIZE * (CAP_HEIGHT + DESCENT) + 7

    def getSpaceBefore(self) -> float:
        return self.space_before

    def getKeepWithNext(self) -> bool:
        return True

    def draw(self) -> None:
        c = self.canv
        baseline = 7 + self.SIZE * DESCENT
        c.setFont(BOLD, self.SIZE)
        c.setFillColor(ACCENT)
        c.drawString(0, baseline, self.text)
        start = pdfmetrics.stringWidth(self.text, BOLD, self.SIZE) + 8
        c.setStrokeColor(RULE)
        c.setLineWidth(0.8)
        y = baseline + self.SIZE * CAP_HEIGHT / 2
        c.line(start, y, self.width, y)


def disc(c: Any, cx: float, cy: float, number: int, r: float = DISC_R, size: float = 14.0, ring: bool = False) -> None:
    if ring:  # on a picture: a white ring keeps the disc apart from the screen behind it
        c.setFillColor(white)
        c.circle(cx, cy, r + 1.8, stroke=0, fill=1)
    c.setFillColor(ACCENT)
    c.circle(cx, cy, r, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont(BOLD, size)
    c.drawCentredString(cx, cy - size * CAP_HEIGHT / 2, str(number))


class Step(Flowable):
    """A numbered step: a filled disc with the number, then the instruction (as in the install guide)."""

    def __init__(self, number: int, text: str) -> None:
        super().__init__()
        self.number = number
        self.para = Paragraph(text, STEP)

    def wrap(self, aw: float, ah: float) -> tuple[float, float]:
        self.width = aw
        _, h = self.para.wrap(aw - STEP_INDENT, ah)
        self.height = max(h, 2 * DISC_R - 2)
        return aw, self.height

    def getSpaceAfter(self) -> float:
        return 7.0

    def draw(self) -> None:
        c = self.canv
        top = self.height
        disc(c, DISC_R, top - STEP.fontSize + STEP.fontSize * CAP_HEIGHT / 2, self.number)
        self.para.drawOn(c, STEP_INDENT, top - self.para.height)


class Callout(Flowable):
    """A tinted note with an accent bar: "This is normal —", "Tip —", "Good to know —"."""

    def __init__(self, label: str, text: str) -> None:
        super().__init__()
        self.para = Paragraph(f"<b>{label}</b> — {text}", NOTE)

    def wrap(self, aw: float, ah: float) -> tuple[float, float]:
        self.width = aw
        _, h = self.para.wrap(aw - 3 - 20, ah)
        self.height = h + 13
        return aw, self.height

    def getSpaceBefore(self) -> float:
        return 3.0

    def getSpaceAfter(self) -> float:
        return 9.0

    def draw(self) -> None:
        c = self.canv
        c.setFillColor(NOTE_FILL)
        c.rect(0, 0, self.width, self.height, stroke=0, fill=1)
        c.setFillColor(ACCENT)
        c.rect(0, 0, 3, self.height, stroke=0, fill=1)
        self.para.drawOn(c, 3 + 10, 6.5)


def normal(text: str) -> Callout:
    return Callout("This is normal", text)


def tip(text: str) -> Callout:
    return Callout("Tip", text)


def know(text: str) -> Callout:
    return Callout("Good to know", text)


class HelpBox(Flowable):
    """The install guide's "Need help?" box: a label on the left, who to call on the right."""

    LABEL_W = 104.0

    def __init__(self, contact: str | None) -> None:
        super().__init__()
        question = f"Contact:<br/><b>{contact}</b>" if contact else f"Contact {NO_CONTACT}."
        self.text = Paragraph(f"{question}<br/>On a call, say which chapter and step you are on.", STEP)
        self.title = Paragraph("Need help?", BOX_TITLE)

    def wrap(self, aw: float, ah: float) -> tuple[float, float]:
        self.width = aw
        _, self.text_h = self.text.wrap(aw - 28 - self.LABEL_W, ah)
        self.title.wrap(self.LABEL_W, ah)
        self.height = 10 + self.text_h + 11
        return aw, self.height

    def draw(self) -> None:
        c = self.canv
        c.setFillColor(white)
        c.setStrokeColor(ACCENT)
        c.setLineWidth(1.2)
        c.roundRect(0, 0, self.width, self.height, 6, stroke=1, fill=1)
        baseline = self.height - 10 - STEP.fontSize
        self.title.drawOn(c, 14, baseline + BOX_TITLE.fontSize - self.title.height)
        self.text.drawOn(c, 14 + self.LABEL_W, self.height - 10 - self.text_h)


class TintBox(Flowable):
    """A tinted box with a title and a few paragraphs (the install guide's "Before you start")."""

    def __init__(self, title: str, paragraphs: Sequence[str]) -> None:
        super().__init__()
        self.title = Paragraph(title, BOX_TITLE)
        self.paras = [Paragraph(p, NOTE) for p in paragraphs]

    def wrap(self, aw: float, ah: float) -> tuple[float, float]:
        self.width = aw
        inner = aw - 28
        title_h = self.title.wrap(inner, ah)[1]
        self.heights = [p.wrap(inner, ah)[1] for p in self.paras]
        self.height = 10 + title_h + 5 + sum(self.heights) + 6 * (len(self.paras) - 1) + 11
        return aw, self.height

    def draw(self) -> None:
        c = self.canv
        c.setFillColor(TINT)
        c.roundRect(0, 0, self.width, self.height, 6, stroke=0, fill=1)
        y = self.height - 10
        self.title.drawOn(c, 14, y - self.title.height)
        y -= self.title.height + 5
        for p, h in zip(self.paras, self.heights, strict=True):
            p.drawOn(c, 14, y - h)
            y -= h + 6


def grid(rows: Sequence[tuple[str, str]], first_w: float, *, head: tuple[str, str] | None = None) -> Table:
    """A two-column table of short texts: a name in bold, then what it means; optionally a heading row."""
    data: list[list[Any]] = [[Paragraph(a, CELL_HEAD), Paragraph(t, CELL)] for a, t in rows]
    commands: list[tuple[Any, ...]] = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5.5),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, RULE),
    ]
    if head:
        data.insert(0, [Paragraph(head[0], CELL_HEAD), Paragraph(head[1], CELL_HEAD)])
        commands.append(("BACKGROUND", (0, 0), (-1, 0), TINT))
    t = Table(data, colWidths=[first_w, CONTENT_W - first_w], hAlign="LEFT", repeatRows=1 if head else 0)
    t.setStyle(TableStyle(commands))
    return t


# ---------------------------------------------------------------------------------------------- pictures


class Screens:
    """The captured pictures and where things are on them (manifest.json from capture_screens.mjs)."""

    def __init__(self, folder: Path, cache: Path) -> None:
        self.folder, self.cache = folder, cache
        manifest = folder / "manifest.json"
        if not manifest.is_file():
            raise SystemExit(
                f"no screenshots in {folder}: run node docs/client-guides/capture_screens.mjs first (see the README)"
            )
        self.manifest = json.loads(manifest.read_text(encoding="utf-8"))
        self.scale = float(self.manifest.get("scale", 2))
        self.used: set[str] = set()
        self.files: set[Path] = set()

    def shot(self, name: str) -> dict[str, Any]:
        try:
            info: dict[str, Any] = self.manifest["shots"][name]
        except KeyError:
            raise SystemExit(f"picture {name!r} is missing from {self.folder}; capture the screens again") from None
        return info

    def has(self, name: str) -> bool:
        return name in self.manifest.get("shots", {})

    def image(self, name: str, crop: tuple[float, float, float, float], width_pt: float) -> Path:
        """The cropped picture, scaled to DPI at its printed width (cached by source, crop and size)."""
        src = self.folder / self.shot(name)["file"]
        x, y, w, h = crop
        target_w = max(1, round(width_pt / 72 * DPI))
        key = f"{src.stat().st_mtime_ns}:{src.stat().st_size}:{crop}:{target_w}"
        out = self.cache / f"{name}-{hashlib.sha256(key.encode()).hexdigest()[:16]}.png"
        if not out.exists():
            self.cache.mkdir(parents=True, exist_ok=True)
            with Image.open(src) as im:
                s = self.scale
                part = im.convert("RGB").crop((round(x * s), round(y * s), round((x + w) * s), round((y + h) * s)))
                if part.width > target_w:
                    part = part.resize((target_w, round(part.height * target_w / part.width)), Image.Resampling.LANCZOS)
                part.save(out, optimize=True)
        self.used.add(name)
        self.files.add(out)
        return out

    def prune(self) -> None:
        """Remove cached cuts this build did not use (older crops, older captures)."""
        for old in self.cache.glob("*.png"):
            if old not in self.files:
                old.unlink()


SCREENS: Screens | None = None


def screens() -> Screens:
    if SCREENS is None:
        raise RuntimeError("load the screenshots first")
    return SCREENS


Region = tuple[float, float, float, float]


def area(x0: float, y0: float, x1: float, y1: float) -> Region:
    """A rectangle of the 1366 × 800 screen, by its corners (screen pixels)."""
    return (x0, y0, x1 - x0, y1 - y0)


def strip(name: str, key: str, top: float, bottom: float) -> Region:
    """A marked element's full width, from ``top`` to ``bottom`` of the screen (e.g. the middle of a PDF page)."""
    marks = screens().shot(name)["marks"]
    if key not in marks:
        raise SystemExit(f"{name}: nothing marked {key!r}; capture the screens again")
    x, _, w, _ = marks[key]
    return area(x, top, x + w, bottom)


def around(name: str, *keys: str) -> Region:
    """The smallest rectangle holding the marked elements of a picture (add room with the picture's pad)."""
    marks = screens().shot(name)["marks"]
    missing = [k for k in keys if k not in marks]
    if missing:
        raise SystemExit(f"{name}: nothing marked {', '.join(missing)}; capture the screens again")
    boxes = [marks[k] for k in keys]
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    return area(x0, y0, max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes))


@dataclass
class Mark:
    """Frame a marked element of a picture; with ``number``, put that step's disc beside the frame."""

    key: str
    number: int | None = None
    side: str = "left"  # where the disc goes: left, right, top, bottom
    frame: bool = True
    borrow: str | None = None  # take the position from this other picture of the same screen (same layout)


@dataclass
class Picture:
    name: str
    caption: str
    width: float  # printed width in pt
    region: str | Region | None = None  # a marked element, a rectangle (screen pixels) or the whole screen
    pad: float | tuple[float, float, float, float] = 12.0  # around a region: one value, or (left, top, right, bottom)
    marks: list[Mark] = field(default_factory=list)


class Shot(Flowable):
    """A screenshot with a thin edge, orange frames, step discs and a one-line caption underneath."""

    GAP = 5.0

    def __init__(self, pic: Picture) -> None:
        super().__init__()
        self.pic = pic
        sc = screens()
        info = sc.shot(pic.name)
        self.marks: dict[str, list[float]] = info["marks"]
        vw, vh = float(info["width"]), float(info["height"])
        if pic.region is None:
            x, y, w, h = 0.0, 0.0, vw, vh
        elif isinstance(pic.region, str):
            if pic.region not in self.marks:
                raise SystemExit(f"{pic.name}: no region {pic.region!r} on the picture; capture the screens again")
            x, y, w, h = self.marks[pic.region]
        else:
            x, y, w, h = pic.region
        pad = pic.pad if isinstance(pic.pad, tuple) else (pic.pad,) * 4
        x0, y0 = max(0.0, x - pad[0]), max(0.0, y - pad[1])
        x1, y1 = min(vw, x + w + pad[2]), min(vh, y + h + pad[3])
        self.crop = (x0, y0, x1 - x0, y1 - y0)
        self.k = pic.width / self.crop[2]  # pt per screen pixel
        self.img_w, self.img_h = pic.width, self.crop[3] * self.k
        self.path = sc.image(pic.name, self.crop, pic.width)
        self.caption = Paragraph(pic.caption, CAPTION)
        for m in pic.marks:
            if m.borrow:
                self.marks = {**self.marks, m.key: sc.shot(m.borrow)["marks"].get(m.key, [])}
            if not self.marks.get(m.key):
                raise SystemExit(f"{pic.name}: nothing marked {m.key!r}; capture the screens again")

    def wrap(self, aw: float, ah: float) -> tuple[float, float]:
        if self.img_w > aw + 0.5:
            raise SystemExit(f"{self.pic.name}: {self.img_w:.0f} pt wide, but only {aw:.0f} pt available")
        self.width = aw
        self.cap_w = min(aw, max(self.img_w, 220.0))
        _, self.cap_h = self.caption.wrap(self.cap_w, ah)
        if len(self.caption.blPara.lines) > 1:
            WARNINGS.add(f"{self.pic.name}: the caption takes more than one line")
        self.height = self.img_h + self.GAP + self.cap_h
        return aw, self.height

    def getSpaceBefore(self) -> float:
        return 4.0

    def getSpaceAfter(self) -> float:
        return 11.0

    def _rect(self, key: str) -> tuple[float, float, float, float] | None:
        """A marked element in picture coordinates (pt, origin at the picture's bottom left), cut to the picture;
        None when it lies outside the part of the screen shown."""
        x, y, w, h = self.marks[key]
        cx, cy, cw, ch = self.crop
        left, top = max(x, cx), max(y, cy)
        right, bottom = min(x + w, cx + cw), min(y + h, cy + ch)
        if right - left < 2 or bottom - top < 2:
            return None
        return ((left - cx) * self.k, self.img_h - (bottom - cy) * self.k, (right - left) * self.k, (bottom - top) * self.k)

    def draw(self) -> None:
        c = self.canv
        x0 = (self.width - self.img_w) / 2 if self.img_w < self.width - 1 else 0.0
        y0 = self.cap_h + self.GAP
        c.drawImage(str(self.path), x0, y0, self.img_w, self.img_h)
        c.setStrokeColor(PICTURE_EDGE)
        c.setLineWidth(0.6)
        c.rect(x0, y0, self.img_w, self.img_h, stroke=1, fill=0)
        for m in self.pic.marks:
            rect = self._rect(m.key)
            if rect is None:
                WARNINGS.add(f"{self.pic.name}: {m.key!r} is outside the part of the screen shown")
                continue
            rx, ry, rw, rh = rect
            rx, ry = rx + x0, ry + y0
            if m.frame:
                p = 2.5
                c.setStrokeColor(FRAME)
                c.setLineWidth(1.8)
                c.roundRect(rx - p, ry - p, rw + 2 * p, rh + 2 * p, 3.5, stroke=1, fill=0)
            if m.number is not None:
                gap = MARK_R + 5
                cx, cy = {
                    "left": (rx - gap, ry + rh / 2),
                    "right": (rx + rw + gap, ry + rh / 2),
                    "top": (rx + rw / 2, ry + rh + gap),
                    "bottom": (rx + rw / 2, ry - gap),
                }[m.side]
                # a disc may stick out sideways, into the margin or the gap beside the picture, but not above or below
                cx = min(max(cx, x0 - MARK_R + 1), x0 + self.img_w + MARK_R - 1)
                cy = min(max(cy, y0 + MARK_R + 2), y0 + self.img_h - MARK_R - 2)
                disc(c, cx, cy, m.number, r=MARK_R, size=11.0, ring=True)
        cap_x = x0 if self.img_w >= self.cap_w else (self.width - self.cap_w) / 2
        self.caption.drawOn(c, cap_x, 0)


def shot(name: str, caption: str, width: float, region: str | Region | None = None, *,
         pad: float | tuple[float, float, float, float] = 12.0, marks: Sequence[Mark] = ()) -> Shot:
    return Shot(Picture(name, caption, width, region, pad, list(marks)))


def side_by_side(left: list[Flowable], right: Flowable, right_w: float, gap: float = 16.0) -> Table:
    """Steps on the left, a narrow picture (a dialog, the sign-in page) on the right."""
    t = Table([[left, right]], colWidths=[CONTENT_W - right_w - gap, right_w + gap], hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("LEFTPADDING", (1, 0), (1, 0), gap),
            ]
        )
    )
    return t


# ---------------------------------------------------------------------------------------------- the document


class GuideDoc(BaseDocTemplate):
    def __init__(self, filename: str, version: str, contact: str | None) -> None:
        super().__init__(
            filename,
            pagesize=A4,
            leftMargin=MARGIN_X,
            rightMargin=MARGIN_X,
            topMargin=MARGIN_TOP,
            bottomMargin=MARGIN_BOTTOM,
            title="FS-Platform — User guide",
            author="FS-Platform",
            subject=f"How to use FS-Platform {version} day to day",
            creator="docs/client-guides/user_guide.py",
            pageCompression=1,
            initialFontName=REGULAR,
            initialFontSize=11,
        )
        self.version = version
        self.help_box = HelpBox(contact)  # at the foot of the cover
        body = Frame(MARGIN_X, MARGIN_BOTTOM, CONTENT_W, PAGE_H - MARGIN_TOP - MARGIN_BOTTOM, id="body",
                     leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([
            PageTemplate("cover", [body], onPage=self.cover_page),
            PageTemplate("page", [body], onPage=self.page),
        ])

    def afterFlowable(self, flowable: Flowable) -> None:
        if isinstance(flowable, ChapterTitle):
            number = f"{flowable.number:>2}".replace(" ", "\u2007")  # a figure space is as wide as a digit
            self.notify("TOCEntry", (0, f"{number}&nbsp;&nbsp;&nbsp;{flowable.title}", self.page, flowable.key))

    def cover_page(self, c: Any, doc: Any) -> None:
        c.saveState()
        c.setFillColor(ACCENT)
        c.rect(0, PAGE_H - 8 * mm, PAGE_W, 8 * mm, stroke=0, fill=1)
        self.help_box.wrap(CONTENT_W, PAGE_H)
        self.help_box.drawOn(c, MARGIN_X, MARGIN_BOTTOM)
        c.restoreState()

    def page(self, c: Any, doc: Any) -> None:
        c.saveState()
        y = MARGIN_BOTTOM - 10 * mm
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.line(MARGIN_X, y + 15, PAGE_W - MARGIN_X, y + 15)
        c.setFont(REGULAR, FOOTER_SIZE)
        c.setFillColor(MUTED)
        c.drawString(MARGIN_X, y, f"FS-Platform user guide · version {self.version}")
        c.drawRightString(PAGE_W - MARGIN_X, y, f"Page {doc.page}")
        c.restoreState()


# ---------------------------------------------------------------------------------------------- content
#
# The pictures are cut from 1366 × 800 screenshots; regions and pads are in screen pixels (see manifest.json:
# "main" is the working area right of the menu and below the top bar). The numbers on a picture repeat the
# numbers of the steps that go with it.

SIDE_W = 236.0  # a narrow picture beside the steps
FULL = CONTENT_W


def chapter(number: int, title: str, intro: str) -> list[Flowable]:
    return [CondPageBreak(PAGE_H * 0.45), ChapterTitle(number, title), Spacer(1, 9), para(intro, INTRO)]


def cover(version: str) -> list[Flowable]:
    return [
        Spacer(1, 22),
        para("FS-Platform — User guide", COVER_TITLE),
        Spacer(1, 8),
        para("Preparing and reviewing Ind AS financial statements, step by step", COVER_SUB),
        para(f"FS-Platform {html.escape(version)} · for Windows 10 and 11", COVER_SUB),
        Spacer(1, 20),
        shot("tour", "The Dashboard: where your working day with FS-Platform starts.", FULL, pad=0),
        Spacer(1, 4),
        para(
            "For everyone at the firm who uses FS-Platform: to prepare financial statements, to review them, "
            "or to look after colleagues’ access. It starts where the installation guide ends — FS-Platform is "
            "installed and your firm is set up.",
            COVER_TEXT,
        ),
        Spacer(1, 14),
        TintBox(
            "In this guide",
            [
                "<b>Every day</b> — opening FS-Platform and signing in, the screen, a client and its engagement, "
                "the statements workbook, the checks, the fix plan that a second person approves, draft and "
                "final PDFs.",
                "<b>Together</b> — colleagues and their roles, the administrator’s tasks (people, passwords, "
                "backups), and answers to common questions.",
            ],
        ),
        NextPageTemplate("page"),
        PageBreak(),
    ]


def contents() -> list[Flowable]:
    toc = TableOfContents(dotsMinLevel=0)
    toc.levelStyles = [TOC_ENTRY]
    return [
        para("Contents", TITLE),
        toc,
        Spacer(1, 16),
        TintBox(
            "How to read this guide",
            [
                f"Words in bold, like {b('Sign in')}, are buttons, menus and fields, written exactly as on the "
                "screen. Words in italics are what the screen says.",
                "The numbered steps tell you what to do. The same numbers appear on the pictures, next to an "
                "orange frame around the button or field the step is about.",
                "<b>This is normal</b> boxes explain messages that may look worrying but are expected. "
                "<b>Tip</b> and <b>Good to know</b> boxes save you time.",
                "The firm and the people in the pictures are invented: Asha Menon, the administrator, who is also "
                "a partner, and her colleague Meera Iyer, a manager, at Corvus Advisors. Passwords, one-time "
                "links and folder names are hidden behind grey boxes.",
            ],
        ),
    ]


def ch_opening() -> list[Flowable]:
    return [
        *chapter(1, "Opening FS-Platform",
                 "FS-Platform runs on this PC. You work in your web browser, but nothing leaves the PC: there is "
                 "no website and no cloud."),
        Heading("Open FS-Platform and sign in", 6),
        side_by_side(
            [
                Step(1, f"Double-click {b('FS-Platform')} on the desktop, or click it in the Start menu. "
                        "Your web browser opens with the sign-in page."),
                Step(2, f"Type your {b('E-mail')} and your {b('Password')}."),
                Step(3, f"Click {b('Sign in')}. Your {b('Dashboard')} opens: chapter 2 shows you around."),
                para(f"New here? Click {b('Create an account')} (chapter 9). Forgotten your password? Your "
                     "administrator can reset it (chapter 10)."),
            ],
            shot("sign-in", "The sign-in page.", SIDE_W, "card", pad=14,
                 marks=[Mark("email", 2), Mark("password"), Mark("signin", 3)]),
            SIDE_W,
        ),
        normal("FS-Platform keeps running in the background, also when you close the browser. Its small icon "
               "sits next to the clock, at the bottom right of the screen (click the ^ arrow if you do not see "
               "it). Leave it running. If it already runs, the desktop icon simply opens the browser again."),
        Heading("Sign out"),
        para(f"Colleagues use FS-Platform on this same PC, each with their own account. When you leave the PC, "
             f"click {b('Sign out')} at the top right of the screen (number 5 in chapter 2)."),
        Heading("When FS-Platform signs you out by itself"),
        para("To protect client data, FS-Platform signs you out after 60 minutes without activity, and after 12 "
             "hours in any case. The sign-in page then says why, for example "
             f"{i('You were signed out after a period of inactivity.')}"),
        normal("sign in again. Everything you saved is still there; only something you were still typing is "
               "lost."),
    ]


def ch_tour() -> list[Flowable]:
    legend = [
        f"The menu: {b('Dashboard')} (this page), {b('Clients & entities')}, {b('Audit trail')} and "
        f"{b('Rule registry')}, the list of all the checks. Administrators also see {b('Administration')}. "
        f"{b('Help')} and {b('Release notes')} are at the bottom.",
        "Your firm’s name.",
        f"{b('? Help')} explains the screen you are on, in a few lines.",
        "Your name. It opens your profile.",
        f"{b('Sign out')}.",
        "Your figures at a glance: engagements, open High findings, review notes waiting for you, and "
        "engagements past their due date.",
        f"{b('My engagements')}: every engagement you work on, with its status. Click a name to open it.",
        f"{b('New engagement')} takes you to your clients (chapter 3).",
    ]
    return [
        *chapter(2, "A tour of the screen",
                 "Every screen has the same frame: the menu on the left, a bar along the top, and your work in "
                 "the middle. This is Asha’s Dashboard, the first page after signing in."),
        shot("tour", "Asha’s Dashboard. The numbers match the list below.", FULL, pad=0,
             marks=[Mark("clients", 1, frame=False), Mark("firm", 2, "right", frame=False),
                    Mark("stats", 6, "left", frame=False), Mark("engagements", 7, "left", frame=False),
                    Mark("newEngagement", 8, "left")]),
        *[Step(n, text) for n, text in enumerate(legend[:2], start=1)],
        side_by_side(
            [Step(n, text) for n, text in enumerate(legend[2:5], start=3)],
            shot("tour", "The top right corner, close up.", SIDE_W,
                 around("tour", "helpButton", "me", "signout"), pad=(8, 8, 12, 23),
                 marks=[Mark("helpButton", 3, "bottom"), Mark("me", 4, "bottom"), Mark("signout", 5, "bottom")]),
            SIDE_W,
        ),
        Spacer(1, 6),
        *[Step(n, text) for n, text in enumerate(legend[5:], start=6)],
        para(f"The top bar also lets you choose how amounts are grouped — {b('Lakh / crore (12,34,567)')} or "
             f"{b('Million (1,234,567)')} — and a light or dark {b('Theme')}."),
        tip("lists can be sorted: click a column heading. In a long list, the ↑ and ↓ keys move between the "
            "lines and Enter opens one."),
    ]


def ch_first_engagement() -> list[Flowable]:
    statuses = [
        ("Draft Setup", "Created; no workbook yet."),
        ("Data Received", "A workbook has been uploaded."),
        ("Checked", "The checks have run."),
        ("Fix Planned, Fix Approved", "A fix plan has been proposed, then approved by a second person."),
        ("Fixed Verified", "The fixes are applied to a new version and verified."),
        ("Review", f"Ready for the partner’s review ({b('Move to review')} at the top right)."),
        ("Draft Released To Client", "A draft PDF has been released to the client."),
        ("Mgmt Confirmed", "The client has confirmed the management representations."),
        ("Final Locked, Archived", "Final and locked: nothing can change any more."),
    ]
    return [
        *chapter(3, "Your first client and engagement",
                 "FS-Platform organises the work the way you do: a client (a company or a group you work for), its "
                 "entities (each company that reports), and an engagement for each set of statements — for "
                 "example the FY 2025-26 annual accounts. Partners, Managers and administrators create them."),
        Heading("Create the client", 6),
        Step(1, f"In the menu on the left, click {b('Clients & entities')}."),
        Step(2, f"Click {b('New client')} at the top right."),
        shot("clients", "The list of clients, still empty.", FULL, area(0, 0, 1366, 240), pad=0,
             marks=[Mark("nav", 1, "right"), Mark("newClient", 2, "left")]),
        side_by_side(
            [
                Step(3, f"Type the client’s {b('Name')}. {b('PAN')}, {b('CIN')}, {b('Group')} and "
                        f"{b('Office')} are optional; leave {b('Data classification')} on {i('confidential')}."),
                Step(4, f"Click {b('Create')}. The client’s page opens."),
                know("PAN and CIN are stored encrypted and shown masked."),
            ],
            shot("new-client", "A new client.", SIDE_W, "dialog", pad=0,
                 marks=[Mark("name", 3, "right"), Mark("create", 4, "right")]),
            SIDE_W,
        ),
        Heading("Add the entity"),
        side_by_side(
            [
                Step(5, f"On the client’s page, click {b('Add entity')} (top right)."),
                Step(6, f"Type the company’s full legal {b('Name')}. The currencies (INR) and the country (IN) "
                        f"are filled in; change them only for a foreign company. Click {b('Add')}."),
                tip(f"for a group, add each company as an entity and choose its {b('Parent entity')}: the "
                    "client’s page then shows the group structure."),
            ],
            shot("add-entity", "A new entity.", SIDE_W, "dialog", pad=0,
                 marks=[Mark("name", 6, "right"), Mark("add", 6, "right")]),
            SIDE_W,
        ),
        Heading("Create the engagement"),
        side_by_side(
            [
                Step(7, f"Click {b('New engagement')}, next to {b('Add entity')}. It stays greyed out until the "
                        "client has an entity."),
                Step(8, f"Check the {b('Entity')}, type a {b('Name')} — for example "
                        f"{i('FY 2025-26 statutory accounts')} — and choose the {b('Type')} ({i('Annual')}, "
                        f"{i('Interim')} or {i('SPFS (quarterly)')}), the {b('Period end')} and, if you like, a "
                        f"{b('Due date')}."),
                Step(9, f"Click {b('Create')}. The engagement opens."),
            ],
            shot("new-engagement", "A new engagement.", SIDE_W, "dialog", pad=0,
                 marks=[Mark("name", 8, "right"), Mark("create", 9, "right")]),
            SIDE_W,
        ),
        Heading("The engagement page"),
        shot("workspace-new", "A new engagement: the status bar and the tabs.", FULL, area(232, 51.5, 1366, 330),
             pad=0, marks=[Mark("rail"), Mark("tabs")]),
        KeepTogether([
            para("Everything about one set of statements is on this page. The tabs are the stages of the work: "
                 f"{b('Files')}, {b('Checks')}, {b('Fix plan')}, {b('PDF & release')}, {b('Review notes')} and "
                 "more. Under the name, a bar shows how far the engagement has come, for example "
                 f"{i('Step 1 of 11: Draft Setup')}. FS-Platform moves it along as you work:"),
            grid(statuses, 150),
        ]),
        Spacer(1, 8),
        tip(f"on a smaller screen the last tabs ({b('Audit timeline')}, {b('Settings')}) may be cut off on the "
            "right: scroll the row of tabs sideways."),
    ]


def ch_upload() -> list[Flowable]:
    return [
        *chapter(4, "Uploading the statements workbook",
                 "FS-Platform works on the Excel workbook in which you prepare the financial statements — the "
                 "Balance Sheet, the Statement of Profit and Loss, the cash flow statement, the statement of "
                 "changes in equity and the notes, with the previous year alongside."),
        Step(1, f"Open the engagement and click the {b('Files')} tab."),
        Step(2, f"Leave {b('File type')} on {i('Statements workbook (.xlsx / .xlsm)')}."),
        Step(3, f"Drag the workbook from its folder onto the dashed box, or click {b('Choose File')} and pick it."),
        shot("files-empty", "The Files tab of a new engagement.", FULL, area(232, 190, 1366, 500), pad=0,
             marks=[Mark("kind", 2, "left"), Mark("choose", 3, "left")]),
        Step(4, f"FS-Platform stores the file as {i('version 1')} and lists it under {b('Files')}. Click "
                f"{b('run checks on it now')} (chapter 5)."),
        shot("files-uploaded", "The workbook is stored as version 1.", FULL, area(232, 480, 1366, 792), pad=0,
             marks=[Mark("runNow", 4, "right")]),
        know("your file is never changed. Every upload, and every set of fixes, becomes a new version; earlier "
             "versions stay as they were. Click a file in the list to see its versions, to download one, or to "
             "compare two."),
        normal("workbooks with links to other files are refused, and macros in .xlsm files are stored but never "
               "run. Remove the links in Excel, save, and upload again."),
        tip("when you correct the workbook in Excel later, upload it here again: it becomes the next version. "
            "Once the statements have been fixed and verified, FS-Platform checks every new version by itself."),
    ]


def finding_example() -> str:
    """The finding on the picture, in words (from the values the capture read off the screen)."""
    facts = screens().shot("finding").get("facts") or {}
    caption, printed, expected = facts.get("caption"), facts.get("printed"), facts.get("expected")
    if not (caption and printed and expected):
        return "Here, a printed total does not equal the sum of its printed lines."
    what = f"the total of {caption[6:].lower()}" if caption.lower().startswith("total ") else f"“{caption}”"
    printed, expected = html.escape(printed, quote=False), html.escape(expected, quote=False)
    return f"Here, {html.escape(what, quote=False)} is printed as {printed}, but its printed lines add up to {expected}."


def ch_checks() -> list[Flowable]:
    severities = [
        ("High", "The printed statements are wrong or do not agree: a total that does not add up, a Balance "
                 "Sheet that does not tally, a figure that differs from its note, a wrong DIN. A final PDF needs "
                 "all of them resolved."),
        ("Medium", "Probably wrong, or a matter of judgement: a ratio, a period label, the note numbering."),
        ("Low", "A small presentation point, such as the units line or a number format."),
        ("Info", "For information only."),
    ]
    return [
        *chapter(5, "Running the checks and reading the results",
                 "The checks read the workbook the way a careful reviewer would: do the totals add up as printed, "
                 "does the Balance Sheet tally, do the notes agree with the statements and with each other, are "
                 "the previous year, the signatories and the going-concern note right."),
        Step(1, f"On the {b('Checks')} tab, click {b('Run checks')}. A bar shows the progress; it usually "
                "takes less than a minute."),
        Step(2, "The tiles count the findings that are still open, by severity."),
        Step(3, f"Each line of {b('Findings')} is one problem: its number and rule, the {b('Severity')}, "
                f"{b('Where')} it is — {i('PL!C14')} is cell C14 of the sheet PL — and its caption. Narrow the "
                f"list with the boxes above it: tick {b('High')}, pick a sheet, or search."),
        shot("checks", "The results of the checks.", FULL, area(232, 262, 1366, 800), pad=0,
             marks=[Mark("run", 1, "right"), Mark("stats", 2, "left", frame=False), Mark("filters", 3, "right")]),
        grid(severities, 70, head=("Severity", "What it means")),
        Spacer(1, 6),
        Heading("Look at one finding"),
        side_by_side(
            [
                Step(4, "Click a finding. On the right you see what is wrong, the printed and the expected "
                        "figure, the difference, the evidence (the figures that were added up) and a "
                        f"{b('Suggested fix')}."),
                para(finding_example()),
                para(f"{b('Fix kind')} says what happens next. {i('Auto')}: the fix plan corrects it (chapter 6). "
                     f"{i('Approval')}: the fix plan proposes it and a second person ticks it. {i('Manual')}: "
                     "correct it in Excel and upload the workbook again."),
            ],
            shot("finding", "The details of a finding.", SIDE_W, area(921.5, 60, 1346, 600), pad=0,
                 marks=[Mark("title", 4, frame=False)]),
            SIDE_W,
        ),
        side_by_side(
            [
                Step(5, f"Further down the details, record what you decided: choose a {b('Status')} — "
                        f"{i('Open')}, {i('Accepted')}, {i('Resolved')}, or {i('Not an issue')}, which needs a "
                        f"reason — add a {b('Comment')} if you like, and click {b('Save')}. {b('Assign to me')} "
                        "puts the finding on your list."),
                Step(6, f"To ask a colleague, type your question under {b('Raise a review note on this finding')} "
                        f"and click {b('Raise note')}."),
                tip(f"{b('Exceptions register')} and {b('HTML report')}, above the list, download all the "
                    "findings for your working papers."),
            ],
            shot("finding-actions", "Deciding what to do with a finding.", SIDE_W, area(921.5, 60, 1346, 450),
                 pad=0, marks=[Mark("status", 5), Mark("save", 5, "top"), Mark("note", 6)]),
            SIDE_W,
        ),
    ]


def ch_fix_plan() -> list[Flowable]:
    return [
        *chapter(6, "The fix plan: two people, one change",
                 "FS-Platform can correct many findings itself: a total rewritten so that it adds the printed "
                 "lines, a typed figure (a “plug”) taken out of a formula, a missing total added. These "
                 "corrections are collected in a fix plan. One person proposes it and a second person approves "
                 "it: whoever proposes a plan can never approve it, not even a Partner."),
        Heading("Meera proposes the plan", 6),
        Step(1, f"Open the engagement, click the {b('Fix plan')} tab, then {b('Propose fix plan')}. FS-Platform "
                "works out the corrections; it takes about a minute."),
        Step(2, "The plan lists every correction: the cell, the formula or text before (struck through) and "
                "after, and the reason. Findings it cannot correct stay open on the Checks tab: correct those in "
                "Excel."),
        shot("fix-proposed", "Meera’s view of the plan she proposed.", FULL,
             around("fix-proposed", "approve", "notice"), pad=(37, 68, 18, 16),
             marks=[Mark("propose", 1, borrow="fix-propose"), Mark("notice"), Mark("approve")]),
        normal(f"Meera sees {i('You proposed this plan: a different person must approve it')}, and "
               f"{b('Approve plan')} stays greyed out for her."),
        Heading("Asha approves and applies it"),
        Step(3, f"Asha, the partner, opens the same engagement and its {b('Fix plan')} tab. Corrections with a "
                f"tick box in the {b('Approve')} column need her tick; those marked {i('auto')} are included "
                "anyway."),
        Step(4, f"She clicks {b('Approve plan')}. If she disagrees, {b('Reject')} with a reason sends the plan "
                "back."),
        shot("fix-approve", "Asha’s view: the same plan, ready to approve.", 410.0, area(232, 58, 1366, 600), pad=0,
             marks=[Mark("approve", 4, "bottom"), Mark("pick", 3)]),
        Step(5, f"She clicks {b('Apply and verify')}. FS-Platform writes the corrections into a new version of "
                f"the workbook and checks it again. {i('Verification passed')} means that only the planned cells "
                "changed and the totals now add up."),
        shot("fix-verified", "The corrected version passed verification.", FULL, "result", pad=8),
        Step(6, f"Back on the {b('Checks')} tab, click {b('Run checks')} once more. The corrected version is then "
                "checked too, and the list shows only what is left to correct in Excel."),
        know(f"the original workbook is never changed. The corrected one is the next version on the "
             f"{b('Files')} tab: download it from there to carry on in Excel."),
    ]


def ch_pdf() -> list[Flowable]:
    final: list[Flowable] = []
    if screens().has("pdf-final"):
        final += [
            Step(6, f"Back on {b('PDF & release')}, check that both {b('FINAL PDF gates')} show ✓, then click "
                    f"{b('Render FINAL')}."),
            Step(7, f"After a minute or two the {b('Reconciliation')} box says {i('final')} and "
                    f"{i('0 misses — PDF matches the workbook')}. Click {b('Download (watermarked)')} to keep "
                    "a copy."),
            shot("pdf-final", "A final PDF, checked against the workbook.", FULL, area(232, 262, 1366, 532), pad=0,
                 marks=[Mark("final", 6, "bottom", borrow="pdf-final-ready"), Mark("mode", 7, "right"),
                        Mark("matches")]),
            side_by_side(
                [
                    para("The final PDF has no big DRAFT marking across its pages."),
                    normal("until the engagement is locked, the copies you view or download are still stamped "
                           f"lightly with {i('DRAFT – for review')}, your e-mail address and the time; after "
                           f"locking, with {i('FINAL')}."),
                ],
                shot("pdf-final-watermark", "The middle of the final PDF’s first page.", SIDE_W,
                     strip("pdf-final-watermark", "page", 190, 600), pad=0),
                SIDE_W,
            ),
        ]
    elif screens().has("pdf-final-ready"):  # Render FINAL could not be pictured: show the button only
        final += [
            Step(6, f"Back on {b('PDF & release')}, check that every gate shows ✓, then click {b('Render FINAL')}."),
            shot("pdf-final-ready", "No High finding is open: Render FINAL can be used.", 360.0,
                 around("pdf-final-ready", "final", "gates"), pad=(37, 14, 20, 72),
                 marks=[Mark("final", 6, "bottom")]),
        ]
    return [
        *chapter(7, "PDFs: draft and final",
                 "FS-Platform prints the statements to PDF and then looks for every printed figure of the "
                 "workbook in the PDF, so that a stale or incomplete PDF cannot slip through."),
        Heading("A draft PDF", 6),
        Step(1, f"Open the {b('PDF & release')} tab. Leave {b('Engine')} on {i('Auto')}."),
        Step(2, f"Click {b('Render draft PDF')}. After a minute or two the PDF appears below, under "
                f"{b('Viewer')}. {i('0 misses — PDF matches the workbook')} means that every figure of the "
                "workbook is in the PDF."),
        Step(3, f"To keep a copy, click {b('Download (watermarked)')}."),
        shot("pdf-draft", "A draft PDF, checked against the workbook.", FULL, area(232, 262, 1366, 800), pad=0,
             marks=[Mark("engine", 1), Mark("draft", 2), Mark("matches"), Mark("download", 3)]),
        side_by_side(
            [
                normal(f"drafts say {i('DRAFT – for review')} across every page."),
                para("Every PDF you view or download is also stamped with your e-mail address and the time: "
                     "lightly across each page and at its foot, and in the coloured bar above the viewer. That "
                     "is how FS-Platform keeps track of copies."),
            ],
            shot("pdf-draft-watermark", "The middle of a draft’s first page.", SIDE_W,
                 strip("pdf-draft-watermark", "page", 190, 600), pad=0),
            SIDE_W,
        ),
        Heading("The final PDF"),
        para(f"{b('Render FINAL')} is for the Partner and the Manager. It becomes available once the latest "
             "version of the workbook has been checked, no High finding is open and the signatory checks pass. "
             f"The list {b('FINAL PDF gates')} shows each condition with ✓ or ✗."),
        Step(4, "Correct in Excel what the fix plan could not, and upload the corrected workbook on the "
                f"{b('Files')} tab. FS-Platform checks it again by itself."),
        Step(5, "A High finding that is not a real problem — you have checked it, and the statements are "
                f"right — is closed on the {b('Checks')} tab instead: set its {b('Status')} to "
                f"{i('Not an issue')} and give the reason (chapter 5, step 5). It then no longer blocks the "
                "final PDF, and the reason is kept in the audit trail."),
        *final,
        know(f"when the statements are signed, the Partner closes the engagement with {b('Mark FINAL and lock')} "
             "at the top of the engagement page. FS-Platform first lists anything still missing: review notes "
             "cleared, the disclosure checklist complete, a final PDF, the client’s confirmation of the "
             "management representations. A locked engagement cannot change; unlocking it needs a Partner and "
             "an administrator together."),
    ]


def ch_other_tabs() -> list[Flowable]:
    tabs = [
        ("Review notes", "Questions within the team. A reviewer raises a note on the engagement, a finding, a "
                         "cell or a PDF page; the preparer answers; a Partner or Reviewer clears it. All notes "
                         "must be cleared before the engagement is locked."),
        ("Disclosure checklist", f"The disclosure questions. {b('Auto-answer from latest check')} answers what "
                                 f"FS-Platform can see; answer the rest with {i('Yes')}, {i('No')} or "
                                 f"{i('Not applicable')}. It must be complete before the engagement is locked."),
        ("Audit timeline", f"Everything that happened on the engagement: who did what, and when. Nobody can "
                           f"change or delete an entry. The {b('CSV')} button downloads it for your file."),
        ("Narrative", "Compares the figures quoted in text notes, such as going concern, with the statements: "
                      "green matches, red does not."),
        ("Related parties", "Reconciles each related party between the related-party note, the trial balance "
                            "and the notes to the statements."),
        ("Client", "Items for the client to confirm (management representations) and questions to the client."),
        ("Settings", "The engagement’s name and due date, and settings such as the units (lakhs, crores, "
                     "millions)."),
        ("AI review", "Switched off on this PC: nothing is sent anywhere unless your administrator turns it on."),
    ]
    return [
        *chapter(8, "Other tabs worth knowing",
                 "You will not need every tab every day. This is what each of the others is for."),
        grid(tabs, 118, head=("Tab", "What it is for")),
        Spacer(1, 10),
        shot("review-notes", f"A review note from Asha and Meera’s answer; Asha can now {b('Clear')} it.",
             FULL, area(232, 250, 1366, 560), pad=0, marks=[Mark("clear")]),
        shot("checklist", f"The disclosure checklist after {b('Auto-answer from latest check')}.", FULL,
             area(232, 270, 1366, 800), pad=0, marks=[Mark("auto")]),
    ]


def ch_colleagues() -> list[Flowable]:
    roles = [
        ("Partner", "Everything on the client’s engagements: approves fix plans, makes the final PDF, clears "
                    "review notes and locks the engagement when the statements are signed."),
        ("Manager", "Prepares and reviews: uploads, runs checks, proposes fix plans and approves other people’s, "
                    "makes PDFs and releases drafts to the client."),
        ("Staff / Article", "Prepares: uploads workbooks, runs checks, proposes fix plans and makes draft PDFs. "
                            "A Manager or Partner approves."),
        ("Reviewer (read-only)", "Sees the client’s engagements, runs checks, raises and clears review notes — "
                                 "for example your quality reviewer — but changes nothing in the workbook."),
        ("Firm Admin", "Looks after people, roles, clients and backups. Sees the work on a client only with a "
                       "role on that client too."),
    ]
    return [
        *chapter(9, "Working with colleagues",
                 "FS-Platform runs on this one PC. Colleagues use it here too, each with their own account, and "
                 "sign out when they leave the PC. A new colleague creates an account; then the administrator "
                 "gives them a role on the right clients."),
        Heading("Meera creates her account", 6),
        side_by_side(
            [
                Step(1, f"On the sign-in page, Meera clicks {b('Create an account')}."),
                Step(2, f"She types her {b('Full name')}, her {b('E-mail')} and a {b('Password')}, then the password "
                        f"again under {b('Confirm password')}. It needs 12 characters or more: a short sentence "
                        "works well, like “Blue tea cups at noon” (make up your own). She clicks "
                        f"{b('Create account')}."),
                Step(3, "She is signed in, but the page says she has no access to any clients yet. She tells "
                        "her administrator the e-mail address shown there."),
                normal("a new account sees no client until an administrator gives it a role."),
            ],
            shot("sign-up", "Creating an account.", SIDE_W, "card", pad=14, marks=[Mark("create", 2, "left")]),
            SIDE_W,
        ),
        shot("no-role", "Meera’s first view: no access yet.", FULL, area(232, 51.5, 1366, 330), pad=0,
             marks=[Mark("notice", 3, "left", frame=False)]),
        Heading("Asha gives Meera a role"),
        Step(4, f"Asha clicks {b('Administration')} in the menu. Under {b('Users')}, a new colleague shows "
                f"{b('No role yet')}. She clicks {b('Give a role')} on Meera’s line."),
        shot("users-no-role", "A colleague without a role.", FULL, area(232, 190, 1366, 460), pad=0,
             marks=[Mark("badge"), Mark("give", 4, "right")]),
        Step(5, f"Under {b('Assign a role')}, the {b('User')} is already chosen. She chooses the {b('Role')} and "
                f"the {b('Client')}, then clicks {b('Assign')}."),
        shot("assign", "Meera becomes Manager of Corvus Data Services.", FULL, area(232, 190, 1366, 350), pad=0,
             marks=[Mark("role"), Mark("client"), Mark("assign", 5, "right")]),
        side_by_side(
            [
                Step(6, f"FS-Platform asks Asha to confirm it’s her: a short {b('Reason')}, which is kept in the "
                        f"audit trail, and, if asked, {b('Your password')}. She clicks {b('Confirm')}."),
                Step(7, f"Meera clicks {b('Check again')}. Her Dashboard now lists the client’s engagements, "
                        "and she can start work."),
            ],
            shot("confirm", "Confirm it’s you (password hidden).", SIDE_W, "dialog", pad=0,
                 marks=[Mark("reason", 6, "right"), Mark("password"), Mark("confirm", 6, "right")]),
            SIDE_W,
        ),
        KeepTogether([  # a heading does not stay with a KeepTogether that follows it: put it inside
            Heading("The roles in plain words"),
            para("Roles are given client by client: Meera can be Manager on one client and Staff on another. In "
                 f"the list of assignments, {i('Tenant-wide')} means for the whole firm."),
            grid(roles, 118, head=("Role", "What it allows")),
        ]),
        Spacer(1, 8),
        know(f"the administrator who set up FS-Platform with {b('I also work on engagements')} ticked becomes "
             "Partner of every client they create."),
    ]


def ch_admin() -> list[Flowable]:
    return [
        *chapter(10, "Administrator corner",
                 "Administrators look after people, passwords and backups. Each such change asks you to confirm "
                 "it’s you and is recorded in the audit trail."),
        Heading("Users", 6),
        para(f"{menu('Administration', 'Users')} lists everyone with their status and last sign-in. "
             f"{b('Disable')} stops someone from signing in, for example a colleague who has left; their work "
             f"and history stay. {b('Enable')} undoes it. {b('Invite user')} creates an account for someone "
             "else: FS-Platform shows a link on which they choose their own password."),
        Heading("A forgotten password"),
        Step(1, f"On {menu('Administration', 'Users')}, click {b('Reset password')} on the colleague’s line."),
        Step(2, "Confirm it’s you: a reason and, if asked, your password."),
        Step(3, f"FS-Platform shows a one-time link. Click {b('Copy link')} and open it in the browser on this "
                "PC together with your colleague, or give it to them: they choose a new password there."),
        shot("reset-link", "A one-time link to choose a new password (link hidden).", FULL,
             around("reset-link", "link"), pad=(0, 6, 0, 6), marks=[Mark("copy", 3, "right")]),
        normal("there is no e-mail: FS-Platform runs on this PC only, so the link is shown on the screen. It "
               f"works once, and only until the time shown after {i('valid until')}."),
        Heading("Backups"),
        para("FS-Platform backs itself up every day, on this PC, and keeps the last 14 backups. Keep a second "
             "copy somewhere else as well:"),
        Step(4, f"Open {menu('Administration', 'Backups & restore')}. In {b('Backup copy folder')}, type a folder "
                "on another drive, a USB disk or a network folder, for example "
                f"{i('D:' + chr(92) + 'FS-Platform backups')}."),
        Step(5, f"Click {b('Save')}. From then on every backup is also copied there. The {b('Backup log')} "
                "below lists every backup and whether it worked."),
        shot("backups", "The second backup folder and the backup log (locations hidden).", FULL,
             around("backups", "folder", "log"), pad=(22, 44, 22, 12),
             marks=[Mark("folder", 4, "left"), Mark("save", 5, "right")]),
        para("To back up right now, right-click the FS-Platform icon next to the clock and choose "
             f"{b('Back up now')}."),
        know(f"to restore a backup, use {b('FS-Platform Recovery')} in the Start menu; it needs the PC’s "
             "administrator rights. If in doubt, call for help first."),
        Heading("Confirm it’s you"),
        para("Administrative changes — giving or removing a role, resetting a password, disabling an account, "
             f"changing the firm or backup settings — open a small window that asks for a {b('Reason')}, kept "
             f"in the audit trail, and {b('Your password')} if you last confirmed more than 5 minutes ago."),
        normal("five wrong passwords in this window lock your account for 15 minutes and sign you out."),
    ]


def ch_faq(contact: str | None) -> list[Flowable]:
    def qa(question: str, *answers: Flowable | str) -> list[Flowable]:
        return [para(question, QUESTION), *[para(a) if isinstance(a, str) else a for a in answers]]

    return [
        *chapter(11, "Troubleshooting and questions", "Most questions have a short answer."),
        para("I typed a wrong password several times and now I cannot sign in.", QUESTION),
        side_by_side(
            [
                para("After five wrong passwords FS-Platform locks the account for 15 minutes, and the sign-in "
                     f"page says the account is {i('temporarily locked')}. Wait 15 minutes and try again. Five "
                     f"wrong passwords in the {i('Confirm it’s you')} window lock it too, and sign you out."),
                para("Forgotten the password? Ask your administrator to reset it (chapter 10)."),
            ],
            shot("locked", "After too many wrong passwords.", SIDE_W, around("locked", "message", "signin"),
                 pad=(18, 14, 18, 12), marks=[Mark("message")]),
            SIDE_W,
        ),
        *qa("I closed the browser by mistake.",
            f"Nothing is lost: FS-Platform keeps running in the background. Double-click {b('FS-Platform')} on "
            "the desktop again, or double-click its icon next to the clock (right-click it for "
            f"{b('Open FS-Platform')}). Sign in again if asked."),
        *qa("The browser says the page cannot be reached.",
            "FS-Platform is not running, for example after someone chose "
            f"{b('Quit FS-Platform')} on its icon next to the clock. Double-click {b('FS-Platform')} on the "
            "desktop and wait a few seconds."),
        *qa("Where is our data?",
            "Only on this PC, in C:\\ProgramData\\FSPlatform: the database, the workbooks and PDFs, the backups "
            "and the log files. Nothing is sent over the internet. Right-click the icon next to the clock and "
            f"choose {b('Open data folder')} to see it, but do not move, rename or change anything there."),
        *qa("A colleague signs in to Windows with a different account on this PC.",
            f"The PC’s administrator allows that account once: {b('FS-Platform Recovery')} in the Start menu, "
            f"then {i('Allow another Windows account to use FS-Platform')}."),
        *qa("Our only administrator has forgotten their password.",
            f"Use {b('FS-Platform Recovery')} in the Start menu (it needs the PC’s administrator rights): "
            f"{i('Reset an administrator’s password')} shows a one-time link."),
        *qa("My workbook was refused.",
            "FS-Platform takes .xlsx and .xlsm workbooks. A workbook with links to other files is refused: "
            "remove the links in Excel, save, and upload it again."),
        *qa("Who do I call?",
            f"Before you call, the administrator can save a file for support with {b('FS-Platform Recovery')} "
            f"→ {i('Save diagnostics for support')}."),
        Spacer(1, 6),
        HelpBox(contact),
    ]


def story(version: str, contact: str | None) -> list[Flowable]:
    return [
        *cover(version),
        *contents(),
        PageBreak(),
        *ch_opening(),
        *ch_tour(),
        *ch_first_engagement(),
        *ch_upload(),
        *ch_checks(),
        *ch_fix_plan(),
        *ch_pdf(),
        *ch_other_tabs(),
        *ch_colleagues(),
        *ch_admin(),
        *ch_faq(contact),
    ]


# ---------------------------------------------------------------------------------------------- checks and main


def jargon(pdf: Path) -> list[str]:
    """Banned words in the finished PDF's text (whole words, any case), as "page N: word"."""
    pattern = re.compile(r"\b(" + "|".join(re.escape(w) for w in BANNED) + r")\b", re.IGNORECASE)
    return [
        f"page {n}: {match.group(0)!r}"
        for n, page in enumerate(PdfReader(str(pdf)).pages, start=1)
        for match in pattern.finditer(page.extract_text() or "")
    ]


def contact_markup(contact: str) -> str:
    """Escape the contact line; keep phone-number digit groups together and "·" at the end of a line."""
    text = html.escape(" ".join(contact.split()), quote=False)
    text = re.sub(r"(?<=[\d+]) (?=\d)", "&nbsp;", text)
    return text.replace(" · ", "&nbsp;· ")


def default_version() -> str:
    try:
        source = VERSION_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"cannot read {VERSION_FILE}: {exc}; pass --version") from exc
    match = re.search(r"""^__version__\s*=\s*["']([^"']+)["']""", source, re.MULTILINE)
    if not match:
        raise SystemExit(f"no __version__ in {VERSION_FILE}; pass --version")
    return match.group(1)


def build(out: Path, screens_dir: Path, version: str, contact: str | None) -> int:
    """Write the guide; return its number of pages."""
    global SCREENS
    register_fonts()
    WARNINGS.clear()
    SCREENS = Screens(screens_dir, screens_dir.parent / "pdf-cache")
    out.parent.mkdir(parents=True, exist_ok=True)
    markup = contact_markup(contact) if contact else None
    doc = GuideDoc(str(out), version, markup)
    doc.multiBuild(story(version, markup))
    screens().prune()
    return len(PdfReader(str(out)).pages)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the illustrated FS-Platform user guide (A4 PDF).")
    parser.add_argument("--screens", type=Path, default=DEFAULT_SCREENS,
                        help="folder with the captured PNG files and manifest.json (default: %(default)s)")
    parser.add_argument("--version",
                        help="version on the cover and in the footer (default: __version__ in "
                             "fsoffline/src/fsoffline/__init__.py)")
    parser.add_argument("--contact",
                        help='who to ask for help, e.g. "Priya Shah · +91 98200 00000 · priya@example.com" '
                             "(default: the person who sent you FS-Platform)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output PDF (default: %(default)s)")
    args = parser.parse_args(argv)

    version = args.version or default_version()
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,31}", version):
        parser.error(f"unexpected version {version!r}")
    contact = (args.contact or "").strip() or None
    out = args.out.resolve()
    pages = build(out, args.screens.resolve(), version, contact)
    print(f"wrote {out} ({pages} pages, version {version}, {len(screens().used)} pictures)")
    captured = screens().manifest.get("app_version")
    if captured and captured != version:
        print(f"warning: the pictures show FS-Platform {captured}, the guide says {version}: capture them again",
              file=sys.stderr)
    if out.is_relative_to(REPO) and not out.is_relative_to(REPO / "dist"):
        print("warning: the PDF is inside the repository but outside dist/: never commit it", file=sys.stderr)
    for note in screens().manifest.get("notes", []):
        print(f"warning: while capturing: {note}", file=sys.stderr)
    for warning in sorted(WARNINGS):
        print(f"warning: {warning}", file=sys.stderr)
    problems = jargon(out)
    for problem in problems:
        print(f"error: a technical word slipped into the text, {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
