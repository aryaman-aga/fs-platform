"""Surgical OOXML patch writer (02 §7.4, decision D4, control C5).

The corrected workbook is produced by editing only the targeted ``<c>``
elements of the original package; every other byte-level structure (styles,
widths, merges, print setup, page breaks, grouping, comments, validation,
conditional formats, names, charts, images) is carried over untouched.
Never round-trips through openpyxl.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

from fscore.engine.formula import ExcelError, FormulaError, translate
from fscore.engine.refs import col_to_index, index_to_col, split_addr
from fscore.io.ooxml import (
    NS_CT,
    NS_MAIN,
    NS_PKG_REL,
    NS_R,
    REL_CALC_CHAIN,
    REL_WORKSHEET,
    parse_xml,
    q,
    resolve_target,
)

_C, _ROW, _F, _V, _IS, _T = q("c"), q("row"), q("f"), q("v"), q("is"), q("t")


class PatchError(Exception):
    pass


class WorkbookLockedError(PatchError):
    pass


def number_text(v: float) -> str:
    """Cached numeric value at 15 significant digits (Excel's precision)."""
    if not math.isfinite(v):
        raise PatchError("non-finite value")
    if float(v).is_integer() and abs(v) < 1e15:
        return str(int(v))
    s = format(float(v), ".15g")
    if "e" in s:
        mant, exp = s.split("e")
        return f"{mant}E{int(exp):+d}"
    return s


@dataclass
class _SheetDoc:
    name: str
    part: str
    root: etree._Element
    rows: dict[int, etree._Element] = field(default_factory=dict)
    cells: dict[tuple[int, int], etree._Element] = field(default_factory=dict)
    dirty: bool = False

    @property
    def sheet_data(self) -> etree._Element:
        sd = self.root.find(q("sheetData"))
        if sd is None:
            raise PatchError(f"{self.name}: no sheetData")
        return sd


class XlsxPatcher:
    """Collects cell-level edits and writes a patched copy of an .xlsx package."""

    def __init__(self, src: Path) -> None:
        self.src = Path(src)
        with zipfile.ZipFile(self.src) as z:
            self.infos = z.infolist()
            self.parts: dict[str, bytes] = {i.filename: z.read(i.filename) for i in self.infos}
        self.removed: set[str] = set()
        self._wb_part = self._find_workbook_part()
        self._wb_root = parse_xml(self.parts[self._wb_part])
        self._wb_dirty = False
        self._sheet_parts = self._map_sheets()
        self._docs: dict[str, _SheetDoc] = {}
        self.log: list[str] = []
        self.extra_touched: set[str] = set()  # parts replaced directly in self.parts by callers

    # ------------------------------------------------------------------ package map
    def _find_workbook_part(self) -> str:
        rels = parse_xml(self.parts["_rels/.rels"])
        for rel in rels.findall(q("Relationship", NS_PKG_REL)):
            if rel.get("Type", "").endswith("/officeDocument"):
                return rel.get("Target", "xl/workbook.xml").lstrip("/")
        return "xl/workbook.xml"

    def _rels_part(self, part: str) -> str:
        d, f = part.rsplit("/", 1) if "/" in part else ("", part)
        return f"{d}/_rels/{f}.rels" if d else f"_rels/{f}.rels"

    def _map_sheets(self) -> dict[str, str]:
        rels_root = parse_xml(self.parts[self._rels_part(self._wb_part)])
        rid_to_target = {
            r.get("Id"): resolve_target(self._wb_part, r.get("Target", ""))
            for r in rels_root.findall(q("Relationship", NS_PKG_REL))
            if r.get("Type") == REL_WORKSHEET
        }
        out: dict[str, str] = {}
        sheets = self._wb_root.find(q("sheets"))
        for sh in sheets.findall(q("sheet")) if sheets is not None else []:
            rid = sh.get(f"{{{NS_R}}}id")
            if rid in rid_to_target:
                out[sh.get("name", "")] = rid_to_target[rid]
        return out

    def sheet_names(self) -> list[str]:
        return list(self._sheet_parts)

    def _doc(self, sheet: str) -> _SheetDoc:
        if sheet not in self._docs:
            part = self._sheet_parts.get(sheet)
            if part is None:
                for n, p in self._sheet_parts.items():
                    if n.strip().lower() == sheet.strip().lower():
                        part = p
                        sheet = n
                        break
            if part is None:
                raise PatchError(f"sheet not found: {sheet}")
            root = parse_xml(self.parts[part])
            doc = _SheetDoc(sheet, part, root)
            for row in doc.sheet_data.findall(_ROW):
                r = int(row.get("r", "0"))
                doc.rows[r] = row
                for c in row.findall(_C):
                    ref = c.get("r")
                    if ref:
                        doc.cells[split_addr(ref)] = c
            self._docs[sheet] = doc
        return self._docs[sheet]

    # ------------------------------------------------------------------ cells
    def _row(self, doc: _SheetDoc, r: int) -> etree._Element:
        row = doc.rows.get(r)
        if row is not None:
            return row
        sd = doc.sheet_data
        row = etree.Element(_ROW)
        row.set("r", str(r))
        later = [rr for rr in doc.rows if rr > r]
        if later:
            sd.insert(sd.index(doc.rows[min(later)]), row)
        else:
            sd.append(row)
        doc.rows[r] = row
        return row

    def _cell(self, doc: _SheetDoc, r: int, c: int, *, create: bool = True) -> etree._Element | None:
        el = doc.cells.get((r, c))
        if el is not None or not create:
            return el
        row = self._row(doc, r)
        el = etree.Element(_C)
        el.set("r", f"{index_to_col(c)}{r}")
        # insert in column order; copy the style of the nearest styled cell in the row
        siblings = [(split_addr(x.get("r", "A1"))[1], x) for x in row.findall(_C)]
        after = [x for col, x in siblings if col > c]
        if after:
            row.insert(row.index(after[0]), el)
        else:
            row.append(el)
        styled = sorted(((abs(col - c), x.get("s")) for col, x in siblings if x.get("s")), key=lambda t: t[0])
        if styled:
            el.set("s", styled[0][1] or "0")
        spans = row.get("spans")
        if spans:
            lo, _, hi = spans.partition(":")
            try:
                row.set("spans", f"{min(int(lo), c)}:{max(int(hi), c)}")
            except ValueError:
                del row.attrib["spans"]
        doc.cells[(r, c)] = el
        doc.dirty = True
        return el

    def _expand_shared(self, doc: _SheetDoc, si: str) -> None:
        """Turn every member of a shared-formula group into an explicit formula."""
        master_el = None
        master_text = ""
        members: list[etree._Element] = []
        for (_r, _c), el in doc.cells.items():
            f = el.find(_F)
            if f is not None and f.get("t") == "shared" and f.get("si") == si:
                members.append(el)
                if f.text and f.get("ref"):
                    master_el, master_text = el, f.text
        if master_el is None:
            raise PatchError(f"{doc.name}: shared formula si={si} has no master")
        mr, mc = split_addr(master_el.get("r", "A1"))
        for el in members:
            f = el.find(_F)
            assert f is not None
            r, c = split_addr(el.get("r", "A1"))
            try:
                text = translate(master_text, r - mr, c - mc)
            except FormulaError as exc:
                raise PatchError(f"{doc.name}!{el.get('r')}: cannot expand shared formula: {exc}") from exc
            f.attrib.clear()
            f.text = text
        doc.dirty = True
        self.log.append(f"{doc.name}: expanded shared formula group si={si} ({len(members)} cells)")

    def _prepare_formula_cell(self, doc: _SheetDoc, el: etree._Element) -> None:
        f = el.find(_F)
        if f is None:
            return
        if f.get("t") == "shared":
            if f.get("ref") is not None and f.text:
                self._expand_shared(doc, f.get("si", ""))
            else:
                # a child: give it its own explicit formula (the group is unaffected)
                si = f.get("si", "")
                master = next((x for x in doc.cells.values() if (ff := x.find(_F)) is not None and ff.get("t") == "shared" and ff.get("si") == si and ff.get("ref")), None)
                if master is not None:
                    mf = master.find(_F)
                    assert mf is not None
                    mr, mc = split_addr(master.get("r", "A1"))
                    r, c = split_addr(el.get("r", "A1"))
                    f.attrib.clear()
                    f.text = translate(mf.text or "", r - mr, c - mc)
        elif f.get("t") == "array":
            f.attrib.clear()

    @staticmethod
    def _set_v(el: etree._Element, value: Any) -> None:
        for child in el.findall(_IS):
            el.remove(child)
        v = el.find(_V)
        if value is None:
            if v is not None:
                el.remove(v)
            if "t" in el.attrib:
                del el.attrib["t"]
            return
        if v is None:
            v = etree.SubElement(el, _V)
            f = el.find(_F)
            # <f> must precede <v>
            if f is not None and el.index(f) > el.index(v):
                el.remove(v)
                el.insert(el.index(f) + 1, v)
        if isinstance(value, bool):
            el.set("t", "b")
            v.text = "1" if value else "0"
        elif isinstance(value, ExcelError):
            el.set("t", "e")
            v.text = str(value)
        elif isinstance(value, (int, float)):
            if "t" in el.attrib:
                del el.attrib["t"]
            v.text = number_text(float(value))
        else:
            el.set("t", "str")
            v.text = str(value)

    def set_formula(self, sheet: str, addr: str, formula: str, cached: Any = None) -> None:
        if formula.startswith("="):
            formula = formula[1:]
        if not formula.strip():
            raise PatchError("empty formula")
        doc = self._doc(sheet)
        r, c = split_addr(addr)
        el = self._cell(doc, r, c)
        assert el is not None
        self._prepare_formula_cell(doc, el)
        f = el.find(_F)
        if f is None:
            f = etree.Element(_F)
            el.insert(0, f)
        f.attrib.clear()
        f.text = formula
        self._set_v(el, cached)
        doc.dirty = True

    def set_value(self, sheet: str, addr: str, value: float | None) -> None:
        doc = self._doc(sheet)
        r, c = split_addr(addr)
        el = self._cell(doc, r, c)
        assert el is not None
        self._prepare_formula_cell(doc, el)
        f = el.find(_F)
        if f is not None:
            el.remove(f)
        self._set_v(el, value)
        doc.dirty = True

    def set_text(self, sheet: str, addr: str, text: str) -> None:
        """Typed text as an inline string (control C6: never a formula)."""
        doc = self._doc(sheet)
        r, c = split_addr(addr)
        el = self._cell(doc, r, c)
        assert el is not None
        self._prepare_formula_cell(doc, el)
        for tag in (_F, _V, _IS):
            for child in el.findall(tag):
                el.remove(child)
        el.set("t", "inlineStr")
        is_el = etree.SubElement(el, _IS)
        t = etree.SubElement(is_el, _T)
        t.text = text
        if text != text.strip():
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        doc.dirty = True

    def refresh_cached(self, sheet: str, addr: str, value: Any) -> None:
        """Update the cached <v> of an existing formula cell (values changed upstream)."""
        doc = self._doc(sheet)
        r, c = split_addr(addr)
        el = self._cell(doc, r, c, create=False)
        if el is None or el.find(_F) is None:
            return
        self._set_v(el, value)
        doc.dirty = True

    def hide_row(self, sheet: str, row: int) -> None:
        doc = self._doc(sheet)
        el = self._row(doc, row)
        el.set("hidden", "1")
        doc.dirty = True

    def hide_col(self, sheet: str, col: int) -> None:
        doc = self._doc(sheet)
        root = doc.root
        cols = root.find(q("cols"))
        if cols is None:
            cols = etree.Element(q("cols"))
            sd = doc.sheet_data
            root.insert(root.index(sd), cols)
        for el in list(cols.findall(q("col"))):
            lo, hi = int(el.get("min", "1")), int(el.get("max", "1"))
            if lo <= col <= hi:
                # split the range so only `col` changes
                pos = cols.index(el)
                parts = []
                if lo < col:
                    left = etree.Element(q("col"), _attrs(el))
                    left.set("min", str(lo))
                    left.set("max", str(col - 1))
                    parts.append(left)
                mid = etree.Element(q("col"), _attrs(el))
                mid.set("min", str(col))
                mid.set("max", str(col))
                mid.set("hidden", "1")
                parts.append(mid)
                if col < hi:
                    right = etree.Element(q("col"), _attrs(el))
                    right.set("min", str(col + 1))
                    right.set("max", str(hi))
                    parts.append(right)
                cols.remove(el)
                for i, p in enumerate(parts):
                    cols.insert(pos + i, p)
                doc.dirty = True
                return
        new = etree.Element(q("col"))
        new.set("min", str(col))
        new.set("max", str(col))
        new.set("width", "8.7109375")
        new.set("hidden", "1")
        existing = cols.findall(q("col"))
        after = [e for e in existing if int(e.get("min", "1")) > col]
        if after:
            cols.insert(cols.index(after[0]), new)
        else:
            cols.append(new)
        doc.dirty = True

    # ------------------------------------------------------------------ workbook-level
    def _full_calc_and_drop_chain(self) -> None:
        root = self._wb_root
        calc = root.find(q("calcPr"))
        if calc is None:
            calc = etree.Element(q("calcPr"))
            # CT_Workbook sequence: …, sheets, functionGroups, externalReferences, definedNames, calcPr, …
            anchor = None
            for tag in ("definedNames", "externalReferences", "functionGroups", "sheets"):
                anchor = root.find(q(tag))
                if anchor is not None:
                    break
            if anchor is not None:
                root.insert(root.index(anchor) + 1, calc)
            else:
                root.append(calc)
        calc.set("fullCalcOnLoad", "1")
        self._wb_dirty = True
        rels_part = self._rels_part(self._wb_part)
        rels = parse_xml(self.parts[rels_part])
        chain_targets = []
        for rel in list(rels.findall(q("Relationship", NS_PKG_REL))):
            if rel.get("Type") == REL_CALC_CHAIN:
                chain_targets.append(resolve_target(self._wb_part, rel.get("Target", "")))
                rels.remove(rel)
        if chain_targets:
            self.parts[rels_part] = etree.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)
            for t in chain_targets:
                if t in self.parts:
                    self.removed.add(t)
            ct = parse_xml(self.parts["[Content_Types].xml"])
            for ov in list(ct.findall(q("Override", NS_CT))):
                if ov.get("PartName", "").lstrip("/") in chain_targets:
                    ct.remove(ov)
            self.parts["[Content_Types].xml"] = etree.tostring(ct, xml_declaration=True, encoding="UTF-8", standalone=True)

    # ------------------------------------------------------------------ write
    def write(self, dest: Path) -> Path:
        dest = Path(dest)
        if dest.resolve() == self.src.resolve():
            raise PatchError("refusing to write into the source file (the original is never modified)")
        lock = dest.parent / f"~${dest.name}"
        if lock.exists():
            raise WorkbookLockedError(f"{dest.name} is open in Excel (lock file present); close it and retry")
        self._full_calc_and_drop_chain()
        touched = {"[Content_Types].xml", self._rels_part(self._wb_part)}
        for doc in self._docs.values():
            if doc.dirty:
                self.parts[doc.part] = etree.tostring(doc.root, xml_declaration=True, encoding="UTF-8", standalone=True)
                touched.add(doc.part)
        if self._wb_dirty:
            self.parts[self._wb_part] = etree.tostring(self._wb_root, xml_declaration=True, encoding="UTF-8", standalone=True)
            touched.add(self._wb_part)
        touched |= self.extra_touched
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".fscheck-", suffix=".xlsx", dir=str(dest.parent))
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            with zipfile.ZipFile(tmp, "w") as zout:
                for info in self.infos:
                    if info.filename in self.removed:
                        continue
                    zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    zi.external_attr = info.external_attr
                    zout.writestr(zi, self.parts[info.filename])
            errors = validate_package(tmp, only=touched)
            if errors:
                raise PatchError("XML validation failed: " + "; ".join(errors[:5]))
            with tmp.open("rb") as fh:
                os.fsync(fh.fileno())
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                tmp.unlink()
        return dest


_CELL_R = re.compile(r"^([A-Z]{1,3})(\d+)$")


def _attrs(el: etree._Element) -> dict[str, str]:
    return {str(k): str(v) for k, v in el.attrib.items()}


def validate_package(path: Path, only: set[str] | None = None) -> list[str]:
    """Strict validation of every XML part (+ structural checks on worksheets).

    ``only`` restricts the parse to the given parts (the rest were copied byte for byte).
    """
    errors: list[str] = []
    strict = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=True, recover=False)
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        return [f"not a zip package: {exc}"]
    with z:
        names = z.namelist()
        if "[Content_Types].xml" not in names:
            errors.append("missing [Content_Types].xml")
        for name in names:
            if not name.endswith((".xml", ".rels")):
                continue
            if only is not None and name not in only:
                continue
            data = z.read(name)
            try:
                root = etree.fromstring(data, parser=strict)
            except etree.XMLSyntaxError as exc:
                errors.append(f"{name}: {exc}")
                continue
            if root.tag == q("worksheet"):
                errors.extend(_check_sheet_structure(name, root))
    return errors


def _check_sheet_structure(name: str, root: etree._Element) -> list[str]:
    errs: list[str] = []
    sd = root.find(q("sheetData"))
    if sd is None:
        return errs
    last_row = 0
    for row in sd.findall(_ROW):
        r = int(row.get("r", "0") or 0)
        if r and r <= last_row:
            errs.append(f"{name}: row {r} out of order")
        last_row = r or last_row
        last_col = 0
        for c in row.findall(_C):
            ref = c.get("r", "")
            m = _CELL_R.match(ref)
            if not m:
                if ref:
                    errs.append(f"{name}: bad cell ref {ref}")
                continue
            if int(m.group(2)) != r:
                errs.append(f"{name}: cell {ref} in row {r}")
            col = col_to_index(m.group(1))
            if col <= last_col:
                errs.append(f"{name}: cell {ref} out of order")
            last_col = col
            f = c.find(_F)
            if f is not None and f.get("t") not in ("shared",) and not (f.text or "").strip() and f.get("t") != "dataTable":
                errs.append(f"{name}: empty formula in {ref}")
            kids = [etree.QName(k).localname for k in c]
            if "f" in kids and "v" in kids and kids.index("f") > kids.index("v"):
                errs.append(f"{name}: <f> after <v> in {ref}")
    return errs


__all__ = ["NS_MAIN", "PatchError", "WorkbookLockedError", "XlsxPatcher", "number_text", "validate_package"]
