"""Parser for the related-party note (Ind AS 24): party list, transactions, balances (02 §6.9).

Handles the common layouts: nature heading → party rows; party heading →
nature rows; and two-column "nature | party" rows. Hidden rows are kept (with
``hidden=True``) so RPT-03 can report values that would not print.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from fscore.engine.refs import make_addr
from fscore.engine.statements import header_labels, norm_caption, period_columns_at
from fscore.rpt.names import looks_like_party, normalise
from fscore.rpt.natures import (
    BALANCE_NATURES,
    SECTION_RX,
    balance_nature,
    relationship_of,
    transaction_nature,
)

if TYPE_CHECKING:
    from fscore.checks.base import CheckContext
    from fscore.io.xlsx_reader import Sheet

_TOTAL = re.compile(r"^\s*(sub[\s-]*)?total\b|\btotal\s*$", re.I)
# a relationship label may name a company ("Step-down subsidiary of X Limited")
_REL_START = re.compile(r"^\s*((ultimate|intermediate)\s+)?(holding|parent|fellow|step|setp|subsidiar|wholly|associate|joint|key|kmp|relatives?|enterprises?|directors?)\b", re.I)
_RPT_TITLE = re.compile(r"related\s+part", re.I)


@dataclass
class RPTParty:
    name: str
    relationship: str | None
    relationship_text: str
    row: int
    hidden: bool = False

    @property
    def norm(self) -> str:
        return normalise(self.name)


@dataclass
class RPTRow:
    section: str  # transactions | balances
    nature: str
    nature_text: str
    party: str
    values: dict[str, Decimal] = field(default_factory=dict)  # period -> displayed value
    cells: dict[str, str] = field(default_factory=dict)  # period -> address
    row: int = 0
    hidden: bool = False
    sheet: str = ""

    @property
    def norm(self) -> str:
        return normalise(self.party)


@dataclass
class RPTNote:
    sheet: str
    r1: int
    r2: int
    parties: list[RPTParty] = field(default_factory=list)
    rows: list[RPTRow] = field(default_factory=list)
    totals: list[RPTRow] = field(default_factory=list)
    captions: list[tuple[int, str]] = field(default_factory=list)  # every text in the region (for RPT-12)
    period_cols: dict[str, int] = field(default_factory=dict)
    nature_headings: list[tuple[int, str, str]] = field(default_factory=list)  # (row, nature, text)
    blank_rows: list[tuple[int, str, str]] = field(default_factory=list)  # party rows with no figures: (row, nature, party)

    def party_names(self) -> list[str]:
        return [p.name for p in self.parties]

    def find_party(self, name: str) -> RPTParty | None:
        from fscore.rpt.names import best_match

        match, _score = best_match(name, self.party_names(), threshold=90)
        return next((p for p in self.parties if p.name == match), None) if match else None

    def section_rows(self, section: str, *, visible_only: bool = True) -> list[RPTRow]:
        return [r for r in self.rows if r.section == section and (not visible_only or not r.hidden)]


def rpt_region(ctx: CheckContext) -> tuple[str, int, int] | None:
    smap = ctx.statements
    for note in sorted(smap.notes.values(), key=lambda n: (n.sheet, n.row)):
        if _RPT_TITLE.search(note.title):
            return (note.sheet, note.row, note.end_row or ctx.wb.sheet(note.sheet).max_row)
    sname = smap.sheet("rpt")
    if sname:
        sh = ctx.wb.sheet(sname)
        return (sname, 1, sh.max_row)
    return None


def _texts(sh: Sheet, row: int, before_col: int | None) -> list[str]:
    out = []
    limit = before_col or (sh.max_col + 1)
    for col in range(1, min(limit, sh.max_col + 1)):
        c = sh.cell(row, col)
        if c is not None and isinstance(c.value, str) and c.value.strip():
            out.append(c.value.strip())
    return out


def parse_rpt_note(ctx: CheckContext) -> RPTNote | None:
    if "rpt_note" in ctx.shared:
        return ctx.shared["rpt_note"]  # type: ignore[no-any-return]
    region = rpt_region(ctx)
    if region is None:
        ctx.shared["rpt_note"] = None
        return None
    sname, r1, r2 = region
    sh = ctx.wb.sheet(sname)
    note = RPTNote(sname, r1, r2)
    section: str | None = None
    current_rel: str | None = None
    current_rel_text = ""
    nature: str | None = None
    nature_text = ""
    party_ctx: str | None = None
    for row in range(r1, r2 + 1):
        cols = period_columns_at(ctx, sh, row)
        first_num_col = min(cols.values()) if cols else None
        texts = _texts(sh, row, first_num_col)
        all_texts = _texts(sh, row, None)
        for t in texts or all_texts[:1]:
            note.captions.append((row, t))
        values: dict[str, Decimal] = {}
        cells: dict[str, str] = {}
        for period, col in cols.items():
            c = sh.cell(row, col)
            if c is None or not c.is_number:
                continue
            v = ctx.displayed(sh, row, col)
            if v is None:
                continue
            values[period] = v
            cells[period] = make_addr(row, col)
        caption = " ".join(texts).strip()
        if not caption and not values:
            continue
        hidden = sh.row_hidden(row)
        low = norm_caption(caption)
        # ---- section headings (no numbers on the row)
        if not values and caption:
            sec = next((k for k, rx in SECTION_RX.items() if rx.search(caption)), None)
            if sec is not None and len(caption) < 140:
                section = sec
                nature, nature_text, party_ctx = None, "", None
                continue
        # ---- party list
        if section == "parties":
            if values:
                continue
            row_texts = all_texts
            rel_here = relationship_of(row_texts[0]) if row_texts else None
            rel_label = bool(rel_here) and (not looks_like_party(row_texts[0]) or bool(_REL_START.match(row_texts[0])))
            if len(row_texts) >= 2 and rel_label:
                for name in row_texts[1:]:
                    for part in re.split(r"\s*;\s*|\n", name):
                        if part.strip():
                            note.parties.append(RPTParty(part.strip(), rel_here, row_texts[0], row, hidden))
                current_rel, current_rel_text = rel_here, row_texts[0]
                continue
            if len(row_texts) >= 2 and relationship_of(row_texts[-1]) and not looks_like_party(row_texts[-1]):
                # "Name | Relationship" order
                note.parties.append(RPTParty(row_texts[0], relationship_of(row_texts[-1]), row_texts[-1], row, hidden))
                continue
            if len(row_texts) == 1:
                t = row_texts[0]
                rel = relationship_of(t)
                if rel and not looks_like_party(t) and len(t) < 90:
                    current_rel, current_rel_text = rel, t
                    continue
                if current_rel and len(t) < 120 and not re.match(r"^(name|nature|particulars|sr\.?\s*no)", t, re.I):
                    note.parties.append(RPTParty(t, current_rel, current_rel_text, row, hidden))
            continue
        if not values:
            if not caption:
                continue
            nat = balance_nature(caption) if section == "balances" else transaction_nature(caption)
            if nat is None and section is None:
                nat = transaction_nature(caption) or balance_nature(caption)
            if nat and not looks_like_party(caption):
                nature, nature_text, party_ctx = nat, caption, None
                note.nature_headings.append((row, nat, caption))
            elif looks_like_party(caption) or note.find_party(caption):
                party_ctx = caption
                if nature:
                    note.blank_rows.append((row, nature, caption))
            continue
        # ---- numeric rows
        if _TOTAL.search(low) or _TOTAL.search(caption):
            if nature:
                sec = section or ("balances" if nature in BALANCE_NATURES else "transactions")
                note.totals.append(RPTRow(sec, nature, nature_text, "Total", values, cells, row, hidden, sname))
            continue
        party = caption
        nat = nature
        if len(texts) >= 2:
            # two-column layouts: one text is the nature, the other the party
            n0 = balance_nature(texts[0]) if section == "balances" else transaction_nature(texts[0])
            if n0 and not looks_like_party(texts[0]):
                nat, party = n0, " ".join(texts[1:])
            else:
                n1 = balance_nature(texts[-1]) if section == "balances" else transaction_nature(texts[-1])
                if n1 and not looks_like_party(texts[-1]):
                    nat, party = n1, " ".join(texts[:-1])
        elif party_ctx and not looks_like_party(caption):
            n2 = balance_nature(caption) if section == "balances" else transaction_nature(caption)
            if n2:
                nat, party = n2, party_ctx
        if not party:
            continue
        sec = section or ("balances" if (nat or "") in BALANCE_NATURES else "transactions")
        note.rows.append(RPTRow(sec, nat or "other_transaction" if sec == "transactions" else nat or "other_balance",
                                nature_text if nat == nature else caption, party, values, cells, row, hidden, sname))
    # period columns of the region (for RPT-12 scope checks)
    note.period_cols = period_columns_at(ctx, sh, r2)
    ctx.shared["rpt_note"] = note
    # equity issued to group companies in the current period (used by GC-05)
    eq = [r for r in note.rows if r.nature == "equity_issue" and not r.hidden and "CY" in r.values]
    if eq:
        ctx.shared["rpt_equity_issue_cy"] = sum((r.values["CY"] for r in eq), Decimal(0))
    return note


def out_of_scope_columns(ctx: CheckContext, note: RPTNote) -> list[tuple[int, int, str]]:
    """(row, col, header text) of printed period headers in the RPT region that are outside the scope."""
    from fscore.engine.periods import in_scope

    sh = ctx.wb.sheet(note.sheet)
    out = []
    seen_cols: set[int] = set()
    for r, c, lab in header_labels(ctx, sh):
        if not (note.r1 <= r <= note.r2) or c in seen_cols:
            continue
        if not ctx.is_printed(sh, r, c):
            continue
        if not in_scope(lab, ctx.config):
            out.append((r, c, lab.text))
            seen_cols.add(c)
    return out
