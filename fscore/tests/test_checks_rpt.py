"""Related-party rules RPT-01…12 on a small synthetic entity (all names and figures invented)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from helpers_narr import FELLOW_UK, FELLOW_US, HOLDING, EntityOpts, build_entity, config, rules, run

from fscore.rpt import Confirmation
from fscore.runner import build_context, run_checks


def _run(tmp_path: Path, name: str, opts: EntityOpts, **cfg: object):  # type: ignore[no-untyped-def]
    return run(build_entity(tmp_path / f"{name}.xlsx", opts), config(**cfg) if cfg else None)


def _msgs(res, rule: str) -> str:  # type: ignore[no-untyped-def]
    return " | ".join(f.explanation for f in res.findings if f.rule_id == rule)


def _map_bal(opts: EntityOpts, fn) -> None:  # type: ignore[no-untyped-def]
    opts.bal = [(n, [fn(n, p, cy, py) for p, cy, py in rows]) for n, rows in opts.bal]


def test_clean_entity_reconciles(tmp_path: Path) -> None:
    res = _run(tmp_path, "clean", EntityOpts())
    assert rules(res, "RPT") == []
    statuses = {(r["counterparty"], r["nature"], r["period"]): r["status"] for r in res.rpt_recon}
    assert statuses[(HOLDING, "trade_payable", "CY")] == "Agreed"
    assert statuses[(FELLOW_US, "services_rendered", "PY")] == "Agreed"


def test_rpt09_omitted_balance(tmp_path: Path) -> None:
    o = EntityOpts()
    o.bal = [(n, rows) for n, rows in o.bal if n != "Trade payables"]
    res = _run(tmp_path, "omit", o)
    assert "trade_payable 30.40 (CY) per the trial balance is not disclosed" in _msgs(res, "RPT-09")


def test_rpt09_wrong_counterparty_reported_once(tmp_path: Path) -> None:
    o = EntityOpts()
    _map_bal(o, lambda n, p, cy, py: (FELLOW_US if n.startswith("Recoverable") else p, cy, py))
    res = _run(tmp_path, "cp", o)
    hits = [f for f in res.findings if f.rule_id == "RPT-09" and "wrong counterparty" in f.tags]
    assert len(hits) == 2  # CY and PY, each once
    assert all(FELLOW_UK in f.explanation for f in hits)


def test_rpt09_gross_vs_net(tmp_path: Path) -> None:
    o = EntityOpts()
    o.tb_rows = [*o.tb_rows, ("40030", f"IC Recovery adjustment - {FELLOW_US}", "Income", "Revenue from Operations", "Intercompany Revenue",
                              "rev.related_support", 2_000_000, 0)]
    res = _run(tmp_path, "gross", o)
    assert "gross vs net" in _msgs(res, "RPT-09")


def test_rpt09_debit_balance_shown_as_payable(tmp_path: Path) -> None:
    o = EntityOpts()
    o.tb_rows = [r if r[0] != "22020" else (*r[:6], 500_000, -2_210_000) for r in o.tb_rows]
    _map_bal(o, lambda n, p, cy, py: (p, -5.0 if n == "Trade payables" else cy, py))
    res = _run(tmp_path, "debit", o)
    assert "debit balance shown as a liability" in _msgs(res, "RPT-09")


def test_rpt02_duplicate_and_stale_rows(tmp_path: Path) -> None:
    o = EntityOpts()
    o.bal = [(n, rows + rows if n == "Trade receivables" else rows) for n, rows in o.bal]
    _map_bal(o, lambda n, p, cy, py: (p, 85.0, 85.0) if n == "Loan payable" else (p, cy, py))
    res = _run(tmp_path, "dup", o)
    msgs = _msgs(res, "RPT-02")
    assert "appears twice" in msgs and "stale row" in msgs


def test_rpt03_hidden_row_with_values(tmp_path: Path) -> None:
    res = _run(tmp_path, "hidden", EntityOpts(rpt_hidden_rows=[("balances", "Corvus Systems Pte. Ltd.", 3.00, 2.00)]))
    assert "will not print" in _msgs(res, "RPT-03")


def test_rpt04_totals_vs_source_note(tmp_path: Path) -> None:
    o = EntityOpts()
    _map_bal(o, lambda n, p, cy, py: (p, 33.30 if n == "Trade payables" else cy, py))
    res = _run(tmp_path, "tot", o)
    assert "trade payables to related parties (CY) is 33.30" in _msgs(res, "RPT-04")


def test_rpt05_loan_roll_forward(tmp_path: Path) -> None:
    o = EntityOpts()
    _map_bal(o, lambda n, p, cy, py: (p, 95.0 if n == "Loan payable" else cy, py))
    res = _run(tmp_path, "roll", o)
    assert "Loan from" in _msgs(res, "RPT-05")


def test_rpt06_confirmations(tmp_path: Path) -> None:
    path = build_entity(tmp_path / "conf.xlsx", EntityOpts())
    cfg = config()
    ctx = build_context(path, cfg)
    ctx.shared["rpt_confirmations"] = [Confirmation(HOLDING, "trade_payable", Decimal("28.00")),
                                       Confirmation(FELLOW_US, "trade_receivable", Decimal("60.30"))]
    res = run_checks(path, cfg, context=ctx)
    msgs = _msgs(res, "RPT-06")
    assert "difference" in msgs and "one-sided" in msgs
    assert "Corvus Technologies" not in msgs.split("one-sided")[0]  # the agreed confirmation is not reported


def test_rpt07_name_variant_in_another_note(tmp_path: Path) -> None:
    res = _run(tmp_path, "names", EntityOpts(extra_narrative=["Corvus Holding Private Limited"]))
    assert "differs from the related-party note" in _msgs(res, "RPT-07")


def test_rpt08_intercompany_account_without_counterparty(tmp_path: Path) -> None:
    o = EntityOpts()
    o.tb_rows = [*o.tb_rows, ("22090", "Intercompany Payable - Misc", "Liabilities", "Current Liabilities", "Intercompany Payables", "", -100_000, 0)]
    res = _run(tmp_path, "nocp", o)
    assert "no identifiable counterparty" in _msgs(res, "RPT-08")


def test_rpt10_equity_issue_not_disclosed(tmp_path: Path) -> None:
    o = EntityOpts()
    o.txn = [t for t in o.txn if t[0] != "Issue of equity shares"]
    res = _run(tmp_path, "eq", o)
    assert "Equity shares of 50.00 were issued" in _msgs(res, "RPT-10")


def test_rpt01_party_missing_from_list(tmp_path: Path) -> None:
    o = EntityOpts()
    o.parties = [p for p in o.parties if p[1] != FELLOW_UK]
    res = _run(tmp_path, "party", o)
    assert "Corvus Analytics Limited" in _msgs(res, "RPT-01")


def test_rpt11_relationship_and_spelling(tmp_path: Path) -> None:
    o = EntityOpts()
    o.parties = [("Ultimate holding company", HOLDING), ("Fellow subsidiary", FELLOW_US), ("Setp-down subsidiary", FELLOW_UK), o.parties[3]]
    res = _run(tmp_path, "rel", o)
    msgs = _msgs(res, "RPT-11")
    assert "immediate holding company" in msgs and "Setp-down" in msgs


def test_rpt11_group_structure(tmp_path: Path) -> None:
    o = EntityOpts()
    o.parties = [("Holding company", HOLDING), ("Subsidiary", FELLOW_US), ("Fellow subsidiary", FELLOW_UK), o.parties[3]]
    gs = {"Corvus Data Services Private Limited": HOLDING, FELLOW_US: HOLDING, FELLOW_UK: HOLDING}
    res = _run(tmp_path, "gs", o, rpt={"group_structure": gs})
    assert "fellow subsidiary" in _msgs(res, "RPT-11")


def test_rpt12_period_scope(tmp_path: Path) -> None:
    o = EntityOpts(rpt_extra_column="As at March 31, 2024", rpt_extra_caption="Balance as at June 30, 2026 includes amounts payable on demand")
    res = _run(tmp_path, "scope", o)
    msgs = _msgs(res, "RPT-12")
    assert "Column 'As at March 31, 2024'" in msgs and "June 30, 2026" in msgs
    assert msgs.count("As at March 31, 2024") == 1
