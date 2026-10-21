"""Shared context for every check (02 §6). Checks read the workbook only through
this object, so caching (rendered values, parsed formulas, linear forms) and
the "is this printed?" rules are implemented once.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fscore.config import EngagementConfig
from fscore.engine import formula as F
from fscore.engine.display import Rendered, format_info, is_date_format
from fscore.engine.linear import LinearForm, linear_form
from fscore.engine.printsel import select_print_sheets
from fscore.engine.refs import make_addr, split_addr
from fscore.io.xlsx_reader import Cell, Sheet, Workbook
from fscore.models import Certainty, Evidence, Finding, FixKind, Severity
from fscore.registry import get_rule

if TYPE_CHECKING:
    from fscore.engine.keyfigures import KeyFigures
    from fscore.engine.statements import StatementMap
    from fscore.engine.tables import Table

_TOTAL_RE = re.compile(r"^\s*(sub[\s-]*)?total\b|\btotal\s*(\(|$)|^\s*grand total|^\s*net\s+(block|carrying)", re.I)


def is_total_caption(text: str) -> bool:
    return bool(_TOTAL_RE.search(text or ""))


@dataclass(frozen=True)
class CellPos:
    sheet: str
    row: int
    col: int

    @property
    def addr(self) -> str:
        return make_addr(self.row, self.col)


class CheckContext:
    """Everything a check needs about one workbook run."""

    def __init__(
        self,
        wb: Workbook,
        config: EngagementConfig,
        *,
        pdf_path: Path | None = None,
        prior_year_path: Path | None = None,
    ) -> None:
        self.wb = wb
        self.config = config
        self.pdf_path = pdf_path
        self.prior_year_path = prior_year_path
        self.findings: list[Finding] = []
        self.warnings: list[str] = []
        self.shared: dict[str, Any] = {}  # results one check publishes for later ones (e.g. narrative mentions)
        self._linear: dict[tuple[str, int, int], LinearForm | None] = {}
        self._parsed: dict[tuple[str, int, int], F.Node | None] = {}
        self._captions: dict[tuple[str, int], str] = {}

    # ------------------------------------------------------------------ sheets
    @cached_property
    def print_sheets(self) -> list[Sheet]:
        sheets = select_print_sheets(self.wb, self.config)
        for s in sheets:
            s.ensure_loaded()
        return sheets

    @cached_property
    def print_sheet_names(self) -> set[str]:
        return {s.name for s in self.print_sheets}

    def sheet(self, name: str) -> Sheet | None:
        return self.wb.get_sheet(name)

    def is_print_sheet(self, name: str) -> bool:
        s = self.wb.get_sheet(name)
        return s is not None and s.name in self.print_sheet_names

    # ------------------------------------------------------------------ analysis layers (lazy)
    @cached_property
    def statements(self) -> StatementMap:
        from fscore.engine.statements import detect_statements

        return detect_statements(self)

    @cached_property
    def key_figures(self) -> KeyFigures:
        from fscore.engine.keyfigures import compute_key_figures

        return compute_key_figures(self)

    def tables(self, sheet: Sheet) -> list[Table]:
        key = f"tables:{sheet.name}"
        if key not in self.shared:
            from fscore.engine.tables import detect_tables

            self.shared[key] = detect_tables(self, sheet)
        return self.shared[key]  # type: ignore[no-any-return]

    # ------------------------------------------------------------------ cells
    def cell(self, sheet: str | Sheet, row: int, col: int) -> Cell | None:
        s = sheet if isinstance(sheet, Sheet) else self.wb.get_sheet(sheet)
        return None if s is None else s.cell(row, col)

    def rendered(self, sheet: Sheet, cell: Cell) -> Rendered:
        return self.wb.render_cell(cell)

    def displayed(self, sheet: Sheet | str, row: int, col: int) -> Decimal | None:
        """Printed number of a cell in its own units (None for text/blank/errors/dates)."""
        s = sheet if isinstance(sheet, Sheet) else self.wb.get_sheet(sheet)
        if s is None:
            return None
        c = s.cell(row, col)
        if c is None or c.value is None:
            return None
        if isinstance(c.value, (str, bool)):
            return None
        if is_date_format(s.number_format(c)):
            return None
        return self.wb.render_cell(c).value

    def displayed_or_zero(self, sheet: Sheet | str, row: int, col: int) -> Decimal:
        v = self.displayed(sheet, row, col)
        return v if v is not None else Decimal(0)

    def printed_text(self, sheet: Sheet, row: int, col: int) -> str:
        c = sheet.cell(row, col)
        if c is None or c.value is None:
            return ""
        return self.wb.render_cell(c).clean

    def decimals(self, sheet: Sheet, cell: Cell) -> int:
        return format_info(sheet.number_format(cell)).value_decimals

    def is_printed(self, sheet: Sheet, row: int, col: int) -> bool:
        """On paper: print sheet, visible sheet, in print area, row/col visible, format not ';;;'."""
        if sheet.name not in self.print_sheet_names or not sheet.visible:
            return False
        if not sheet.is_printed(row, col):
            return False
        c = sheet.cell(row, col)
        return not (c is not None and format_info(sheet.number_format(c)).hidden)

    def iter_printed_numbers(self, sheet: Sheet) -> Iterator[Cell]:
        """Visible numeric cells in the print area (excluding date-formatted cells)."""
        for c in sheet.iter_cells():
            if not c.is_number:
                continue
            if not self.is_printed(sheet, c.row, c.col):
                continue
            if is_date_format(sheet.number_format(c)):
                continue
            yield c

    def iter_printed_text(self, sheet: Sheet) -> Iterator[Cell]:
        for c in sheet.iter_cells():
            if c.is_text and self.is_printed(sheet, c.row, c.col) and str(c.value).strip():
                yield c

    # ------------------------------------------------------------------ formulas
    def parsed(self, sheet: Sheet, cell: Cell) -> F.Node | None:
        key = (sheet.name, cell.row, cell.col)
        if key not in self._parsed:
            node: F.Node | None = None
            if cell.formula:
                try:
                    node = F.parse(cell.formula)
                except F.FormulaError:
                    node = None
            self._parsed[key] = node
        return self._parsed[key]

    def linear(self, sheet: Sheet, cell: Cell) -> LinearForm | None:
        key = (sheet.name, cell.row, cell.col)
        if key not in self._linear:
            node = self.parsed(sheet, cell)
            self._linear[key] = None if node is None else linear_form(node, sheet.name)
        return self._linear[key]

    def resolve_sheet(self, name: str) -> Sheet | None:
        return self.wb.get_sheet(name)

    # ------------------------------------------------------------------ captions
    def caption(self, sheet: Sheet, row: int, before_col: int | None = None) -> str:
        """Row label: text cells of the row to the left of ``before_col`` (or of the first number)."""
        key = (sheet.name, row) if before_col is None else (sheet.name + f"#{before_col}", row)
        if key in self._captions:
            return self._captions[key]
        parts: list[str] = []
        limit = before_col or 10_000
        for col in range(1, min(limit, sheet.max_col + 1)):
            c = sheet.cell(row, col)
            if c is None or c.value is None:
                continue
            if isinstance(c.value, str) and c.value.strip():
                t = c.value.strip()
                if re.fullmatch(r"\(?[ivxlcdm]+\)?|\(?[a-z]\)|\d{1,2}(\.\d+)?", t, re.I) and parts == []:
                    # item numbering such as (a), (ii), 4.1 — keep but separate
                    parts.append(t)
                    continue
                parts.append(t)
            elif c.is_number and before_col is None:
                if not (float(c.value).is_integer() and abs(c.value) < 100 and not c.is_formula):  # note numbers are fine
                    break
        text = " ".join(parts).strip()
        self._captions[key] = text
        return text

    def column_header(self, sheet: Sheet, col: int, above_row: int, max_up: int = 40) -> str:
        """Nearest header text above a numeric cell in the same column."""
        for r in range(above_row - 1, max(0, above_row - max_up), -1):
            c = sheet.cell(r, col)
            if c is None or c.value is None:
                continue
            if isinstance(c.value, str) and c.value.strip():
                return c.value.strip()
            if is_date_format(sheet.number_format(c)):
                return self.wb.render_cell(c).clean
        return ""

    # ------------------------------------------------------------------ findings
    def enabled(self, rule_id: str) -> bool:
        return rule_id not in self.config.disabled_rules

    def emit(
        self,
        rule_id: str,
        *,
        sheet: str | None = None,
        cells: list[str] | None = None,
        explanation: str,
        severity: Severity | None = None,
        certainty: Certainty | None = None,
        fix_kind: FixKind | None = None,
        caption: str | None = None,
        printed: str | None = None,
        expected: str | None = None,
        difference: Decimal | None = None,
        evidence: list[Evidence] | None = None,
        suggested_fix: str | None = None,
        period: str | None = None,
        key: str = "",
        tags: list[str] | None = None,
        category: str | None = None,
    ) -> Finding | None:
        if not self.enabled(rule_id):
            return None
        info = get_rule(rule_id)
        f = Finding(
            rule_id=rule_id,
            rule_version=info.version,
            family=info.family,
            severity=severity or info.severity,
            certainty=certainty or info.certainty,
            category=category or info.title,
            sheet=sheet,
            cells=cells or [],
            caption=caption,
            printed=printed,
            expected=expected,
            difference=difference,
            evidence=evidence or [],
            explanation=explanation,
            suggested_fix=suggested_fix,
            fix_kind=fix_kind or info.fix_kind,
            period=period,
            key=key,
            tags=tags or [],
        )
        f.fingerprint = f.compute_fingerprint()
        # one finding per (rule, location, key)
        if any(x.fingerprint == f.fingerprint for x in self.findings):
            return None
        self.findings.append(f)
        return f

    def evidence_for(self, sheet: Sheet | str, row: int, col: int, label: str | None = None) -> Evidence:
        s = sheet if isinstance(sheet, Sheet) else self.wb.get_sheet(sheet)
        if s is None:
            return Evidence(label=label or "?")
        from fscore.models import CellRef

        c = s.cell(row, col)
        printed = self.wb.render_cell(c).clean if c is not None and c.value is not None else ""
        return Evidence(
            label=label or self.caption(s, row) or make_addr(row, col),
            ref=CellRef(sheet=s.name, addr=make_addr(row, col)),
            printed=printed,
            value=self.displayed(s, row, col),
        )


def fmt_amount(d: Decimal | None, decimals: int = 2) -> str:
    if d is None:
        return ""
    q = Decimal(1).scaleb(-decimals)
    v = d.quantize(q)
    s = f"{abs(v):,.{decimals}f}"
    return f"({s})" if v < 0 else s


def addr_to_rc(addr: str) -> tuple[int, int]:
    return split_addr(addr)
