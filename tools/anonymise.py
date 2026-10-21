"""Anonymise a real workbook for CI fixtures (build pack 10 §4).

    python tools/anonymise.py <in.xlsx> <out.xlsx> --names names.txt [--seed 7]

* replaces entity, person, DIN, CIN, PAN, GSTIN, membership-number, FRN and
  address strings with stable tokens (ENTITY_1, PERSON_2, DIN_A …);
* scales every amount-like numeric constant by one random factor, then
  recalculates the formulas so equalities, casting and ratios survive while the
  absolute values change;
* keeps formulas, formats, hidden rows/columns and print areas (surgical OOXML
  edits — never an openpyxl round trip).

The token map is written next to the output as ``<out>.map.json`` and must stay
on the firm's machine (it reverses the anonymisation).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from lxml import etree

from fscore.engine.calc import Recalculator
from fscore.engine.display import format_info, is_date_format
from fscore.engine.refs import make_addr
from fscore.engine.selftest import bounds, name_lookup
from fscore.io.ooxml import NS_MAIN, parse_xml, q
from fscore.io.ooxml_patch import XlsxPatcher
from fscore.io.xlsx_reader import Workbook

ID_PATTERNS = [
    ("CIN", re.compile(r"(?P<id>\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b)")),
    ("GSTIN", re.compile(r"(?P<id>\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z\d]Z[A-Z\d]\b)")),
    ("PAN", re.compile(r"(?P<id>\b[A-Z]{5}\d{4}[A-Z]\b)")),
    ("FRN", re.compile(r"(?P<id>\b\d{6}[A-Z]\b)")),
    ("DIN", re.compile(r"DIN[\s:.\-]*(?P<id>\d{8})\b", re.I)),
    ("MEMBERSHIP", re.compile(r"(?:M\.?\s?No\.?|Membership\s+No\.?)[\s:.\-]*(?P<id>\d{6})\b", re.I)),
]


class Tokeniser:
    def __init__(self, names: list[str]) -> None:
        self.map: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        self.names = sorted({n.strip() for n in names if n.strip()}, key=len, reverse=True)

    def token(self, kind: str, original: str) -> str:
        key = f"{kind}:{original}"
        if key not in self.map:
            self.counters[kind] = self.counters.get(kind, 0) + 1
            n = self.counters[kind]
            label = chr(64 + n) if kind in ("DIN", "CIN", "PAN", "FRN", "GSTIN") and n <= 26 else str(n)
            self.map[key] = f"{kind}_{label}"
        return self.map[key]

    def text(self, s: str) -> str:
        out = s
        for name in self.names:
            if name.lower() in out.lower():
                kind = "ENTITY" if re.search(r"\b(ltd|limited|llp|inc|pte|pty|gmbh|bv|fz|llc|plc|private)\b", name, re.I) else "PERSON"
                out = re.sub(re.escape(name), self.token(kind, name), out, flags=re.I)
        for kind, rx in ID_PATTERNS:
            out = rx.sub(lambda m, k=kind: m.string[m.start():m.start("id")] + self.token(k, m.group("id")) + m.string[m.end("id"):m.end()], out)
        return out


def _amount_like(fmt: str, value: float) -> bool:
    if is_date_format(fmt):
        return False
    info = format_info(fmt)
    if info.kind == "percent":
        return False
    # note numbers and years in General format are not amounts
    return not (float(value).is_integer() and abs(value) < 100 and info.kind == "general")


def anonymise(src: Path, dst: Path, names: list[str], seed: int = 7) -> dict[str, Any]:
    rng = random.Random(seed)
    factor = Decimal(str(round(rng.uniform(0.55, 1.85), 4)))
    tok = Tokeniser(names)
    patcher = XlsxPatcher(src)
    with Workbook.open(src) as wb:
        consts: dict[tuple[str, int, int], float] = {}
        formulas: dict[tuple[str, int, int], str] = {}
        for sh in wb.sheets:
            for c in sh.iter_cells():
                key = (sh.name, c.row, c.col)
                if c.formula is not None:
                    formulas[key] = c.formula
                elif c.is_number and _amount_like(sh.number_format(c), float(c.value)):
                    consts[key] = float(Decimal(str(c.value)) * factor)
                elif c.is_text:
                    new = tok.text(str(c.value))
                    if new != c.value:
                        patcher.set_text(sh.name, c.addr, new)

        def base(key: tuple[str, int, int]) -> Any:
            if key in consts:
                return consts[key]
            s = wb.get_sheet(key[0])
            cell = s.cell(key[1], key[2]) if s else None
            return None if cell is None else cell.value

        rc = Recalculator(formulas, base, name_lookup=name_lookup(wb), bounds=bounds(wb), date1904=wb.date1904,
                          fallback=lambda k: base(k))
        values = rc.compute_all()
        for (sname, r, c), v in consts.items():
            patcher.set_value(sname, make_addr(r, c), v)
        for (sname, r, c), v in values.items():
            patcher.refresh_cached(sname, make_addr(r, c), v)
    _anonymise_shared_strings(patcher, tok)
    patcher.write(dst)
    info = {"factor": str(factor), "tokens": tok.map}
    Path(str(dst) + ".map.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def _anonymise_shared_strings(patcher: XlsxPatcher, tok: Tokeniser) -> None:
    part = "xl/sharedStrings.xml"
    if part not in patcher.parts:
        return
    root = parse_xml(patcher.parts[part])
    for t in root.iter(q("t")):
        if t.text:
            t.text = tok.text(t.text)
    patcher.parts[part] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    patcher.extra_touched.add(part)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--names", type=Path, help="text file with one entity / person / party name per line")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    names = a.names.read_text(encoding="utf-8").splitlines() if a.names else []
    info = anonymise(a.src, a.dst, names, a.seed)
    print(f"written {a.dst} (scale factor {info['factor']}, {len(info['tokens'])} tokens); keep {a.dst}.map.json private")


if __name__ == "__main__":
    main()

__all__ = ["NS_MAIN", "anonymise", "zipfile"]
