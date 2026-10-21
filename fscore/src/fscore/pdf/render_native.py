"""Built-in renderer (ReportLab): print areas → PDF, offline, no Excel needed.

Fidelity rules followed (02 §10A): print areas (several areas start new
pages), hidden rows/columns, column widths and row heights, the workbook's
own rendered number text, bold/italic/underline, font colour (white stays
invisible), fills, borders, merges, alignment and indent, wrap (rows without a
custom height grow like Excel's autofit), text overflow into empty cells,
'####' for numbers that do not fit, orientation, fit-to-width / scale,
margins, print titles, manual row breaks, down-then-over page order,
header/footer codes (&P, &N, &A, &F, &D, &T). Draft pages get the
"DRAFT – for review" watermark and the run ID in an overlay footer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas

from fscore.engine.refs import Area
from fscore.io.pdf_reader import OVERLAY_RGB
from fscore.io.styles import Font
from fscore.io.xlsx_reader import Cell, Sheet, Workbook
from fscore.pdf.typefaces import face, register_reportlab_fonts

ENGINE = "native"
ENGINE_VERSION = "fscore-native/1"

PAPER_SIZES: dict[int, tuple[float, float]] = {
    1: (612.0, 792.0),  # Letter
    5: (612.0, 1008.0),  # Legal
    8: (841.89, 1190.55),  # A3
    9: (595.28, 841.89),  # A4
    11: (419.53, 595.28),  # A5
}

PAD = 2.0  # horizontal cell padding (pt)
INDENT_PT = 9.0  # one Excel indent level (pt)
_NOTE_HEAD = re.compile(r"^\s*(?:note\s*(?:no\.?)?\s*)?(\d{1,3})(?:\s*[.:)\-–]|\s+)\s*([A-Z][^\n]{2,80})", re.I)
_NOTE_ONLY = re.compile(r"^\s*note\s*(?:no\.?)?\s*(\d{1,3})\b", re.I)


@dataclass
class PageBox:
    sheet: str
    rows: list[int]
    cols: list[int]
    title_rows: list[int]
    first_of_sheet: bool = False


@dataclass
class SheetPlan:
    sheet: Sheet
    scale: float
    page_w: float
    page_h: float
    margins: dict[str, float]
    col_w: dict[int, float]
    row_h: dict[int, float]
    pages: list[PageBox] = field(default_factory=list)
    note_rows: dict[int, str] = field(default_factory=dict)  # row -> "Note 23 Revenue…"
    title: str = ""


def col_width_pt(width_chars: float) -> float:
    """Excel column width (characters of the default font) → points."""
    if width_chars <= 0:
        return 0.0
    px = int(width_chars * 7 + 5) if width_chars >= 1 else int(width_chars * 12 + 0.5)
    return px * 0.75


def _font_of(wb: Workbook, cell: Cell | None) -> Font:
    return wb.styles.font(cell.style) if cell is not None else wb.styles.fonts[0]


def _hex_to_rgb(h: str | None) -> tuple[float, float, float]:
    if not h:
        return (0.0, 0.0, 0.0)
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)


def _cell_text(wb: Workbook, cell: Cell | None) -> str:
    if cell is None or cell.value is None:
        return ""
    if isinstance(cell.value, str) and not cell.is_error:
        r = wb.render_cell(cell)
        return r.text if r.text else cell.value
    return wb.render_cell(cell).text


def _is_numeric_cell(cell: Cell | None) -> bool:
    return cell is not None and cell.is_number


def _wrap_lines(text: str, width: float, font: str, size: float) -> list[str]:
    out: list[str] = []
    for para in text.split("\n"):
        words = para.split(" ")
        cur = ""
        for w in words:
            cand = w if not cur else cur + " " + w
            if stringWidth(cand, font, size) <= width or not cur:
                cur = cand
                # a single over-long word is broken by characters
                while stringWidth(cur, font, size) > width and len(cur) > 1:
                    cut = len(cur)
                    while cut > 1 and stringWidth(cur[:cut], font, size) > width:
                        cut -= 1
                    out.append(cur[:cut])
                    cur = cur[cut:]
            else:
                out.append(cur)
                cur = w
        out.append(cur)
    return out


def _merge_map(sheet: Sheet) -> tuple[dict[tuple[int, int], Area], set[tuple[int, int]]]:
    anchors: dict[tuple[int, int], Area] = {}
    covered: set[tuple[int, int]] = set()
    for m in sheet.merges:
        anchors[(m.r1, m.c1)] = m
        for r in range(m.r1, m.r2 + 1):
            for c in range(m.c1, m.c2 + 1):
                if (r, c) != (m.r1, m.c1):
                    covered.add((r, c))
    return anchors, covered


def plan_sheet(wb: Workbook, sheet: Sheet) -> SheetPlan:
    """Paginate one sheet: rows down, column chunks over (down-then-over)."""
    sheet.ensure_loaded()
    ps = sheet.page
    pw, ph = PAPER_SIZES.get(ps.paper_size, PAPER_SIZES[9])
    if ps.orientation == "landscape":
        pw, ph = ph, pw
    margins = {k: v * 72.0 for k, v in ps.margins.items()}
    areas = sheet.print_bounds
    col_w: dict[int, float] = {}
    row_h: dict[int, float] = {}
    anchors, covered = _merge_map(sheet)
    for area in areas:
        for c in range(area.c1, area.c2 + 1):
            col_w[c] = 0.0 if sheet.col_hidden(c) else col_width_pt(sheet.col_width(c))
    # rows: stored height, or autofit for wrapped text when Excel would autofit
    base_font = wb.styles.fonts[0]
    for area in areas:
        for r in range(area.r1, area.r2 + 1):
            if sheet.row_hidden(r):
                row_h[r] = 0.0
                continue
            h = sheet.row_heights.get(r)
            custom = h is not None
            need = h if h is not None else sheet.default_row_height
            for c in range(area.c1, area.c2 + 1):
                cell = sheet.cell(r, c)
                if cell is None or cell.value is None or (r, c) in covered:
                    continue
                f = _font_of(wb, cell)
                min_h = f.size * 1.3
                if not custom:
                    need = max(need, min_h)
                xf = wb.styles.xf(cell.style)
                if xf.wrap and isinstance(cell.value, str) and not custom:
                    width = col_w.get(c, 0.0)
                    if (r, c) in anchors:
                        m = anchors[(r, c)]
                        width = sum(col_w.get(cc, 0.0) for cc in range(m.c1, m.c2 + 1))
                    if width > 2 * PAD:
                        lines = _wrap_lines(_cell_text(wb, cell), width - 2 * PAD, face(f.bold, f.italic), f.size)
                        need = max(need, len(lines) * f.size * 1.22 + 2)
            row_h[r] = need
    _ = base_font
    # scale
    content_w = max((sum(col_w.get(c, 0.0) for c in range(a.c1, a.c2 + 1)) for a in areas), default=0.0)
    avail_w = pw - margins["left"] - margins["right"]
    avail_h = ph - margins["top"] - margins["bottom"]
    if ps.fit_to_page:
        scale = 1.0
        if ps.fit_to_width and content_w > 0:
            scale = min(scale, avail_w * ps.fit_to_width / content_w)
        if ps.fit_to_height:
            total_h = max((sum(row_h.get(r, 0.0) for r in range(a.r1, a.r2 + 1)) for a in areas), default=0.0)
            if total_h > 0:
                scale = min(scale, avail_h * ps.fit_to_height / total_h)
        scale = max(scale, 0.10)
    else:
        scale = max(min(ps.scale, 400), 10) / 100.0
    plan = SheetPlan(sheet, scale, pw, ph, margins, col_w, row_h)
    # print titles
    title_rows: list[int] = []
    if sheet.print_titles_rows:
        a, b = sheet.print_titles_rows
        title_rows = [r for r in range(a, b + 1) if not sheet.row_hidden(r)]
        for r in title_rows:
            if r not in row_h:
                row_h[r] = sheet.row_heights.get(r, sheet.default_row_height)
    breaks = set(sheet.row_breaks)
    unscaled_h = avail_h / scale
    unscaled_w = avail_w / scale
    first = True
    for area in areas:
        vis_cols = [c for c in range(area.c1, area.c2 + 1) if col_w.get(c, 0.0) > 0]
        vis_rows = [r for r in range(area.r1, area.r2 + 1) if row_h.get(r, 0.0) > 0]
        # column chunks (only when not fitting the width)
        chunks: list[list[int]] = []
        cur: list[int] = []
        acc = 0.0
        for c in vis_cols:
            w = col_w[c]
            if cur and acc + w > unscaled_w + 0.01:
                chunks.append(cur)
                cur, acc = [], 0.0
            cur.append(c)
            acc += w
        if cur:
            chunks.append(cur)
        # row pages
        row_pages: list[list[int]] = []
        cur_rows: list[int] = []
        acc_h = 0.0
        titles_h = sum(row_h.get(r, 0.0) for r in title_rows)
        for r in vis_rows:
            h = row_h[r]
            limit = unscaled_h - (titles_h if row_pages and title_rows else 0.0)
            if cur_rows and acc_h + h > limit + 0.01:
                row_pages.append(cur_rows)
                cur_rows, acc_h = [], 0.0
            cur_rows.append(r)
            acc_h += h
            if r in breaks:
                row_pages.append(cur_rows)
                cur_rows, acc_h = [], 0.0
        if cur_rows:
            row_pages.append(cur_rows)
        for ci, cols in enumerate(chunks):
            for ri, rows in enumerate(row_pages):
                trows = [t for t in title_rows if t not in rows] if (ri > 0 and title_rows) else []
                plan.pages.append(PageBox(sheet.name, rows, cols, trows, first_of_sheet=first and ci == 0 and ri == 0))
                first = False
    # note headings and title (for bookmarks)
    for area in areas:
        for r in range(area.r1, area.r2 + 1):
            if sheet.row_hidden(r):
                continue
            for c in range(area.c1, min(area.c2, area.c1 + 2) + 1):
                cell = sheet.cell(r, c)
                if cell is None or not isinstance(cell.value, str):
                    continue
                t = cell.value.strip()
                hm = _NOTE_HEAD.match(t) or _NOTE_ONLY.match(t)
                if hm and wb.styles.font(cell.style).bold:
                    plan.note_rows.setdefault(r, f"Note {hm.group(1)}" + (f" {hm.group(2).strip()}" if hm.lastindex and hm.lastindex >= 2 else ""))
                    break
    plan.title = _sheet_title(wb, sheet)
    return plan


def _sheet_title(wb: Workbook, sheet: Sheet) -> str:
    """First bold statement-like heading of the print area, else the sheet name."""
    pat = re.compile(r"balance sheet|profit and loss|cash flow|changes in equity|notes? to|related part|going concern|ratios|accounting policies", re.I)
    for area in sheet.print_bounds[:1]:
        for r in range(area.r1, min(area.r2, area.r1 + 12) + 1):
            for c in range(area.c1, min(area.c2, area.c1 + 3) + 1):
                cell = sheet.cell(r, c)
                if cell is not None and isinstance(cell.value, str) and pat.search(cell.value):
                    return cell.value.strip()[:90]
    return sheet.name


# ---------------------------------------------------------------- header / footer codes


def _hf_sections(code: str) -> dict[str, str]:
    """Split an Excel header/footer string into left/center/right sections."""
    out = {"L": "", "C": "", "R": ""}
    cur = "C"
    i = 0
    buf: list[str] = []
    while i < len(code):
        ch = code[i]
        if ch == "&" and i + 1 < len(code):
            nxt = code[i + 1]
            if nxt in "LCR":
                out[cur] += "".join(buf)
                buf = []
                cur = nxt
                i += 2
                continue
            if nxt == '"':
                j = code.find('"', i + 2)
                i = (j + 1) if j > 0 else len(code)
                continue
            if nxt.isdigit():
                j = i + 1
                while j < len(code) and code[j].isdigit():
                    j += 1
                i = j
                continue
            if nxt == "K":
                i += 8
                continue
            if nxt in "BIUESXY":
                i += 2
                continue
            buf.append("&" + nxt)
            i += 2
            continue
        buf.append(ch)
        i += 1
    out[cur] += "".join(buf)
    return out


def _hf_expand(text: str, page: int, total: int, sheet: str, file: str, now: datetime) -> str:
    return (
        text.replace("&P", str(page)).replace("&N", str(total)).replace("&A", sheet).replace("&F", file)
        .replace("&D", now.strftime("%d-%b-%Y")).replace("&T", now.strftime("%H:%M")).replace("&Z", "").replace("&G", "")
        .replace("&&", "&")
    )


# ---------------------------------------------------------------- drawing


@dataclass
class NativeResult:
    pages: int
    sheet_first_page: dict[str, int]
    bookmarks: list[tuple[int, str, int]]  # (level, title, page)


def _overlay_color(c: rl_canvas.Canvas) -> None:
    c.setFillColorRGB(OVERLAY_RGB[0] / 255, OVERLAY_RGB[1] / 255, OVERLAY_RGB[2] / 255)


def draw_overlay(c: rl_canvas.Canvas, page_w: float, page_h: float, *, page: int, total: int, draft: bool, run_id: str) -> None:
    """Page number / run ID footer and the DRAFT watermark, in the reserved overlay colour."""
    c.saveState()
    _overlay_color(c)
    c.setFont(face(), 7)
    label = f"Page {page} of {total}"
    if draft:
        label = f"DRAFT – for review · Run {run_id or '-'} · " + label
    c.drawRightString(page_w - 24, 14, label)
    if draft:
        c.setFillAlpha(0.13)
        c.setFont(face(True), 64)
        c.translate(page_w / 2, page_h / 2)
        c.rotate(35)
        c.drawCentredString(0, -20, "DRAFT – for review")
    c.restoreState()


def _border_width(style: str | None) -> float:
    return {"hair": 0.25, "thin": 0.5, "medium": 1.0, "thick": 1.5, "double": 0.5, "dashed": 0.5, "dotted": 0.5,
            "mediumDashed": 1.0, "dashDot": 0.5, "mediumDashDot": 1.0, "dashDotDot": 0.5, "slantDashDot": 1.0}.get(style or "", 0.5)


def _draw_border(c: rl_canvas.Canvas, style: str, x1: float, y1: float, x2: float, y2: float, horizontal: bool) -> None:
    c.setLineWidth(_border_width(style))
    if style in ("dashed", "mediumDashed"):
        c.setDash(3, 2)
    elif style in ("dotted", "hair"):
        c.setDash(1, 1)
    else:
        c.setDash()
    if style == "double":
        off = 0.9
        if horizontal:
            c.line(x1, y1 + off, x2, y2 + off)
            c.line(x1, y1 - off, x2, y2 - off)
        else:
            c.line(x1 - off, y1, x2 - off, y2)
            c.line(x1 + off, y1, x2 + off, y2)
    else:
        c.line(x1, y1, x2, y2)
    c.setDash()


def _draw_page(c: rl_canvas.Canvas, wb: Workbook, plan: SheetPlan, box: PageBox) -> None:
    sheet = plan.sheet
    s = plan.scale
    m = plan.margins
    anchors, covered = _merge_map(sheet)
    rows = box.title_rows + box.rows
    cols = box.cols
    x_of: dict[int, float] = {}
    x = 0.0
    for col in cols:
        x_of[col] = x
        x += plan.col_w[col]
    content_w = x
    area_right: float | None = content_w  # right edge of the printed columns (overflowing text is clipped here)
    y_of: dict[int, float] = {}
    y = 0.0
    for r in rows:
        y_of[r] = y
        y += plan.row_h[r]
    c.saveState()
    left = m["left"]
    if sheet.page.center_horizontally:
        left += max(0.0, (plan.page_w - m["left"] - m["right"] - content_w * s) / 2)
    top = plan.page_h - m["top"]
    # local coordinates: origin at the top-left of the print block, y grows downward
    c.translate(left, top)
    c.scale(s, s)

    def rect_of(r: int, col: int) -> tuple[float, float, float, float]:
        x0 = x_of[col]
        w = plan.col_w[col]
        h = plan.row_h[r]
        if (r, col) in anchors:
            mg = anchors[(r, col)]
            w = sum(plan.col_w.get(cc, 0.0) for cc in range(mg.c1, mg.c2 + 1) if cc in x_of)
            h = sum(plan.row_h.get(rr, 0.0) for rr in range(mg.r1, mg.r2 + 1) if rr in y_of)
        return x0, y_of[r], w, h

    # pass 1: fills and gridlines
    for r in rows:
        for col in cols:
            if (r, col) in covered:
                continue
            cell = sheet.cell(r, col)
            if cell is None:
                continue
            fill = wb.styles.fill(cell.style)
            if fill.color and fill.color.upper() != "FFFFFF":
                x0, y0, w, h = rect_of(r, col)
                c.setFillColorRGB(*_hex_to_rgb(fill.color))
                c.rect(x0, -(y0 + h), w, h, stroke=0, fill=1)
    if sheet.page.grid_lines:
        c.setStrokeColorRGB(0.75, 0.75, 0.75)
        c.setLineWidth(0.25)
        for r in rows:
            c.line(0, -y_of[r], content_w, -y_of[r])
        c.line(0, -y, content_w, -y)
        for col in cols:
            c.line(x_of[col], 0, x_of[col], -y)
        c.line(content_w, 0, content_w, -y)
    # pass 2: text
    occupied = {(r, col) for r in rows for col in cols if (sheet.cell(r, col) is not None and sheet.cell(r, col).value not in (None, ""))}  # type: ignore[union-attr]
    for r in rows:
        for idx, col in enumerate(cols):
            if (r, col) in covered:
                continue
            cell = sheet.cell(r, col)
            if cell is None or cell.value is None:
                continue
            text = _cell_text(wb, cell)
            if text == "":
                continue
            f = _font_of(wb, cell)
            fname = face(f.bold, f.italic)
            size = f.size
            xf = wb.styles.xf(cell.style)
            numeric = _is_numeric_cell(cell)
            align = xf.horizontal or ("right" if numeric else ("center" if isinstance(cell.value, bool) or cell.is_error else "left"))
            x0, y0, w, h = rect_of(r, col)
            c.setFillColorRGB(*_hex_to_rgb(f.color))
            if numeric:
                body = text.lstrip()
                tw = stringWidth(body, fname, size)
                if tw > w - PAD + 0.5 and w > 0:
                    n = max(1, int((w - PAD) / max(stringWidth("#", fname, size), 0.1)))
                    body = "#" * n
                    tw = stringWidth(body, fname, size)
                baseline = y0 + h - size * 0.25
                if align == "left":
                    tx = x0 + PAD
                elif align == "center":
                    tx = x0 + (w - tw) / 2
                else:
                    tx = x0 + w - PAD - tw
                c.setFont(fname, size)
                c.drawString(tx, -baseline, body)
                continue
            indent = xf.indent * INDENT_PT
            if xf.wrap:
                lines = _wrap_lines(text, max(w - 2 * PAD - indent, 4), fname, size)
                c.setFont(fname, size)
                lh = size * 1.22
                ty = y0 + size * 1.0
                for ln in lines:
                    if ty > y0 + h + 0.5:
                        break
                    lw = stringWidth(ln, fname, size)
                    if align in ("center", "centerContinuous"):
                        tx = x0 + (w - lw) / 2
                    elif align == "right":
                        tx = x0 + w - PAD - lw
                    else:
                        tx = x0 + PAD + indent
                    c.drawString(tx, -ty, ln)
                    ty += lh
                continue
            # single line, overflowing into empty neighbours like Excel
            body = text.replace("\n", " ")
            tw = stringWidth(body, fname, size)
            avail = w
            if align in ("left", "general", "justify", "fill", "distributed") and tw + PAD + indent > w:
                for nxt in cols[idx + 1 :]:
                    if (r, nxt) in occupied or (r, nxt) in covered:
                        break
                    avail += plan.col_w[nxt]
                    if tw + PAD + indent <= avail:
                        break
            baseline = y0 + h - size * 0.25
            c.setFont(fname, size)
            if align in ("center", "centerContinuous"):
                tx = x0 + (w - tw) / 2
            elif align == "right":
                tx = x0 + w - PAD - tw
            else:
                tx = x0 + PAD + indent
            # like Excel, nothing prints beyond the print area: clip overflowing text at its right edge
            right_edge = area_right if area_right is not None else tx + tw
            if tx + tw > right_edge:
                while body and tx + stringWidth(body, fname, size) > right_edge - PAD:
                    body = body[:-1]
                tw = stringWidth(body, fname, size)
            c.drawString(tx, -baseline, body)
            if f.underline:
                c.setLineWidth(0.5)
                c.setStrokeColorRGB(*_hex_to_rgb(f.color))
                c.line(tx, -(baseline + 1.2), tx + tw, -(baseline + 1.2))
    # pass 3: borders
    c.setStrokeColorRGB(0, 0, 0)
    for r in rows:
        for col in cols:
            if (r, col) in covered:
                continue
            cell = sheet.cell(r, col)
            if cell is None:
                continue
            b = wb.styles.border(cell.style)
            if not (b.top or b.bottom or b.left or b.right):
                continue
            x0, y0, w, h = rect_of(r, col)
            if b.top:
                _draw_border(c, b.top, x0, -y0, x0 + w, -y0, True)
            if b.bottom:
                _draw_border(c, b.bottom, x0, -(y0 + h), x0 + w, -(y0 + h), True)
            if b.left:
                _draw_border(c, b.left, x0, -y0, x0, -(y0 + h), False)
            if b.right:
                _draw_border(c, b.right, x0 + w, -y0, x0 + w, -(y0 + h), False)
    c.restoreState()


def _draw_header_footer(c: rl_canvas.Canvas, plan: SheetPlan, page: int, total: int, file_name: str, now: datetime) -> None:
    ps = plan.sheet.page
    for code, y in ((ps.header, plan.page_h - plan.margins.get("header", 21.6) - 6), (ps.footer, plan.margins.get("footer", 21.6))):
        if not code:
            continue
        secs = _hf_sections(code)
        c.setFont(face(), 8)
        c.setFillColorRGB(0, 0, 0)
        for key, txt in secs.items():
            t = _hf_expand(txt, page, total, plan.sheet.name, file_name, now).strip()
            if not t:
                continue
            if key == "L":
                c.drawString(plan.margins["left"], y, t)
            elif key == "R":
                c.drawRightString(plan.page_w - plan.margins["right"], y, t)
            else:
                c.drawCentredString(plan.page_w / 2, y, t)


def render_native(
    xlsx_path: Path,
    pdf_path: Path,
    sheets: list[str],
    *,
    draft: bool = True,
    run_id: str = "",
    title: str = "",
    author: str = "FS-Platform",
) -> NativeResult:
    """Render the given print sheets (in order) to ``pdf_path``."""
    register_reportlab_fonts()
    now = datetime.now()
    with Workbook.open(xlsx_path) as wb:
        plans = []
        for name in sheets:
            sh = wb.get_sheet(name)
            if sh is None:
                continue
            plan = plan_sheet(wb, sh)
            if plan.pages:
                plans.append(plan)
        total = sum(len(p.pages) for p in plans)
        c = rl_canvas.Canvas(str(pdf_path), pagesize=PAPER_SIZES[9], invariant=0, pageCompression=1, initialFontName=face(), initialFontSize=9, initialLeading=11)
        c.setTitle(title or xlsx_path.stem)
        c.setAuthor(author)
        c.setCreator(f"FS-Platform {ENGINE_VERSION}")
        c.setSubject("DRAFT – for review" if draft else "Financial statements")
        page_no = 0
        first_page: dict[str, int] = {}
        bookmarks: list[tuple[int, str, int]] = []
        for plan in plans:
            for box in plan.pages:
                page_no += 1
                c.setPageSize((plan.page_w, plan.page_h))
                if box.first_of_sheet:
                    first_page[plan.sheet.name] = page_no
                    bookmarks.append((1, plan.title or plan.sheet.name, page_no))
                for r in box.rows:
                    if r in plan.note_rows:
                        bookmarks.append((2, plan.note_rows[r], page_no))
                _draw_page(c, wb, plan, box)
                _draw_header_footer(c, plan, page_no, total, xlsx_path.name, now)
                draw_overlay(c, plan.page_w, plan.page_h, page=page_no, total=total, draft=draft, run_id=run_id)
                c.showPage()
        if page_no == 0:
            c.setPageSize(PAPER_SIZES[9])
            draw_overlay(c, *PAPER_SIZES[9], page=1, total=1, draft=draft, run_id=run_id)
            c.showPage()
            page_no = 1
        c.save()
    return NativeResult(pages=page_no, sheet_first_page=first_page, bookmarks=_dedupe(bookmarks))


def _dedupe(items: list[tuple[int, str, int]]) -> list[tuple[int, str, int]]:
    seen: set[tuple[int, str]] = set()
    out = []
    for lvl, title, page in items:
        key = (lvl, title)
        if key in seen:
            continue
        seen.add(key)
        out.append((lvl, title, page))
    return out
