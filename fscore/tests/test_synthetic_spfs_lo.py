"""SPFS quarterly layout, and LibreOffice as an independent recalculation oracle."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from helpers_synthetic import cached_workbook, libreoffice_recalc, printed_alias, soffice

from fscore.engine.evaluator import values_equal
from fscore.io.xlsx_reader import Workbook
from fscore.synthetic.oracle import print_sheets, scan_casting, tie_gaps


@pytest.fixture(scope="module")
def spfs():
    return cached_workbook(seed=7, layout="spfs")


def test_spfs_builds_balances_and_ties(spfs) -> None:
    assert spfs.expected == []
    assert scan_casting(spfs.path) == []
    assert tie_gaps(spfs.path, spfs.index) == {"bs": {"CY": 0, "PY": 0}, "cf": {"CY": 0, "PY": 0}}
    with Workbook.open(spfs.path) as wb:
        assert wb.sheet("PL").cell(1, 1).value == "Corvus Systems Pte. Ltd."
        assert "million" in wb.sheet("BS").cell(3, 1).value
        _s, _r, cols = spfs.index["pl.revenue"]
        assert set(cols) == {"Q4_CY", "Q4_PY", "CY", "PY"}
        for alias in ("pl.revenue", "pl.employee", "pl.other_expenses", "pl.depreciation"):
            for q, fy in (("Q4_CY", "CY"), ("Q4_PY", "PY")):
                qv, fv = printed_alias(wb, spfs.index, alias, q), printed_alias(wb, spfs.index, alias, fy)
                assert 0 < abs(qv) < abs(fv), (alias, q)


def test_spfs_functional_currency_helpers(spfs) -> None:
    with Workbook.open(spfs.path) as wb:
        bs = wb.sheet("BS")
        fc = spfs.spec.entity["fc_cols"]
        for k, col in fc.items():
            assert bs.col_hidden(col)
            _s, r_ta, _c = spfs.index["bs.total_assets"]
            _s, r_tel, _c = spfs.index["bs.total_eq_liab"]
            ta, tel = bs.cell(r_ta, col).value, bs.cell(r_tel, col).value
            assert abs(ta - tel) < 1e-6, k


def test_spfs_translation_gap_and_stale_column() -> None:
    r = cached_workbook(seed=7, layout="spfs", inject=("translation_gap", "stale_dec_column"))
    gaps = tie_gaps(r.path, r.index)
    assert abs(gaps["bs"]["CY"]) > Decimal("0.20")
    with Workbook.open(r.path) as wb:
        assert wb.sheet("BS").cell(5, 5).value.startswith("As at")
        assert not wb.sheet("BS").col_hidden(5)


def _compare_with_libreoffice(path: Path, out_dir: Path) -> list[str]:
    lo = libreoffice_recalc(path, out_dir)
    mism: list[str] = []
    with Workbook.open(path) as ours, Workbook.open(lo) as theirs:
        for name in print_sheets(ours):
            a, b = ours.sheet(name), theirs.sheet(name)
            for c in a.iter_cells():
                if c.formula is None:
                    continue
                cb = b.cell(c.row, c.col)
                v = None if cb is None else cb.value
                if not values_equal(v, c.value):
                    mism.append(f"{name}!{c.addr}: ours {c.value!r} lo {v!r} ({c.formula[:60]})")
    return mism


@pytest.mark.libreoffice
@pytest.mark.skipif(soffice() is None, reason="LibreOffice not installed")
@pytest.mark.parametrize(("exact", "layout"), [(False, "annual"), (True, "annual"), (False, "spfs")])
def test_libreoffice_recalculates_to_our_cached_values(tmp_path, exact: bool, layout: str) -> None:
    r = cached_workbook(seed=7, exact=exact, layout=layout)
    mism = _compare_with_libreoffice(r.path, tmp_path)
    assert not mism, "\n".join(mism[:15])
