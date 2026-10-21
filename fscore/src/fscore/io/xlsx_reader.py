"""Fast, read-only workbook model built directly from the OOXML parts.

One pass over each sheet's XML yields, for every cell: the formula (shared
formulas expanded by reference translation), Excel's cached value, the style
(number format, font colour, fill, bold, borders), plus row/column hidden
states, merges, print areas, page setup and sheet grouping. Backup sheets are
parsed lazily on first access (02 §3.9 performance).
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Generic, TypeVar, cast, overload

from lxml import etree

from fscore.engine.display import Rendered, compile_format
from fscore.engine.formula import ExcelError, FormulaError, translate
from fscore.engine.refs import Area, make_addr, parse_area, split_addr
from fscore.io.ooxml import (
    NS_MAIN,
    NS_R,
    REL_EXTERNAL_LINK,
    REL_SHARED_STRINGS,
    REL_STYLES,
    REL_THEME,
    REL_WORKSHEET,
    Package,
    q,
    sha256_file,
)
from fscore.io.styles import Styles, contrast_ratio, parse_styles, parse_theme

_C = q("c")
_ROW = q("row")
_F = q("f")
_V = q("v")
_IS = q("is")
_T = q("t")
_R = q("r")
_RPH = q("rPh")


class WorkbookReadError(Exception):
    pass


@dataclass(slots=True)
class Cell:
    row: int
    col: int
    value: Any = None  # Excel's cached value: float | str | bool | ExcelError | None
    formula: str | None = None  # without '='; shared children carry the translated text
    style: int = 0
    t: str = "n"
    shared_si: int | None = None
    shared_ref: str | None = None  # set on a shared-formula master
    is_array: bool = False

    @property
    def addr(self) -> str:
        return make_addr(self.row, self.col)

    @property
    def is_formula(self) -> bool:
        return self.formula is not None

    @property
    def is_number(self) -> bool:
        return isinstance(self.value, (int, float)) and not isinstance(self.value, bool)

    @property
    def is_text(self) -> bool:
        return isinstance(self.value, str) and not isinstance(self.value, ExcelError)

    @property
    def is_error(self) -> bool:
        return isinstance(self.value, ExcelError)


@dataclass
class PageSetup:
    orientation: str = "portrait"
    paper_size: int = 9  # A4
    scale: int = 100
    fit_to_page: bool = False
    fit_to_width: int = 1
    fit_to_height: int = 1
    margins: dict[str, float] = field(default_factory=lambda: {"left": 0.7, "right": 0.7, "top": 0.75, "bottom": 0.75, "header": 0.3, "footer": 0.3})
    header: str = ""
    footer: str = ""
    center_horizontally: bool = False
    grid_lines: bool = False


@dataclass
class DefinedName:
    name: str
    value: str
    local_sheet: int | None = None
    hidden: bool = False


T = TypeVar("T")


class _Lazy(Generic[T]):
    """Attribute filled by the sheet parser: reading it loads the sheet on first use."""

    def __set_name__(self, owner: type, name: str) -> None:
        self.attr = "_" + name

    @overload
    def __get__(self, obj: None, objtype: type | None = None) -> _Lazy[T]: ...
    @overload
    def __get__(self, obj: object, objtype: type | None = None) -> T: ...
    def __get__(self, obj: object | None, objtype: type | None = None) -> T | _Lazy[T]:
        if obj is None:
            return self
        if not getattr(obj, "loaded", True):
            obj.ensure_loaded()  # type: ignore[attr-defined]
        return cast(T, getattr(obj, self.attr))

    def __set__(self, obj: object, value: T) -> None:
        setattr(obj, self.attr, value)


class Sheet:
    cells: _Lazy[dict[tuple[int, int], Cell]] = _Lazy()
    rows_hidden: _Lazy[set[int]] = _Lazy()
    rows_present: _Lazy[set[int]] = _Lazy()
    cols_hidden: _Lazy[set[int]] = _Lazy()
    row_heights: _Lazy[dict[int, float]] = _Lazy()
    col_widths: _Lazy[dict[int, float]] = _Lazy()
    row_styles: _Lazy[dict[int, int]] = _Lazy()
    default_row_height: _Lazy[float] = _Lazy()
    default_col_width: _Lazy[float] = _Lazy()
    zero_height: _Lazy[bool] = _Lazy()
    tab_selected: _Lazy[bool] = _Lazy()
    merges: _Lazy[list[Area]] = _Lazy()
    page: _Lazy[PageSetup] = _Lazy()
    row_breaks: _Lazy[list[int]] = _Lazy()
    col_breaks: _Lazy[list[int]] = _Lazy()
    max_row: _Lazy[int] = _Lazy()
    max_col: _Lazy[int] = _Lazy()
    shared_masters: _Lazy[dict[int, tuple[int, int, str, str]]] = _Lazy()
    unsupported: _Lazy[list[str]] = _Lazy()

    def __init__(self, wb: Workbook, name: str, index: int, part: str, state: str) -> None:
        self.loaded = False
        self.wb = wb
        self.name = name
        self.index = index
        self.part = part
        self.state = state  # visible | hidden | veryHidden
        self.cells = {}
        self.rows_hidden = set()
        self.rows_present = set()
        self.cols_hidden = set()
        self.row_heights = {}
        self.col_widths = {}
        self.row_styles = {}
        self.default_row_height = 15.0
        self.default_col_width = 8.43
        self.zero_height = False
        self.tab_selected = False
        self.merges = []
        self.print_area: list[Area] | None = None
        self.print_area_invalid = False
        self.print_titles_rows: tuple[int, int] | None = None
        self.print_titles_cols: tuple[int, int] | None = None
        self.page = PageSetup()
        self.row_breaks = []
        self.col_breaks = []
        self.max_row = 0
        self.max_col = 0
        self.shared_masters = {}  # si -> (row, col, text, ref)
        self.unsupported = []

    # ------------------------------------------------------------------ access
    @property
    def visible(self) -> bool:
        return self.state == "visible"

    def ensure_loaded(self) -> Sheet:
        if not self.loaded:
            self.wb._load_sheet(self)
        return self

    def peek_tab_selected(self) -> bool:
        """Read only the sheet header (up to <sheetData>) to learn whether the tab is selected (grouped)."""
        if self.loaded:
            return bool(self.tab_selected)
        pkg = self.wb._pkg
        if pkg is None or not pkg.has(self.part):
            return False
        with pkg.open(self.part) as fh:
            for _ev, el in etree.iterparse(fh, events=("start",), resolve_entities=False, no_network=True):
                if el.tag == q("sheetView"):
                    return el.get("tabSelected") in ("1", "true")
                if el.tag == q("sheetData"):
                    return False
        return False

    def cell(self, row: int, col: int) -> Cell | None:
        self.ensure_loaded()
        return self.cells.get((row, col))

    def cell_at(self, addr: str) -> Cell | None:
        r, c = split_addr(addr)
        return self.cell(r, c)

    def value(self, row: int, col: int) -> Any:
        c = self.cell(row, col)
        return None if c is None else c.value

    def iter_cells(self) -> list[Cell]:
        self.ensure_loaded()
        return sorted(self.cells.values(), key=lambda c: (c.row, c.col))

    def row_hidden(self, row: int) -> bool:
        if row in self.rows_hidden:
            return True
        return self.zero_height and row not in self.rows_present

    def col_hidden(self, col: int) -> bool:
        return col in self.cols_hidden

    def is_hidden(self, row: int, col: int) -> bool:
        return self.row_hidden(row) or self.col_hidden(col)

    def col_width(self, col: int) -> float:
        return self.col_widths.get(col, self.default_col_width)

    def row_height(self, row: int) -> float:
        return self.row_heights.get(row, self.default_row_height)

    @cached_property
    def print_bounds(self) -> list[Area]:
        """Print area, or the used range when none (or an invalid one) is defined (02 §15)."""
        self.ensure_loaded()
        if self.print_area:
            return self.print_area
        if self.max_row == 0:
            return []
        return [Area(self.name, 1, 1, self.max_row, self.max_col)]

    def in_print_area(self, row: int, col: int) -> bool:
        return any(a.contains(row, col) for a in self.print_bounds)

    def merged_anchor(self, row: int, col: int) -> tuple[int, int] | None:
        for m in self.merges:
            if m.contains(row, col):
                return (m.r1, m.c1)
        return None

    # ------------------------------------------------------------------ rendering
    def number_format(self, cell: Cell) -> str:
        return self.wb.styles.number_format(cell.style)

    def rendered(self, cell: Cell) -> Rendered:
        return self.wb.render_cell(cell)

    def font_invisible(self, cell: Cell) -> bool:
        """White / no-contrast font against the cell fill (HID-02)."""
        st = self.wb.styles
        font = st.font(cell.style)
        fill = st.fill(cell.style)
        return contrast_ratio(font.color, fill.color) < 1.25

    def is_bold(self, cell: Cell) -> bool:
        return self.wb.styles.font(cell.style).bold

    def has_top_border(self, cell: Cell) -> bool:
        return self.wb.styles.border(cell.style).top is not None

    def is_printed(self, row: int, col: int) -> bool:
        """Cell would appear on paper: in print area, row/col visible."""
        return self.in_print_area(row, col) and not self.is_hidden(row, col)

    def text_at(self, row: int, col: int) -> str:
        c = self.cell(row, col)
        if c is None or c.value is None:
            return ""
        if isinstance(c.value, str):
            return c.value
        return self.rendered(c).clean

    def __repr__(self) -> str:
        return f"<Sheet {self.name!r} {self.state}>"


_PA_SPLIT = re.compile(r",(?=(?:[^']*'[^']*')*[^']*$)")


class Workbook:
    """Read-only workbook. Use ``Workbook.open(path)``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.sheets: list[Sheet] = []
        self._by_name: dict[str, Sheet] = {}
        self._by_lower: dict[str, Sheet] = {}
        self.styles = Styles()
        self.sst: list[str] = []
        self.defined_names: list[DefinedName] = []
        self.date1904 = False
        self.full_calc_on_load = False
        self.active_tab = 0
        self.has_external_links = False
        self.has_vba = False
        self.cells_read = 0
        self._render_cache: dict[tuple[int, Any], Rendered] = {}
        self._pkg: Package | None = None

    # ------------------------------------------------------------------ open
    @classmethod
    def open(cls, path: str | Path, *, eager: list[str] | None = None) -> Workbook:
        p = Path(path)
        if not p.exists():
            raise WorkbookReadError(f"file not found: {p.name}")
        with p.open("rb") as fh:
            magic = fh.read(8)
        if magic.startswith(b"\xd0\xcf\x11\xe0"):
            raise WorkbookReadError("the workbook is password-protected or in the legacy .xls format; save it as .xlsx without a password")
        if not magic.startswith(b"PK"):
            raise WorkbookReadError("not an .xlsx/.xlsm package")
        wb = cls(p)
        try:
            wb._pkg = Package(p)
            wb._read_workbook()
        except (KeyError, etree.XMLSyntaxError, ValueError) as exc:
            raise WorkbookReadError(f"cannot read workbook structure: {exc}") from exc
        for name in eager or []:
            sh = wb.get_sheet(name)
            if sh is not None:
                sh.ensure_loaded()
        return wb

    def close(self) -> None:
        if self._pkg is not None:
            self._pkg.close()
            self._pkg = None

    def __enter__(self) -> Workbook:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @cached_property
    def sha256(self) -> str:
        return sha256_file(self.path)

    # ------------------------------------------------------------------ lookup
    def get_sheet(self, name: str) -> Sheet | None:
        if name in self._by_name:
            return self._by_name[name]
        return self._by_lower.get(name.strip().lower())

    def sheet(self, name: str) -> Sheet:
        s = self.get_sheet(name)
        if s is None:
            raise KeyError(name)
        return s

    @property
    def sheet_names(self) -> list[str]:
        return [s.name for s in self.sheets]

    def cell(self, sheet: str, row: int, col: int) -> Cell | None:
        s = self.get_sheet(sheet)
        return None if s is None else s.cell(row, col)

    def name_value(self, name: str, sheet_index: int | None = None) -> str | None:
        local = [d for d in self.defined_names if d.name.lower() == name.lower() and d.local_sheet == sheet_index]
        if local:
            return local[0].value
        glob = [d for d in self.defined_names if d.name.lower() == name.lower() and d.local_sheet is None]
        return glob[0].value if glob else None

    def render_cell(self, cell: Cell) -> Rendered:
        key = (cell.style, cell.value if not isinstance(cell.value, float) else float(cell.value))
        try:
            hit = self._render_cache.get(key)
        except TypeError:
            hit = None
        if hit is not None:
            return hit
        fmt = self.styles.number_format(cell.style)
        if isinstance(cell.value, ExcelError):
            r = Rendered(str(cell.value), None, "error")
        else:
            r = compile_format(fmt).render(cell.value, date1904=self.date1904)
        if len(self._render_cache) < 500_000:
            self._render_cache[key] = r
        return r

    # ------------------------------------------------------------------ parsing
    def _read_workbook(self) -> None:
        pkg = self._pkg
        assert pkg is not None
        wb_part = pkg.workbook_part()
        root = pkg.xml(wb_part)
        rels = pkg.rels(wb_part)
        self.has_vba = any(n.endswith("vbaProject.bin") for n in pkg.names)
        self.has_external_links = any(t == REL_EXTERNAL_LINK for t, _tg, _m in rels.values())
        pr = root.find(q("workbookPr"))
        if pr is not None:
            self.date1904 = pr.get("date1904") in ("1", "true")
        calc = root.find(q("calcPr"))
        if calc is not None:
            self.full_calc_on_load = calc.get("fullCalcOnLoad") in ("1", "true")
        bv = root.find(q("bookViews"))
        if bv is not None and len(bv):
            self.active_tab = int(bv[0].get("activeTab", "0"))
        # theme, styles, shared strings
        theme: list[str] | None = None
        styles_root = None
        for typ, target, _mode in rels.values():
            if typ == REL_THEME and pkg.has(target):
                theme = parse_theme(pkg.xml(target))
        for typ, target, _mode in rels.values():
            if typ == REL_STYLES and pkg.has(target):
                styles_root = pkg.xml(target)
        self.styles = parse_styles(styles_root, theme)
        for typ, target, _mode in rels.values():
            if typ == REL_SHARED_STRINGS and pkg.has(target):
                self.sst = _read_sst(pkg, target)
        # sheets
        sheets_el = root.find(q("sheets"))
        idx = 0
        for sh in sheets_el.findall(q("sheet")) if sheets_el is not None else []:
            rid = sh.get(f"{{{NS_R}}}id", "")
            rel = rels.get(rid)
            if rel is None or rel[0] != REL_WORKSHEET:
                idx += 1  # chartsheets / dialogsheets keep their index
                continue
            s = Sheet(self, sh.get("name", f"Sheet{idx + 1}"), idx, rel[1], sh.get("state", "visible"))
            self.sheets.append(s)
            self._by_name[s.name] = s
            self._by_lower.setdefault(s.name.strip().lower(), s)
            idx += 1
        # defined names (print areas / titles)
        dn_el = root.find(q("definedNames"))
        for dn in dn_el.findall(q("definedName")) if dn_el is not None else []:
            ls = dn.get("localSheetId")
            self.defined_names.append(
                DefinedName(dn.get("name", ""), (dn.text or "").strip(), int(ls) if ls is not None else None, dn.get("hidden") in ("1", "true"))
            )
        sheet_by_index = dict(enumerate(self._all_sheet_order(root)))
        for d in self.defined_names:
            low = d.name.lower()
            if d.local_sheet is None or d.local_sheet not in sheet_by_index:
                continue
            tsheet = sheet_by_index[d.local_sheet]
            if tsheet is None:
                continue
            if low == "_xlnm.print_area":
                if "#REF" in d.value.upper():
                    tsheet.print_area_invalid = True
                    continue
                areas: list[Area] = []
                for part in _PA_SPLIT.split(d.value):
                    try:
                        areas.append(parse_area(part.strip(), tsheet.name))
                    except ValueError:
                        tsheet.print_area_invalid = True
                tsheet.print_area = [a for a in areas if a.sheet == tsheet.name or a.sheet.strip() == tsheet.name.strip()] or areas
            elif low == "_xlnm.print_titles":
                for part in _PA_SPLIT.split(d.value):
                    ref = part.split("!")[-1].replace("$", "")
                    a, _, b = ref.partition(":")
                    if a.isdigit() and b.isdigit():
                        tsheet.print_titles_rows = (int(a), int(b))
                    elif a.isalpha() and b.isalpha():
                        from fscore.engine.refs import col_to_index

                        tsheet.print_titles_cols = (col_to_index(a), col_to_index(b))

    def _all_sheet_order(self, root: etree._Element) -> list[Sheet | None]:
        """localSheetId indexes all <sheet> entries (including chart sheets)."""
        out: list[Sheet | None] = []
        sheets_el = root.find(q("sheets"))
        for sh in sheets_el.findall(q("sheet")) if sheets_el is not None else []:
            out.append(self._by_name.get(sh.get("name", "")))
        return out

    def _load_sheet(self, sheet: Sheet) -> None:
        pkg = self._pkg
        if pkg is None:
            raise WorkbookReadError("workbook is closed")
        sheet.loaded = True
        if not pkg.has(sheet.part):
            return
        sst = self.sst
        cells = sheet.cells
        pending_children: list[Cell] = []
        cur_row = 0
        next_col = 1
        with pkg.open(sheet.part) as fh:
            ctx = etree.iterparse(fh, events=("start", "end"), huge_tree=True, resolve_entities=False, no_network=True, remove_blank_text=True)
            for event, el in ctx:
                tag = el.tag
                if event == "start":
                    if tag == _ROW:
                        r_attr = el.get("r")
                        cur_row = int(r_attr) if r_attr else cur_row + 1
                        next_col = 1
                        sheet.rows_present.add(cur_row)
                        if el.get("hidden") in ("1", "true"):
                            sheet.rows_hidden.add(cur_row)
                        ht = el.get("ht")
                        if ht:
                            sheet.row_heights[cur_row] = float(ht)
                        if el.get("customFormat") in ("1", "true") and el.get("s"):
                            sheet.row_styles[cur_row] = int(el.get("s", "0"))
                    continue
                # end events
                if tag == _C:
                    ref = el.get("r")
                    if ref:
                        row, col = split_addr(ref)
                    else:
                        row, col = cur_row, next_col
                    next_col = col + 1
                    t = el.get("t", "n")
                    cell = Cell(row, col, style=int(el.get("s", "0")), t=t)
                    f_el = el.find(_F)
                    v_el = el.find(_V)
                    vtext = v_el.text if v_el is not None else None
                    if t == "s":
                        try:
                            cell.value = sst[int(vtext)] if vtext is not None else None
                        except (ValueError, IndexError):
                            cell.value = None
                    elif t == "str":
                        cell.value = vtext if vtext is not None else ""
                    elif t == "inlineStr":
                        is_el = el.find(_IS)
                        cell.value = _rich_text(is_el) if is_el is not None else ""
                    elif t == "b":
                        cell.value = vtext in ("1", "true")
                    elif t == "e":
                        cell.value = ExcelError(vtext or "#N/A")
                    elif t == "d":
                        cell.value = vtext
                    else:
                        if vtext is not None and vtext != "":
                            try:
                                cell.value = float(vtext)
                            except ValueError:
                                cell.value = vtext
                    if f_el is not None:
                        ftype = f_el.get("t", "normal")
                        ftext = f_el.text or ""
                        if ftype == "shared":
                            si = int(f_el.get("si", "-1"))
                            cell.shared_si = si
                            if ftext:
                                cell.formula = ftext
                                cell.shared_ref = f_el.get("ref")
                                sheet.shared_masters[si] = (row, col, ftext, f_el.get("ref", ""))
                            else:
                                master = sheet.shared_masters.get(si)
                                if master is not None:
                                    cell.formula = _translate_safe(master[2], row - master[0], col - master[1], sheet)
                                else:
                                    pending_children.append(cell)
                        elif ftype == "dataTable":
                            sheet.unsupported.append(f"{cell.addr}: data table formula")
                        else:
                            cell.formula = ftext
                            cell.is_array = ftype == "array"
                    if cell.value is not None or cell.formula is not None or cell.style:
                        cells[(row, col)] = cell
                        if row > sheet.max_row and (cell.value is not None or cell.formula is not None):
                            sheet.max_row = row
                        if col > sheet.max_col and (cell.value is not None or cell.formula is not None):
                            sheet.max_col = col
                    el.clear()
                elif tag == _ROW:
                    el.clear()
                    while el.getprevious() is not None:
                        parent = el.getparent()
                        if parent is None:
                            break
                        del parent[0]
                elif tag == q("sheetFormatPr"):
                    sheet.default_row_height = float(el.get("defaultRowHeight", "15"))
                    if el.get("defaultColWidth"):
                        sheet.default_col_width = float(el.get("defaultColWidth", "8.43"))
                    elif el.get("baseColWidth"):
                        sheet.default_col_width = float(el.get("baseColWidth", "8")) + 0.43
                    sheet.zero_height = el.get("zeroHeight") in ("1", "true")
                elif tag == q("col"):
                    lo, hi = int(el.get("min", "1")), int(el.get("max", "1"))
                    hidden = el.get("hidden") in ("1", "true")
                    width = el.get("width")
                    for c in range(lo, min(hi, 16384) + 1):
                        if hidden:
                            sheet.cols_hidden.add(c)
                        if width:
                            sheet.col_widths[c] = float(width)
                        if hi - lo > 2000 and c - lo > 200:  # whole-sheet column ranges: stop expanding widths
                            if hidden:
                                sheet.cols_hidden.update(range(c + 1, min(hi, 16384) + 1))
                            break
                elif tag == q("sheetView"):
                    sheet.tab_selected = el.get("tabSelected") in ("1", "true")
                elif tag == q("mergeCell"):
                    ref = el.get("ref")
                    if ref:
                        with contextlib.suppress(ValueError):
                            sheet.merges.append(parse_area(ref, sheet.name))
                elif tag == q("pageSetup"):
                    ps = sheet.page
                    ps.orientation = el.get("orientation", ps.orientation)
                    ps.paper_size = int(el.get("paperSize", str(ps.paper_size)))
                    ps.scale = int(el.get("scale", "100"))
                    ps.fit_to_width = int(el.get("fitToWidth", "1"))
                    ps.fit_to_height = int(el.get("fitToHeight", "1"))
                elif tag == q("pageSetUpPr"):
                    sheet.page.fit_to_page = el.get("fitToPage") in ("1", "true")
                elif tag == q("pageMargins"):
                    for k in ("left", "right", "top", "bottom", "header", "footer"):
                        if el.get(k):
                            sheet.page.margins[k] = float(el.get(k, "0.7"))
                elif tag == q("oddHeader"):
                    sheet.page.header = el.text or ""
                elif tag == q("oddFooter"):
                    sheet.page.footer = el.text or ""
                elif tag == q("printOptions"):
                    sheet.page.center_horizontally = el.get("horizontalCentered") in ("1", "true")
                    sheet.page.grid_lines = el.get("gridLines") in ("1", "true")
                elif tag == q("brk"):
                    parent = el.getparent()
                    if parent is not None and parent.tag == q("rowBreaks"):
                        sheet.row_breaks.append(int(el.get("id", "0")))
                    elif parent is not None and parent.tag == q("colBreaks"):
                        sheet.col_breaks.append(int(el.get("id", "0")))
        for cell in pending_children:
            master = sheet.shared_masters.get(cell.shared_si or -1)
            if master is not None:
                cell.formula = _translate_safe(master[2], cell.row - master[0], cell.col - master[1], sheet)
        self.cells_read += len(cells)

    def iter_formula_cells(self, sheets: list[str] | None = None) -> list[tuple[Sheet, Cell]]:
        out: list[tuple[Sheet, Cell]] = []
        for s in self.sheets:
            if sheets is not None and s.name not in sheets:
                continue
            for c in s.iter_cells():
                if c.formula is not None:
                    out.append((s, c))
        return out


def _translate_safe(text: str, drow: int, dcol: int, sheet: Sheet) -> str:
    try:
        return translate(text, drow, dcol)
    except FormulaError as exc:
        sheet.unsupported.append(f"shared formula translation failed: {exc}")
        return text


def _rich_text(el: etree._Element) -> str:
    parts: list[str] = []
    for node in el.iter():
        if node.tag == _T:
            parent = node.getparent()
            if parent is not None and parent.tag == _RPH:
                continue
            parts.append(node.text or "")
    return "".join(parts)


def _read_sst(pkg: Package, part: str) -> list[str]:
    out: list[str] = []
    with pkg.open(part) as fh:
        for _ev, el in etree.iterparse(fh, events=("end",), tag=q("si"), huge_tree=True, resolve_entities=False, no_network=True):
            out.append(_rich_text(el))
            el.clear()
    return out


def open_workbook(path: str | Path) -> Workbook:
    return Workbook.open(path)


__all__ = ["NS_MAIN", "Cell", "DefinedName", "PageSetup", "Sheet", "Workbook", "WorkbookReadError", "open_workbook"]
