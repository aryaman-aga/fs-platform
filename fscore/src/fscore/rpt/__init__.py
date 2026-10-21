"""Related-party engine (02 §6.9): RPT note parser, TB-driven ledger, three-way reconciliation.

Public API::

    from fscore.rpt import parse_rpt_note, build_ledger, build_grid, reconcile_confirmations, Confirmation

``parse_rpt_note(ctx)`` → :class:`RPTNote` (party list, transaction / balance rows, hidden rows);
``build_ledger(ctx, known_parties=[...])`` → :class:`RPTLedger` (RPT-08, per counterparty × nature × period);
``build_grid(ctx, note, ledger)`` → list of :class:`GridRow` (RPT-09 three-way grid, also published as
``ctx.shared['rpt_recon']``); ``reconcile_confirmations(grid, confirmations)`` → RPT-06 results.
"""

from fscore.rpt.ledger import LedgerRow, RPTLedger, build_ledger
from fscore.rpt.note import RPTNote, RPTParty, RPTRow, parse_rpt_note
from fscore.rpt.recon import Confirmation, GridRow, build_grid, reconcile_confirmations

__all__ = [
    "Confirmation", "GridRow", "LedgerRow", "RPTLedger", "RPTNote", "RPTParty", "RPTRow",
    "build_grid", "build_ledger", "parse_rpt_note", "reconcile_confirmations",
]
