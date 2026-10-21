"""Fix solver (02 §7.2–7.3, §15/15A).

1. Strip previous balancing adjustments on the designated lines (they are
   always recomputed from scratch, never trusted).
2. Fixed-point totals-only rewrite: every failing printed total is rewritten to
   add its displayed components, typed rounding plugs are removed, missing total
   cells are added, row totals of cross-cast tables are rewritten; the whole
   dependency closure is re-evaluated after each pass (cap ``max_passes``).
3. Balancing: only rounding-sized gaps (≤ threshold, control C3) go to the
   designated lines, solved jointly in paise in the order PY Balance Sheet →
   PY cash flow → CY Balance Sheet → CY cash flow; the quarter and year
   columns of the same period end move together. Larger gaps are reported.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from fscore.checks.balance import bs_gaps, cf_gaps
from fscore.checks.base import CheckContext
from fscore.checks.casting import casting_failures, is_balancing_cell
from fscore.checks.crosscast import cross_failures
from fscore.config import EngagementConfig
from fscore.engine import formula as F
from fscore.engine.calc import Recalculator
from fscore.engine.display import format_info
from fscore.engine.linear import LinearForm, Term
from fscore.engine.periods import scope_key
from fscore.engine.refs import Area, make_addr, quote_sheet
from fscore.engine.selftest import THRESHOLD, bounds, name_lookup, run_selftest
from fscore.engine.statements import column_label_at
from fscore.fix.rewrite import add_adjustment, remove_plug, strip_adjustment, totals_rewrite
from fscore.io.xlsx_reader import Workbook
from fscore.models import BalancingAdjustment, CellRef, EditGroup

Key = tuple[str, int, int]
PAISA = Decimal("0.01")


@dataclass
class Change:
    key: Key
    group: EditGroup
    reason: str
    new_formula: str | None = None
    new_value: float | None = None
    rules: list[str] = field(default_factory=list)


@dataclass
class SolveResult:
    changes: dict[Key, Change]
    original_formula: dict[Key, str | None]
    original_value: dict[Key, Any]
    final_values: dict[Key, Any]
    balancing: list[BalancingAdjustment]
    unresolved: list[str]
    converged: bool
    passes: int
    selftest_rate: float
    allowed: bool
    wb: Workbook
    ctx: CheckContext


def canon_formula(text: str | None) -> str | None:
    if text is None:
        return None
    try:
        return F.to_text(F.parse(text))
    except F.FormulaError:
        return text


class Solver:
    def __init__(self, path: Path, config: EngagementConfig) -> None:
        self.path = Path(path)
        self.config = config
        self.wb = Workbook.open(self.path)
        self.ctx = CheckContext(self.wb, config)
        self.print_names = [s.name for s in self.ctx.print_sheets]
        self._lower = {s.name.strip().lower(): s.name for s in self.wb.sheets}
        self.original_formula: dict[Key, str | None] = {}
        self.original_value: dict[Key, Any] = {}
        formulas: dict[Key, str] = {}
        for sh in self.ctx.print_sheets:
            for c in sh.iter_cells():
                k = (sh.name, c.row, c.col)
                self.original_formula[k] = c.formula
                self.original_value[k] = c.value
                if c.formula is not None:
                    formulas[k] = c.formula
        self.rc = Recalculator(
            formulas,
            self._base,
            name_lookup=name_lookup(self.wb),
            bounds=bounds(self.wb),
            date1904=self.wb.date1904,
            fallback=self._fallback,
            canon=self._canon,
        )
        self.changes: dict[Key, Change] = {}
        self.unresolved: list[str] = []
        self.rewritten_dir: dict[Key, str] = {}
        self._bal_base: dict[Key, tuple[str | None, Any]] = {}  # designated cell -> (base formula, typed value)
        self._bal_adj: dict[Key, Decimal] = {}  # designated cell -> current visible adjustment

    # ------------------------------------------------------------------ plumbing
    def _canon(self, name: str) -> str:
        if self.wb.get_sheet(name) is not None and name in {s.name for s in self.wb.sheets}:
            return name
        return self._lower.get(name.strip().lower(), name)

    def _base(self, key: Key) -> Any:
        sh = self.wb.get_sheet(key[0])
        if sh is None:
            return None
        c = sh.cell(key[1], key[2])
        return None if c is None else c.value

    def _fallback(self, key: Key) -> Any:
        if key in self.original_value:
            return self.original_value[key]
        return self._base(key)

    def _sync(self, keys: set[Key] | None = None) -> None:
        """Push recalculated values and the current formulas into the in-memory workbook.

        The recalculator is the single source of truth for formulas: a cell whose
        edit was withdrawn (e.g. a balancing adjustment that equals the original)
        gets its formula back too.
        """
        targets = set(self.rc.values) if keys is None else set(keys)
        targets |= set(self.changes)
        for key in targets:
            sh = self.wb.get_sheet(key[0])
            if sh is None:
                continue
            c = sh.cell(key[1], key[2])
            f = self.rc.formulas.get(key)
            if c is None:
                if f is None:
                    continue
                from fscore.io.xlsx_reader import Cell

                c = Cell(key[1], key[2], style=self._neighbour_style(sh, key[1], key[2]))
                sh.cells[(key[1], key[2])] = c
            if f is not None:
                if c.formula != f:
                    c.formula = f
                    c.shared_si = None
                if key in self.rc.values:
                    c.value = self.rc.values[key]
            else:
                ch = self.changes.get(key)
                if ch is not None and ch.new_value is not None:
                    c.formula = None
                    c.value = ch.new_value
        self.ctx._linear.clear()
        self.ctx._parsed.clear()
        for k in [k for k in self.ctx.shared if k.startswith("tables:")]:
            del self.ctx.shared[k]

    @staticmethod
    def _neighbour_style(sh: Any, row: int, col: int) -> int:
        best = None
        for c in sh.iter_cells():
            if c.row == row and c.style:
                d = abs(c.col - col)
                if best is None or d < best[0]:
                    best = (d, c.style)
        return best[1] if best else 0

    def _set_formula(self, key: Key, formula: str, group: EditGroup, reason: str, rule: str) -> bool:
        cur = self.rc.formulas.get(key)
        if cur is not None and canon_formula(cur) == canon_formula(formula):
            return False
        prev = self.changes.get(key)
        rules = (prev.rules if prev else []) + [rule]
        grp = prev.group if prev and group == EditGroup.TOTALS_REWRITE and prev.group != EditGroup.TOTALS_REWRITE else group
        self.changes[key] = Change(key, grp, reason if not prev else prev.reason, new_formula=formula, rules=rules)
        self.rc.set_formula(key, formula)
        return True

    def _recalc(self, changed: set[Key]) -> None:
        upd = self.rc.update(changed)
        self._sync(upd | changed)

    # ------------------------------------------------------------------ steps
    def initial(self) -> float:
        self.rc.compute_all()
        rep = run_selftest(self.wb, self.print_names)
        self._sync()
        return rep.match_rate

    def balancing_rows(self) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
        pol = self.config.policy.balancing
        out: list[list[tuple[str, int]]] = [[], []]
        for i, target in enumerate((pol.balance_sheet, pol.cash_flow)):
            rx = re.compile(target.caption, re.I)
            for sh in self.ctx.print_sheets:
                if target.sheet and target.sheet.strip().lower() != sh.name.strip().lower():
                    continue
                if i == 1 and not target.sheet and sh.name != self.ctx.statements.sheet("cash_flow"):
                    continue
                if i == 0 and not target.sheet and sh.name in (self.ctx.statements.sheet("profit_loss"), self.ctx.statements.sheet("cash_flow")):
                    continue
                rows = sorted({c.row for c in self.ctx.iter_printed_numbers(sh)})
                for r in rows:
                    cap = self.ctx.caption(sh, r)
                    if cap and rx.search(cap):
                        out[i].append((sh.name, r))
        return out[0], out[1]

    def strip_previous_adjustments(self) -> None:
        bs_rows, cf_rows = self.balancing_rows()
        changed: set[Key] = set()
        for sname, r in bs_rows + cf_rows:
            sh = self.wb.sheet(sname)
            for c in sh.iter_cells():
                if c.row != r or c.formula is None:
                    continue
                base, adj = strip_adjustment(c.formula)
                if adj != 0 and abs(adj) <= self.config.policy.balancing_threshold:
                    key = (sname, r, c.col)
                    self._bal_base[key] = (base, None)
                    self._bal_adj[key] = Decimal(0)
                    self.changes[key] = Change(key, EditGroup.BALANCING, "previous balancing adjustment removed (recomputed from scratch)", new_formula=base, rules=["BAL"])
                    self.rc.set_formula(key, base)
                    changed.add(key)
        if changed:
            self._recalc(changed)

    def fixed_point(self) -> tuple[bool, int]:
        auto_below = Decimal("0.10")
        try:
            from fscore.registry import get_rule

            auto_below = Decimal(str(get_rule("PLUG-01").params.get("auto_remove_below", "0.10")))
        except KeyError:
            pass
        for p in range(1, self.config.policy.max_passes + 1):
            changed: set[Key] = set()
            ctx = self.ctx
            for sh in ctx.print_sheets:
                # 1. failing linear totals (vertical casting)
                for cell, lf, _comps, _total, _exp in casting_failures(ctx, sh):
                    key = (sh.name, cell.row, cell.col)
                    if self.rewritten_dir.get(key) == "cross":
                        self.unresolved.append(f"{sh.name}!{cell.addr}: two-way casting conflict (row and column totals cannot both cast without changing a line)")
                        continue
                    new = totals_rewrite(ctx, sh.name, lf)
                    if new and is_balancing_cell(ctx, sh, cell) and lf.constant and abs(lf.constant) <= self.config.policy.balancing_threshold:
                        # a designated line that is itself a movement (e.g. ΔBS line): cast its base, keep the visible adjustment
                        if key in self._bal_base:
                            self._bal_base[key] = (new, None)
                        new = add_adjustment(new, None, lf.constant)
                    if new and self._set_formula(key, new, EditGroup.TOTALS_REWRITE, "total rewritten to add the displayed components", "CAST-01"):
                        self.rewritten_dir[key] = "down"
                        changed.add(key)
                # 2. typed rounding plugs in totals and links
                for cell in sh.iter_cells():
                    if cell.formula is None:
                        continue
                    key = (sh.name, cell.row, cell.col)
                    plf = ctx.linear(sh, cell)
                    if plf is None or not plf.terms or plf.constant == 0 or abs(plf.constant) >= auto_below:
                        continue
                    if is_balancing_cell(ctx, sh, cell):
                        continue
                    new = totals_rewrite(ctx, sh.name, plf) if plf.is_total else remove_plug(cell.formula)
                    if new and self._set_formula(key, new, EditGroup.PLUG_REMOVAL, f"typed rounding plug {plf.constant} removed", "PLUG-01"):
                        changed.add(key)
                # 3. cross-casting of 'Total' columns on non-total rows
                for table in ctx.tables(sh):
                    if not table.total_cols:
                        continue
                    for r, tcol, comps, _total, _s in cross_failures(ctx, sh, table):
                        key = (sh.name, r, tcol)
                        tcell = sh.cell(r, tcol)
                        if r in table.total_rows and tcell is not None and (self.rewritten_dir.get(key) == "down" or tcell.formula):
                            lf0 = ctx.linear(sh, tcell)
                            if lf0 is not None and lf0.is_total and all(t.area.c1 == tcol for t in lf0.terms):
                                self.unresolved.append(f"{sh.name}!{make_addr(r, tcol)}: grand total cannot cast both down and across")
                                continue
                        xlf = LinearForm([Term(Decimal(1), sh.name, Area(sh.name, r, c, r, c), make_addr(r, c)) for c in comps], Decimal(0), has_sum=True)
                        new = totals_rewrite(ctx, sh.name, xlf)
                        if new and self._set_formula(key, new, EditGroup.TOTALS_REWRITE, "row total rewritten to add the displayed columns", "CAST-02"):
                            self.rewritten_dir[key] = "cross"
                            changed.add(key)
                # 4. missing total cells in total rows (CAST-03)
                changed |= self._missing_totals(sh)
            if not changed:
                return True, p
            self._recalc(changed)
        return False, self.config.policy.max_passes

    def _missing_totals(self, sh: Any) -> set[Key]:
        changed: set[Key] = set()
        ctx = self.ctx
        for table in ctx.tables(sh):
            if not table.total_rows or len(table.num_cols) < 2:
                continue
            prev = table.r1 - 1
            for tr in sorted(table.total_rows):
                block = [r for r in table.rows if prev < r < tr]
                prev = tr
                if len(block) < 2:
                    continue
                for col in table.num_cols:
                    if col == table.note_col:
                        continue
                    tcell = sh.cell(tr, col)
                    if tcell is not None and tcell.value not in (None, ""):
                        continue
                    nonzero = [r for r in block if ctx.displayed(sh, r, col) not in (None, Decimal(0))]
                    if len(nonzero) < 2:
                        continue
                    # mirror the nearest total formula in the same row
                    src = None
                    for dc in itertools.chain.from_iterable((-d, d) for d in range(1, len(table.num_cols) + 1)):
                        nc = sh.cell(tr, col + dc)
                        if nc is not None and nc.formula and col + dc in table.num_cols:
                            src = (nc, dc)
                            break
                    if src is None:
                        new = f"SUM({make_addr(block[0], col)}:{make_addr(block[-1], col)})"
                    else:
                        try:
                            new = F.translate(src[0].formula or "", 0, -src[1])
                        except F.FormulaError:
                            new = f"SUM({make_addr(block[0], col)}:{make_addr(block[-1], col)})"
                    key = (sh.name, tr, col)
                    if self._set_formula(key, new, EditGroup.MISSING_TOTAL, "missing total cell added", "CAST-03"):
                        changed.add(key)
        return changed

    # ------------------------------------------------------------------ balancing
    def _period_end_of(self, sname: str, row: int, col: int) -> tuple[str | None, int | None]:
        sh = self.wb.sheet(sname)
        lab = column_label_at(self.ctx, sh, row, col)
        if lab is None:
            return None, None
        return scope_key(lab, self.config), lab.months

    def _vars_for(self, rows: list[tuple[str, int]], key_end: str) -> list[Key]:
        """Balancing cells for a period end: year column (and the quarter column of the same end)."""
        spec = {p.key: p for p in self.config.scope()}
        target_end = spec[key_end].end if key_end in spec else None
        out: list[Key] = []
        for sname, r in rows:
            sh = self.wb.sheet(sname)
            for c in self.ctx.iter_printed_numbers(sh):
                if c.row != r:
                    continue
                k, _m = self._period_end_of(sname, r, c.col)
                if k is None:
                    continue
                if k in spec and spec[k].end == target_end:
                    out.append((sname, r, c.col))
        return out

    def _apply_adj(self, cells: list[Key], adj: Decimal, *, record: bool, reason: str) -> None:
        """Move each designated cell's single, visible adjustment by ``adj`` (base kept separately)."""
        changed: set[Key] = set()
        for key in cells:
            sh = self.wb.sheet(key[0])
            c = sh.cell(key[1], key[2])
            if key not in self._bal_base:
                f = self.rc.formulas.get(key)
                self._bal_base[key] = (f, None if f is not None else self.original_value.get(key))
            base_f, base_v = self._bal_base[key]
            total = self._bal_adj.get(key, Decimal(0)) + adj
            self._bal_adj[key] = total
            if total == 0 and base_f is None:
                # back to the original typed value
                self.changes.pop(key, None)
                self.rc.set_formula(key, None)
                if c is not None:
                    c.formula = None
                    c.value = base_v
            else:
                new = base_f if total == 0 else add_adjustment(base_f, base_v if base_v is not None else 0, total)
                assert new is not None
                if self.original_formula.get(key) is not None and canon_formula(new) == canon_formula(self.original_formula.get(key)):
                    self.changes.pop(key, None)
                else:
                    self.changes[key] = Change(key, EditGroup.BALANCING, reason, new_formula=new, rules=["BAL"])
                self.rc.set_formula(key, new)
            changed.add(key)
        self._recalc(changed)

    def _gap_vector(self, kind: str) -> dict[str, Decimal]:
        if kind == "bs":
            return {f"{k or ''}#{col}": g for col, k, _a, _b, g in bs_gaps(self.ctx)}
        return {k: g for k, _cc, _bc, _a, _b, g in cf_gaps(self.ctx)}

    def solve_gap(self, kind: str, period: str, rows: list[tuple[str, int]]) -> BalancingAdjustment | None:
        thr = self.config.policy.balancing_threshold
        gaps = self._gap_vector(kind)
        relevant = {k: g for k, g in gaps.items() if (k.split("#")[0] == period if kind == "bs" else k == period)}
        if not relevant or all(g == 0 for g in relevant.values()):
            return None
        stmt = "balance_sheet" if kind == "bs" else "cash_flow"
        worst = max(relevant.values(), key=abs)
        if kind == "cf" and Decimal("0.30") <= abs(worst) <= Decimal("0.40"):
            return BalancingAdjustment(statement=stmt, column=period, period=period, gap=worst, status="investigate",
                                       note="gap of this size usually means the exchange difference on cash is missing: reported, never plugged")
        if abs(worst) > thr:
            return BalancingAdjustment(statement=stmt, column=period, period=period, gap=worst, status="too_large",
                                       note=f"gap exceeds the {thr} rounding threshold: investigate")
        cells = self._vars_for(rows, period)
        if not cells:
            return BalancingAdjustment(statement=stmt, column=period, period=period, gap=worst, status="no_target",
                                       note="no designated balancing line found for this period")
        # measure the effect of +0.01 (Jacobian column), then solve in paise
        self._apply_adj(cells, PAISA, record=False, reason="probe")
        probe = self._gap_vector(kind)
        self._apply_adj(cells, -PAISA, record=False, reason="probe")
        sens = {k: (probe.get(k, Decimal(0)) - relevant[k]) / PAISA for k in relevant}
        num = sum((sens[k] * relevant[k] for k in relevant), Decimal(0))
        den = sum((sens[k] * sens[k] for k in relevant), Decimal(0))
        if den == 0:
            return BalancingAdjustment(statement=stmt, column=period, period=period, gap=worst, status="no_target",
                                       note="the balancing line does not flow into this statement")
        steps = -(num / den) / PAISA  # least-squares step, in paise
        best: tuple[Decimal, Decimal] | None = None
        for cand in {steps.to_integral_value(), steps.to_integral_value() + 1, steps.to_integral_value() - 1}:
            resid = sum(((relevant[k] + sens[k] * cand * PAISA) ** 2 for k in relevant), Decimal(0))
            if best is None or resid < best[0] or (resid == best[0] and abs(cand) < abs(best[1])):
                best = (resid, cand)
        assert best is not None
        adj = best[1] * PAISA
        target = CellRef(sheet=cells[0][0], addr=make_addr(cells[0][1], cells[0][2]))
        self._apply_adj(cells, adj, record=True, reason=f"balancing adjustment for the {stmt.replace('_', ' ')} ({period})")
        after = self._gap_vector(kind)
        left = {k: after.get(k, Decimal(0)) for k in relevant}
        status = "applied" if all(v == 0 for v in left.values()) else "investigate"
        note = "" if status == "applied" else "residual after joint solve: " + ", ".join(f"{k}: {v}" for k, v in left.items() if v)
        if status != "applied" and len(relevant) > 1:
            note += " (several columns for the same period end cannot all be zeroed; report)"
        return BalancingAdjustment(statement=stmt, column=period, period=period, gap=worst, target=target, adjustment=adj, status=status, note=note)

    def balance(self) -> list[BalancingAdjustment]:
        bs_rows, cf_rows = self.balancing_rows()
        out: list[BalancingAdjustment] = []
        scope = [p.key for p in self.config.scope() if p.months == 12] or ["CY", "PY"]
        order = [k for k in ("PY", "CY") if k in scope] + [k for k in scope if k not in ("PY", "CY")]
        for period in order:
            for kind, rows in (("bs", bs_rows), ("cf", cf_rows)):
                res = self.solve_gap(kind, period, rows)
                if res is not None:
                    out.append(res)
        return out


def solve(path: Path, config: EngagementConfig) -> SolveResult:
    s = Solver(path, config)
    rate = s.initial()
    allowed = rate >= THRESHOLD
    if not allowed:
        return SolveResult({}, s.original_formula, s.original_value, dict(s.rc.values), [], [f"evaluator self-test {rate:.2%} below {THRESHOLD:.1%}: fixes disabled"], False, 0, rate, False, s.wb, s.ctx)
    s.strip_previous_adjustments()
    converged, passes = s.fixed_point()
    balancing = s.balance()
    if any(b.status == "applied" for b in balancing):
        c2, p2 = s.fixed_point()
        converged = converged and c2
        passes += p2
    s.unresolved = _still_failing(s, s.unresolved)
    if not converged:
        s.unresolved.append(f"totals did not converge within {config.policy.max_passes} passes")
    # drop no-op changes (e.g. an adjustment stripped and re-added identically)
    final: dict[Key, Change] = {}
    for key, ch in s.changes.items():
        orig = s.original_formula.get(key)
        if ch.new_formula is not None and orig is not None and canon_formula(orig) == canon_formula(ch.new_formula):
            continue
        if ch.new_formula is None and ch.new_value is None:
            continue
        final[key] = ch
    return SolveResult(final, s.original_formula, s.original_value, dict(s.rc.values), balancing, sorted(set(s.unresolved)), converged, passes, rate, True, s.wb, s.ctx)


__all__ = ["Change", "SolveResult", "Solver", "format_info", "quote_sheet", "solve"]


def _still_failing(s: Solver, notes: list[str]) -> list[str]:
    """Keep only conflict notes whose cell still fails to cast at the end (earlier passes may have resolved them)."""
    failing: set[str] = set()
    for sh in s.ctx.print_sheets:
        for cell, *_rest in casting_failures(s.ctx, sh):
            failing.add(f"{sh.name}!{cell.addr}")
        for table in s.ctx.tables(sh):
            if table.total_cols:
                for r, tcol, *_x in cross_failures(s.ctx, sh, table):
                    failing.add(f"{sh.name}!{make_addr(r, tcol)}")
    return sorted({n for n in notes if n.split(":")[0] in failing})
