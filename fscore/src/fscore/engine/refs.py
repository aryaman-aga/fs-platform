"""A1-style reference helpers (columns, cells, ranges, sheet quoting)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache

MAX_ROW = 1_048_576
MAX_COL = 16_384

_CELL_RE = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)$")


@lru_cache(maxsize=4096)
def col_to_index(col: str) -> int:
    """'A' -> 1, 'Z' -> 26, 'AA' -> 27."""
    n = 0
    for ch in col.upper():
        if not "A" <= ch <= "Z":
            raise ValueError(f"bad column {col!r}")
        n = n * 26 + (ord(ch) - 64)
    return n


@lru_cache(maxsize=4096)
def index_to_col(idx: int) -> str:
    if idx < 1:
        raise ValueError(f"bad column index {idx}")
    out = ""
    while idx:
        idx, rem = divmod(idx - 1, 26)
        out = chr(65 + rem) + out
    return out


def split_addr(addr: str) -> tuple[int, int]:
    """'B12' (or '$B$12') -> (row=12, col=2)."""
    m = _CELL_RE.match(addr)
    if not m:
        raise ValueError(f"bad cell address {addr!r}")
    return int(m.group(2)), col_to_index(m.group(1))


def make_addr(row: int, col: int) -> str:
    return f"{index_to_col(col)}{row}"


def quote_sheet(name: str) -> str:
    """Quote a sheet name for use in a formula when needed ('My Sheet'!A1, 'O''Brien'!A1)."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name) and not re.fullmatch(r"[A-Za-z]{1,3}\d+", name):
        return name
    return "'" + name.replace("'", "''") + "'"


@dataclass(frozen=True, slots=True)
class Area:
    """A rectangular block on one sheet (inclusive bounds)."""

    sheet: str
    r1: int
    c1: int
    r2: int
    c2: int

    @property
    def is_cell(self) -> bool:
        return self.r1 == self.r2 and self.c1 == self.c2

    @property
    def size(self) -> int:
        return (self.r2 - self.r1 + 1) * (self.c2 - self.c1 + 1)

    def cells(self) -> Iterator[tuple[int, int]]:
        for r in range(self.r1, self.r2 + 1):
            for c in range(self.c1, self.c2 + 1):
                yield r, c

    def contains(self, row: int, col: int) -> bool:
        return self.r1 <= row <= self.r2 and self.c1 <= col <= self.c2

    def a1(self) -> str:
        a = make_addr(self.r1, self.c1)
        return a if self.is_cell else f"{a}:{make_addr(self.r2, self.c2)}"

    def __str__(self) -> str:
        return f"{quote_sheet(self.sheet)}!{self.a1()}"


def parse_area(text: str, default_sheet: str) -> Area:
    """Parse 'A1', 'A1:B5', 'Sheet!A1:B5', "'My Sheet'!$A$1" into an Area."""
    sheet = default_sheet
    ref = text
    if "!" in text:
        sheet_part, ref = text.rsplit("!", 1)
        if sheet_part.startswith("'") and sheet_part.endswith("'"):
            sheet = sheet_part[1:-1].replace("''", "'")
        else:
            sheet = sheet_part
    ref = ref.replace("$", "")
    if ":" in ref:
        a, b = ref.split(":", 1)
        if a.isdigit() and b.isdigit():  # whole rows 3:5
            return Area(sheet, int(a), 1, int(b), MAX_COL)
        if a.isalpha() and b.isalpha():  # whole columns A:C
            return Area(sheet, 1, col_to_index(a), MAX_ROW, col_to_index(b))
        r1, c1 = split_addr(a)
        r2, c2 = split_addr(b)
        return Area(sheet, min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2))
    r, c = split_addr(ref)
    return Area(sheet, r, c, r, c)


def parse_sqref(sqref: str, sheet: str) -> list[Area]:
    """Space-separated list of areas, as used by print areas / merge refs."""
    return [parse_area(part, sheet) for part in sqref.split() if part]
