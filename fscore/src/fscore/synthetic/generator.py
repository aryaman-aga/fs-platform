"""Synthetic workbook generator (02 §13 item 1; 10 §4).

``make_workbook`` builds a realistic Ind AS workbook for the fictional Corvus
group through Module P, injects the requested error classes and returns the
findings a correct engine must produce. Clean round-mode workbooks have zero
casting differences; exact-mode workbooks carry natural print-rounding
differences, which are listed too (source="natural").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from fscore.prepare.inputs import PrepareInputs, Signatory
from fscore.prepare.spec import WorkbookSpec
from fscore.prepare.statements import build_spec, finalise
from fscore.prepare.writer import compute_values, write_workbook
from fscore.synthetic.inject import ExpectedFinding, InjectContext, build_flags, registry
from fscore.synthetic.ledger import (
    FELLOW_SG,
    FELLOW_UK,
    FELLOW_US,
    HOLDING,
    SUBSIDIARY,
    SyntheticBooks,
    corvus_entity,
    generate_books,
)
from fscore.synthetic.oracle import scan_casting, simulate_totals_fix, tie_gaps

D = Decimal


@dataclass
class SyntheticResult:
    path: Path
    expected: list[ExpectedFinding]
    spec: WorkbookSpec
    books: SyntheticBooks
    gaps: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    index: dict[str, Any] = field(default_factory=dict)
    injections: list[str] = field(default_factory=list)
    protected_cells: list[tuple[str, str]] = field(default_factory=list)
    values: dict[tuple[str, int, int], Any] = field(default_factory=dict)

    def expected_rules(self) -> set[str]:
        return {e.rule_id for e in self.expected}

    def required(self) -> list[ExpectedFinding]:
        return [e for e in self.expected if e.required]


def list_injections(layout: str | None = None) -> dict[str, str]:
    """Injector name -> description (filtered to a layout when given)."""
    return {n: i.description for n, i in sorted(registry().items()) if layout is None or layout in i.layouts}


def injection_rules() -> dict[str, tuple[str, ...]]:
    return {n: i.rules for n, i in sorted(registry().items())}


def spfs_books(seed: int = 7, exact: bool = False) -> SyntheticBooks:
    """Books for a foreign fellow subsidiary reporting quarterly SPFS in INR million."""
    books = generate_books(seed=seed, exact=exact, units="millions")
    ent = corvus_entity("millions")
    ent.name = FELLOW_SG
    ent.short = "Corvus Systems"
    ent.cin = "201912345K"
    ent.registration_label = "Registration No."
    ent.address = "10 Synthetic Boulevard, #08-01, Singapore 018980"
    ent.functional_currency = "SGD"
    ent.fellow_subsidiaries = (FELLOW_US, FELLOW_UK, "Corvus Data Services Private Limited")
    ent.subsidiary = SUBSIDIARY
    ent.holding = HOLDING
    ent.ultimate_holding = HOLDING
    ent.directors = [Signatory("Asha Raman", "Director", din="01234567"), Signatory("Lim Wei Jie", "Director", din="09876543")]
    ent.activity = "regional analytics and software support services"
    books.entity = ent
    books.fx = {"CY": {"closing": D("63.80"), "average": D("63.20"), "historical": D("55.40")},
                "PY": {"closing": D("62.10"), "average": D("61.70"), "historical": D("55.40")}}
    books.tb.entity = ent.name
    books.tb.functional_currency = "SGD"
    books.tb.fx_rates = books.fx
    return books


def make_workbook(
    path: str | Path,
    *,
    seed: int = 7,
    exact: bool = False,
    units: str = "lakhs",
    layout: str = "annual",
    inject: list[str] | None = None,
    shared_formulas: bool = True,
) -> SyntheticResult:
    names = list(dict.fromkeys(inject or []))
    reg = registry()
    for n in names:
        if n not in reg:
            raise KeyError(f"unknown injection {n!r}; see list_injections()")
        if layout not in reg[n].layouts:
            raise ValueError(f"injection {n!r} needs layout {reg[n].layouts}")
    if layout == "spfs":
        books = spfs_books(seed=seed, exact=exact)
    else:
        books = generate_books(seed=seed, exact=exact, units=units)
    inp = PrepareInputs.from_books(books, layout=layout, flags=build_flags(names))
    cx = build_spec(inp)
    ic = InjectContext(cx)
    ic.values = compute_values(cx.spec)
    expected: list[ExpectedFinding] = []
    fc_vals = None
    for phase in ("build", "post"):
        if phase == "post" and layout == "spfs":
            fc_vals = compute_values(cx.spec)  # FC helper columns come from the books, not from INR-only errors
        for n in names:
            if reg[n].phase == phase:
                expected.extend(_tag(reg[n].fn(ic), n))
    vals = finalise(cx, fc_values=fc_vals)
    ic.values = vals
    for n in names:
        if reg[n].phase == "text":
            expected.extend(_tag(reg[n].fn(ic), n))
    vals = compute_values(cx.spec)
    out = write_workbook(cx.spec, path, vals, shared_formulas=shared_formulas)
    expected = _dedupe(expected)
    index = cx.spec.entity["index"]
    _add_oracle_findings(out, index, expected, natural=not names)
    gaps = simulate_totals_fix(out, index)
    return SyntheticResult(out, expected, cx.spec, books, gaps, index, names, list(ic.protected), vals)


def _dedupe(items: list[ExpectedFinding]) -> list[ExpectedFinding]:
    seen: dict[tuple[str, str, tuple[str, ...]], ExpectedFinding] = {}
    for e in items:
        k = e.key()
        if k in seen:
            prev = seen[k]
            prev.required = prev.required or e.required
            if e.note not in prev.note:
                prev.note = f"{prev.note}; {e.note}"
        else:
            seen[k] = e
    return list(seen.values())


def _tag(items: list[ExpectedFinding], name: str) -> list[ExpectedFinding]:
    for e in items:
        e.note = f"[{name}] {e.note}".strip()
    return items


def _add_oracle_findings(path: Path, index: dict[str, Any], expected: list[ExpectedFinding], *, natural: bool) -> None:
    """Casting differences and broken ties visible in the written file but not declared by an injector."""
    seen = {(e.rule_id, e.sheet, c) for e in expected for c in e.cells}
    source = "natural" if natural else "collateral"
    for issue in scan_casting(path):
        if ("CAST-01", issue.sheet, issue.cell) in seen or ("CAST-02", issue.sheet, issue.cell) in seen:
            continue
        expected.append(ExpectedFinding("CAST-01", issue.sheet, [issue.cell], f"printed {issue.printed} vs printed lines {issue.components}", "High", source))
        seen.add(("CAST-01", issue.sheet, issue.cell))
    gaps = tie_gaps(path, index)
    for k, gap in gaps["bs"].items():
        if gap:
            sheet, row, cols = index["bs.total_assets"]
            from fscore.engine.refs import make_addr

            cell = make_addr(row, cols[k])
            if ("BAL-01", sheet, cell) not in seen:
                expected.append(ExpectedFinding("BAL-01", sheet, [cell], f"Balance Sheet {k} does not tally on print by {gap}", "High", source))
    for k, gap in gaps["cf"].items():
        if gap:
            sheet, row, cols = index["cf.closing_cash"]
            from fscore.engine.refs import make_addr

            cell = make_addr(row, cols[k])
            if ("BAL-02", sheet, cell) not in seen:
                expected.append(ExpectedFinding("BAL-02", sheet, [cell], f"cash flow {k} closing cash ≠ Balance Sheet cash by {gap}", "High", source))


def make_version_pair(directory: str | Path, *, seed: int = 7, change: str = "plug_total_minus") -> tuple[SyntheticResult, SyntheticResult, list[tuple[str, str, str, str]]]:
    """Two versions of one workbook: v1 clean, v2 with ``change`` applied.

    Used for PDF staleness (render v1, reconcile against v2: PDF-01) and for
    "opened and saved since last run" tests. Returns the printed cells whose
    displayed text differs between the versions (sheet, cell, v1, v2).
    """
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    v1 = make_workbook(d / "v1.xlsx", seed=seed)
    v2 = make_workbook(d / "v2.xlsx", seed=seed, inject=[change])
    from fscore.io.xlsx_reader import Workbook
    from fscore.synthetic.oracle import print_sheets

    moved: list[tuple[str, str, str, str]] = []
    with Workbook.open(v1.path) as a, Workbook.open(v2.path) as b:
        for name in print_sheets(a):
            sa, sb = a.sheet(name), b.get_sheet(name)
            if sb is None:
                continue
            for c in sa.iter_cells():
                if not c.is_number or not sa.is_printed(c.row, c.col):
                    continue
                cb = sb.cell(c.row, c.col)
                ta = a.render_cell(c).clean
                tbv = b.render_cell(cb).clean if cb is not None else ""
                if ta != tbv:
                    moved.append((name, c.addr, ta, tbv))
    return v1, v2, moved
