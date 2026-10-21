"""Build the one-page client installation guide for the FS-Platform desktop edition (A4 PDF).

The guide is for a non-technical person who installs FS-Platform on a Windows PC, on their own or
during a phone or screen-share call. Regenerate it for every release:

    .venv/bin/python docs/client-guides/install_guide.py --contact "Name · phone · e-mail"

The PDF is written to dist/ (git-ignored). Never commit it: the confidentiality scan rejects PDF files
outside fixtures/synthetic.
"""

from __future__ import annotations

import argparse
import html
import math
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph

REPO = Path(__file__).resolve().parents[2]
FONT_DIR = REPO / "fscore" / "src" / "fscore" / "pdf" / "fonts"  # DejaVu: has the → and ₹ glyphs
VERSION_FILE = REPO / "fsoffline" / "src" / "fsoffline" / "__init__.py"
DEFAULT_OUT = REPO / "dist" / "client-guides" / "FS-Platform-Install-Guide.pdf"
SETUP_FILE = "FS-Platform-Setup.exe"  # the fixed download link always serves this name
NO_CONTACT = "the person who sent you FS-Platform"

# One accent colour plus greys. Every text colour has at least 7:1 contrast on white, and the tints
# print as light grey, so the page works on a black-and-white printer.
INK = HexColor("#1F2933")
MUTED = HexColor("#4B5563")
ACCENT = HexColor("#1B5E7A")
TINT = HexColor("#EAF2F6")
NOTE_FILL = HexColor("#F1F6F9")
RULE = HexColor("#A3BBC7")

PAGE_W, PAGE_H = A4
MARGIN_X = 18 * mm
MARGIN_TOP = 17 * mm
MARGIN_BOTTOM = 15 * mm
CONTENT_W = PAGE_W - 2 * MARGIN_X
COLUMN_GAP = 20.0
COLUMN_W = (CONTENT_W - COLUMN_GAP) / 2

REGULAR, BOLD, OBLIQUE, BOLD_OBLIQUE = "Guide", "Guide-Bold", "Guide-Oblique", "Guide-BoldOblique"
CAP_HEIGHT = 0.73  # DejaVu Sans, as a fraction of the font size
DESCENT = 0.24

DISC_R = 11.5  # step-number disc
STEP_INDENT = 2 * DISC_R + 7


# ---------------------------------------------------------------------------------------------- fonts


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
    name: str, size: float, leading: float, *, font: str = REGULAR, color: Color = INK
) -> ParagraphStyle:
    # No hyphenation and no splitting inside words, so file names and button names stay whole.
    return ParagraphStyle(
        name,
        fontName=font,
        fontSize=size,
        leading=leading,
        textColor=color,
        splitLongWords=0,
        hyphenationLang="",
        embeddedHyphenation=0,
    )


INTRO = style("intro", 12, 16, color=MUTED)
BOX_TITLE = style("box-title", 12.5, 16, font=BOLD, color=ACCENT)
BOX_ITEM = style("box-item", 10.5, 14)
STEP = style("step", 11, 15)
NOTE = style("note", 10.5, 14)
REF = style("ref", 10.5, 14)
HELP_TITLE = style("help-title", 13, 17, font=BOLD, color=ACCENT)
HELP = style("help", 11, 15)


# --------------------------------------------------------------------------------------------- layout


@dataclass
class Block:
    """A measured piece of the page; ``draw(canvas, x, top)`` paints it with its top edge at ``top``."""

    height: float
    draw: Callable[[Canvas, float, float], None]


class Column:
    """Blocks stacked top-down with fixed gaps; measured before anything is drawn."""

    def __init__(self) -> None:
        self.items: list[tuple[float, Block]] = []

    def add(self, block: Block, gap: float = 0.0) -> Column:
        self.items.append((gap if self.items else 0.0, block))
        return self

    @property
    def height(self) -> float:
        return sum(gap + block.height for gap, block in self.items)

    def draw(self, c: Canvas, x: float, top: float) -> None:
        y = top
        for gap, block in self.items:
            y -= gap
            block.draw(c, x, y)
            y -= block.height


WARNINGS: list[str] = []


def paragraph(text: str, st: ParagraphStyle, width: float) -> tuple[Paragraph, float]:
    """Wrap ``text`` to ``width``; note a near-empty last line so a wording change cannot slip one in."""
    para = Paragraph(text, st)
    _, height = para.wrap(width, PAGE_H)
    lines = para.blPara.lines
    if len(lines) > 1:
        last = lines[-1]
        extra = last.extraSpace if hasattr(last, "extraSpace") else last[0]
        if width - extra < 0.2 * width:
            plain = html.unescape(re.sub(r"<[^>]+>", "", text))
            WARNINGS.append(f"short last line: …{plain[-40:]!r}")
    return para, height


def para_block(text: str, st: ParagraphStyle, width: float) -> Block:
    para, height = paragraph(text, st, width)
    return Block(height, lambda c, x, top: para.drawOn(c, x, top - height))


def nb(text: str) -> str:
    """Keep a phrase on one line."""
    return text.replace(" ", "&nbsp;")


def bold(text: str) -> str:
    return f"<b>{nb(text)}</b>"


def clicks(*names: str) -> str:
    """Button names in bold joined by arrows, e.g. a menu path."""
    return "&nbsp;→ ".join(bold(name) for name in names)


def heading(text: str, width: float) -> Block:
    """Section heading in the accent colour, with a hairline running to the column's right edge."""
    size = 13.0
    height = size * (CAP_HEIGHT + DESCENT)

    def draw(c: Canvas, x: float, top: float) -> None:
        baseline = top - size * CAP_HEIGHT
        c.setFont(BOLD, size)
        c.setFillColor(ACCENT)
        c.drawString(x, baseline, text)
        start = x + pdfmetrics.stringWidth(text, BOLD, size) + 8
        c.setStrokeColor(RULE)
        c.setLineWidth(0.8)
        y = baseline + size * CAP_HEIGHT / 2
        c.line(start, y, x + width, y)

    return Block(height, draw)


def step(number: int, text: str, width: float, note: str | None = None) -> Block:
    """A numbered step: a filled disc with the number, the instruction, and an optional reassuring note."""
    text_w = width - STEP_INDENT
    body, body_h = paragraph(text, STEP, text_w)
    bar, pad_x, pad_top, pad_bottom, note_gap = 3.0, 9.0, 4.5, 6.0, 6.0
    note_para, note_h = None, 0.0
    if note:
        note_para, inner_h = paragraph(note, NOTE, text_w - bar - 2 * pad_x)
        note_h = pad_top + inner_h + pad_bottom
    height = body_h + (note_gap + note_h if note_para else 0.0)

    def draw(c: Canvas, x: float, top: float) -> None:
        # Centre the disc on the capitals of the first line.
        cy = top - STEP.fontSize + STEP.fontSize * CAP_HEIGHT / 2
        cx = x + DISC_R
        c.setFillColor(ACCENT)
        c.circle(cx, cy, DISC_R, stroke=0, fill=1)
        num_size = 14.0
        c.setFillColor(white)
        c.setFont(BOLD, num_size)
        c.drawCentredString(cx, cy - num_size * CAP_HEIGHT / 2, str(number))
        tx = x + STEP_INDENT
        body.drawOn(c, tx, top - body_h)
        if note_para:
            ny = top - body_h - note_gap
            c.setFillColor(NOTE_FILL)
            c.rect(tx, ny - note_h, text_w, note_h, stroke=0, fill=1)
            c.setFillColor(ACCENT)
            c.rect(tx, ny - note_h, bar, note_h, stroke=0, fill=1)
            note_para.drawOn(c, tx + bar + pad_x, ny - note_h + pad_bottom)

    return Block(height, draw)


def normal(reason: str) -> str:
    return f"<b>This is normal</b> — {reason}"


def topic(label: str, text: str) -> str:
    """A run-in label in the accent colour, for the short reference items."""
    return f'<font color="#{ACCENT.hexval()[2:]}"><b>{label}</b></font> {text}'


# ------------------------------------------------------------------------------------------- content


def header(version: str) -> Block:
    title, title_size = "Installing FS-Platform", 26.0
    # Two small lines at the top right; the second shares the title's baseline.
    labels, label_size = (f"FS-Platform {version}", "Windows 10 and 11"), 10.5
    label_w = max(pdfmetrics.stringWidth(text, BOLD, label_size) for text in labels)
    if pdfmetrics.stringWidth(title, BOLD, title_size) + label_w + 16 > CONTENT_W:
        raise LayoutError("the version does not fit beside the title; use a shorter --version")
    intro, intro_h = paragraph(
        "Follow the steps in order. Each one tells you exactly what to click or type.", INTRO, CONTENT_W
    )
    title_h = title_size * (CAP_HEIGHT + DESCENT)
    gap = 7.0

    def draw(c: Canvas, x: float, top: float) -> None:
        baseline = top - title_size * CAP_HEIGHT
        c.setFillColor(INK)
        c.setFont(BOLD, title_size)
        c.drawString(x, baseline, title)
        c.setFillColor(MUTED)
        right = x + CONTENT_W
        c.setFont(BOLD, label_size)
        c.drawRightString(right, baseline + 13, labels[0])
        c.setFont(REGULAR, label_size)
        c.drawRightString(right, baseline, labels[1])
        intro.drawOn(c, x, top - title_h - gap - intro_h)

    return Block(title_h + gap + intro_h, draw)


def before_you_start() -> Block:
    """The tinted box listing what the PC needs, as a two-by-two checklist."""
    pad_x, pad_top, pad_bottom = 14.0, 10.0, 11.0
    inner_w = CONTENT_W - 2 * pad_x
    col_gap, tick, item_indent = 20.0, 9.0, 16.0
    col_w = (inner_w - col_gap) / 2
    rows = [
        (f"{nb('Windows 10 or 11')} (64-bit)", f"About {nb('3 GB')} of free disk space"),
        (
            "Permission to install programs (an administrator account, or approval from IT)",
            f"The setup file <b>{SETUP_FILE}</b> (about {nb('470 MB')})",
        ),
    ]
    title, title_h = paragraph("Before you start", BOX_TITLE, inner_w)
    cells = [[paragraph(text, BOX_ITEM, col_w - item_indent) for text in row] for row in rows]
    row_heights = [max(h for _, h in row) for row in cells]
    footer, footer_h = paragraph(
        "<b>Not needed:</b> internet after the download, any other program, or an app on your phone.",
        BOX_ITEM,
        inner_w,
    )
    title_gap, row_gap, footer_gap = 5.0, 6.0, 7.0
    height = (
        pad_top
        + title_h
        + title_gap
        + sum(row_heights)
        + row_gap * (len(rows) - 1)
        + footer_gap
        + footer_h
        + pad_bottom
    )

    def draw(c: Canvas, x: float, top: float) -> None:
        c.setFillColor(TINT)
        c.roundRect(x, top - height, CONTENT_W, height, 6, stroke=0, fill=1)
        y = top - pad_top
        title.drawOn(c, x + pad_x, y - title_h)
        y -= title_h + title_gap
        for row, row_h in zip(cells, row_heights, strict=True):
            for i, (para, para_h) in enumerate(row):
                cx = x + pad_x + i * (col_w + col_gap)
                mid = y - BOX_ITEM.fontSize + BOX_ITEM.fontSize * CAP_HEIGHT / 2  # first line's capitals
                c.setFillColor(white)
                c.setStrokeColor(ACCENT)
                c.setLineWidth(0.9)
                c.rect(cx, mid - tick / 2, tick, tick, stroke=1, fill=1)
                para.drawOn(c, cx + item_indent, y - para_h)
            y -= row_h + row_gap
        y += row_gap - footer_gap
        footer.drawOn(c, x + pad_x, y - footer_h)

    return Block(height, draw)


def install_column() -> Column:
    w = COLUMN_W
    gap = 11.0
    return (
        Column()
        .add(heading("Install FS-Platform", w))
        .add(
            step(
                1,
                f"Download <b>{SETUP_FILE}</b> from the link you were sent, or copy it from the USB stick to "
                "your desktop.",
                w,
                note=normal(
                    "if your browser says the file “isn’t commonly downloaded”, choose <b>Keep</b> "
                    f"(in Edge: {clicks('…', 'Keep', 'Show more', 'Keep anyway')})."
                ),
            ),
            gap=12,
        )
        .add(
            step(2, f"Double-click <b>{SETUP_FILE}</b> in your Downloads folder or on the desktop.", w),
            gap=gap,
        )
        .add(
            step(
                3,
                f"If a blue window says “Windows protected your PC”, click {bold('More info')}, "
                f"then {bold('Run anyway')}.",
                w,
                note=normal("FS-Platform is new, so Windows does not know it yet."),
            ),
            gap=gap,
        )
        .add(
            step(
                4,
                "Windows asks “Do you want to allow this app to make changes to your device?” "
                "Click <b>Yes</b>. It may ask for an administrator’s password.",
                w,
                note=normal("Windows asks this for every new program."),
            ),
            gap=gap,
        )
        .add(
            step(
                5,
                "Click <b>Next</b>, <b>Next</b> and <b>Install</b>. It takes a few minutes, as it also unpacks "
                "the part that makes PDFs. Then click <b>Finish</b>.",
                w,
            ),
            gap=gap,
        )
    )


def start_column() -> Column:
    w = COLUMN_W
    gap = 11.0
    return (
        Column()
        .add(heading("First start (only once)", w))
        .add(
            step(
                6,
                "Double-click <b>FS-Platform</b> on your desktop (or open it from the Start menu). "
                "Your browser shows the setup page.",
                w,
            ),
            gap=12,
        )
        .add(
            step(
                7,
                "Type your firm’s name, your name, your e-mail and a password (twice): "
                f"{nb('12 characters')} or more — a short sentence works well, like “Blue tea cups at noon”. "
                "Keep <b>I also work on engagements</b> ticked if you do the work yourself. Click "
                f"{bold('Create the firm')}. You are {nb('the administrator.')}",
                w,
            ),
            gap=gap,
        )
        .add(
            step(8, f"Click {bold('Sign in')}, type your password and click {bold('Sign in')}. Done!", w),
            gap=gap,
        )
        .add(heading("Using FS-Platform", w), gap=16)
        .add(
            para_block(
                topic(
                    "Every day:",
                    "open <b>FS-Platform</b> from the desktop icon; it opens in your browser and keeps "
                    "running in the background (icon next to the clock, or under ^ on Windows 11). Your data "
                    "stays on this PC and is backed up every day.",
                ),
                REF,
                w,
            ),
            gap=9,
        )
        .add(
            para_block(
                topic(
                    "Colleagues on this PC:",
                    f"on the sign-in page they click {bold('Create an account')}. Then you give them a client: "
                    f"{clicks('Administration', 'Assignments')}. Until then they see no clients (that is "
                    "expected). Own Windows sign-in? Add it once: Start menu → "
                    "<b>FS-Platform Recovery</b> → <b>Allow another Windows account</b>.",
                ),
                REF,
                w,
            ),
            gap=8,
        )
        .add(
            para_block(
                topic(
                    "Forgotten password?",
                    f"An administrator resets it in {clicks('Administration', 'Users', 'Reset password')}.",
                ),
                REF,
                w,
            ),
            gap=8,
        )
    )


def need_help(contact: str | None) -> Block:
    pad_x, pad_top, pad_bottom = 14.0, 10.0, 11.0
    label_w = 104.0
    # The contact details get a line of their own, so a phone number or e-mail address is easy to read.
    question = f"Questions? Contact:<br/><b>{contact}</b>" if contact else f"Questions? Contact {NO_CONTACT}."
    text, text_h = paragraph(
        f"{question}<br/>On a call, say which step number you are on.", HELP, CONTENT_W - 2 * pad_x - label_w
    )
    title, title_h = paragraph("Need help?", HELP_TITLE, label_w)
    height = pad_top + max(text_h, title_h) + pad_bottom

    def draw(c: Canvas, x: float, top: float) -> None:
        c.setFillColor(white)
        c.setStrokeColor(ACCENT)
        c.setLineWidth(1.2)
        c.roundRect(x, top - height, CONTENT_W, height, 6, stroke=1, fill=1)
        # Put the label's baseline on the first line of the text.
        baseline = top - pad_top - HELP.fontSize
        title.drawOn(c, x + pad_x, baseline + HELP_TITLE.fontSize - title_h)
        text.drawOn(c, x + pad_x + label_w, top - pad_top - text_h)

    return Block(height, draw)


# ---------------------------------------------------------------------------------------------- page


class LayoutError(RuntimeError):
    pass


def build(out: Path, version: str, contact: str | None) -> float:
    """Draw the guide on one A4 page; return the spare height in pt (negative: spacing was tightened)."""
    register_fonts()
    WARNINGS.clear()
    head, box = header(version), before_you_start()
    left, right = install_column(), start_column()
    help_box = need_help(contact_markup(contact) if contact else None)
    columns_h = max(left.height, right.height)

    # Space between header → box → columns → help box. Spare height is shared out so the help box ends on
    # the bottom margin; if space is short (a long --contact), the gaps shrink towards the minimum first.
    comfortable, minimum = [16.0, 18.0, 16.0], [8.0, 10.0, 8.0]
    room = PAGE_H - MARGIN_TOP - MARGIN_BOTTOM - (head.height + box.height + columns_h + help_box.height)
    if room < sum(minimum):
        over = math.ceil(sum(minimum) - room)
        raise LayoutError(f"the guide is {over} pt too tall for one A4 page; shorten the text or --contact")
    spare = room - sum(comfortable)
    if spare >= 0:
        gaps = [g + spare / len(comfortable) for g in comfortable]
    else:
        share = (room - sum(minimum)) / (sum(comfortable) - sum(minimum))
        gaps = [lo + share * (hi - lo) for lo, hi in zip(minimum, comfortable, strict=True)]

    out.parent.mkdir(parents=True, exist_ok=True)
    # Start on the bundled font, so the PDF does not reference (unembedded) Helvetica at all.
    c = Canvas(str(out), pagesize=A4, pageCompression=1, initialFontName=REGULAR, initialFontSize=11)
    c.setTitle("Installing FS-Platform")
    c.setSubject(f"How to install FS-Platform {version} on a Windows PC")
    c.setAuthor("FS-Platform")
    c.setCreator("docs/client-guides/install_guide.py")

    y = PAGE_H - MARGIN_TOP
    head.draw(c, MARGIN_X, y)
    y -= head.height + gaps[0]
    box.draw(c, MARGIN_X, y)
    y -= box.height + gaps[1]
    left.draw(c, MARGIN_X, y)
    right.draw(c, MARGIN_X + COLUMN_W + COLUMN_GAP, y)
    y -= columns_h + gaps[2]
    help_box.draw(c, MARGIN_X, y)
    c.showPage()
    c.save()
    return spare


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the one-page FS-Platform installation guide (A4 PDF)."
    )
    parser.add_argument(
        "--version",
        help="version printed beside the title (default: __version__ in fsoffline/src/fsoffline/__init__.py)",
    )
    parser.add_argument(
        "--contact",
        help='who to ask for help, e.g. "Priya Shah · +91 98200 00000 · priya@example.com" '
        "(default: the person who sent you FS-Platform)",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output PDF (default: %(default)s)")
    args = parser.parse_args(argv)

    version = args.version or default_version()
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,31}", version):
        parser.error(f"unexpected version {version!r}")
    contact = (args.contact or "").strip() or None
    out = args.out.resolve()
    try:
        spare = build(out, version, contact)
    except LayoutError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    fit = f"{spare:.0f} pt spare height" if spare >= 0 else f"spacing tightened by {-spare:.0f} pt to fit"
    print(f"wrote {out} (one A4 page, version {version}, {fit})")
    if out.is_relative_to(REPO) and not out.is_relative_to(REPO / "dist"):
        WARNINGS.append("the PDF is inside the repository but outside dist/: never commit it")
    for warning in WARNINGS:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
