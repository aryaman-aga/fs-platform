"""Schedule III ratios (02 §6.5): RATIO-01…03.

Every ratio is recomputed from the *displayed* statement figures with the
standard definition (and the common alternative definitions). The printed
ratio must match one of them within one unit of its last printed digit:

* RATIO-01 — matches no definition (e.g. an average divided by 2 twice);
* RATIO-02 — matches a definition that contradicts its caption / numerator text
  (ROCE on PBT while the caption says EBIT), or CY and PY use different
  definitions, or the two years are printed with different formats;
* RATIO-03 — |% change| > 25 % without a reason in the "Reason for variance" column.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from fscore.checks.base import CheckContext
from fscore.checks.xref import Loc, ev, has_number, line_loc, loc, unrounded
from fscore.engine.display import format_info
from fscore.engine.periods import parse_period, scope_key
from fscore.engine.statements import norm_caption
from fscore.io.xlsx_reader import Sheet
from fscore.models import Certainty, Severity
from fscore.registry import check

ZERO = Decimal(0)
TWO = Decimal(2)

KINDS: list[tuple[str, re.Pattern[str]]] = [
    ("dscr", re.compile(r"debt\s+service\s+coverage|\bdscr\b")),
    ("debt_equity", re.compile(r"debt[\s-]+(to[\s-]+)?equity|gearing")),
    ("current_ratio", re.compile(r"^current\s+ratio|\bcurrent\s+ratio\b")),
    ("roe", re.compile(r"return\s+on\s+(equity|net\s+worth)|\broe\b")),
    ("inventory_turnover", re.compile(r"inventor(y|ies)\s+turnover")),
    ("tr_turnover", re.compile(r"(trade\s+)?receivables?\s+turnover|debtors?\s+turnover")),
    ("tp_turnover", re.compile(r"(trade\s+)?payables?\s+turnover|creditors?\s+turnover")),
    ("nct", re.compile(r"net\s+capital\s+turnover|working\s+capital\s+turnover")),
    ("npr", re.compile(r"net\s+profit\s+(ratio|margin)")),
    ("roce", re.compile(r"return\s+on\s+capital\s+employed|\broce\b")),
    ("roi", re.compile(r"return\s+on\s+investment|\broi\b")),
]


@dataclass
class Variant:
    name: str
    value: Decimal
    standard: bool = True
    contradicts: str = ""  # caption keyword this variant would contradict ("ebit", "revenue")


@dataclass
class RatioRow:
    kind: str
    row: int
    caption: str
    numerator_text: str = ""
    cols: dict[str, int] = field(default_factory=dict)
    var_col: int | None = None
    reason_col: int | None = None


def _div(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None or b is None or b == 0:
        return None
    try:
        return a / b
    except InvalidOperation:
        return None


def _bs_sum(ctx: CheckContext, rx: re.Pattern[str], period: str, *, raw: bool = False) -> Decimal | None:
    """Sum of Balance Sheet rows whose caption matches (e.g. non-current + current borrowings)."""
    sname = ctx.statements.sheet("balance_sheet")
    if not sname:
        return None
    sh = ctx.wb.sheet(sname)
    col = ctx.statements.period_cols.get(sname, {}).get(period)
    if col is None:
        return None
    total, found = ZERO, False
    for r in range(1, sh.max_row + 1):
        cap = norm_caption(ctx.caption(sh, r))
        if cap and not cap.startswith("total") and rx.search(cap) and has_number(ctx, sh, r, col):
            v = unrounded(ctx, sh, r, col) if raw else ctx.displayed(sh, r, col)
            if v is not None:
                total += v
                found = True
    return total if found else None


def _cf_sum(ctx: CheckContext, rx: re.Pattern[str], period: str) -> Decimal | None:
    sname = ctx.statements.sheet("cash_flow")
    if not sname:
        return None
    sh = ctx.wb.sheet(sname)
    col = ctx.statements.period_cols.get(sname, {}).get(period)
    if col is None:
        return None
    total, found = ZERO, False
    for r in range(1, sh.max_row + 1):
        cap = norm_caption(ctx.caption(sh, r))
        if cap and rx.search(cap) and has_number(ctx, sh, r, col):
            v = ctx.displayed(sh, r, col)
            if v is not None:
                total += v
                found = True
    return total if found else None


class _Figures:
    """Statement figures for one period (with the previous period for averages).

    ``raw=False``: the printed figures (02 §6.5). ``raw=True``: the same figures
    without print rounding and without approved balancing adjustments, so a ratio
    typed from the unrounded books is not flagged for a paisa of rounding.
    """

    def __init__(self, ctx: CheckContext, *, raw: bool = False) -> None:
        self.ctx = ctx
        self.raw = raw

    def _v(self, x: Loc | None) -> Decimal | None:
        if x is None or x.value is None:
            return None
        return unrounded(self.ctx, x.sheet, x.row, x.col) if self.raw else x.value

    def line(self, key: str, p: str) -> Decimal | None:
        return self._v(line_loc(self.ctx, key, p))

    def kf(self, key: str, p: str) -> Decimal | None:
        if not self.raw:
            return self.ctx.key_figures.get(key, p)
        ref = self.ctx.key_figures.ref(key, p)
        if ref is None:
            if key in ("total_equity",):
                sc, oe = self.kf("share_capital", p), self.kf("other_equity", p)
                return None if sc is None or oe is None else sc + oe
            return self.ctx.key_figures.get(key, p)
        from fscore.engine.refs import split_addr

        r, c = split_addr(ref.addr)
        return unrounded(self.ctx, ref.sheet, r, c)

    def equity(self, p: str) -> Decimal | None:
        if p == "PY1":
            return self._soce_opening_equity()
        return self.kf("total_equity", p)

    def _soce_opening_equity(self) -> Decimal | None:
        sname = self.ctx.statements.sheet("soce")
        if not sname:
            return None
        sh = self.ctx.wb.sheet(sname)
        py_start = self.ctx.config.year_end.replace(year=self.ctx.config.year_end.year - 2)
        for r in range(1, sh.max_row + 1):
            lab = parse_period(self.ctx.caption(sh, r))
            if lab is None or lab.end is None or abs((lab.end - py_start).days) > 1:
                continue
            cols = [c for c in range(1, sh.max_col + 1) if has_number(self.ctx, sh, r, c)]
            if cols:
                return unrounded(self.ctx, sh, r, cols[-1]) if self.raw else self.ctx.displayed(sh, r, cols[-1])
        return None

    def debt(self, p: str, *, leases: bool) -> Decimal | None:
        b = _bs_sum(self.ctx, re.compile(r"^borrowings$"), p, raw=self.raw)
        if not leases:
            return b
        lease = _bs_sum(self.ctx, re.compile(r"^lease\s+liabilit"), p, raw=self.raw)
        if b is None and lease is None:
            return None
        return (b or ZERO) + (lease or ZERO)

    def trade_payables(self, p: str) -> Decimal | None:
        return _bs_sum(self.ctx, re.compile(r"outstanding\s+dues|^trade\s+payables$"), p, raw=self.raw)

    def wc(self, p: str) -> Decimal | None:
        ca, cl = self.kf("total_ca", p), self.kf("total_cl", p)
        return None if ca is None or cl is None else ca - cl


def _avg(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    return None if a is None or b is None else (a + b) / TWO


def _variants(ctx: CheckContext, f: _Figures, kind: str, p: str) -> list[Variant]:
    prev = {"CY": "PY", "PY": "PY1"}.get(p)
    out: list[Variant] = []

    def add(name: str, v: Decimal | None, *, standard: bool = True, contradicts: str = "") -> None:
        if v is not None:
            out.append(Variant(name, v, standard, contradicts))

    pat, rev, pbt, fin = f.line("pat", p), f.line("revenue", p), f.line("pbt", p), f.line("finance_costs", p)
    if kind == "current_ratio":
        add("current assets ÷ current liabilities", _div(f.kf("total_ca", p), f.kf("total_cl", p)))
    elif kind == "debt_equity":
        eq = f.equity(p)
        add("(borrowings + lease liabilities) ÷ equity", _div(f.debt(p, leases=True), eq))
        add("borrowings ÷ equity", _div(f.debt(p, leases=False), eq))
    elif kind == "roe":
        if prev:
            add("profit after tax ÷ average equity", _div(pat, _avg(f.equity(p), f.equity(prev))))
        add("profit after tax ÷ closing equity", _div(pat, f.equity(p)), standard=False)
    elif kind == "tr_turnover":
        tr = f.kf("trade_receivables", p)
        if prev:
            add("revenue ÷ average trade receivables", _div(rev, _avg(tr, f.kf("trade_receivables", prev))))
        add("revenue ÷ closing trade receivables", _div(rev, tr), standard=False)
    elif kind == "tp_turnover":
        tp = f.trade_payables(p)
        tp_prev = f.trade_payables(prev) if prev else None
        for label, num in (("other expenses", f.line("other_expenses", p)), ("total expenses", f.line("total_expenses", p))):
            if prev:
                add(f"{label} ÷ average trade payables", _div(num, _avg(tp, tp_prev)))
            add(f"{label} ÷ closing trade payables", _div(num, tp), standard=False)
    elif kind == "nct":
        if prev:
            add("revenue ÷ average working capital", _div(rev, _avg(f.wc(p), f.wc(prev))))
        add("revenue ÷ closing working capital", _div(rev, f.wc(p)), standard=False)
    elif kind == "npr":
        add("profit after tax ÷ revenue from operations", _div(pat, rev))
        add("profit after tax ÷ total income", _div(pat, f.line("total_income", p)), standard=False, contradicts="revenue")
    elif kind == "roce":
        ebit = None if pbt is None or fin is None else pbt + fin
        eq = f.equity(p)
        for ce_label, debt in (("equity + total debt", f.debt(p, leases=True)), ("equity + borrowings", f.debt(p, leases=False))):
            ce = None if eq is None or debt is None else eq + debt
            add(f"EBIT ÷ ({ce_label})", _div(ebit, ce))
            add(f"PBT ÷ ({ce_label})", _div(pbt, ce), standard=False, contradicts="ebit")
    elif kind == "dscr":
        dep = f.line("depreciation", p)
        earn = None if pat is None or dep is None or fin is None else pat + dep + fin
        service = _cf_sum(ctx, re.compile(r"interest\s+paid|lease\s+liabilit|repayment|repaid"), p)
        if service is not None and service != 0:
            add("(PAT + depreciation + finance costs) ÷ debt service", _div(earn, abs(service)))
    return out


def _period_of_header(text: str, ctx: CheckContext) -> str | None:
    t = (text or "").lower()
    lab = parse_period(t)
    if lab is not None and lab.end is not None:
        return scope_key(lab, ctx.config)
    if re.search(r"current\s+(year|period)|this\s+year|\bcy\b", t):
        return "CY"
    if re.search(r"previous\s+(year|period)|prior\s+(year|period)|last\s+year|\bpy\b", t):
        return "PY"
    return None


def _ratio_rows(ctx: CheckContext) -> list[tuple[Sheet, list[RatioRow]]]:
    out = []
    for sh in ctx.print_sheets:
        for t in ctx.tables(sh):
            rows: list[RatioRow] = []
            for r in t.rows:
                cap = norm_caption(ctx.caption(sh, r, before_col=2))
                kind = next((k for k, rx in KINDS if rx.search(cap)), None)
                if kind:
                    rows.append(RatioRow(kind, r, ctx.caption(sh, r, before_col=2)))
            if len(rows) < 3:
                continue
            # header cells (text) over every column of the table, numeric or not
            hdr_rows = t.header_rows or [t.r1 - 1]
            headers: dict[int, str] = {}
            for hr in hdr_rows:
                for c in range(1, sh.max_col + 1):
                    cell = sh.cell(hr, c)
                    if cell is not None and isinstance(cell.value, str) and cell.value.strip():
                        headers[c] = (headers.get(c, "") + " " + cell.value).strip()
            cols: dict[str, int] = {}
            for c in t.num_cols:
                p = _period_of_header(headers.get(c, "") or t.headers.get(c, ""), ctx)
                if p and p not in cols:
                    cols[p] = c
            if not cols:
                continue
            var_col = next((c for c, h in headers.items() if re.search(r"variance|change|movement", h.lower()) and "reason" not in h.lower()), None)
            reason_col = next((c for c, h in headers.items() if re.search(r"reason|explanation|remark|comment", h.lower())), None)
            num_col = next((c for c, h in headers.items() if re.search(r"numerator", h.lower())), None)
            for rr in rows:
                rr.cols = dict(cols)
                rr.var_col, rr.reason_col = var_col, reason_col
                if num_col is not None:
                    cell = sh.cell(rr.row, num_col)
                    rr.numerator_text = str(cell.value) if cell is not None and isinstance(cell.value, str) else ""
            out.append((sh, rows))
    return out


def _tolerance(ctx: CheckContext, sh: Sheet, x: Loc) -> Decimal:
    cell = sh.cell(x.row, x.col)
    d = format_info(sh.number_format(cell)).value_decimals if cell is not None else 2
    return Decimal(1).scaleb(-max(d, 0))


def _matches(printed: Decimal, value: Decimal, tol: Decimal, *, pct_cell: bool) -> bool:
    if abs(printed - value) <= tol:
        return True
    # ratios printed as a plain number of percent (14.50 for 14.50 %)
    return not pct_cell and abs(printed - value * 100) <= tol


@check("RATIO-01", "RATIO-02", "RATIO-03")
def check_ratios(ctx: CheckContext) -> None:
    figures = _Figures(ctx)
    raw_figures = _Figures(ctx, raw=True)
    for sh, rows in _ratio_rows(ctx):
        for rr in rows:
            chosen: dict[str, Variant] = {}
            flagged = False
            for p, col in rr.cols.items():
                if not has_number(ctx, sh, rr.row, col):
                    continue
                x = loc(ctx, sh, rr.row, col)
                if x.value is None:
                    continue
                variants = _variants(ctx, figures, rr.kind, p)
                if not variants:
                    continue
                raw_variants = _variants(ctx, raw_figures, rr.kind, p)
                cell = sh.cell(rr.row, col)
                pct_cell = cell is not None and format_info(sh.number_format(cell)).percent > 0
                tol = _tolerance(ctx, sh, x)
                hit = next((v for v in variants if v.standard and _matches(x.value, v.value, tol, pct_cell=pct_cell)), None)
                hit = hit or next((v for v in raw_variants if v.standard and _matches(x.value, v.value, tol, pct_cell=pct_cell)), None)
                hit = hit or next((v for v in variants if _matches(x.value, v.value, tol, pct_cell=pct_cell)), None)
                std = next((v for v in variants if v.standard), variants[0])
                caption_text = (rr.caption + " " + rr.numerator_text).lower()
                if hit is None:
                    if not any(v.standard for v in variants):
                        continue  # only non-standard definitions computable (e.g. PY without opening balances)
                    doubled = std.value and abs(x.value - std.value * TWO) <= tol * 2
                    why = " — the average appears to be divided by 2 twice" if doubled else ""
                    flagged = True
                    ctx.emit(
                        "RATIO-01", sheet=sh.name, cells=[x.addr], caption=rr.caption, printed=ctx.printed_text(sh, rr.row, col),
                        expected=_fmt_ratio(std.value, pct_cell), difference=x.value - std.value,
                        explanation=f"{rr.caption} ({p}) prints {ctx.printed_text(sh, rr.row, col)}; recomputed from the statements as {std.name} it is {_fmt_ratio(std.value, pct_cell)}{why}.",
                        evidence=[ev(ctx, x, rr.caption)], suggested_fix="Recompute the ratio with the Schedule III definition from the printed statement figures.",
                        period=p, key=f"{rr.kind}:{p}",
                    )
                    continue
                chosen[p] = hit
                contradicted = hit.contradicts and (
                    (hit.contradicts == "ebit" and re.search(r"\bebit\b|before\s+interest", caption_text))
                    or (hit.contradicts == "revenue" and re.search(r"revenue|sales|turnover", caption_text))
                )
                if not hit.standard and contradicted:
                    flagged = True
                    ctx.emit(
                        "RATIO-02", sheet=sh.name, cells=[x.addr], caption=rr.caption, printed=ctx.printed_text(sh, rr.row, col),
                        expected=_fmt_ratio(std.value, pct_cell), difference=x.value - std.value,
                        explanation=f"{rr.caption} ({p}) is computed as {hit.name}, which contradicts its caption / numerator ('{rr.numerator_text or rr.caption}'); as defined it is {_fmt_ratio(std.value, pct_cell)}.",
                        evidence=[ev(ctx, x, rr.caption)], suggested_fix="Use the numerator / denominator stated in the caption (Schedule III definition).",
                        period=p, key=f"{rr.kind}:{p}:def", certainty=Certainty.LIKELY,
                    )
            if not flagged and "CY" in chosen and "PY" in chosen and chosen["CY"].name != chosen["PY"].name and chosen["CY"].standard != chosen["PY"].standard:
                x = loc(ctx, sh, rr.row, rr.cols["PY"])
                ctx.emit(
                    "RATIO-02", sheet=sh.name, cells=[x.addr], caption=rr.caption, printed=ctx.printed_text(sh, rr.row, rr.cols["PY"]),
                    explanation=f"{rr.caption}: the current year uses '{chosen['CY'].name}' but the previous year uses '{chosen['PY'].name}'.",
                    evidence=[ev(ctx, loc(ctx, sh, rr.row, rr.cols["CY"]), "CY"), ev(ctx, x, "PY")],
                    suggested_fix="Compute both years with the same definition.", key=f"{rr.kind}:basis",
                )
            _format_consistency(ctx, sh, rr)
            _variance(ctx, sh, rr)


def _fmt_ratio(v: Decimal, pct: bool) -> str:
    return f"{v * 100:.2f}%" if pct else f"{v:.2f}"


def _format_consistency(ctx: CheckContext, sh: Sheet, rr: RatioRow) -> None:
    if "CY" not in rr.cols or "PY" not in rr.cols:
        return
    a, b = sh.cell(rr.row, rr.cols["CY"]), sh.cell(rr.row, rr.cols["PY"])
    if a is None or b is None or not a.is_number or not b.is_number:
        return
    fa, fb = format_info(sh.number_format(a)), format_info(sh.number_format(b))
    if (fa.percent, fa.decimals) != (fb.percent, fb.decimals):
        x = loc(ctx, sh, rr.row, rr.cols["PY"])
        ctx.emit(
            "RATIO-02", sheet=sh.name, cells=[x.addr], caption=rr.caption, printed=ctx.printed_text(sh, rr.row, rr.cols["PY"]),
            explanation=f"{rr.caption}: the two years are printed in different formats ({fa.code} vs {fb.code}).",
            evidence=[ev(ctx, loc(ctx, sh, rr.row, rr.cols["CY"]), "CY"), ev(ctx, x, "PY")],
            suggested_fix="Use one number format (decimals, % or times) for both years.", key=f"{rr.kind}:format",
            severity=Severity.LOW, certainty=Certainty.CERTAIN,
        )


_NO_REASON = re.compile(r"^(-|–|—|na|n\.a\.?|nil|none|not applicable)?$", re.I)


def _variance(ctx: CheckContext, sh: Sheet, rr: RatioRow) -> None:
    if "CY" not in rr.cols or "PY" not in rr.cols:
        return
    cy, py = ctx.displayed(sh, rr.row, rr.cols["CY"]), ctx.displayed(sh, rr.row, rr.cols["PY"])
    var: Decimal | None = None
    var_cell = None
    if rr.var_col is not None and has_number(ctx, sh, rr.row, rr.var_col):
        var = ctx.displayed(sh, rr.row, rr.var_col)
        var_cell = loc(ctx, sh, rr.row, rr.var_col)
        fi = format_info(sh.number_format(sh.cell(rr.row, rr.var_col)))  # type: ignore[arg-type]
        if var is not None and not fi.percent and abs(var) > 1:
            var = var / 100  # variance printed as a number of percent
    if var is None and cy is not None and py:
        var = (cy - py) / abs(py)
    if var is None or abs(var) <= Decimal("0.25"):
        return
    reason = ""
    if rr.reason_col is not None:
        rc = sh.cell(rr.row, rr.reason_col)
        reason = str(rc.value).strip() if rc is not None and rc.value is not None else ""
    if reason and not _NO_REASON.match(reason) and len(reason) > 3:
        return
    from fscore.engine.refs import make_addr

    cells = [var_cell.addr] if var_cell else [make_addr(rr.row, rr.cols["CY"])]
    if rr.reason_col is not None:
        cells.append(make_addr(rr.row, rr.reason_col))
    anchor = var_cell or loc(ctx, sh, rr.row, rr.cols["CY"])
    ctx.emit(
        "RATIO-03", sheet=sh.name, cells=cells, caption=rr.caption, printed=ctx.printed_text(sh, anchor.row, anchor.col),
        explanation=f"{rr.caption} changed by {var * 100:.2f}% (more than 25 %) but no reason for the variance is given (Schedule III).",
        evidence=[ev(ctx, anchor, "Variance")], suggested_fix="Explain the reason for the variance in the 'Reason for variance' column.",
        key=f"{rr.kind}:variance",
    )

