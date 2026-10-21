"""Helpers for the cross-reference / roll-forward / schedule / ratio check tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fscore.checks.base import CheckContext
from fscore.config import EngagementConfig
from fscore.io.xlsx_reader import Workbook
from fscore.models import Finding
from fscore.prepare.spec import SheetSpec, WorkbookSpec
from fscore.prepare.writer import compute_values, write_workbook
from fscore.registry import load_all_checks
from fscore.synthetic.generator import SyntheticResult

FAMILIES = ("ROLL-", "XREF-", "SUB-", "RATIO-")
MODULES = ("fscore.checks.rollforward", "fscore.checks.xref", "fscore.checks.tie_outs", "fscore.checks.subledger",
           "fscore.checks.share_capital", "fscore.checks.ratios")


def config_for(layout: str = "annual") -> EngagementConfig:
    if layout == "spfs":
        return EngagementConfig(period_type="spfs", units="millions")
    return EngagementConfig()


def context(path: Path, config: EngagementConfig | None = None, *, prior_year: Path | None = None) -> CheckContext:
    return CheckContext(Workbook.open(path), config or EngagementConfig(), prior_year_path=prior_year)


def run_rules(path: Path, config: EngagementConfig | None = None, *, prior_year: Path | None = None) -> list[Finding]:
    """Run only this family's checks, in registry order, and return their findings."""
    ctx = context(path, config, prior_year=prior_year)
    for impl in load_all_checks():
        if impl.name.rsplit(".", 1)[0] in MODULES:
            impl.fn(ctx)
    return [f for f in ctx.findings if f.rule_id.startswith(FAMILIES)]


def found(findings: list[Finding], rule: str, sheet: str, *cells: str) -> bool:
    """A finding of ``rule`` on ``sheet`` whose cells overlap ``cells`` (any cell when none given)."""
    return any(f.rule_id == rule and f.sheet == sheet and (not cells or set(cells) & set(f.cells)) for f in findings)


def summary(findings: list[Finding]) -> list[tuple[str, str | None, list[str]]]:
    return [(f.rule_id, f.sheet, f.cells) for f in findings]


def rewrite(result: SyntheticResult, mutate: Callable[[WorkbookSpec], None], path: Path) -> Path:
    """Apply an extra mutation to a generated workbook spec and write it again (values recomputed)."""
    mutate(result.spec)
    return write_workbook(result.spec, path, compute_values(result.spec), shared_formulas=True)


def cell_of(result: SyntheticResult, alias: str, period: str) -> tuple[str, int, int]:
    sheet, row, cols = result.index[alias]
    return sheet, row, int(cols[period])


# ------------------------------------------------------------------------------------------- small books


def statement_sheet(spec: WorkbookSpec, name: str, title: str, h1: str = "As at March 31, 2026", h2: str = "As at March 31, 2025",
                    *, role: str = "note") -> SheetSpec:
    s = spec.add(SheetSpec(name, role=role, selected=True))
    s.set(1, 1, "Synthetic Widgets Private Limited", style="title")
    s.set(2, 1, title, style="subtitle")
    s.set(3, 1, "(All amounts in ₹ lakhs, unless otherwise stated)", style="unit")
    s.set(5, 1, "Particulars", style="header_left")
    s.set(5, 2, h1, style="header")
    s.set(5, 3, h2, style="header")
    s.col_widths = {1: 48, 2: 16, 3: 16, 4: 16, 5: 16, 6: 16, 7: 30}
    return s


def write_small(spec: WorkbookSpec, path: Path) -> Path:
    for s in spec.sheets:
        if s.print_area is None:
            s.print_area = (1, 1, max(s.max_row, 1), max(s.max_col, 1))
    return write_workbook(spec, path, shared_formulas=False)
