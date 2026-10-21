"""The synthetic double-entry ledger: balanced, tied and deterministic."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fscore.prepare.mapping import ASSET_PREFIXES, LIABILITY_PREFIXES
from fscore.synthetic.ledger import ACCOUNTS, generate_books, key_balance, ledger_cash_flow


@pytest.mark.parametrize("exact", [False, True])
@pytest.mark.parametrize("units", ["lakhs", "millions"])
def test_trial_balances_net_to_zero(exact: bool, units: str) -> None:
    books = generate_books(seed=7, exact=exact, units=units)
    for p in ("CY", "PY"):
        assert books.tb.total(p) == 0
    assert all(a.key for a in books.tb.accounts), "every synthetic account is mapped"


@pytest.mark.parametrize("seed", [7, 11, 23, 101])
def test_balance_sheet_balances_and_cash_flow_ties(seed: int) -> None:
    books = generate_books(seed=seed)
    for yk in ("PY", "CY"):
        close = books.years[yk].bs_close
        assets = sum((v for c, v in close.items() if ACCOUNTS[c][1].startswith(ASSET_PREFIXES)), Decimal(0))
        eq_liab = -sum((v for c, v in close.items() if ACCOUNTS[c][1].startswith(LIABILITY_PREFIXES)), Decimal(0))
        pl = books.years[yk].pl
        profit = -sum(pl.values(), Decimal(0))  # year's result not yet closed to retained earnings
        assert assets == eq_liab + profit
        cf = ledger_cash_flow(books, yk)
        assert cf["cash_open"] + cf["net_change"] == cf["cash_close"]


def test_economics_loss_then_profit() -> None:
    books = generate_books(seed=7)
    assert ledger_cash_flow(books, "PY")["pbt"] < 0
    assert ledger_cash_flow(books, "CY")["pbt"] > 0
    assert key_balance(books.years["CY"].bs_close, "oe.retained_earnings") > 0  # debit = accumulated losses


def test_deterministic_per_seed() -> None:
    a, b, c = generate_books(seed=7), generate_books(seed=7), generate_books(seed=8)
    amounts = lambda bk: [(x.code, x.amounts["CY"], x.amounts["PY"]) for x in bk.tb.accounts]  # noqa: E731
    assert amounts(a) == amounts(b)
    assert amounts(a) != amounts(c)


def test_round_mode_quantum_and_exact_paise() -> None:
    r = generate_books(seed=7, exact=False, units="lakhs")
    assert all(x.amounts[p] % 1000 == 0 for x in r.tb.accounts for p in ("CY", "PY"))
    x = generate_books(seed=7, exact=True, units="lakhs")
    assert any(a.amounts["CY"] % 1 != 0 for a in x.tb.accounts), "exact mode carries paise"


def test_supplementary_rolls_are_consistent() -> None:
    books = generate_books(seed=7)
    for yk in ("PY", "CY"):
        yd = books.years[yk]
        for roll in yd.far.values():
            assert roll.opening + roll.additions - roll.disposals == roll.closing
            assert roll.dep_opening + roll.dep_charge - roll.dep_disposals == roll.dep_closing
        g = yd.gratuity
        assert g["opening"] + g["service_cost"] + g["interest_cost"] + g["remeasurement"] - g["benefits_paid"] == g["closing"]
        ls = yd.lease
        assert ls["opening"] + ls["additions"] + ls["interest"] - ls["payments"] == ls["closing"]
        hl = yd.holding_loan
        assert hl["opening"] + hl["received"] + hl["credited"] - hl["repaid"] == hl["closing"]
    assert books.years["CY"].far["computers"].opening == books.years["PY"].far["computers"].closing
