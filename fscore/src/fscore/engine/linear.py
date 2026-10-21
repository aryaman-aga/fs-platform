"""Linear-form extraction: formula -> Σ coef × reference + constant.

Totals are recognised as linear combinations with unit coefficients of cells
or ranges (SUM, +, -, ROUND wrappers). The constant term exposes typed
rounding plugs (PLUG-01/02). ``SUMPRODUCT(ROUND(range,d))`` — the form the
fix engine writes — is linear in ``range`` so re-checks stay idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from fscore.engine import formula as F
from fscore.engine.refs import Area


@dataclass(frozen=True, slots=True)
class Term:
    coef: Decimal
    sheet: str
    area: Area
    text: str  # original reference text (keeps quoting and $)
    rounded: int | None = None  # ROUND(x, d) wrapper digits, if any

    @property
    def is_cell(self) -> bool:
        return self.area.is_cell


@dataclass(slots=True)
class LinearForm:
    terms: list[Term] = field(default_factory=list)
    constant: Decimal = Decimal(0)
    rounded_digits: int | None = None  # outer ROUND(…, d)
    has_sum: bool = False

    @property
    def unit(self) -> bool:
        return all(t.coef in (Decimal(1), Decimal(-1)) for t in self.terms)

    @property
    def n_cells(self) -> int:
        return sum(t.area.size for t in self.terms)

    @property
    def is_total(self) -> bool:
        """A casting candidate: unit coefficients over two or more cells (or a SUM)."""
        return bool(self.terms) and self.unit and (len(self.terms) >= 2 or self.has_sum or self.n_cells >= 2)

    @property
    def is_link(self) -> bool:
        return len(self.terms) == 1 and self.terms[0].is_cell and self.terms[0].coef == 1 and not self.has_sum

    @property
    def is_scaled_link(self) -> bool:
        return len(self.terms) == 1 and self.terms[0].is_cell and self.terms[0].coef not in (Decimal(1), Decimal(-1))

    def scaled(self, k: Decimal) -> LinearForm:
        return LinearForm(
            [Term(t.coef * k, t.sheet, t.area, t.text, t.rounded) for t in self.terms],
            self.constant * k,
            None,
            self.has_sum,
        )

    def add(self, other: LinearForm, sign: int = 1) -> LinearForm:
        s = Decimal(sign)
        return LinearForm(
            self.terms + [Term(t.coef * s, t.sheet, t.area, t.text, t.rounded) for t in other.terms],
            self.constant + other.constant * s,
            None,
            self.has_sum or other.has_sum,
        )


def _const(node: F.Node) -> Decimal | None:
    if isinstance(node, F.Num):
        return Decimal(node.text) if node.text else Decimal(repr(node.value))
    if isinstance(node, F.Unary):
        c = _const(node.operand)
        return None if c is None else (-c if node.op == "-" else c)
    if isinstance(node, F.Percent):
        c = _const(node.operand)
        return None if c is None else c / 100
    if isinstance(node, F.Binary) and node.op in "+-*/^":
        a, b = _const(node.left), _const(node.right)
        if a is None or b is None:
            return None
        if node.op == "+":
            return a + b
        if node.op == "-":
            return a - b
        if node.op == "*":
            return a * b
        if node.op == "/":
            return None if b == 0 else a / b
        if node.op == "^":
            try:
                return a ** int(b) if b == b.to_integral_value() else None
            except (ArithmeticError, ValueError):
                return None
    return None


def _ref_term(node: F.Ref, sheet: str, allow_range: bool) -> Term | None:
    p = node.parts
    if p.kind == "error":
        return None
    if p.kind != "cell" and not allow_range:
        return None
    if p.kind in ("cols", "rows"):
        return None  # whole columns/rows are never totals of a printed block
    sh = node.sheet or sheet
    return Term(Decimal(1), sh, Area(sh, p.r1, p.c1, p.r2, p.c2), node.text)


def linear_form(node: F.Node, sheet: str, *, _in_sum: bool = False) -> LinearForm | None:
    """Return the linear form of a formula AST, or None if it is not linear."""
    c = _const(node)
    if c is not None:
        return LinearForm([], c)
    if isinstance(node, F.Ref):
        t = _ref_term(node, sheet, allow_range=_in_sum)
        return None if t is None else LinearForm([t], Decimal(0))
    if isinstance(node, F.Unary):
        inner = linear_form(node.operand, sheet, _in_sum=_in_sum)
        if inner is None:
            return None
        return inner.scaled(Decimal(-1)) if node.op == "-" else inner
    if isinstance(node, F.Percent):
        inner = linear_form(node.operand, sheet, _in_sum=_in_sum)
        return None if inner is None else inner.scaled(Decimal("0.01"))
    if isinstance(node, F.Binary):
        if node.op in "+-":
            a = linear_form(node.left, sheet, _in_sum=_in_sum)
            b = linear_form(node.right, sheet, _in_sum=_in_sum)
            if a is None or b is None:
                return None
            return a.add(b, 1 if node.op == "+" else -1)
        if node.op == "*":
            ka, kb = _const(node.left), _const(node.right)
            if ka is not None:
                inner = linear_form(node.right, sheet, _in_sum=_in_sum)
                return None if inner is None else inner.scaled(ka)
            if kb is not None:
                inner = linear_form(node.left, sheet, _in_sum=_in_sum)
                return None if inner is None else inner.scaled(kb)
            return None
        if node.op == "/":
            kb = _const(node.right)
            if kb is None or kb == 0:
                return None
            inner = linear_form(node.left, sheet, _in_sum=_in_sum)
            return None if inner is None else inner.scaled(Decimal(1) / kb)
        return None
    if isinstance(node, F.Func):
        name = node.name
        if name == "SUM":
            out = LinearForm([], Decimal(0), has_sum=True)
            for arg in node.args:
                if isinstance(arg, F.Missing):
                    continue
                part = linear_form(arg, sheet, _in_sum=True)
                if part is None:
                    return None
                out = out.add(part)
            out.has_sum = True
            return out
        if name == "ROUND" and len(node.args) == 2:
            d = _const(node.args[1])
            inner = linear_form(node.args[0], sheet, _in_sum=_in_sum)
            if inner is None or d is None:
                return None
            if len(inner.terms) == 1 and not inner.constant and inner.terms[0].coef == 1:
                t = inner.terms[0]
                inner = LinearForm([Term(t.coef, t.sheet, t.area, t.text, int(d))], Decimal(0), None, inner.has_sum)
            inner.rounded_digits = int(d)
            return inner
        if name == "SUMPRODUCT" and len(node.args) == 1:
            arg = node.args[0]
            if isinstance(arg, F.Func) and arg.name == "ROUND" and len(arg.args) == 2 and isinstance(arg.args[0], F.Ref):
                d = _const(arg.args[1])
                t = _ref_term(arg.args[0], sheet, allow_range=True)
                if t is None or d is None:
                    return None
                return LinearForm([Term(t.coef, t.sheet, t.area, t.text, int(d))], Decimal(0), has_sum=True)
            if isinstance(arg, F.Ref):
                t = _ref_term(arg, sheet, allow_range=True)
                return None if t is None else LinearForm([t], Decimal(0), has_sum=True)
            return None
    return None


def linear_form_text(formula: str, sheet: str) -> LinearForm | None:
    try:
        return linear_form(F.parse(formula), sheet)
    except F.FormulaError:
        return None


def constant_in_formula(lf: LinearForm) -> Decimal:
    return lf.constant
