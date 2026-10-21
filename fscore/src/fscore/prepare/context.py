"""Build context shared by the statement, note and backup builders."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fscore.engine.refs import index_to_col
from fscore.prepare.inputs import PrepareInputs
from fscore.prepare.layout import Fx, Page, q2
from fscore.prepare.mapping import presentation_sign
from fscore.prepare.spec import WorkbookSpec

TB_SHEET = "NS_TB_FT27"
TB_FIRST_ROW = 5
TB_KEY_COL = "H"

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def long_date(d: date) -> str:
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


@dataclass
class BuildContext:
    spec: WorkbookSpec
    inp: PrepareInputs
    pages: dict[str, Page] = field(default_factory=dict)
    tb_last_row: int = 0
    tb_cols: dict[str, str] = field(default_factory=dict)  # period -> TB column letter
    notes: dict[str, int] = field(default_factory=dict)  # note topic -> number
    expected_rows: dict[str, Any] = field(default_factory=dict)  # build-time injection bookkeeping
    precomputed: dict[str, Any] = field(default_factory=dict)  # figures derived before layout (tax reconciliation, PY ratios)

    # ------------------------------------------------------------------ periods
    @property
    def ye(self) -> date:
        return self.inp.entity.year_end

    @property
    def py(self) -> date:
        return self.inp.entity.py_end

    @property
    def cy_open(self) -> date:
        """First day of the current year (the day after the PY end)."""
        return self.py + timedelta(days=1)

    @property
    def py_open(self) -> date:
        return date(self.py.year - 1, self.py.month, self.py.day) + timedelta(days=1)

    @property
    def spfs(self) -> bool:
        return self.inp.layout == "spfs"

    @property
    def pl_periods(self) -> list[str]:
        return ["Q4_CY", "Q4_PY", "CY", "PY"] if self.spfs else ["CY", "PY"]

    @property
    def div(self) -> int:
        return self.inp.divisor

    @property
    def unit_line(self) -> str:
        return f"(All amounts in {self.inp.entity.unit_label}, unless otherwise stated)"

    # ------------------------------------------------------------------ TB formulas
    def tb(self, keys: str | list[str], period: str, sign: int | None = None) -> Fx:
        """SUMIF link(s) to the TB backup in presentation units and sign."""
        ks = [keys] if isinstance(keys, str) else list(keys)
        if sign is None:
            sign = presentation_sign(ks[0])
        col = self.tb_cols[period]
        last = self.tb_last_row
        parts = [f'SUMIF({TB_SHEET}!${TB_KEY_COL}${TB_FIRST_ROW}:${TB_KEY_COL}${last},"{k}",{TB_SHEET}!${col}${TB_FIRST_ROW}:${col}${last})' for k in ks]
        body = parts[0] if len(parts) == 1 else "(" + "+".join(parts) + ")"
        f = f"{body}/{self.div}" if self.div != 1 else body
        return Fx(("-" if sign < 0 else "") + f)

    def tb_value(self, keys: str | list[str], period: str, sign: int | None = None) -> Decimal:
        """The exact (unrounded) presentation value of TB keys — rupees / divisor."""
        ks = [keys] if isinstance(keys, str) else list(keys)
        if sign is None:
            sign = presentation_sign(ks[0])
        total = Decimal(0)
        for a in self.inp.tb.accounts:
            if a.key in ks:
                total += self.amount_for(a.code, a.amount("CY" if period in ("CY", "Q4_CY") else "PY"), period)
        return sign * total / self.div

    def amount_for(self, code: str, fy_amount: Decimal, period: str) -> Decimal:
        if period.startswith("Q4_"):
            frac = self.inp.quarter_fraction.get(code, Decimal("0.25"))
            return self.quarter_amount(fy_amount, frac)
        return fy_amount

    def quarter_amount(self, fy: Decimal, frac: Decimal) -> Decimal:
        q = Decimal("0.01") if self.inp.exact else Decimal(self.div) / 100
        return (fy * frac / q).quantize(Decimal(1)) * q

    def u(self, rupees: Decimal) -> Decimal:
        """Rupees -> presentation unit, rounded to 2 dp (typed figures)."""
        return q2(Decimal(rupees) / self.div)

    def raw(self, rupees: Decimal) -> Decimal:
        return Decimal(rupees) / self.div

    def col_letter(self, col: int) -> str:
        return index_to_col(col)
