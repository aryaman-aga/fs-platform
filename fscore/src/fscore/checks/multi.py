"""MULTI-01…05 — multi-period, multi-currency and multi-entity layouts (02 §6.11, lessons 15A).

* MULTI-01  every printed column's period is read from its header (often a
  formula-linked heading); columns outside the configured scope, printed
  functional-currency helper columns, columns whose figures link to a
  different period than their heading says, and tables missing the current
  period column are reported.
* MULTI-02  an out-of-scope column that cannot be hidden because hiding is
  sheet-wide and the column holds other printed content.
* MULTI-03  intermediate-date / stale quarterly roll-forward rows: hideable
  only if the visible rows still add from opening to closing.
* MULTI-04  the functional-currency Balance Sheet balances but the INR one
  does not → translation reserve / rate problem; never plugged.
* MULTI-05  intercompany balances and transactions must mirror across the
  group's entity files (``mirror_check``; a single-file run has nothing to compare).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any

from rapidfuzz import fuzz

from fscore.checks.base import CheckContext, fmt_amount
from fscore.checks.hide_safety import (
    column_currency,
    column_hide_safety,
    label_out_of_scope,
    out_of_scope_columns,
    out_of_scope_rows,
    row_hide_safety,
    table_col_label,
)
from fscore.engine.periods import PeriodLabel, parse_period, scope_key
from fscore.engine.refs import index_to_col, make_addr
from fscore.engine.statements import column_label_at, header_labels
from fscore.io.xlsx_reader import Sheet
from fscore.models import CellRef, Certainty, Evidence, Finding, FixKind, Severity
from fscore.registry import check, get_rule


def _scope_text(ctx: CheckContext) -> str:
    return ", ".join(
        f"{p.key} ({'3 months' if p.months == 3 else str(p.months) + ' months' if p.months != 12 else 'year'} to {p.end:%d-%b-%Y})"
        for p in ctx.config.scope()
    )


def _header_row(ctx: CheckContext, sh: Sheet, col: int) -> int | None:
    rows = [r for r, c, _l in header_labels(ctx, sh) if c == col]
    return min(rows) if rows else None


# ============================================================================ MULTI-01 / 02


@check("MULTI-01", "MULTI-02")
def check_column_scope(ctx: CheckContext) -> None:
    for sname, col, label in out_of_scope_columns(ctx):
        sh = ctx.wb.sheet(sname)
        hr = _header_row(ctx, sh, col) or 1
        letter = index_to_col(col)
        verdict = column_hide_safety(ctx, sname, col)
        fc = column_currency(ctx, sh, col, hr)
        what = f"functional-currency ({fc}) helper column" if fc else "column"
        ctx.emit(
            "MULTI-01", sheet=sname, cells=[make_addr(hr, col)],
            explanation=f"Printed {what} {letter} headed '{label}' is outside the configured period scope: {_scope_text(ctx)}.",
            printed=label, evidence=[ctx.evidence_for(sh, hr, col, "Column heading")],
            suggested_fix=(f"Hide column {letter} (approval item; hiding was checked to be safe)." if verdict.safe
                           else f"Do not hide column {letter}: it also holds {', '.join(verdict.reasons)}. Move the tables apart first."),
            fix_kind=FixKind.APPROVAL if verdict.safe else FixKind.MANUAL, key=f"oos:{col}",
        )
        if not verdict.safe:
            ctx.emit(
                "MULTI-02", sheet=sname, cells=[letter] + [b.split("!")[-1] for b in verdict.blocking_cells[:10]],
                explanation=f"Column {letter} ('{label}') cannot be hidden safely: hiding is sheet-wide and the column also holds "
                            f"{', '.join(verdict.reasons)} ({', '.join(verdict.blocking_cells[:5])}).",
                evidence=[Evidence(label="Other printed content in the column", ref=CellRef(sheet=b.split("!")[0], addr=b.split("!")[1]))
                          for b in verdict.blocking_cells[:5]],
                suggested_fix="Move the other content out of the column (or into its own sheet) before hiding, or leave the column visible.",
                key=f"unsafe:{col}",
            )
    _check_header_vs_links(ctx)
    _check_missing_current_column(ctx)


def _same_period(a: PeriodLabel, b: PeriodLabel) -> bool:
    return a.end == b.end and (a.months is None or b.months is None or a.months == b.months)


def _check_header_vs_links(ctx: CheckContext) -> None:
    """A column's heading says one period but most of its linked figures come from another period's column."""
    for sh in ctx.print_sheets:
        for t in ctx.tables(sh):
            for col in t.num_cols:
                if col == t.note_col or sh.col_hidden(col):
                    continue
                lab = table_col_label(ctx, sh, t, col)
                if lab is None or lab.end is None:
                    continue
                votes: Counter[str] = Counter()
                src_label: dict[str, PeriodLabel] = {}
                sample: dict[str, str] = {}
                for row in t.rows:
                    cell = sh.cell(row, col)
                    if cell is None or cell.formula is None:
                        continue
                    lf = ctx.linear(sh, cell)
                    if lf is None or not lf.is_link:
                        continue
                    term = lf.terms[0]
                    src = ctx.wb.get_sheet(term.sheet)
                    if src is None or (src.name == sh.name and term.area.c1 == col):
                        continue
                    slab = column_label_at(ctx, src, term.area.r1, term.area.c1)
                    if slab is None or slab.end is None:
                        continue
                    k = f"{slab.end}|{slab.months}"
                    votes["same" if _same_period(lab, slab) else k] += 1
                    src_label.setdefault(k, slab)
                    sample.setdefault(k, f"{src.name}!{make_addr(term.area.r1, term.area.c1)}")
                total = sum(votes.values())
                if total < 2:
                    continue
                k, n = votes.most_common(1)[0]
                if k != "same" and n * 2 > total:
                    slab = src_label[k]
                    ctx.emit(
                        "MULTI-01", sheet=sh.name, cells=[make_addr(t.header_rows[-1] if t.header_rows else t.r1, col)],
                        severity=Severity.HIGH if label_out_of_scope(lab, ctx.config) or not label_out_of_scope(slab, ctx.config) else Severity.MEDIUM,
                        explanation=f"Column {index_to_col(col)} is headed '{lab.text}' but {n} of its {total} linked figures come from "
                                    f"'{slab.text}' columns (e.g. {sample[k]}).",
                        printed=lab.text, expected=slab.describe(),
                        suggested_fix="Correct the heading or relink the column to the right period's figures.",
                        key=f"linkperiod:{t.r1}:{col}",
                    )


def _check_missing_current_column(ctx: CheckContext) -> None:
    pairs = [("CY", "PY"), ("Q4_CY", "Q4_PY")]
    for sh in ctx.print_sheets:
        for t in ctx.tables(sh):
            keys: set[str] = set()
            oos: list[str] = []
            for col in t.num_cols:
                head = t.headers.get(col) or ""
                lab = parse_period(head) if head else None
                if lab is None or lab.end is None:
                    continue
                k = scope_key(lab, ctx.config)
                if k:
                    keys.add(k)
                elif label_out_of_scope(lab, ctx.config):
                    oos.append(head)
            for cur, prev in pairs:
                if prev in keys and cur not in keys and any(p.key == cur for p in ctx.config.scope()):
                    hr = t.header_rows[-1] if t.header_rows else t.r1
                    ctx.emit(
                        "MULTI-01", sheet=sh.name, cells=[make_addr(hr, min(t.num_cols))], severity=Severity.HIGH, certainty=Certainty.LIKELY,
                        explanation=f"Table '{t.title or 'at row ' + str(t.r1)}' shows the comparative period but no current-period ({cur}) column"
                                    + (f"; it has out-of-scope column(s) {', '.join(oos)}." if oos else "."),
                        suggested_fix="Add / relink the current-period column.", key=f"missingcur:{t.r1}:{cur}",
                    )


# ============================================================================ MULTI-03


@check("MULTI-03")
def check_intermediate_rows(ctx: CheckContext) -> None:
    for sname, row, caption in out_of_scope_rows(ctx):
        verdict = row_hide_safety(ctx, sname, row)
        if verdict.safe:
            ctx.emit(
                "MULTI-03", sheet=sname, cells=[make_addr(row, 1)], fix_kind=FixKind.APPROVAL,
                explanation=f"Row '{caption}' carries an intermediate / out-of-scope date. It can be hidden: the visible rows still add from "
                            "opening to closing.",
                suggested_fix=f"Hide row {row} (approval item).", key=f"row:{row}",
            )
        else:
            ctx.emit(
                "MULTI-03", sheet=sname, cells=[make_addr(row, 1)] + [b.split("!")[-1] for b in verdict.blocking_cells[:5]],
                fix_kind=FixKind.MANUAL,
                explanation=f"Row '{caption}' carries an intermediate / out-of-scope date and cannot simply be hidden: "
                            f"{'; '.join(verdict.reasons)}.",
                evidence=[Evidence(label="Depends on it", ref=CellRef(sheet=b.split('!')[0], addr=b.split('!')[1])) for b in verdict.blocking_cells[:5]],
                suggested_fix="Restate the roll-forward for the reporting period (opening → movements → closing) before hiding stale rows.",
                key=f"row:{row}",
            )


# ============================================================================ MULTI-04


def _bs_total_rows(ctx: CheckContext) -> tuple[Sheet, int, int] | None:
    smap = ctx.statements
    bs = smap.roles.get("balance_sheet")
    ta, tel = smap.line("total_assets"), smap.line("total_eq_liab")
    if not bs or ta is None or tel is None:
        return None
    return ctx.wb.sheet(bs), ta.row, tel.row


def _currency_of(ctx: CheckContext, sh: Sheet, col: int) -> str | None:
    hr = _header_row(ctx, sh, col)
    if hr is not None:
        cur = column_currency(ctx, sh, col, hr)
        if cur:
            return cur
    for r in range(1, min(sh.max_row, 20) + 1):
        c = sh.cell(r, col)
        if c is not None and isinstance(c.value, str):
            cur = column_currency(ctx, sh, col, r)
            if cur:
                return cur
    return None


@check("MULTI-04")
def check_translation(ctx: CheckContext) -> None:
    """FC Balance Sheet balances, INR does not, by more than a rounding-sized gap.

    Rounding-sized gaps (≤ the balancing threshold, control C3) are casting /
    balancing matters (BAL-01); larger gaps with a balanced FC Balance Sheet point
    to the translation (rates, translation reserve). Lines whose INR figure is not
    the FC figure at the closing rate are listed as evidence.
    """
    found = _bs_total_rows(ctx)
    if found is None:
        return
    sh, ta_row, tel_row = found
    threshold = ctx.config.policy.balancing_threshold
    cols = sorted({c.col for c in sh.iter_cells() if c.row in (ta_row, tel_row) and c.is_number})
    fc_cols: dict[Any, list[tuple[int, Decimal, str]]] = defaultdict(list)
    inr_cols: dict[Any, list[tuple[int, Decimal]]] = defaultdict(list)
    for col in cols:
        a, b = ctx.displayed(sh, ta_row, col), ctx.displayed(sh, tel_row, col)
        if a is None or b is None:
            continue
        lab = column_label_at(ctx, sh, ta_row, col)
        period = lab.end if lab is not None else None
        cur = _currency_of(ctx, sh, col)
        if cur:
            fc_cols[period].append((col, a - b, cur))
        else:
            inr_cols[period].append((col, a - b))
    for period, fcs in fc_cols.items():
        fc_ok = next(((c, cur) for c, d, cur in fcs if d == 0), None)
        if fc_ok is None:
            continue
        fc_col, cur = fc_ok
        for col, diff in inr_cols.get(period, []):
            if abs(diff) <= threshold:
                continue
            ctx.emit(
                "MULTI-04", sheet=sh.name, cells=[make_addr(ta_row, col), make_addr(tel_row, col)],
                explanation=f"The {cur} Balance Sheet balances but the INR Balance Sheet in column {index_to_col(col)} does not "
                            f"(total assets − total equity and liabilities = {fmt_amount(diff)}, beyond rounding). This points to a "
                            "translation-reserve or exchange-rate problem.",
                difference=diff,
                evidence=[ctx.evidence_for(sh, ta_row, col, "Total assets"), ctx.evidence_for(sh, tel_row, col, "Total equity and liabilities"),
                          *_rate_outliers(ctx, sh, col, fc_col, ta_row, tel_row)],
                suggested_fix="Recompute the translation (closing rate for balances, average for P&L, historical for equity) and the "
                              "translation reserve; never plug the difference.",
                key=f"translation:{col}",
            )


def _rate_outliers(ctx: CheckContext, sh: Sheet, inr_col: int, fc_col: int, ta_row: int, tel_row: int) -> list[Evidence]:
    """Asset lines whose INR figure is not the FC figure at the dominant (closing) rate."""
    pairs: list[tuple[int, Decimal, Decimal]] = []
    for row in range(1, ta_row):
        a, f = ctx.displayed(sh, row, inr_col), ctx.displayed(sh, row, fc_col)
        if a and f and not (sh.cell(row, inr_col) is not None and sh.cell(row, inr_col).formula and  # type: ignore[union-attr]
                            (lf := ctx.linear(sh, sh.cell(row, inr_col))) is not None and lf.is_total):  # type: ignore[arg-type]
            pairs.append((row, a, f))
    if len(pairs) < 3:
        return []
    rates = Counter((a / f).quantize(Decimal("0.01")) for _r, a, f in pairs)
    rate = rates.most_common(1)[0][0]
    out = []
    for row, a, f in pairs:
        if abs(a - f * rate) > abs(a) * Decimal("0.005") + Decimal("0.05"):
            out.append(Evidence(label=f"{ctx.caption(sh, row)} — INR {fmt_amount(a)} vs FC {f} × {rate}",
                                ref=CellRef(sheet=sh.name, addr=make_addr(row, inr_col)), value=a))
    return out[:5]


# ============================================================================ MULTI-05

_IC_MARKER = re.compile(r"inter[\s-]?company|\bic\b|related\s+part|group\s+compan|holding\s+compan|fellow\s+subsidiar", re.I)
_SUFFIX = re.compile(r"\b(private|pvt|limited|ltd|llp|inc|incorporated|corp|corporation|pte|plc|gmbh|bv|b\.v|fz[\s-]?llc|llc|pty|co)\b\.?", re.I)
_NATURE_KEYS = [
    ("receivable", re.compile(r"^(tr|ofa|oca|onca|inv)\.", re.I)),
    ("payable", re.compile(r"^(tp|borr|ofl|ocl|lease)\.", re.I)),
    ("income", re.compile(r"^(rev|oi)\.", re.I)),
    ("expense", re.compile(r"^(oth|fin|emp|dep)\.", re.I)),
]
_NATURE_WORDS = [
    ("receivable", re.compile(r"receivable|recoverable|debtor|due\s+from|loan\s+(to|given)|advance\s+to", re.I)),
    ("payable", re.compile(r"payable|creditor|due\s+to|loan\s+from|borrowing|intercompany\s+loan", re.I)),
    ("income", re.compile(r"revenue|income|sales|support\s+service\s+charges", re.I)),
    ("expense", re.compile(r"expense|charges|interest\s+on|cost|fees", re.I)),
]
_MIRROR = {"receivable": "payable", "payable": "receivable", "income": "expense", "expense": "income"}


def norm_entity(name: str) -> str:
    t = _SUFFIX.sub(" ", (name or "").lower())
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", t).split())


def _match_entity(text: str, entities: list[str], exclude: str) -> str | None:
    low = norm_entity(text)
    best, score = None, 0.0
    for e in entities:
        if e == exclude:
            continue
        ne = norm_entity(e)
        if not ne:
            continue
        s = 100.0 if ne in low else fuzz.partial_ratio(ne, low)
        if s > score:
            best, score = e, s
    return best if score >= 90 else None


def intercompany_lines(ctx: CheckContext, entities: list[str], self_name: str) -> list[dict[str, Any]]:
    """IC lines from the entity's trial balance: counterparty, nature, CY amount (presentation units)."""
    tb_name = ctx.statements.roles.get("trial_balance")
    sh = ctx.wb.get_sheet(tb_name) if tb_name else None
    if sh is None:
        return []
    header_row = None
    for c in sh.iter_cells():
        if c.row > 40:
            break
        if isinstance(c.value, str) and re.match(r"^\s*account", c.value, re.I):
            header_row = c.row
            break
    if header_row is None:
        return []
    heads: dict[int, str] = {hc.col: str(hc.value).strip() for hc in sh.iter_cells() if hc.row == header_row and isinstance(hc.value, str)}
    key_col = next((k for k, h in heads.items() if re.search(r"group|mapping|fs\s+line|schedule", h, re.I)), None)
    amt_col: int | None = None
    for k, h in sorted(heads.items()):
        lab = parse_period(h)
        if lab is not None and scope_key(lab, ctx.config) == "CY" and (amt_col is None or "inr" in h.lower()):
            amt_col = k
    if amt_col is None:
        return []
    divisor = Decimal(ctx.config.unit_divisor)
    out: list[dict[str, Any]] = []
    for r in range(header_row + 1, sh.max_row + 1):
        texts = [str(tc.value) for col in range(1, amt_col) if (tc := sh.cell(r, col)) is not None and isinstance(tc.value, str)]
        if not texts:
            continue
        text = " | ".join(texts)
        cp = _match_entity(text, entities, self_name)
        if cp is None:
            continue
        nature = None
        if key_col is not None:
            kc = sh.cell(r, key_col)
            if kc is not None and isinstance(kc.value, str):
                nature = next((n for n, rx in _NATURE_KEYS if rx.search(kc.value.strip())), None)
        if nature is None:
            nature = next((n for n, rx in _NATURE_WORDS if rx.search(text)), None)
        amt_cell = sh.cell(r, amt_col)
        if nature is None or amt_cell is None or not amt_cell.is_number:
            continue
        out.append({"counterparty": cp, "nature": nature, "amount": Decimal(str(amt_cell.value)) / divisor,
                    "sheet": sh.name, "cell": make_addr(r, amt_col), "text": text})
    return out


def _finding(rule_id: str, **kw: Any) -> Finding:
    info = get_rule(rule_id)
    f = Finding(rule_id=rule_id, rule_version=info.version, family=info.family, severity=kw.pop("severity", info.severity),
                certainty=kw.pop("certainty", info.certainty), category=info.title, fix_kind=kw.pop("fix_kind", info.fix_kind), **kw)
    f.fingerprint = f.compute_fingerprint()
    return f


def mirror_check(
    workbooks: dict[str, CheckContext],
    *,
    tolerance: Decimal = Decimal("0.05"),
    rel_tolerance: Decimal = Decimal("0.005"),
) -> list[Finding]:
    """MULTI-05: intercompany balances / transactions must mirror between the group's entity files.

    ``workbooks`` maps each entity name to its check context (one workbook per entity).
    """
    names = list(workbooks)
    lines = {e: intercompany_lines(ctx, names, e) for e, ctx in workbooks.items()}
    agg: dict[tuple[str, str, str], tuple[Decimal, list[dict[str, Any]]]] = {}
    for e, lst in lines.items():
        for ln in lst:
            k = (e, ln["counterparty"], ln["nature"])
            total, items = agg.get(k, (Decimal(0), []))
            agg[k] = (total + ln["amount"], [*items, ln])
    findings: list[Finding] = []
    done: set[tuple[str, str, str]] = set()
    for (a, b, nature), (amt_a, items_a) in sorted(agg.items()):
        mirror = _MIRROR[nature]
        if (b, a, mirror) in done:
            continue
        done.add((a, b, nature))
        amt_b, items_b = agg.get((b, a, mirror), (None, []))
        if amt_b is None:
            findings.append(_finding(
                "MULTI-05", sheet=items_a[0]["sheet"], cells=[i["cell"] for i in items_a[:10]], key=f"{a}|{b}|{nature}|one-sided",
                explanation=f"{a} records a {nature} with {b} of {fmt_amount(abs(amt_a))} but {b}'s books show no matching {mirror} with {a}.",
                evidence=[Evidence(label=i["text"][:80], ref=CellRef(sheet=i["sheet"], addr=i["cell"]), value=i["amount"]) for i in items_a[:5]],
                suggested_fix="Confirm the balance with the counterparty; record the missing entry or the item in transit.",
            ))
            continue
        diff = abs(amt_a) - abs(amt_b)
        limit = max(tolerance, rel_tolerance * max(abs(amt_a), abs(amt_b)))
        if abs(diff) > limit:
            findings.append(_finding(
                "MULTI-05", sheet=items_a[0]["sheet"], cells=[i["cell"] for i in items_a[:10]], key=f"{a}|{b}|{nature}",
                explanation=f"{a}'s {nature} with {b} ({fmt_amount(abs(amt_a))}) does not mirror {b}'s {mirror} with {a} "
                            f"({fmt_amount(abs(amt_b))}); difference {fmt_amount(diff)}.",
                difference=diff,
                evidence=[Evidence(label=f"{a}: {i['text'][:60]}", ref=CellRef(sheet=i["sheet"], addr=i["cell"]), value=i["amount"]) for i in items_a[:3]]
                + [Evidence(label=f"{b}: {i['text'][:60]}", ref=CellRef(sheet=i["sheet"], addr=i["cell"]), value=i["amount"]) for i in items_b[:3]],
                suggested_fix="Reconcile the intercompany account with the counterparty (rates, items in transit, misposting).",
            ))
    return findings


@check("MULTI-05")
def check_mirror_single_file(ctx: CheckContext) -> None:
    """A single-entity run has no counterparty files to mirror against; the platform calls
    :func:`mirror_check` with all entity workbooks of a group engagement."""
    return None
