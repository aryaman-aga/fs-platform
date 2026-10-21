"""hide_safety API used by the fix planner (column / row hide verdicts, out-of-scope listings)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from helpers_checks_t import clean_spec, context, write
from test_checks_multi import _rollforward_spec, add_bs_column

from fscore.checks.hide_safety import (
    HideVerdict,
    allowed_row_dates,
    column_hide_safety,
    out_of_scope_columns,
    out_of_scope_rows,
    row_hide_safety,
)
from fscore.config import EngagementConfig


def test_out_of_scope_listings(tmp_path: Path) -> None:
    spec = clean_spec()
    add_bs_column(spec, 5, "As at December 31, 2025")
    ctx = context(write(spec, tmp_path / "a.xlsx"))
    assert out_of_scope_columns(ctx) == [("BS", 5, "As at December 31, 2025")]
    assert out_of_scope_rows(ctx) == []
    v = column_hide_safety(ctx, "BS", 5)
    assert isinstance(v, HideVerdict) and v.safe and v.blocking_cells == []


def test_in_scope_column_is_never_safe(tmp_path: Path) -> None:
    ctx = context(write(clean_spec(), tmp_path / "b.xlsx"))
    v = column_hide_safety(ctx, "BS", 4)  # PY column: figures and the directors' signature block
    assert not v.safe
    assert "signature block" in v.reasons and "another table's figures" in v.reasons
    assert any(c.startswith("BS!D") for c in v.blocking_cells)


def test_column_feeding_a_visible_cross_total(tmp_path: Path) -> None:
    spec = clean_spec()
    spec.sheet("Notes").set(6, 4, "As at December 31, 2025", style="header")  # 'Furniture' column relabelled out of scope
    ctx = context(write(spec, tmp_path / "c.xlsx"))
    v = column_hide_safety(ctx, "Notes", 4)
    assert not v.safe and "feeds a visible total in another column" in v.reasons


def test_rows(tmp_path: Path) -> None:
    ctx = context(write(_rollforward_spec(), tmp_path / "d.xlsx"))
    rows = {r: cap for _s, r, cap in out_of_scope_rows(ctx)}
    assert set(rows) == {9, 16}
    assert row_hide_safety(ctx, "Note 3-5", 9).safe  # intermediate balance: visible rows still add
    v = row_hide_safety(ctx, "Note 3-5", 16)
    assert not v.safe and v.blocking_cells and "opening to closing" in " ".join(v.reasons)
    v2 = row_hide_safety(ctx, "Note 3-5", 8)  # an ordinary movement row feeding the closing balance
    assert not v2.safe


def test_zero_row_can_be_hidden(tmp_path: Path) -> None:
    spec = _rollforward_spec()
    s = spec.sheet("Note 3-5")
    s.set(16, 3, 0.0, style="amount")
    s.set(16, 4, 0.0, style="amount")
    ctx = context(write(spec, tmp_path / "e.xlsx"))
    assert row_hide_safety(ctx, "Note 3-5", 16).safe


def test_allowed_row_dates_annual_and_spfs() -> None:
    from datetime import date

    annual = allowed_row_dates(EngagementConfig())
    assert {date(2026, 3, 31), date(2025, 3, 31), date(2025, 4, 1), date(2024, 4, 1), date(2024, 3, 31)} <= annual
    assert date(2025, 12, 31) not in annual
    spfs = allowed_row_dates(EngagementConfig(period_type="spfs"))
    assert date(2025, 12, 31) not in spfs and date(2025, 4, 1) in spfs
