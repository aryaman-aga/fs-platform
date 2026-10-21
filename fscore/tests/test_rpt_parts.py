"""Unit tests for the related-party building blocks: names, natures, grid helpers, confirmations."""

from __future__ import annotations

from decimal import Decimal

from fscore.rpt.ledger import RPTLedger
from fscore.rpt.names import (
    extract_party_from_account,
    find_party_in_text,
    looks_like_party,
    normalise,
    similarity,
)
from fscore.rpt.natures import balance_nature, relationship_of, transaction_nature
from fscore.rpt.note import RPTNote, RPTRow
from fscore.rpt.recon import (
    Confirmation,
    GridRow,
    classification_switches,
    reconcile_confirmations,
    stale_fy_equals_q4,
)


def test_normalise_indian_forms_but_keep_foreign_forms_and_huf() -> None:
    assert normalise("Corvus Holdings Pvt. Ltd.") == normalise("Corvus Holdings Private Limited")
    assert normalise("Corvus Data Services Private Limited") != normalise("Corvus Data Services FZ-LLC")
    assert normalise("Asha Raman (Director)") == normalise("Asha Raman – Director")
    assert normalise("Ramesh Kumar (HUF)") != normalise("Ramesh Kumar")
    assert normalise("Kestrel Traders (UL)") == normalise("Kestrel Traders")


def test_similarity_and_party_detection() -> None:
    assert similarity("Corvus Holding Private Limited", "Corvus Holdings Private Limited") >= 90
    assert similarity("Corvus Data Services", "Corvus Data Services FZ-LLC") < 90
    assert looks_like_party("Corvus Technologies Inc.")
    assert not looks_like_party("Holding company")
    assert not looks_like_party("Support services rendered")
    assert find_party_in_text("Intercompany Payable - Corvus Holdings Private Limited", ["Corvus Holdings Private Limited"]) == "Corvus Holdings Private Limited"
    assert extract_party_from_account("Intercompany Receivable - Corvus Analytics Limited") == "Corvus Analytics Limited"


def test_natures_and_relationships() -> None:
    assert transaction_nature("Support services rendered") == "services_rendered"
    assert transaction_nature("Support services received") == "services_received"
    assert transaction_nature("Issue of equity shares (including securities premium)") == "equity_issue"
    assert transaction_nature("Repayment of loan") == "loan_repaid"
    assert balance_nature("Recoverable from group companies") == "recoverable"
    assert balance_nature("Loan payable") == "loan_payable"
    assert relationship_of("Holding company and ultimate holding company") == "holding"
    assert relationship_of("Setp-down subsidiary of Corvus Holdings Private Limited") == "step_down_subsidiary"
    assert relationship_of("Key managerial personnel") == "kmp"


def _row(section: str, nature: str, party: str, values: dict[str, str], row: int = 10) -> RPTRow:
    return RPTRow(section, nature, nature, party, {k: Decimal(v) for k, v in values.items()}, dict.fromkeys(values, f"B{row}"), row, False, "RPT")


def test_fy_equal_to_quarter_is_a_stale_copy() -> None:
    note = RPTNote("RPT", 1, 40, rows=[_row("transactions", "services_rendered", "Corvus Technologies Inc.", {"Q4_CY": "27.40", "CY": "27.40"})])
    hits = stale_fy_equals_q4(note, RPTLedger(sheet=None))
    assert [(r.party, fy) for r, fy in hits] == [("Corvus Technologies Inc.", "CY")]


def test_classification_switch_between_years() -> None:
    note = RPTNote("RPT", 1, 40, rows=[
        _row("transactions", "services_received", "Corvus Analytics Limited", {"CY": "12.00"}, 11),
        _row("transactions", "reimbursement", "Corvus Analytics Limited", {"PY": "9.00"}, 13),
    ])
    assert len(classification_switches(note)) == 1


def test_confirmations_difference_and_one_sided() -> None:
    grid = [GridRow("Corvus Holdings Private Limited", "trade_payable", "CY", tb_value=Decimal("30.40")),
            GridRow("Corvus Technologies Inc.", "trade_receivable", "CY", tb_value=Decimal("60.30"))]
    out = reconcile_confirmations(grid, [Confirmation("Corvus Holdings Pvt Ltd", "trade_payable", Decimal("28.00"))])
    statuses = {(r["matched_party"], r["status"]) for r in out}
    assert ("Corvus Holdings Private Limited", "difference") in statuses
    assert ("Corvus Technologies Inc.", "one-sided (no confirmation received)") in statuses
