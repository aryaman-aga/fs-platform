"""Formula rewrites used by the fix engine (02 §7.2).

* totals-only rewrite: a failing total adds the *displayed* components —
  single reference ``X`` → ``ROUND(X,d)``; a range inside SUM →
  ``SUMPRODUCT(ROUND(range,d))`` (not ``SUM(ROUND())``, which needs array entry
  in older Excel); ranges containing text are split into numeric runs and
  ranges with mixed display decimals are split by decimals; signs kept; the
  typed plug dropped; quoted sheet names preserved;
* plug removal on links (``'Note'!G23-0.01`` → ``'Note'!G23``) by AST surgery.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from fscore.checks.base import CheckContext
from fscore.engine import formula as F
from fscore.engine.display import format_info
from fscore.engine.linear import LinearForm, Term
from fscore.engine.refs import make_addr, quote_sheet


@dataclass
class Run:
    sheet: str
    r1: int
    c1: int
    r2: int
    c2: int
    decimals: int


def _ref_text(term: Term, run: Run, own_sheet: str, quote_from: str) -> str:
    a = make_addr(run.r1, run.c1)
    b = make_addr(run.r2, run.c2)
    body = a if (run.r1, run.c1) == (run.r2, run.c2) else f"{a}:{b}"
    # keep the original absolute markers when the run is the whole original term
    if (run.r1, run.c1, run.r2, run.c2) == (term.area.r1, term.area.c1, term.area.r2, term.area.c2):
        return term.text
    if run.sheet != own_sheet or "!" in quote_from:
        return f"{quote_sheet(run.sheet)}!{body}"
    return body


def _runs(ctx: CheckContext, term: Term) -> list[Run]:
    """Split a term's area into numeric runs of equal display decimals (text cells excluded)."""
    sh = ctx.wb.get_sheet(term.sheet)
    a = term.area
    if sh is None:
        return []
    if a.is_cell:
        cell0 = sh.cell(a.r1, a.c1)
        d0 = format_info(sh.number_format(cell0)).value_decimals if cell0 is not None else 2
        return [Run(term.sheet, a.r1, a.c1, a.r2, a.c2, d0)]
    vertical = a.c1 == a.c2
    horizontal = a.r1 == a.r2
    cells: list[tuple[int, int]] = []
    if vertical or horizontal:
        cells = [(r, c) for r, c in a.cells()]
    else:  # 2-D: row by row
        cells = [(r, c) for r, c in a.cells()]
    runs: list[Run] = []
    cur: Run | None = None
    for r, c in cells:
        cell = sh.cell(r, c)
        numeric_or_blank = cell is None or cell.value is None or cell.is_number
        if cell is not None and cell.formula is not None and not cell.is_number and cell.value not in (None, ""):
            numeric_or_blank = False
        if not numeric_or_blank:
            cur = None
            continue
        d = format_info(sh.number_format(cell)).value_decimals if cell is not None and cell.value is not None else (cur.decimals if cur else 2)
        contiguous = cur is not None and (
            (vertical and r == cur.r2 + 1 and c == cur.c1) or (horizontal and c == cur.c2 + 1 and r == cur.r1)
        )
        if cur is not None and contiguous and d == cur.decimals:
            cur.r2, cur.c2 = r, c
        else:
            if not (vertical or horizontal) and cur is not None and r == cur.r1 and c == cur.c2 + 1 and d == cur.decimals:
                cur.c2 = c
                continue
            cur = Run(term.sheet, r, c, r, c, d)
            runs.append(cur)
    # drop runs made only of blanks
    out = []
    for run in runs:
        has_num = any((sh.cell(r, c) is not None and sh.cell(r, c).is_number) for r in range(run.r1, run.r2 + 1) for c in range(run.c1, run.c2 + 1))  # type: ignore[union-attr]
        if has_num:
            out.append(run)
    return out


def totals_rewrite(ctx: CheckContext, own_sheet: str, lf: LinearForm) -> str | None:
    """Formula text that adds the displayed components of ``lf`` (plug dropped)."""
    parts: list[str] = []
    for term in lf.terms:
        sign = "-" if term.coef < 0 else "+"
        for run in _runs(ctx, term):
            ref = _ref_text(term, run, own_sheet, term.text)
            d = run.decimals
            if (run.r1, run.c1) == (run.r2, run.c2):
                expr = f"ROUND({ref},{d})"
            else:
                expr = f"SUMPRODUCT(ROUND({ref},{d}))"
            parts.append(sign + expr)
    if not parts:
        return None
    text = "".join(parts)
    return text[1:] if text.startswith("+") else text


def _is_const(node: F.Node) -> bool:
    if isinstance(node, F.Num):
        return True
    if isinstance(node, (F.Unary, F.Percent)):
        return _is_const(node.operand)
    if isinstance(node, F.Binary) and node.op in "+-*/^":
        return _is_const(node.left) and _is_const(node.right)
    return False


def _strip(node: F.Node) -> F.Node | None:
    """Remove additive constant terms from the top-level sum of an expression."""
    if _is_const(node):
        return None
    if isinstance(node, F.Binary) and node.op in "+-":
        left = _strip(node.left)
        right = _strip(node.right)
        if left is None and right is None:
            return None
        if right is None:
            return left
        if left is None:
            return right if node.op == "+" else F.Unary("-", right)
        return F.Binary(node.op, left, right)
    if isinstance(node, F.Func) and node.name == "SUM":
        args = [a for a in node.args if not _is_const(a)]
        return F.Func("SUM", args) if args else None
    if isinstance(node, F.Func) and node.name == "ROUND" and len(node.args) == 2:
        inner = _strip(node.args[0])
        return None if inner is None else F.Func("ROUND", [inner, node.args[1]])
    return node


def remove_plug(formula: str) -> str | None:
    """Formula text with typed additive constants removed (None if nothing is left)."""
    try:
        node = F.parse(formula)
    except F.FormulaError:
        return None
    stripped = _strip(node)
    if stripped is None:
        return None
    return F.to_text(stripped)


def add_adjustment(formula: str | None, value: object, adj: Decimal) -> str:
    """Visible balancing adjustment: '=<original>+0.02' (C3)."""
    a = format(adj.normalize(), "f") if adj != 0 else "0"
    sign = "-" if adj < 0 else "+"
    mag = a.lstrip("-")
    if formula:
        base = formula
        try:
            node = F.parse(formula)
            if isinstance(node, F.Binary) and node.op in ("&", "=", "<>", "<", ">", "<=", ">="):
                base = f"({formula})"
        except F.FormulaError:
            base = f"({formula})"
        return f"{base}{sign}{mag}"
    base_v = value if isinstance(value, (int, float)) else 0
    return f"{_num(float(base_v))}{sign}{mag}"


def _num(v: float) -> str:
    from fscore.engine.display import to_decimal15

    d = to_decimal15(v)
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def strip_adjustment(formula: str) -> tuple[str, Decimal]:
    """Split '<base>+0.02' into (base, 0.02). Returns (formula, 0) when there is no trailing constant."""
    try:
        node = F.parse(formula)
    except F.FormulaError:
        return formula, Decimal(0)
    if isinstance(node, F.Binary) and node.op in "+-" and _is_const(node.right):
        from fscore.engine.linear import _const

        c = _const(node.right)
        if c is not None:
            base = F.to_text(node.left)
            return base, (c if node.op == "+" else -c)
    return formula, Decimal(0)
