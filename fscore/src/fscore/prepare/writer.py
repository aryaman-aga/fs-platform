"""Write a WorkbookSpec to .xlsx with Excel-compatible cached values.

New files only (Module P output, synthetic fixtures): client workbooks are
never written through this path — corrections use the OOXML patcher (D4).
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import xlsxwriter
from lxml import etree

from fscore.engine.calc import Recalculator
from fscore.engine.formula import ExcelError, FormulaError, translate
from fscore.engine.refs import MAX_COL, MAX_ROW, make_addr, split_addr
from fscore.io.ooxml import NS_MAIN, parse_xml, q
from fscore.prepare.spec import WorkbookSpec
from fscore.prepare.style import xlsxwriter_props

Key = tuple[str, int, int]


def compute_values(spec: WorkbookSpec) -> dict[Key, Any]:
    """Evaluate every formula of the spec in dependency order."""
    sheet_bounds = {s.name: (max(s.max_row, 1), max(s.max_col, 1)) for s in spec.sheets}
    by_lower = {s.name.lower(): s.name for s in spec.sheets}

    def canon(name: str) -> str:
        return name if name in sheet_bounds else by_lower.get(name.lower(), name)

    formulas: dict[Key, str] = {}
    for s in spec.sheets:
        for (r, c), cs in s.cells.items():
            if cs.formula is not None:
                formulas[(s.name, r, c)] = cs.formula

    def base(key: Key) -> Any:
        return spec.constant((canon(key[0]), key[1], key[2]))

    rc = Recalculator(
        dict(formulas.items()),
        lambda k: base(k),
        bounds=lambda sh: sheet_bounds.get(canon(sh), (MAX_ROW, MAX_COL)),
    )
    # sheet names inside formulas may differ in case: normalise lookups through canon()
    orig_get = rc._get

    def get(sheet: str, row: int, col: int) -> Any:
        return orig_get(canon(sheet), row, col)

    rc.ev.getter = get
    values = rc.compute_all()
    if rc.errors:
        bad = ", ".join(f"{k[0]}!{make_addr(k[1], k[2])}" for k in list(rc.errors)[:5])
        raise ValueError(f"formula errors in spec: {bad}")
    return values


def write_workbook(spec: WorkbookSpec, path: str | Path, values: dict[Key, Any] | None = None, *, shared_formulas: bool = False) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if values is None:
        values = compute_values(spec)
    fd, tmp_name = tempfile.mkstemp(suffix=".xlsx", dir=str(out.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    wb = xlsxwriter.Workbook(str(tmp), {"strings_to_numbers": False, "strings_to_formulas": False, "strings_to_urls": False})
    wb.set_calc_mode("auto")
    formats: dict[str, Any] = {}

    def fmt(key: str) -> Any:
        if key not in formats:
            formats[key] = wb.add_format(xlsxwriter_props(key))
        return formats[key]

    first_selected = True
    for s in spec.sheets:
        ws = wb.add_worksheet(s.name)
        for col, width in s.col_widths.items():
            hidden = col in s.hidden_cols
            ws.set_column(col - 1, col - 1, width, None, {"hidden": hidden})
        for col in s.hidden_cols:
            if col not in s.col_widths:
                ws.set_column(col - 1, col - 1, 8.43, None, {"hidden": True})
        for row in sorted(set(s.hidden_rows) | set(s.row_heights)):
            ws.set_row(row - 1, s.row_heights.get(row), None, {"hidden": row in s.hidden_rows})
        merged_anchor = {(m[0], m[1]): m for m in s.merges}
        merged_skip = {(r, c) for m in s.merges for r in range(m[0], m[2] + 1) for c in range(m[1], m[3] + 1)} - set(merged_anchor)
        for (r, c), cs in sorted(s.cells.items()):
            if (r, c) in merged_skip:
                continue
            f = fmt(cs.style)
            if (r, c) in merged_anchor:
                m = merged_anchor[(r, c)]
                ws.merge_range(m[0] - 1, m[1] - 1, m[2] - 1, m[3] - 1, "" if cs.value is None else cs.value, f)
                continue
            if cs.formula is not None:
                v = values.get((s.name, r, c), 0)
                if isinstance(v, ExcelError):
                    v = str(v)
                elif v is None:
                    v = 0
                ws.write_formula(r - 1, c - 1, "=" + cs.formula, f, v)
            elif cs.value is None:
                ws.write_blank(r - 1, c - 1, None, f)
            elif isinstance(cs.value, bool):
                ws.write_boolean(r - 1, c - 1, cs.value, f)
            elif isinstance(cs.value, (int, float)):
                ws.write_number(r - 1, c - 1, float(cs.value), f)
            else:
                ws.write_string(r - 1, c - 1, str(cs.value), f)
        if s.print_area:
            r1, c1, r2, c2 = s.print_area
            ws.print_area(r1 - 1, c1 - 1, r2 - 1, c2 - 1)
        ws.set_paper(9)
        if s.landscape:
            ws.set_landscape()
        else:
            ws.set_portrait()
        if s.fit_width:
            ws.fit_to_pages(1, 0)
        ws.set_margins(left=0.5, right=0.5, top=0.6, bottom=0.6)
        if s.header:
            ws.set_header(s.header)
        if s.footer:
            ws.set_footer(s.footer)
        ws.hide_gridlines(2)
        if s.state == "hidden":
            ws.hide()
        elif s.selected:
            ws.select()
            if first_selected:
                ws.activate()
                first_selected = False
    wb.close()
    if shared_formulas:
        _make_shared(tmp)
    os.replace(tmp, out)
    return out


_C_RE = re.compile(rb"<c r=\"([A-Z]+)(\d+)\"")


def _make_shared(path: Path, min_run: int = 3) -> None:
    """Convert vertical runs of translation-equivalent formulas into shared formulas.

    xlsxwriter writes every formula explicitly; Excel writes most column
    formulas as shared formulas. Producing them makes the synthetic fixtures
    exercise the reader's translation and the patcher's expansion (02 §7.4).
    """
    with zipfile.ZipFile(path) as zin:
        items = [(i, zin.read(i.filename)) for i in zin.infolist()]
    out_items = []
    for info, data in items:
        if info.filename.startswith("xl/worksheets/sheet") and info.filename.endswith(".xml"):
            data = _share_sheet(data, min_run)
        out_items.append((info, data))
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for info, data in out_items:
            zout.writestr(info, data)
    shutil.move(str(tmp), str(path))


def _share_sheet(data: bytes, min_run: int) -> bytes:
    root = parse_xml(data)
    sheet_data = root.find(q("sheetData"))
    if sheet_data is None:
        return data
    # column -> list of (row, c element) with formulas
    cols: dict[str, list[tuple[int, etree._Element]]] = defaultdict(list)
    for row in sheet_data.findall(q("row")):
        for c in row.findall(q("c")):
            f = c.find(q("f"))
            if f is not None and f.get("t") is None and f.text:
                r, _ci = split_addr(c.get("r", "A1"))
                cols[re.sub(r"\d", "", c.get("r", "A1"))].append((r, c))
    si = 0
    for cells in cols.values():
        cells.sort(key=lambda x: x[0])
        i = 0
        while i < len(cells):
            r0, c0 = cells[i]
            f0 = c0.find(q("f"))
            assert f0 is not None
            base_text = f0.text or ""
            j = i + 1
            while j < len(cells) and cells[j][0] == cells[j - 1][0] + 1:
                fj = cells[j][1].find(q("f"))
                try:
                    if fj is None or translate(base_text, cells[j][0] - r0, 0) != fj.text:
                        break
                except FormulaError:
                    break
                j += 1
            if j - i >= min_run:
                last_r = cells[j - 1][0]
                col_letters = re.sub(r"\d", "", c0.get("r", "A1"))
                f0.set("t", "shared")
                f0.set("ref", f"{col_letters}{r0}:{col_letters}{last_r}")
                f0.set("si", str(si))
                for k in range(i + 1, j):
                    fk = cells[k][1].find(q("f"))
                    assert fk is not None
                    fk.text = None
                    fk.attrib.clear()
                    fk.set("t", "shared")
                    fk.set("si", str(si))
                si += 1
            i = j
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


__all__ = ["NS_MAIN", "compute_values", "write_workbook"]
