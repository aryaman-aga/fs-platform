"""Module P: TB gate, mapping library, cast-safe preparation, disclosure checklist, rollover."""

from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import pytest

from fscore.prepare.api import prepare, prepare_from_books
from fscore.prepare.disclosure import run_checklist
from fscore.prepare.mapping import MappingVersion, suggest_key
from fscore.prepare.rollover import rollover, signed_figures_from_tb
from fscore.prepare.tb import TBAccount, load_tb, validate_tb
from fscore.synthetic.ledger import generate_books
from fscore.synthetic.oracle import scan_casting, tie_gaps


@pytest.fixture(scope="module")
def books():
    return generate_books(seed=7, exact=True)


@pytest.fixture(scope="module")
def prepared(books, tmp_path_factory):
    return prepare_from_books(books, tmp_path_factory.mktemp("prep") / "prepared.xlsx")


def test_prepared_workbook_casts_and_reports(prepared) -> None:
    assert prepared.ok, prepared.message
    assert Path(prepared.out_path).exists() and len(prepared.sha256) == 64
    assert scan_casting(prepared.out_path) == []  # cast-safe totals by construction
    gaps = tie_gaps(prepared.out_path, prepared.index)
    assert all(abs(v) <= Decimal("0.20") for kind in gaps.values() for v in kind.values())
    assert prepared.templates["library"].startswith("house-templates@")
    assert prepared.disclosure is not None
    auto = [i for i in prepared.disclosure.items if i.auto]
    assert auto and all(i.answer == "yes" for i in auto), [(i.code, i.evidence) for i in auto if i.answer != "yes"]
    assert {i.code for i in prepared.disclosure.blocking} == {i.code for i in prepared.disclosure.items if not i.auto and i.mandatory}


def test_checklist_manual_answers_complete_it(prepared) -> None:
    manual = {i.code: "na" for i in prepared.disclosure.items if not i.auto}
    report = run_checklist(prepared.out_path, manual, answered_by="manager@example-firm.in")
    assert report.complete and report.completion == 100.0
    assert next(i for i in report.items if i.code == "SCH3-CRYPTO").answered_by == "manager@example-firm.in"
    report_no = run_checklist(prepared.out_path, {**manual, "SCH3-BENAMI": "no"})
    assert [i.code for i in report_no.blocking] == ["SCH3-BENAMI"]


def test_gate_blocks_unbalanced_tb(books, tmp_path) -> None:
    tb = copy.deepcopy(books.tb)
    tb.accounts[0].amounts["CY"] += Decimal("1234.56")
    out = tmp_path / "blocked.xlsx"
    r = prepare(tb, MappingVersion.from_tb(tb, suggest=False), books.years, books.entity, out, opening=books.opening)
    assert not r.ok and not out.exists()
    assert any(i.code == "TB-NET" for i in r.tb_validation.issues)


def test_gate_blocks_unmapped_accounts(books, tmp_path) -> None:
    tb = copy.deepcopy(books.tb)
    tb.accounts.append(TBAccount(code="99999", name="Suspense", amounts={"CY": Decimal("100"), "PY": Decimal(0)}))
    tb.accounts.append(TBAccount(code="99998", name="Suspense contra", amounts={"CY": Decimal("-100"), "PY": Decimal(0)}))
    mapping = MappingVersion.from_tb(books.tb, suggest=False)
    r = prepare(tb, mapping, books.years, books.entity, tmp_path / "x.xlsx", opening=books.opening)
    assert not r.ok
    assert set(r.unmapped) == {"99999", "99998"}


def test_gate_blocks_missing_supplementary(books, tmp_path) -> None:
    years = copy.deepcopy(books.years)
    years["PY"].gratuity = {}
    r = prepare(books.tb, MappingVersion.from_tb(books.tb, suggest=False), years, books.entity, tmp_path / "y.xlsx", opening=books.opening)
    assert not r.ok and any("actuarial" in m for m in r.missing_supplementary)


def test_period_mismatch_blocks(books) -> None:
    from datetime import date

    res = validate_tb(books.tb, expected_period_ends={"CY": date(2026, 6, 30)})
    assert not res.ok and any(i.code == "TB-PERIOD" for i in res.issues)


def test_mapping_library_diff_and_suggestions(books) -> None:
    v1 = MappingVersion.from_tb(books.tb, client="Corvus", suggest=False)
    tb2 = copy.deepcopy(books.tb)
    tb2.accounts.append(TBAccount(code="53095", name="Bank charges and miscellaneous expenses", key=""))
    v2 = MappingVersion.from_tb(tb2, client="Corvus", suggest=True)
    v2.rows["13010"] = "tr.good.related"
    d = v2.diff(v1)
    assert d.added == ["53095"] and ("13010", "tr.good.third_party", "tr.good.related") in d.changed
    assert suggest_key("Accumulated Depreciation - Computers") == "ppe.accdep.computers"
    assert v1.sha256 != v2.sha256


def test_tb_loader_reads_the_backup_sheet(prepared) -> None:
    tb = load_tb(prepared.out_path, sheet="NS_TB_FT27", periods={"CY": "Mar-26", "PY": "Mar-25"})
    assert len(tb.accounts) == 78
    assert tb.total("CY") == 0 and tb.total("PY") == 0
    assert tb.by_key("cash.on_hand", "CY") != 0


def test_rollover_comparatives_and_carry_forward(books) -> None:
    prior = signed_figures_from_tb(books.tb, "CY", divisor=100_000, entity=books.entity, mapping=MappingVersion.from_tb(books.tb, suggest=False))
    nxt = copy.deepcopy(books.tb)
    for a in nxt.accounts:
        a.amounts = {"CY": a.amounts["CY"], "PY": a.amounts["CY"]}
    res = rollover(prior, nxt)
    assert res.ok, res.differences[:3]
    assert res.year_end.year == 2027
    assert res.signatories_to_reconfirm and all(s["reconfirm"] == "yes" for s in res.signatories_to_reconfirm)
    assert res.mapping is not None and res.mapping.version == 2
    nxt.accounts[0].amounts["PY"] += Decimal("500000")
    bad = rollover(prior, nxt)
    assert not bad.ok and bad.differences[0].key == nxt.accounts[0].key
