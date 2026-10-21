"""Excel formula tokenizer, parser (AST) and reference translator.

The tokenizer keeps the original text of every token, so a formula can be
re-assembled byte-for-byte after shifting references (shared formulas, 02 §7.4)
or after rewriting one term (fix engine).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from fscore.engine.refs import MAX_COL, MAX_ROW, col_to_index, index_to_col

ERRORS = ("#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#GETTING_DATA", "#SPILL!", "#CALC!")


class ExcelError(str):
    """An Excel error value (#REF!, #DIV/0!, …)."""

    __slots__ = ()


class FormulaError(ValueError):
    pass


# --------------------------------------------------------------------------- tokens


@dataclass(slots=True)
class Tok:
    type: str  # NUM STR BOOL ERR REF NAME FUNC OP PCT LP RP COMMA LB RB SEMI WS COLON
    text: str
    # parsed reference parts for REF tokens
    sheet: str | None = None
    sheet_text: str = ""  # original prefix including '!' (e.g. "'My Sheet'!")
    ref_text: str = ""


_SHEET_QUOTED = re.compile(r"'((?:[^']|'')+)'!")
_SHEET_PLAIN = re.compile(r"((?:\[\d+\])?[A-Za-z_À-￿][\w.À-￿]*(?::[A-Za-z_][\w.]*)?)!")
_CELL = r"\$?[A-Za-z]{1,3}\$?\d+"
_COLR = r"\$?[A-Za-z]{1,3}"
_ROWR = r"\$?\d+"
_REF_BODY = re.compile(rf"(?:{_CELL}(?::{_CELL})?|{_COLR}:{_COLR}|{_ROWR}:{_ROWR}|#REF!)(?![\w(])", re.I)
_NUM = re.compile(r"(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_NAME = re.compile(r"[A-Za-z_\\À-￿][\w.\\?À-￿]*")
_OPS2 = ("<>", "<=", ">=")


def tokenize(formula: str) -> list[Tok]:
    """Tokenize a formula (with or without the leading '=')."""
    s = formula[1:] if formula.startswith("=") else formula
    toks: list[Tok] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch in " \t\r\n":
            j = i
            while j < n and s[j] in " \t\r\n":
                j += 1
            toks.append(Tok("WS", s[i:j]))
            i = j
            continue
        if ch == '"':
            j = i + 1
            while j < n:
                if s[j] == '"':
                    if j + 1 < n and s[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            toks.append(Tok("STR", s[i : j + 1]))
            i = j + 1
            continue
        if ch == "#":
            for e in ERRORS:
                if s.startswith(e, i) or s[i : i + len(e)].upper() == e:
                    toks.append(Tok("ERR", s[i : i + len(e)]))
                    i += len(e)
                    break
            else:
                raise FormulaError(f"bad error literal at {i}: {s[i:i+10]!r}")
            continue
        # sheet-qualified reference
        m = _SHEET_QUOTED.match(s, i) or _SHEET_PLAIN.match(s, i)
        if m:
            m2 = _REF_BODY.match(s, m.end())
            if m2:
                raw_sheet = m.group(1)
                sheet = raw_sheet.replace("''", "'") if m.re is _SHEET_QUOTED else raw_sheet
                toks.append(Tok("REF", s[i : m2.end()], sheet=sheet, sheet_text=s[i : m.end()], ref_text=m2.group(0)))
                i = m2.end()
                continue
            # sheet-qualified defined name (Sheet1!MyName)
            m3 = _NAME.match(s, m.end())
            if m3:
                toks.append(Tok("NAME", s[i : m3.end()], sheet=m.group(1), sheet_text=s[i : m.end()], ref_text=m3.group(0)))
                i = m3.end()
                continue
        if ch.isdigit() or (ch == "." and i + 1 < n and s[i + 1].isdigit()) or ch == "$" or ch.isalpha() or ch == "_" or ch == "\\" or ord(ch) > 0xBF:
            # a reference like A1, $B$2:C9, A:A, 1:3 (but not a function name such as LOG10( )
            m2 = _REF_BODY.match(s, i)
            if m2 and not (i + len(m2.group(0)) < n and s[i + len(m2.group(0))] == "("):
                body = m2.group(0)
                # a pure number followed by ':' digits is a row range; a bare number is NUM
                if not re.fullmatch(r"\d+(\.\d*)?", body):
                    toks.append(Tok("REF", body, sheet=None, ref_text=body))
                    i = m2.end()
                    continue
            if ch.isdigit() or ch == ".":
                m3 = _NUM.match(s, i)
                if m3:
                    toks.append(Tok("NUM", m3.group(0)))
                    i = m3.end()
                    continue
            m4 = _NAME.match(s, i)
            if m4:
                name = m4.group(0)
                j = m4.end()
                if j < n and s[j] == "(":
                    toks.append(Tok("FUNC", name))
                    i = j  # the LP is emitted next
                    continue
                if name.upper() in ("TRUE", "FALSE"):
                    toks.append(Tok("BOOL", name))
                else:
                    toks.append(Tok("NAME", name))
                i = j
                continue
        if ch == "(":
            toks.append(Tok("LP", ch))
        elif ch == ")":
            toks.append(Tok("RP", ch))
        elif ch == ",":
            toks.append(Tok("COMMA", ch))
        elif ch == ";":
            toks.append(Tok("SEMI", ch))
        elif ch == "{":
            toks.append(Tok("LB", ch))
        elif ch == "}":
            toks.append(Tok("RB", ch))
        elif ch == "%":
            toks.append(Tok("PCT", ch))
        elif ch == ":":
            toks.append(Tok("COLON", ch))
        elif s.startswith(_OPS2, i):
            toks.append(Tok("OP", s[i : i + 2]))
            i += 2
            continue
        elif ch in "+-*/^&=<>":
            toks.append(Tok("OP", ch))
        elif ch == "@":  # implicit intersection marker in dynamic-array Excel
            pass
        else:
            raise FormulaError(f"unexpected character {ch!r} at {i} in {formula!r}")
        i += 1
    return toks


def detokenize(toks: list[Tok]) -> str:
    return "".join(t.text for t in toks)


# --------------------------------------------------------------------------- references


@dataclass(frozen=True, slots=True)
class RefParts:
    r1: int
    c1: int
    r2: int
    c2: int
    abs_r1: bool = False
    abs_c1: bool = False
    abs_r2: bool = False
    abs_c2: bool = False
    kind: str = "cell"  # cell, range, cols, rows, error

    @property
    def is_cell(self) -> bool:
        return self.kind == "cell"


_CELL_PARTS = re.compile(r"(\$?)([A-Za-z]{1,3})(\$?)(\d+)")


def parse_ref_text(ref: str) -> RefParts:
    if ref.upper() == "#REF!":
        return RefParts(0, 0, 0, 0, kind="error")
    if ":" in ref:
        a, b = ref.split(":", 1)
        ma, mb = _CELL_PARTS.fullmatch(a), _CELL_PARTS.fullmatch(b)
        if ma and mb:
            r1, c1 = int(ma.group(4)), col_to_index(ma.group(2))
            r2, c2 = int(mb.group(4)), col_to_index(mb.group(2))
            return RefParts(
                min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2),
                bool(ma.group(3)), bool(ma.group(1)), bool(mb.group(3)), bool(mb.group(1)), "range",
            )
        if re.fullmatch(r"\$?[A-Za-z]{1,3}", a) and re.fullmatch(r"\$?[A-Za-z]{1,3}", b):
            c1, c2 = col_to_index(a.lstrip("$")), col_to_index(b.lstrip("$"))
            return RefParts(1, min(c1, c2), MAX_ROW, max(c1, c2), False, a.startswith("$"), False, b.startswith("$"), "cols")
        if re.fullmatch(r"\$?\d+", a) and re.fullmatch(r"\$?\d+", b):
            r1, r2 = int(a.lstrip("$")), int(b.lstrip("$"))
            return RefParts(min(r1, r2), 1, max(r1, r2), MAX_COL, a.startswith("$"), False, b.startswith("$"), False, "rows")
        raise FormulaError(f"bad range {ref!r}")
    m = _CELL_PARTS.fullmatch(ref)
    if not m:
        raise FormulaError(f"bad reference {ref!r}")
    r, c = int(m.group(4)), col_to_index(m.group(2))
    return RefParts(r, c, r, c, bool(m.group(3)), bool(m.group(1)), bool(m.group(3)), bool(m.group(1)), "cell")


def _shift_cell(text: str, drow: int, dcol: int) -> str:
    m = _CELL_PARTS.fullmatch(text)
    if not m:
        return text
    cabs, col, rabs, row = m.groups()
    c = col_to_index(col)
    r = int(row)
    if not cabs:
        c += dcol
    if not rabs:
        r += drow
    if c < 1 or r < 1 or c > MAX_COL or r > MAX_ROW:
        return "#REF!"
    return f"{cabs}{index_to_col(c)}{rabs}{r}"


def shift_ref_text(ref: str, drow: int, dcol: int) -> str:
    """Shift relative parts of a reference body by (drow, dcol)."""
    if ref.upper() == "#REF!":
        return ref
    if ":" in ref:
        a, b = ref.split(":", 1)
        if _CELL_PARTS.fullmatch(a):
            sa, sb = _shift_cell(a, drow, dcol), _shift_cell(b, drow, dcol)
            return "#REF!" if "#REF!" in (sa, sb) else f"{sa}:{sb}"
        if re.fullmatch(r"\$?[A-Za-z]{1,3}", a):
            def sc(x: str) -> str:
                if x.startswith("$"):
                    return x
                c = col_to_index(x) + dcol
                return index_to_col(c) if 1 <= c <= MAX_COL else "#REF!"

            sa, sb = sc(a), sc(b)
            return "#REF!" if "#REF!" in (sa, sb) else f"{sa}:{sb}"

        def sr(x: str) -> str:
            if x.startswith("$"):
                return x
            r = int(x) + drow
            return str(r) if 1 <= r <= MAX_ROW else "#REF!"

        sa, sb = sr(a), sr(b)
        return "#REF!" if "#REF!" in (sa, sb) else f"{sa}:{sb}"
    return _shift_cell(ref, drow, dcol)


def translate(formula: str, drow: int, dcol: int) -> str:
    """Translate a (shared) formula from its master cell to a cell offset by (drow, dcol)."""
    if drow == 0 and dcol == 0:
        return formula
    toks = tokenize(formula)
    out: list[str] = []
    for t in toks:
        if t.type == "REF":
            out.append(t.sheet_text + shift_ref_text(t.ref_text, drow, dcol))
        else:
            out.append(t.text)
    return "".join(out)


# --------------------------------------------------------------------------- AST


@dataclass(slots=True)
class Num:
    value: float
    text: str = ""


@dataclass(slots=True)
class Str:
    value: str


@dataclass(slots=True)
class Bool:
    value: bool


@dataclass(slots=True)
class Err:
    code: str


@dataclass(slots=True)
class Ref:
    sheet: str | None  # None = same sheet as the formula
    parts: RefParts
    text: str  # original text including any sheet prefix

    @property
    def is_cell(self) -> bool:
        return self.parts.kind == "cell"


@dataclass(slots=True)
class Name:
    name: str
    sheet: str | None = None


@dataclass(slots=True)
class Func:
    name: str
    args: list[Node] = field(default_factory=list)


@dataclass(slots=True)
class Unary:
    op: str
    operand: Node


@dataclass(slots=True)
class Binary:
    op: str
    left: Node
    right: Node


@dataclass(slots=True)
class Percent:
    operand: Node


@dataclass(slots=True)
class Array:
    rows: list[list[Node]]


@dataclass(slots=True)
class Missing:
    pass


Node = Num | Str | Bool | Err | Ref | Name | Func | Unary | Binary | Percent | Array | Missing

_BIN_PREC = {
    "=": 1, "<>": 1, "<": 1, ">": 1, "<=": 1, ">=": 1,
    "&": 2,
    "+": 3, "-": 3,
    "*": 4, "/": 4,
    "^": 5,
}
_UNARY_PREC = 6  # unary minus binds tighter than ^ in Excel (-2^2 = 4)


class _Parser:
    def __init__(self, toks: list[Tok]) -> None:
        self.toks = [t for t in toks if t.type != "WS"]
        self.i = 0

    def peek(self) -> Tok | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> Tok:
        t = self.peek()
        if t is None:
            raise FormulaError("unexpected end of formula")
        self.i += 1
        return t

    def expect(self, typ: str) -> Tok:
        t = self.take()
        if t.type != typ:
            raise FormulaError(f"expected {typ}, got {t.type} {t.text!r}")
        return t

    def parse(self) -> Node:
        if not self.toks:
            return Missing()
        node = self.expr(0)
        if self.peek() is not None:
            raise FormulaError(f"unexpected token {self.peek().text!r}")  # type: ignore[union-attr]
        return node

    def expr(self, min_prec: int) -> Node:
        left = self.unary()
        while True:
            t = self.peek()
            if t is None or t.type != "OP" or t.text not in _BIN_PREC:
                break
            prec = _BIN_PREC[t.text]
            if prec < min_prec:
                break
            self.take()
            # ^ is left-associative in Excel
            right = self.expr(prec + 1)
            left = Binary(t.text, left, right)
        return left

    def unary(self) -> Node:
        t = self.peek()
        if t is not None and t.type == "OP" and t.text in "+-":
            self.take()
            operand = self.unary()
            return Unary(t.text, operand)
        node = self.postfix()
        return node

    def postfix(self) -> Node:
        node = self.primary()
        while True:
            t = self.peek()
            if t is not None and t.type == "PCT":
                self.take()
                node = Percent(node)
                continue
            if t is not None and t.type == "COLON":  # dynamic range A1:INDEX(...) — keep as function
                self.take()
                right = self.primary()
                node = Func(":RANGE", [node, right])
                continue
            break
        return node

    def primary(self) -> Node:
        t = self.take()
        if t.type == "NUM":
            return Num(float(t.text), t.text)
        if t.type == "STR":
            return Str(t.text[1:-1].replace('""', '"'))
        if t.type == "BOOL":
            return Bool(t.text.upper() == "TRUE")
        if t.type == "ERR":
            return Err(t.text.upper())
        if t.type == "REF":
            return Ref(t.sheet, parse_ref_text(t.ref_text), t.text)
        if t.type == "NAME":
            return Name(t.ref_text or t.text, t.sheet)
        if t.type == "FUNC":
            self.expect("LP")
            args: list[Node] = []
            nt = self.peek()
            if nt is not None and nt.type == "RP":
                self.take()
                return Func(t.text.upper().removeprefix("_XLFN.").removeprefix("_XLWS."), args)
            while True:
                nt = self.peek()
                if nt is not None and nt.type in ("COMMA", "RP"):
                    args.append(Missing())
                else:
                    args.append(self.expr(0))
                sep = self.take()
                if sep.type == "RP":
                    break
                if sep.type != "COMMA":
                    raise FormulaError(f"expected , or ) got {sep.text!r}")
            return Func(t.text.upper().removeprefix("_XLFN.").removeprefix("_XLWS."), args)
        if t.type == "LP":
            node = self.expr(0)
            # union inside parentheses: (A1,B1)
            if self.peek() is not None and self.peek().type == "COMMA":  # type: ignore[union-attr]
                items = [node]
                while self.peek() is not None and self.peek().type == "COMMA":  # type: ignore[union-attr]
                    self.take()
                    items.append(self.expr(0))
                self.expect("RP")
                return Func(":UNION", items)
            self.expect("RP")
            return node
        if t.type == "LB":
            rows: list[list[Node]] = [[]]
            while True:
                nt = self.peek()
                if nt is not None and nt.type == "RB":
                    self.take()
                    break
                rows[-1].append(self.unary())
                sep = self.take()
                if sep.type == "RB":
                    break
                if sep.type == "SEMI":
                    rows.append([])
            return Array(rows)
        if t.type == "OP" and t.text in "+-":
            return Unary(t.text, self.unary())
        raise FormulaError(f"unexpected token {t.text!r}")


def parse(formula: str) -> Node:
    return _Parser(tokenize(formula)).parse()


def to_text(node: Node) -> str:
    """Render an AST back to formula text (without '='). Used for generated formulas."""
    if isinstance(node, Num):
        return node.text or repr(node.value)
    if isinstance(node, Str):
        return '"' + node.value.replace('"', '""') + '"'
    if isinstance(node, Bool):
        return "TRUE" if node.value else "FALSE"
    if isinstance(node, Err):
        return node.code
    if isinstance(node, Ref):
        return node.text
    if isinstance(node, Name):
        return node.name
    if isinstance(node, Func):
        if node.name == ":RANGE":
            return f"{to_text(node.args[0])}:{to_text(node.args[1])}"
        if node.name == ":UNION":
            return "(" + ",".join(to_text(a) for a in node.args) + ")"
        return f"{node.name}(" + ",".join("" if isinstance(a, Missing) else to_text(a) for a in node.args) + ")"
    if isinstance(node, Unary):
        return node.op + to_text(node.operand)
    if isinstance(node, Binary):
        return f"{_wrap(node.left, node.op, False)}{node.op}{_wrap(node.right, node.op, True)}"
    if isinstance(node, Percent):
        return to_text(node.operand) + "%"
    if isinstance(node, Array):
        return "{" + ";".join(",".join(to_text(x) for x in row) for row in node.rows) + "}"
    return ""


def _wrap(child: Node, parent_op: str, right: bool) -> str:
    if isinstance(child, Binary):
        cp, pp = _BIN_PREC[child.op], _BIN_PREC[parent_op]
        if cp < pp or (right and cp == pp and parent_op in "-/^&"):
            return "(" + to_text(child) + ")"
    return to_text(child)


def references(node: Node) -> list[Ref]:
    """All Ref nodes in an AST (depth first)."""
    out: list[Ref] = []
    stack: list[Node] = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, Ref):
            out.append(n)
        elif isinstance(n, Func):
            stack.extend(reversed(n.args))
        elif isinstance(n, Binary):
            stack.append(n.right)
            stack.append(n.left)
        elif isinstance(n, (Unary, Percent)):
            stack.append(n.operand)
        elif isinstance(n, Array):
            for row in n.rows:
                stack.extend(row)
    return out


def function_names(node: Node) -> set[str]:
    out: set[str] = set()
    stack: list[Node] = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, Func):
            out.add(n.name)
            stack.extend(n.args)
        elif isinstance(n, Binary):
            stack.extend([n.left, n.right])
        elif isinstance(n, (Unary, Percent)):
            stack.append(n.operand)
    return out
