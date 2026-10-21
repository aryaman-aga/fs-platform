"""Formula evaluator for the subset of Excel used in print sheets (02 §4).

Unsupported functions raise ``Unsupported``; callers then fall back to Excel's
cached value and log the function name (never the cell contents).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_UP, ROUND_UP, Decimal
from typing import Any

from fscore.engine import formula as F
from fscore.engine.display import compile_format, serial_to_datetime, to_decimal15
from fscore.engine.formula import ExcelError
from fscore.engine.refs import MAX_COL, MAX_ROW, Area, parse_area

DIV0 = ExcelError("#DIV/0!")
VALUE = ExcelError("#VALUE!")
REF = ExcelError("#REF!")
NA = ExcelError("#N/A")
NAME = ExcelError("#NAME?")
NUM = ExcelError("#NUM!")

CellGetter = Callable[[str, int, int], Any]


class Unsupported(Exception):
    def __init__(self, what: str) -> None:
        super().__init__(what)
        self.what = what


@dataclass
class RangeVal:
    sheet: str
    area: Area
    getter: CellGetter
    bound_rows: int
    bound_cols: int

    def iter_values(self) -> Iterator[Any]:
        a = self.area
        r2 = min(a.r2, self.bound_rows)
        c2 = min(a.c2, self.bound_cols)
        for r in range(a.r1, r2 + 1):
            for c in range(a.c1, c2 + 1):
                yield self.getter(self.sheet, r, c)

    def rows(self) -> list[list[Any]]:
        a = self.area
        r2 = min(a.r2, self.bound_rows)
        c2 = min(a.c2, self.bound_cols)
        return [[self.getter(self.sheet, r, c) for c in range(a.c1, c2 + 1)] for r in range(a.r1, r2 + 1)]

    @property
    def shape(self) -> tuple[int, int]:
        a = self.area
        return (min(a.r2, self.bound_rows) - a.r1 + 1, min(a.c2, self.bound_cols) - a.c1 + 1)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def to_number(v: Any, *, strict: bool = False) -> float | ExcelError:
    """Coerce a scalar to a number the way Excel arithmetic does."""
    if isinstance(v, ExcelError):
        return v
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if _is_num(v):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if s == "":
            return VALUE if strict else 0.0
        neg = s.startswith("(") and s.endswith(")")
        if neg:
            s = s[1:-1]
        pct = s.endswith("%")
        if pct:
            s = s[:-1]
        try:
            x = float(s)
        except ValueError:
            return VALUE
        x = -x if neg else x
        return x / 100 if pct else x
    return VALUE


def to_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if _is_num(v):
        d = to_decimal15(v)
        if d == d.to_integral_value():
            return str(int(d))
        s = format(d, "f")
        return s.rstrip("0").rstrip(".") if "." in s else s
    return str(v)


def to_bool(v: Any) -> bool | ExcelError:
    if isinstance(v, ExcelError):
        return v
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if _is_num(v):
        return float(v) != 0
    if isinstance(v, str):
        if v.upper() == "TRUE":
            return True
        if v.upper() == "FALSE":
            return False
        return VALUE
    return VALUE


def excel_round_float(x: float, digits: int, mode: str = ROUND_HALF_UP) -> float:
    d = to_decimal15(x)
    q = Decimal(1).scaleb(-int(digits))
    return float(d.quantize(q, rounding=mode))


def _flatten(args: list[Any], *, direct_coerce: bool = True) -> Iterator[Any]:
    """Values of function arguments: ranges yield numbers only; direct args are coerced."""
    for a in args:
        if isinstance(a, RangeVal):
            for v in a.iter_values():
                if isinstance(v, ExcelError):
                    yield v
                elif _is_num(v):
                    yield float(v)
        elif isinstance(a, list):  # array constant
            for row in a:
                for v in row:
                    if _is_num(v):
                        yield float(v)
        else:
            if a is None:
                continue
            if direct_coerce:
                n = to_number(a)
                yield n
            elif _is_num(a):
                yield float(a)


def _first_error(vals: list[Any]) -> ExcelError | None:
    for v in vals:
        if isinstance(v, ExcelError):
            return v
    return None


def _date_serial(d: date | datetime) -> float:
    base = datetime(1899, 12, 30)
    dt = d if isinstance(d, datetime) else datetime(d.year, d.month, d.day)
    return (dt - base).total_seconds() / 86400.0


def _crit_match(crit: Any) -> Callable[[Any], bool]:
    """SUMIF/COUNTIF criteria."""
    if _is_num(crit):
        target = float(crit)
        return lambda v: _is_num(v) and float(v) == target
    s = "" if crit is None else str(crit)
    m = re.match(r"^(<=|>=|<>|<|>|=)?(.*)$", s, re.S)
    op = (m.group(1) if m else None) or "="
    rhs = m.group(2) if m else s
    num = to_number(rhs)
    if not isinstance(num, ExcelError) and rhs.strip() != "":
        x = float(num)
        ops: dict[str, Callable[[float], bool]] = {
            "=": lambda v: v == x, "<>": lambda v: v != x, "<": lambda v: v < x,
            ">": lambda v: v > x, "<=": lambda v: v <= x, ">=": lambda v: v >= x,
        }
        fn = ops[op]
        if op == "<>":
            return lambda v: not (_is_num(v) and float(v) == x)
        return lambda v: _is_num(v) and fn(float(v))
    pattern = "^" + re.escape(rhs.lower()).replace(r"\*", ".*").replace(r"\?", ".") + "$"
    rx = re.compile(pattern, re.S)
    if op == "<>":
        return lambda v: not rx.match(to_text(v).lower())
    return lambda v: bool(rx.match(to_text(v).lower()))


class Evaluator:
    """Evaluates formulas against a cell getter (cached values + overrides)."""

    def __init__(self, getter: CellGetter, *, name_lookup: Callable[[str, str], str | None] | None = None,
                 bounds: Callable[[str], tuple[int, int]] | None = None, date1904: bool = False) -> None:
        self.getter = getter
        self.name_lookup = name_lookup
        self.bounds = bounds or (lambda _s: (MAX_ROW, MAX_COL))
        self.date1904 = date1904
        self._parse_cache: dict[str, F.Node] = {}
        self.unsupported: set[str] = set()

    # ------------------------------------------------------------------ API
    def parse(self, formula: str) -> F.Node:
        node = self._parse_cache.get(formula)
        if node is None:
            node = F.parse(formula)
            if len(self._parse_cache) < 200_000:
                self._parse_cache[formula] = node
        return node

    def evaluate(self, formula: str, sheet: str, row: int = 0, col: int = 0) -> Any:
        node = self.parse(formula)
        v = self.eval(node, sheet, row, col)
        if isinstance(v, RangeVal):
            v = self._implicit(v, row, col)
        if isinstance(v, list):
            v = v[0][0] if v and v[0] else None
        if v is None:
            return 0.0
        return v

    # ------------------------------------------------------------------ core
    def _implicit(self, rv: RangeVal, row: int, col: int) -> Any:
        a = rv.area
        if a.is_cell:
            return self.getter(rv.sheet, a.r1, a.c1)
        if a.c1 == a.c2 and a.r1 <= row <= a.r2:
            return self.getter(rv.sheet, row, a.c1)
        if a.r1 == a.r2 and a.c1 <= col <= a.c2:
            return self.getter(rv.sheet, a.r1, col)
        return VALUE

    def ref_value(self, node: F.Ref, sheet: str) -> Any:
        sh = node.sheet or sheet
        p = node.parts
        if p.kind == "error":
            return REF
        if p.kind == "cell":
            return self.getter(sh, p.r1, p.c1)
        br, bc = self.bounds(sh)
        return RangeVal(sh, Area(sh, p.r1, p.c1, p.r2, p.c2), self.getter, br, bc)

    def eval(self, node: F.Node, sheet: str, row: int = 0, col: int = 0) -> Any:
        if isinstance(node, F.Num):
            return node.value
        if isinstance(node, F.Str):
            return node.value
        if isinstance(node, F.Bool):
            return node.value
        if isinstance(node, F.Err):
            return ExcelError(node.code)
        if isinstance(node, F.Ref):
            return self.ref_value(node, sheet)
        if isinstance(node, F.Missing):
            return None
        if isinstance(node, F.Name):
            return self._name(node, sheet, row, col)
        if isinstance(node, F.Unary):
            v = self._scalar(self.eval(node.operand, sheet, row, col), row, col)
            n = to_number(v)
            if isinstance(n, ExcelError):
                return n
            return -n if node.op == "-" else n
        if isinstance(node, F.Percent):
            v = self._scalar(self.eval(node.operand, sheet, row, col), row, col)
            n = to_number(v)
            return n if isinstance(n, ExcelError) else n / 100.0
        if isinstance(node, F.Binary):
            return self._binary(node, sheet, row, col)
        if isinstance(node, F.Array):
            return [[self._scalar(self.eval(x, sheet, row, col), row, col) for x in r] for r in node.rows]
        if isinstance(node, F.Func):
            return self._func(node, sheet, row, col)
        raise Unsupported(type(node).__name__)

    def _scalar(self, v: Any, row: int, col: int) -> Any:
        if isinstance(v, RangeVal):
            return self._implicit(v, row, col)
        if isinstance(v, list):
            return v[0][0] if v and v[0] else None
        return v

    def _name(self, node: F.Name, sheet: str, row: int, col: int) -> Any:
        if self.name_lookup is None:
            raise Unsupported(f"name:{node.name}")
        text = self.name_lookup(node.name, node.sheet or sheet)
        if text is None:
            return NAME
        if text.startswith("="):
            text = text[1:]
        try:
            sub = self.parse(text)
        except F.FormulaError as exc:
            raise Unsupported(f"name:{node.name}") from exc
        return self.eval(sub, sheet, row, col)

    def _binary(self, node: F.Binary, sheet: str, row: int, col: int) -> Any:
        a = self._scalar(self.eval(node.left, sheet, row, col), row, col)
        b = self._scalar(self.eval(node.right, sheet, row, col), row, col)
        op = node.op
        if op == "&":
            if isinstance(a, ExcelError):
                return a
            if isinstance(b, ExcelError):
                return b
            return to_text(a) + to_text(b)
        if op in ("=", "<>", "<", ">", "<=", ">="):
            if isinstance(a, ExcelError):
                return a
            if isinstance(b, ExcelError):
                return b
            return _compare(a, b, op)
        x = to_number(a)
        if isinstance(x, ExcelError):
            return x
        y = to_number(b)
        if isinstance(y, ExcelError):
            return y
        if op == "+":
            return x + y
        if op == "-":
            return x - y
        if op == "*":
            return x * y
        if op == "/":
            return DIV0 if y == 0 else x / y
        if op == "^":
            try:
                r = x**y
            except (OverflowError, ZeroDivisionError):
                return NUM
            if isinstance(r, complex):
                return NUM
            return float(r)
        raise Unsupported(f"op:{op}")

    # ------------------------------------------------------------------ functions
    def _func(self, node: F.Func, sheet: str, row: int, col: int) -> Any:
        name = node.name
        # lazy functions first
        if name == "IF":
            cond = self._scalar(self.eval(node.args[0], sheet, row, col), row, col)
            b = to_bool(cond)
            if isinstance(b, ExcelError):
                return b
            if b:
                return self.eval(node.args[1], sheet, row, col) if len(node.args) > 1 else True
            return self.eval(node.args[2], sheet, row, col) if len(node.args) > 2 else False
        if name in ("IFERROR", "IFNA"):
            v = self._scalar(self.eval(node.args[0], sheet, row, col), row, col)
            if isinstance(v, ExcelError) and (name == "IFERROR" or v == "#N/A"):
                return self.eval(node.args[1], sheet, row, col)
            return v
        if name == "CHOOSE":
            idx = to_number(self._scalar(self.eval(node.args[0], sheet, row, col), row, col))
            if isinstance(idx, ExcelError):
                return idx
            i = int(idx)
            if i < 1 or i >= len(node.args):
                return VALUE
            return self.eval(node.args[i], sheet, row, col)
        if name == ":RANGE":
            a = self.eval(node.args[0], sheet, row, col)
            b = self.eval(node.args[1], sheet, row, col)
            if isinstance(a, RangeVal) and isinstance(b, RangeVal) and a.sheet == b.sheet:
                ar = Area(a.sheet, min(a.area.r1, b.area.r1), min(a.area.c1, b.area.c1), max(a.area.r2, b.area.r2), max(a.area.c2, b.area.c2))
                return RangeVal(a.sheet, ar, self.getter, a.bound_rows, a.bound_cols)
            raise Unsupported("range-op")
        args = [self.eval(a, sheet, row, col) for a in node.args]
        fn = _FUNCS.get(name)
        if fn is None:
            if name in ("OFFSET", "INDIRECT", "ROW", "COLUMN", "ROWS", "COLUMNS"):
                return self._ref_func(name, node, args, sheet, row, col)
            self.unsupported.add(name)
            raise Unsupported(name)
        return fn(self, args, row, col)

    def _ref_func(self, name: str, node: F.Func, args: list[Any], sheet: str, row: int, col: int) -> Any:
        if name == "ROW":
            if args and isinstance(args[0], RangeVal):
                return float(args[0].area.r1)
            if node.args and isinstance(node.args[0], F.Ref):
                return float(node.args[0].parts.r1)
            return float(row)
        if name == "COLUMN":
            if args and isinstance(args[0], RangeVal):
                return float(args[0].area.c1)
            if node.args and isinstance(node.args[0], F.Ref):
                return float(node.args[0].parts.c1)
            return float(col)
        if name in ("ROWS", "COLUMNS"):
            a = args[0]
            if isinstance(a, RangeVal):
                ar = a.area
                return float(ar.r2 - ar.r1 + 1) if name == "ROWS" else float(ar.c2 - ar.c1 + 1)
            if node.args and isinstance(node.args[0], F.Ref):
                p = node.args[0].parts
                return float(p.r2 - p.r1 + 1) if name == "ROWS" else float(p.c2 - p.c1 + 1)
            return 1.0
        if name == "OFFSET":
            base = node.args[0]
            if not isinstance(base, F.Ref):
                raise Unsupported("OFFSET")
            p = base.parts
            dr = int(to_number(self._scalar(args[1], row, col)) or 0)
            dc = int(to_number(self._scalar(args[2], row, col)) or 0)
            h = int(to_number(self._scalar(args[3], row, col))) if len(args) > 3 and args[3] is not None else p.r2 - p.r1 + 1
            w = int(to_number(self._scalar(args[4], row, col))) if len(args) > 4 and args[4] is not None else p.c2 - p.c1 + 1
            sh = base.sheet or sheet
            ar = Area(sh, p.r1 + dr, p.c1 + dc, p.r1 + dr + h - 1, p.c1 + dc + w - 1)
            if ar.r1 < 1 or ar.c1 < 1:
                return REF
            if ar.is_cell:
                return self.getter(sh, ar.r1, ar.c1)
            br, bc = self.bounds(sh)
            return RangeVal(sh, ar, self.getter, br, bc)
        if name == "INDIRECT":
            text = to_text(self._scalar(args[0], row, col))
            try:
                ar = parse_area(text, sheet)
            except ValueError:
                return REF
            if ar.is_cell:
                return self.getter(ar.sheet, ar.r1, ar.c1)
            br, bc = self.bounds(ar.sheet)
            return RangeVal(ar.sheet, ar, self.getter, br, bc)
        raise Unsupported(name)


def _compare(a: Any, b: Any, op: str) -> bool:
    def rank(v: Any) -> int:
        if v is None:
            return 0
        if _is_num(v):
            return 1
        if isinstance(v, str):
            return 2
        return 3  # bool

    if a is None:
        a = "" if isinstance(b, str) else (False if isinstance(b, bool) else 0.0)
    if b is None:
        b = "" if isinstance(a, str) else (False if isinstance(a, bool) else 0.0)
    ra, rb = rank(a), rank(b)
    if ra != rb:
        x, y = ra, rb
    elif isinstance(a, str):
        x, y = a.lower(), b.lower()  # type: ignore[assignment]
    elif _is_num(a):
        x, y = float(to_decimal15(a)), float(to_decimal15(b))  # type: ignore[assignment]
    else:
        x, y = a, b
    return {
        "=": x == y, "<>": x != y, "<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y,
    }[op]


# ---------------------------------------------------------------------------- library

FuncImpl = Callable[[Evaluator, list[Any], int, int], Any]
_FUNCS: dict[str, FuncImpl] = {}


def _reg(*names: str) -> Callable[[FuncImpl], FuncImpl]:
    def deco(fn: FuncImpl) -> FuncImpl:
        for n in names:
            _FUNCS[n] = fn
        return fn

    return deco


def _s(ev: Evaluator, v: Any, row: int, col: int) -> Any:
    return ev._scalar(v, row, col)


def _n(ev: Evaluator, v: Any, row: int, col: int) -> float | ExcelError:
    return to_number(_s(ev, v, row, col))


@_reg("SUM")
def _sum(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    vals = list(_flatten(args))
    err = _first_error(vals)
    return err if err else float(sum(vals))


@_reg("PRODUCT")
def _product(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    vals = list(_flatten(args))
    err = _first_error(vals)
    if err:
        return err
    return float(math.prod(vals)) if vals else 0.0


@_reg("SUMPRODUCT")
def _sumproduct(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    arrays: list[list[Any]] = []
    for a in args:
        if isinstance(a, RangeVal):
            arrays.append([v for r in a.rows() for v in r])
        elif isinstance(a, list):
            arrays.append([v for r in a for v in r])
        else:
            arrays.append([a])
    if not arrays:
        return 0.0
    n = len(arrays[0])
    if any(len(x) != n for x in arrays):
        return VALUE
    total = 0.0
    for i in range(n):
        p = 1.0
        for arr in arrays:
            v = arr[i]
            if isinstance(v, ExcelError):
                return v
            p *= float(v) if _is_num(v) else 0.0
        total += p
    return total


def _array_map(ev: Evaluator, a: Any, row: int, col: int, fn: Callable[[float], Any]) -> Any:
    """Element-wise application used for ROUND(range, d) inside SUMPRODUCT."""
    if isinstance(a, RangeVal):
        out = []
        for r in a.rows():
            out.append([v if isinstance(v, ExcelError) else (fn(float(v)) if _is_num(v) else (fn(0.0) if v is None else v)) for v in r])
        return out
    if isinstance(a, list):
        return [[fn(float(v)) if _is_num(v) else v for v in r] for r in a]
    n = to_number(a)
    return n if isinstance(n, ExcelError) else fn(n)


def _round_impl(mode: str) -> FuncImpl:
    def impl(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
        d = _n(ev, args[1], row, col) if len(args) > 1 else 0.0
        if isinstance(d, ExcelError):
            return d
        digits = int(d)
        return _array_map(ev, args[0], row, col, lambda x: excel_round_float(x, digits, mode))

    return impl


_FUNCS["ROUND"] = _round_impl(ROUND_HALF_UP)
_FUNCS["ROUNDUP"] = _round_impl(ROUND_UP)
_FUNCS["ROUNDDOWN"] = _round_impl(ROUND_DOWN)


@_reg("TRUNC")
def _trunc(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    d = int(_n(ev, args[1], row, col)) if len(args) > 1 else 0
    return _array_map(ev, args[0], row, col, lambda x: excel_round_float(x, d, ROUND_DOWN))


@_reg("INT")
def _int(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    return _array_map(ev, args[0], row, col, lambda x: float(math.floor(x)))


@_reg("ABS")
def _abs(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    return _array_map(ev, args[0], row, col, abs)


@_reg("SIGN")
def _sign(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    return _array_map(ev, args[0], row, col, lambda x: float((x > 0) - (x < 0)))


@_reg("SQRT")
def _sqrt(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    x = _n(ev, args[0], row, col)
    if isinstance(x, ExcelError):
        return x
    return NUM if x < 0 else math.sqrt(x)


@_reg("POWER")
def _power(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    x, y = _n(ev, args[0], row, col), _n(ev, args[1], row, col)
    if isinstance(x, ExcelError):
        return x
    if isinstance(y, ExcelError):
        return y
    try:
        return float(x**y)
    except (OverflowError, ZeroDivisionError):
        return NUM


@_reg("MOD")
def _mod(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    x, y = _n(ev, args[0], row, col), _n(ev, args[1], row, col)
    if isinstance(x, ExcelError):
        return x
    if isinstance(y, ExcelError):
        return y
    if y == 0:
        return DIV0
    return x - y * math.floor(x / y)


@_reg("FLOOR", "FLOOR.MATH")
def _floor(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    x = _n(ev, args[0], row, col)
    s = _n(ev, args[1], row, col) if len(args) > 1 else 1.0
    if isinstance(x, ExcelError) or isinstance(s, ExcelError):
        return VALUE
    if s == 0:
        return 0.0
    return float((to_decimal15(x) / to_decimal15(s)).to_integral_value(rounding=ROUND_FLOOR) * to_decimal15(s))


@_reg("CEILING", "CEILING.MATH")
def _ceiling(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    x = _n(ev, args[0], row, col)
    s = _n(ev, args[1], row, col) if len(args) > 1 else 1.0
    if isinstance(x, ExcelError) or isinstance(s, ExcelError):
        return VALUE
    if s == 0:
        return 0.0
    return float((to_decimal15(x) / to_decimal15(s)).to_integral_value(rounding=ROUND_CEILING) * to_decimal15(s))


def _agg(fn: Callable[[list[float]], float], empty: float = 0.0) -> FuncImpl:
    def impl(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
        vals = list(_flatten(args))
        err = _first_error(vals)
        if err:
            return err
        return fn(vals) if vals else empty

    return impl


_FUNCS["MIN"] = _agg(min)
_FUNCS["MAX"] = _agg(max)


@_reg("AVERAGE")
def _average(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    vals = list(_flatten(args))
    err = _first_error(vals)
    if err:
        return err
    return DIV0 if not vals else sum(vals) / len(vals)


@_reg("COUNT")
def _count(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    n = 0
    for a in args:
        if isinstance(a, RangeVal):
            n += sum(1 for v in a.iter_values() if _is_num(v))
        elif _is_num(a):
            n += 1
    return float(n)


@_reg("COUNTA")
def _counta(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    n = 0
    for a in args:
        if isinstance(a, RangeVal):
            n += sum(1 for v in a.iter_values() if v is not None and v != "")
        elif a is not None:
            n += 1
    return float(n)


@_reg("COUNTBLANK")
def _countblank(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    a = args[0]
    if isinstance(a, RangeVal):
        return float(sum(1 for v in a.iter_values() if v is None or v == ""))
    return 0.0


@_reg("SUBTOTAL", "AGGREGATE")
def _subtotal(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    code = int(_n(ev, args[0], row, col))
    rest = args[1:]
    base = code % 100
    table: dict[int, FuncImpl] = {1: _average, 2: _count, 3: _counta, 4: _FUNCS["MAX"], 5: _FUNCS["MIN"], 6: _product, 9: _sum}
    fn = table.get(base)
    if fn is None:
        raise Unsupported(f"SUBTOTAL({code})")
    if code >= 100:
        raise Unsupported("SUBTOTAL(1xx) visible-only")  # needs hidden-row info: fall back to cache
    return fn(ev, rest, row, col)


def _pairs(ev: Evaluator, args: list[Any]) -> list[tuple[RangeVal, Callable[[Any], bool]]]:
    out = []
    for i in range(0, len(args) - 1, 2):
        rng = args[i]
        if not isinstance(rng, RangeVal):
            raise Unsupported("criteria range")
        out.append((rng, _crit_match(ev._scalar(args[i + 1], 0, 0))))
    return out


@_reg("SUMIF")
def _sumif(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    rng = args[0]
    if not isinstance(rng, RangeVal):
        raise Unsupported("SUMIF range")
    pred = _crit_match(_s(ev, args[1], row, col))
    sum_rng = args[2] if len(args) > 2 else rng
    if not isinstance(sum_rng, RangeVal):
        raise Unsupported("SUMIF sum range")
    crit_vals = [v for r in rng.rows() for v in r]
    a = sum_rng.area
    sr = RangeVal(sum_rng.sheet, Area(a.sheet, a.r1, a.c1, a.r1 + rng.shape[0] - 1, a.c1 + rng.shape[1] - 1), sum_rng.getter, sum_rng.bound_rows, sum_rng.bound_cols)
    sum_vals = [v for r in sr.rows() for v in r]
    total = 0.0
    for cv, sv in zip(crit_vals, sum_vals, strict=False):
        if pred(cv) and _is_num(sv):
            total += float(sv)
    return total


@_reg("SUMIFS")
def _sumifs(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    sum_rng = args[0]
    if not isinstance(sum_rng, RangeVal):
        raise Unsupported("SUMIFS")
    pairs = _pairs(ev, args[1:])
    sums = [v for r in sum_rng.rows() for v in r]
    crits = [([v for r in rng.rows() for v in r], pred) for rng, pred in pairs]
    total = 0.0
    for i, sv in enumerate(sums):
        if all(i < len(vals) and pred(vals[i]) for vals, pred in crits) and _is_num(sv):
            total += float(sv)
    return total


@_reg("COUNTIF")
def _countif(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    rng = args[0]
    if not isinstance(rng, RangeVal):
        raise Unsupported("COUNTIF")
    pred = _crit_match(_s(ev, args[1], row, col))
    return float(sum(1 for v in rng.iter_values() if pred(v)))


@_reg("COUNTIFS")
def _countifs(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    pairs = _pairs(ev, args)
    crits = [([v for r in rng.rows() for v in r], pred) for rng, pred in pairs]
    n = len(crits[0][0]) if crits else 0
    return float(sum(1 for i in range(n) if all(pred(vals[i]) for vals, pred in crits)))


@_reg("AND")
def _and(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    res = True
    for a in args:
        vals = list(a.iter_values()) if isinstance(a, RangeVal) else [a]
        for v in vals:
            if v is None:
                continue
            b = to_bool(v)
            if isinstance(b, ExcelError):
                return b
            res = res and b
    return res


@_reg("OR")
def _or(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    res = False
    for a in args:
        vals = list(a.iter_values()) if isinstance(a, RangeVal) else [a]
        for v in vals:
            if v is None:
                continue
            b = to_bool(v)
            if isinstance(b, ExcelError):
                return b
            res = res or b
    return res


@_reg("NOT")
def _not(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    b = to_bool(_s(ev, args[0], row, col))
    return b if isinstance(b, ExcelError) else (not b)


@_reg("TRUE")
def _true(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    return True


@_reg("FALSE")
def _false(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    return False


@_reg("TEXT")
def _text(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    v = _s(ev, args[0], row, col)
    fmt = to_text(_s(ev, args[1], row, col))
    if isinstance(v, ExcelError):
        return v
    if isinstance(v, str):
        n = to_number(v)
        if isinstance(n, ExcelError):
            return v
        v = n
    return compile_format(fmt).render(v, date1904=ev.date1904).text


@_reg("CONCATENATE", "CONCAT")
def _concat(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    out = []
    for a in args:
        vals = list(a.iter_values()) if isinstance(a, RangeVal) else [a]
        for v in vals:
            if isinstance(v, ExcelError):
                return v
            out.append(to_text(v))
    return "".join(out)


@_reg("TEXTJOIN")
def _textjoin(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    delim = to_text(_s(ev, args[0], row, col))
    skip = to_bool(_s(ev, args[1], row, col))
    parts = []
    for a in args[2:]:
        vals = list(a.iter_values()) if isinstance(a, RangeVal) else [a]
        for v in vals:
            t = to_text(v)
            if skip and t == "":
                continue
            parts.append(t)
    return delim.join(parts)


def _str_fn(fn: Callable[..., Any]) -> FuncImpl:
    def impl(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
        vals = [_s(ev, a, row, col) for a in args]
        err = _first_error(vals)
        if err:
            return err
        return fn(*vals)

    return impl


_FUNCS["LEFT"] = _str_fn(lambda s, n=1: to_text(s)[: int(to_number(n))])
_FUNCS["RIGHT"] = _str_fn(lambda s, n=1: to_text(s)[-int(to_number(n)) :] if int(to_number(n)) else "")
_FUNCS["MID"] = _str_fn(lambda s, a, n: to_text(s)[int(to_number(a)) - 1 : int(to_number(a)) - 1 + int(to_number(n))])
_FUNCS["LEN"] = _str_fn(lambda s: float(len(to_text(s))))
_FUNCS["UPPER"] = _str_fn(lambda s: to_text(s).upper())
_FUNCS["LOWER"] = _str_fn(lambda s: to_text(s).lower())
_FUNCS["PROPER"] = _str_fn(lambda s: to_text(s).title())
_FUNCS["TRIM"] = _str_fn(lambda s: re.sub(r" +", " ", to_text(s).strip()))
_FUNCS["EXACT"] = _str_fn(lambda a, b: to_text(a) == to_text(b))
_FUNCS["REPT"] = _str_fn(lambda s, n: to_text(s) * int(to_number(n)))
_FUNCS["SUBSTITUTE"] = _str_fn(lambda s, old, new, inst=None: to_text(s).replace(to_text(old), to_text(new)) if inst is None else _subst_nth(to_text(s), to_text(old), to_text(new), int(to_number(inst))))
_FUNCS["VALUE"] = _str_fn(lambda s: to_number(s))
_FUNCS["N"] = _str_fn(lambda v: float(v) if _is_num(v) else (1.0 if v is True else 0.0))
_FUNCS["T"] = _str_fn(lambda v: v if isinstance(v, str) else "")
_FUNCS["ISBLANK"] = _str_fn(lambda v: v is None)
_FUNCS["ISNUMBER"] = _str_fn(lambda v: _is_num(v))
_FUNCS["ISTEXT"] = _str_fn(lambda v: isinstance(v, str))


def _subst_nth(s: str, old: str, new: str, n: int) -> str:
    idx = -1
    for _ in range(n):
        idx = s.find(old, idx + 1)
        if idx < 0:
            return s
    return s[:idx] + new + s[idx + len(old) :]


@_reg("FIND", "SEARCH")
def _find(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    needle = to_text(_s(ev, args[0], row, col))
    hay = to_text(_s(ev, args[1], row, col))
    start = int(_n(ev, args[2], row, col)) if len(args) > 2 else 1
    i = hay.lower().find(needle.lower(), start - 1)
    return VALUE if i < 0 else float(i + 1)


@_reg("ISERROR", "ISERR")
def _iserror(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    return isinstance(_s(ev, args[0], row, col), ExcelError)


@_reg("ISNA")
def _isna(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    v = _s(ev, args[0], row, col)
    return isinstance(v, ExcelError) and v == "#N/A"


@_reg("NA")
def _na(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    return NA


@_reg("DATE")
def _date(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    y, m, d = (_n(ev, a, row, col) for a in args[:3])
    for x in (y, m, d):
        if isinstance(x, ExcelError):
            return x
    yi, mi, di = int(y), int(m), int(d)
    if yi < 1900:
        yi += 1900
    yi += (mi - 1) // 12
    mi = (mi - 1) % 12 + 1
    try:
        base = date(yi, mi, 1)
    except ValueError:
        return NUM
    return _date_serial(base) + (di - 1)


def _dt(ev: Evaluator, v: Any, row: int, col: int) -> datetime | ExcelError:
    n = _n(ev, v, row, col)
    if isinstance(n, ExcelError):
        return n
    dt = serial_to_datetime(n, ev.date1904)
    return VALUE if dt is None else dt


@_reg("YEAR")
def _year(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    d = _dt(ev, args[0], row, col)
    return d if isinstance(d, ExcelError) else float(d.year)


@_reg("MONTH")
def _month(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    d = _dt(ev, args[0], row, col)
    return d if isinstance(d, ExcelError) else float(d.month)


@_reg("DAY")
def _day(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    d = _dt(ev, args[0], row, col)
    return d if isinstance(d, ExcelError) else float(d.day)


def _add_months(d: datetime, months: int) -> datetime:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    import calendar

    last = calendar.monthrange(y, m)[1]
    return datetime(y, m, min(d.day, last))


@_reg("EDATE")
def _edate(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    d = _dt(ev, args[0], row, col)
    n = _n(ev, args[1], row, col)
    if isinstance(d, ExcelError) or isinstance(n, ExcelError):
        return VALUE
    return float(int(_date_serial(_add_months(d, int(n)))))


@_reg("EOMONTH")
def _eomonth(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    d = _dt(ev, args[0], row, col)
    n = _n(ev, args[1], row, col)
    if isinstance(d, ExcelError) or isinstance(n, ExcelError):
        return VALUE
    first = _add_months(datetime(d.year, d.month, 1), int(n) + 1)
    return float(int(_date_serial(first - timedelta(days=1))))


def _lookup_impl(horizontal: bool) -> FuncImpl:
    def impl(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
        key = _s(ev, args[0], row, col)
        table = args[1]
        if not isinstance(table, RangeVal):
            raise Unsupported("LOOKUP table")
        idx = int(_n(ev, args[2], row, col))
        approx = True
        if len(args) > 3 and args[3] is not None:
            b = to_bool(_s(ev, args[3], row, col))
            approx = bool(b) if not isinstance(b, ExcelError) else True
        rows = table.rows()
        if horizontal:
            rows = [list(x) for x in zip(*rows, strict=False)]
        best = None
        for r in rows:
            k = r[0] if r else None
            if not approx:
                if _compare(k, key, "="):
                    return r[idx - 1] if 0 < idx <= len(r) else REF
            else:
                if k is not None and _compare(k, key, "<="):
                    best = r
                elif k is not None:
                    break
        if approx and best is not None:
            return best[idx - 1] if 0 < idx <= len(best) else REF
        return NA

    return impl


_FUNCS["VLOOKUP"] = _lookup_impl(False)
_FUNCS["HLOOKUP"] = _lookup_impl(True)


@_reg("INDEX")
def _index(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    a = args[0]
    r = int(_n(ev, args[1], row, col)) if len(args) > 1 and args[1] is not None else 0
    c = int(_n(ev, args[2], row, col)) if len(args) > 2 and args[2] is not None else 0
    if isinstance(a, RangeVal):
        ar = a.area
        h, _w = a.shape
        if h == 1 and c == 0:
            c, r = r, 1
        if r == 0 and c == 0:
            return a
        rr = ar.r1 + max(r, 1) - 1
        cc = ar.c1 + max(c, 1) - 1
        if rr > ar.r2 or cc > ar.c2:
            return REF
        return a.getter(a.sheet, rr, cc)
    if isinstance(a, list):
        try:
            return a[max(r, 1) - 1][max(c, 1) - 1]
        except IndexError:
            return REF
    return a


@_reg("MATCH")
def _match(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    key = _s(ev, args[0], row, col)
    rng = args[1]
    mode = int(_n(ev, args[2], row, col)) if len(args) > 2 and args[2] is not None else 1
    vals = list(rng.iter_values()) if isinstance(rng, RangeVal) else [v for r in rng for v in r] if isinstance(rng, list) else [rng]
    if mode == 0:
        pred = _crit_match(key) if isinstance(key, str) else (lambda v: _compare(v, key, "="))
        for i, v in enumerate(vals):
            if pred(v):
                return float(i + 1)
        return NA
    best = None
    for i, v in enumerate(vals):
        if v is None:
            continue
        if (mode == 1 and _compare(v, key, "<=")) or (mode == -1 and _compare(v, key, ">=")):
            best = i
        else:
            break
    return NA if best is None else float(best + 1)


@_reg("XLOOKUP")
def _xlookup(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    key = _s(ev, args[0], row, col)
    look, ret = args[1], args[2]
    if not isinstance(look, RangeVal) or not isinstance(ret, RangeVal):
        raise Unsupported("XLOOKUP")
    lv = list(look.iter_values())
    rv = list(ret.iter_values())
    for i, v in enumerate(lv):
        if _compare(v, key, "="):
            return rv[i] if i < len(rv) else NA
    return _s(ev, args[3], row, col) if len(args) > 3 and args[3] is not None else NA


@_reg("PI")
def _pi(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    return math.pi


@_reg("LN")
def _ln(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    x = _n(ev, args[0], row, col)
    if isinstance(x, ExcelError):
        return x
    return NUM if x <= 0 else math.log(x)


@_reg("EXP")
def _exp(ev: Evaluator, args: list[Any], row: int, col: int) -> Any:
    x = _n(ev, args[0], row, col)
    return x if isinstance(x, ExcelError) else math.exp(x)


SUPPORTED_FUNCTIONS = frozenset(_FUNCS) | {"IF", "IFERROR", "IFNA", "CHOOSE", "OFFSET", "INDIRECT", "ROW", "COLUMN", "ROWS", "COLUMNS"}


def values_equal(cached: Any, computed: Any) -> bool:
    """Self-test comparison: numbers at 15 significant digits (with a tiny absolute slack)."""
    if _is_num(cached) and _is_num(computed):
        a, b = float(cached), float(computed)
        if a == b:
            return True
        if abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b)):
            return True
        return to_decimal15(a) == to_decimal15(b)
    if isinstance(cached, ExcelError) or isinstance(computed, ExcelError):
        return str(cached) == str(computed)
    if isinstance(cached, bool) or isinstance(computed, bool):
        return bool(cached == computed)
    if cached is None:
        return computed in (None, "", 0.0)
    if isinstance(cached, str) or isinstance(computed, str):
        return to_text(cached) == to_text(computed)
    return bool(cached == computed)
