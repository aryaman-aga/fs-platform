"""Related parties and intercompany (02 §6.9): RPT-01…12.

The trial balance is the source of truth (RPT-08): the RPT note is reconciled
three ways against the TB-driven ledger and the FS note lines (RPT-09), and its
completeness, relationships and period scope are checked.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from rapidfuzz import fuzz

from fscore.engine.periods import find_dates, parse_period
from fscore.engine.refs import make_addr
from fscore.models import CellRef, Certainty, Evidence, Severity
from fscore.narrative.evaluate import fmt
from fscore.narrative.metrics import compute_metrics, find_caption_rows, row_values
from fscore.registry import check
from fscore.rpt.ledger import RPTLedger, build_ledger
from fscore.rpt.names import best_match, looks_like_party, normalise, similarity
from fscore.rpt.natures import MISSPELLINGS, family
from fscore.rpt.note import RPTNote, RPTRow, out_of_scope_columns, parse_rpt_note
from fscore.rpt.recon import (
    TOL,
    Confirmation,
    build_grid,
    classification_switches,
    fs_nature_values,
    reconcile_confirmations,
    stale_fy_equals_q4,
)

if TYPE_CHECKING:
    from fscore.checks.base import CheckContext

_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


def immediate_holding(ctx: CheckContext) -> tuple[str | None, str]:
    """Immediate holding company: configuration, else the share-capital shareholding table (> 50 %)."""
    if ctx.config.rpt.holding_company:
        return ctx.config.rpt.holding_company, "configuration"
    note = parse_rpt_note(ctx)
    exclude = {note.sheet} if note else set()
    best: tuple[Decimal, str, str] | None = None
    for s in ctx.print_sheets:
        if s.name in exclude:
            continue
        for c in s.iter_cells():
            if not isinstance(c.value, str) or c.col > 3 or not looks_like_party(c.value):
                continue
            # a % on the same row (formatted percent cell or "99.99%" text)
            for col in range(c.col + 1, min(s.max_col, c.col + 10) + 1):
                cc = s.cell(c.row, col)
                if cc is None or cc.value is None:
                    continue
                pct: Decimal | None = None
                if cc.is_number:
                    r = ctx.wb.render_cell(cc)
                    if r.kind == "percent" and r.value is not None:
                        pct = r.value * 100
                elif isinstance(cc.value, str):
                    m = _PCT.search(cc.value)
                    if m:
                        pct = Decimal(m.group(1))
                if pct is not None and pct > 50 and (best is None or pct > best[0]):
                    best = (pct, c.value.strip(), f"{s.name}!{make_addr(c.row, col)}")
                    break
    if best:
        return best[1], best[2]
    return None, ""


def _ev(row: RPTRow, period: str | None = None) -> Evidence:
    addr = row.cells.get(period or "", "") or next(iter(row.cells.values()), make_addr(row.row, 1))
    return Evidence(label=f"{row.party} — {row.nature_text or row.nature}", ref=CellRef(sheet=row.sheet, addr=addr),
                    value=row.values.get(period or "CY"))


@check("RPT-01", "RPT-02", "RPT-03", "RPT-04", "RPT-05", "RPT-06", "RPT-07", "RPT-08", "RPT-09", "RPT-10", "RPT-11", "RPT-12", order=155)
def rpt_checks(ctx: CheckContext) -> None:
    note = parse_rpt_note(ctx)
    holding, holding_src = immediate_holding(ctx)
    known = list(note.party_names()) if note else []
    if holding and holding not in known:
        known.append(holding)
    if holding and not ctx.config.rpt.holding_company:
        # lets the ledger attribute intercompany share capital to the immediate holding company
        ctx.config.rpt.holding_company = holding
        try:
            ledger = build_ledger(ctx, known_parties=known)
        finally:
            ctx.config.rpt.holding_company = None
    else:
        ledger = build_ledger(ctx, known_parties=known)
    if note is None and not ledger.rows:
        return
    grid = build_grid(ctx, note, ledger)
    ctx.shared["rpt_recon"] = [g.as_dict() for g in grid]
    if note is not None:
        _rpt01(ctx, note, ledger)
        _rpt02(ctx, note, ledger)
        _rpt03(ctx, note)
        _rpt04(ctx, note)
        _rpt05(ctx, note)
        _rpt07(ctx, note)
        _rpt10(ctx, note, ledger)
        _rpt11(ctx, note, holding, holding_src)
        _rpt12(ctx, note, grid)
    _rpt06(ctx, grid)
    _rpt08(ctx, ledger)
    _rpt09(ctx, note, ledger, grid)


# ---------------------------------------------------------------------------- RPT-01 party master


def _rpt01(ctx: CheckContext, note: RPTNote, ledger: RPTLedger) -> None:
    names = note.party_names()
    if not names:
        if note.rows:
            ctx.emit("RPT-01", sheet=note.sheet, cells=[make_addr(note.r1, 1)],
                     explanation="The related-party note has transactions or balances but no list of related parties and relationships.", key="nolist")
        return
    reported: set[str] = set()
    for row in note.rows:
        if row.hidden or not any(row.values.values()):
            continue
        match, _s = best_match(row.party, names, threshold=88)
        if match is None and looks_like_party(row.party):
            key = normalise(row.party)
            if key in reported:
                continue
            reported.add(key)
            ctx.emit("RPT-01", sheet=row.sheet, cells=[make_addr(row.row, 1)],
                     explanation=f"'{row.party}' has related-party {row.section} but is not in the list of related parties.",
                     caption=row.party, key=f"missing:{key}", evidence=[_ev(row)])
    for lr in ledger.rows:
        if lr.counterparty == "Unidentified group company" or not any(lr.values.values()):
            continue
        match, _s = best_match(lr.counterparty, names, threshold=88)
        key = normalise(lr.counterparty)
        if match is None and key not in reported:
            reported.add(key)
            ctx.emit("RPT-01", sheet=note.sheet, cells=[make_addr(note.r1, 1)],
                     explanation=f"The trial balance has related-party accounts with '{lr.counterparty}' ({', '.join(lr.accounts[:5])}) but the party is not in the list of related parties.",
                     caption=lr.counterparty, key=f"tb-missing:{key}")
    # parties listed without transactions or balances are information for the reviewer (02 §6.9 RPT-01),
    # published with the reconciliation grid rather than raised as exceptions
    active = {normalise(r.party) for r in note.rows if any(r.values.values())} | {normalise(lr.counterparty) for lr in ledger.rows if any(lr.values.values())}
    inactive = [p.name for p in note.parties
                if p.relationship not in ("kmp", "kmp_relative") and not p.hidden and not any(similarity(p.name, a) >= 90 for a in active if a)]
    ctx.shared["rpt_inactive_parties"] = inactive


# ---------------------------------------------------------------------------- RPT-02 duplicates / stale rows


def _rpt02(ctx: CheckContext, note: RPTNote, ledger: RPTLedger) -> None:
    seen: dict[tuple[str, str, str], RPTRow] = {}
    for row in note.rows:
        if row.hidden:
            continue
        key = (row.section, row.nature, row.norm)
        if key in seen:
            first = seen[key]
            ctx.emit("RPT-02", sheet=row.sheet, cells=[make_addr(first.row, 1), make_addr(row.row, 1)],
                     explanation=f"'{row.party}' appears twice under '{row.nature_text or row.nature}' (rows {first.row} and {row.row}).",
                     caption=row.party, key=f"dup:{row.section}:{row.nature}:{row.norm}", evidence=[_ev(first), _ev(row)])
        else:
            seen[key] = row
    txn_parties = {r.norm for r in note.rows if r.section == "transactions" and not r.hidden and (r.values.get("CY") or 0) != 0}
    for row in note.rows:
        if row.section != "balances" or row.hidden:
            continue
        cy, py = row.values.get("CY"), row.values.get("PY")
        if cy is None or py is None or cy == 0 or cy != py:
            continue
        lr = next((x for x in ledger.rows if similarity(x.counterparty, row.party) >= 90 and family(x.nature) == family(row.nature)), None)
        tb_moved = lr is not None and lr.values.get("CY") is not None and lr.values.get("PY") is not None and lr.values["CY"] != lr.values["PY"]
        if tb_moved or (lr is None and row.norm in txn_parties):
            why = f"the TB shows {fmt(lr.values['CY'])} / {fmt(lr.values['PY'])}" if tb_moved and lr else "the party had transactions this year"
            ctx.emit("RPT-02", sheet=row.sheet, cells=[row.cells.get("CY", make_addr(row.row, 1))],
                     explanation=f"Balance with '{row.party}' is identical for CY and PY ({fmt(cy)}) although {why} — a stale row.",
                     caption=row.party, key=f"stale:{row.row}", evidence=[_ev(row, "CY")])


# ---------------------------------------------------------------------------- RPT-03 hidden rows with values


def _rpt03(ctx: CheckContext, note: RPTNote) -> None:
    for row in note.rows + note.totals:
        if not row.hidden:
            continue
        nonzero = {p: v for p, v in row.values.items() if v != 0}
        if not nonzero:
            continue
        ctx.emit("RPT-03", sheet=row.sheet, cells=[make_addr(row.row, 1), *row.cells.values()],
                 explanation=f"Hidden row {row.row} ('{row.party}', {row.nature_text or row.nature}) holds {', '.join(f'{p}: {fmt(v)}' for p, v in nonzero.items())} that will not print.",
                 caption=row.party, key=f"hidden:{row.row}", evidence=[_ev(row, next(iter(nonzero)))])


# ---------------------------------------------------------------------------- RPT-04 totals vs source notes


def _rpt04(ctx: CheckContext, note: RPTNote) -> None:
    fs = fs_nature_values(ctx, note)
    totals: dict[tuple[str, str], Decimal] = {}
    cells: dict[tuple[str, str], str] = {}
    for row in note.rows:
        if row.hidden:
            continue
        for p, v in row.values.items():
            k = (family(row.nature), p)
            totals[k] = totals.get(k, Decimal(0)) + v
            cells.setdefault(k, f"{row.sheet}!{row.cells.get(p, '')}")
    labels = {"loan_payable": "loans from related parties", "trade_payable": "trade payables to related parties",
              "trade_receivable": "receivables from related parties", "recoverable": "recoverable from group companies",
              "interest_paid": "interest on related-party loans", "services_rendered": "revenue from related parties",
              "services_received": "services from group companies"}
    for (fam, period), total in sorted(totals.items()):
        src = fs.get(fam, {}).get(period)
        if src is None or fam not in labels:
            continue
        fs_v, fs_cell = src
        if abs(abs(total) - abs(fs_v)) > TOL:
            _sheet, _sep, addr = cells[(fam, period)].partition("!")
            ctx.emit("RPT-04", sheet=note.sheet, cells=[addr] if addr else [],
                     explanation=f"RPT note total for {labels[fam]} ({period}) is {fmt(total)} but the source note shows {fmt(fs_v)} ({fs_cell}).",
                     printed=fmt(total), expected=fmt(fs_v), difference=total - fs_v, period=period, key=f"{fam}:{period}",
                     evidence=[Evidence(label="FS note line", ref=CellRef(sheet=fs_cell.split('!')[0], addr=fs_cell.split('!')[1]), value=fs_v)])
    # sales / services to related parties cannot exceed revenue
    for period in {p for (_f, p) in totals}:
        rev = ctx.key_figures.get("revenue", period)
        sales = totals.get(("services_rendered", period))
        if rev is not None and sales is not None and abs(sales) > abs(rev) + TOL:
            ctx.emit("RPT-04", sheet=note.sheet, cells=[],
                     explanation=f"Sales / services to related parties ({fmt(sales)}) exceed revenue from operations ({fmt(rev)}) for {period}.",
                     period=period, key=f"rev:{period}")
    metrics = compute_metrics(ctx)
    for period in {p for (_f, p) in totals}:
        rem = totals.get(("remuneration", period))
        disc = metrics.get("remuneration", period)
        if rem is not None and disc is not None and disc.ref and disc.ref.sheet != note.sheet and abs(abs(rem) - abs(disc.value)) > TOL:
            ctx.emit("RPT-04", sheet=note.sheet, cells=[],
                     explanation=f"KMP remuneration in the RPT note ({fmt(rem)}) differs from the managerial-remuneration disclosure ({fmt(disc.value)}) for {period}.",
                     period=period, key=f"rem:{period}")


# ---------------------------------------------------------------------------- RPT-05 loan roll per party


def _rpt05(ctx: CheckContext, note: RPTNote) -> None:
    tds = ctx.config.rpt.tds_rate
    tol = ctx.config.rpt.loan_tolerance
    parties = {r.norm: r.party for r in note.rows if family(r.nature) == "loan_payable" and not r.hidden}
    for norm, party in parties.items():
        def val(nature: str, period: str, section: str | None = None, norm: str = norm) -> Decimal:
            return sum((r.values.get(period, Decimal(0)) for r in note.rows if r.norm == norm and not r.hidden
                        and family(r.nature) == nature and (section is None or r.section == section)), Decimal(0))

        bal_cy = val("loan_payable", "CY", "balances")
        bal_py = val("loan_payable", "PY", "balances")
        recd = val("loan_received", "CY")
        repaid = val("loan_repaid", "CY")
        interest = val("interest_paid", "CY")
        row = next(r for r in note.rows if r.norm == norm and family(r.nature) == "loan_payable" and not r.hidden)
        if bal_cy == 0 and bal_py == 0:
            continue
        expected_credited = bal_py + recd + interest * (1 - tds) - repaid
        expected_paid = bal_py + recd - repaid
        if abs(expected_credited - bal_cy) > tol and abs(expected_paid - bal_cy) > tol:
            ctx.emit("RPT-05", sheet=note.sheet, cells=[row.cells.get("CY", make_addr(row.row, 1))],
                     explanation=(f"Loan from '{party}': PY {fmt(bal_py)} + received {fmt(recd)} + interest {fmt(interest)} × (1 − {tds}) − repaid {fmt(repaid)}"
                                  f" = {fmt(expected_credited)} (or {fmt(expected_paid)} if interest was paid), but the CY balance is {fmt(bal_cy)}."),
                     expected=fmt(expected_credited), printed=fmt(bal_cy), difference=bal_cy - expected_credited, key=f"roll:{norm}",
                     evidence=[_ev(row, "CY")])
        if bal_cy > 0 and interest == 0 and val("interest_paid", "PY") == 0:
            ctx.emit("RPT-05", sheet=note.sheet, cells=[row.cells.get("CY", make_addr(row.row, 1))], severity=Severity.LOW,
                     explanation=f"Loan balance with '{party}' ({fmt(bal_cy)}) but no interest is disclosed (interest-free? state it).", key=f"noint:{norm}")
    for r in note.rows:
        if family(r.nature) == "interest_paid" and not r.hidden and (r.values.get("CY") or 0) != 0:
            has_bal = any(x.norm == r.norm and family(x.nature) == "loan_payable" and (x.values.get("CY") or x.values.get("PY")) for x in note.rows)
            if not has_bal:
                ctx.emit("RPT-05", sheet=note.sheet, cells=[r.cells.get("CY", make_addr(r.row, 1))], severity=Severity.LOW,
                         explanation=f"Interest to '{r.party}' ({fmt(r.values['CY'])}) but no loan balance with the party in either year.", key=f"nobal:{r.norm}")


# ---------------------------------------------------------------------------- RPT-06 confirmations


def _rpt06(ctx: CheckContext, grid: list[Any]) -> None:
    confs: list[Confirmation] | None = ctx.shared.get("rpt_confirmations")
    if not confs:
        return
    results = reconcile_confirmations(grid, confs)
    ctx.shared["rpt_confirmation_results"] = results
    for r in results:
        if r["status"] == "agreed":
            continue
        ctx.emit("RPT-06", explanation=f"Intercompany confirmation — {r['party']} ({r['nature']}): {r['status']}; confirmed {r['confirmed']}, our books {r['ours']}.",
                 key=f"conf:{r['party']}:{r['nature']}", caption=r["party"])


# ---------------------------------------------------------------------------- RPT-07 names across notes


def _window_similarity(party: str, text: str) -> float:
    """Best ratio between the party name and any run of the caption's words of about the same length."""
    pw, tw = party.split(), text.split()
    if not pw or not tw:
        return 0.0
    best = 0.0
    for size in {max(1, len(pw) - 1), len(pw), len(pw) + 1}:
        for i in range(max(1, len(tw) - size + 1)):
            best = max(best, float(fuzz.ratio(party, " ".join(tw[i:i + size]))))
    return best


def _rpt07(ctx: CheckContext, note: RPTNote) -> None:
    names = note.party_names() or [r.party for r in note.rows]
    if not names:
        return
    own = normalise(ctx.config.entity or ctx.config.client or "")
    first = ctx.wb.sheets[0].cell(1, 1) if ctx.wb.sheets else None
    if (not own or own == "client") and first is not None and isinstance(first.value, str) and looks_like_party(first.value):
        own = normalise(first.value)  # page titles carry the reporting entity's name
    seen: set[str] = set()
    for s in ctx.print_sheets:
        for c in s.iter_cells():
            if not isinstance(c.value, str) or c.col > 3 or not ctx.is_printed(s, c.row, c.col):
                continue
            if s.name == note.sheet and note.r1 <= c.row <= note.r2:
                continue
            text = c.value.strip()
            if len(text) > 90 or not looks_like_party(text):
                continue
            nt = normalise(text)
            if own and (nt == own or (nt.startswith(own + " ") and own + " " not in [normalise(n) + " " for n in names])):
                continue  # the reporting entity's own name (page titles)
            if any(normalise(n) and re.search(rf"(^|\s){re.escape(normalise(n))}($|\s)", nt) for n in names):
                continue  # the note's name is used verbatim ("Equity shares of <party>")
            scored = [(n, _window_similarity(normalise(n), nt)) for n in names if len(normalise(n)) >= 8]
            if not scored:
                continue
            best, score = max(scored, key=lambda x: x[1])
            if score < 90:
                continue
            key = normalise(text)
            if key in seen:
                continue
            seen.add(key)
            ctx.emit("RPT-07", sheet=s.name, cells=[make_addr(c.row, c.col)],
                     explanation=f"'{text}' here differs from the related-party note's '{best}' — use one name consistently.",
                     caption=text, expected=best, key=f"name:{key}")


# ---------------------------------------------------------------------------- RPT-08 ledger quality


def _rpt08(ctx: CheckContext, ledger: RPTLedger) -> None:
    for acc in ledger.accounts:
        if acc.counterparty is None and any(acc.raw.values()):
            ctx.emit("RPT-08", sheet=ledger.sheet, cells=[make_addr(acc.row, 1)], severity=Severity.MEDIUM, certainty=Certainty.LIKELY,
                     explanation=f"Intercompany account {acc.code} ('{acc.name}') has no identifiable counterparty; name the group company in the account description or mapping.",
                     key=f"nocp:{acc.code}")


# ---------------------------------------------------------------------------- RPT-09 three-way reconciliation


def _rpt09(ctx: CheckContext, note: RPTNote | None, ledger: RPTLedger, grid: list[Any]) -> None:
    sheet = note.sheet if note else ledger.sheet
    ledger_families = {family(r.nature) for r in ledger.rows}
    for g in grid:
        cells = [g.rpt_cell.split("!")[1]] if g.rpt_cell and "!" in g.rpt_cell and g.rpt_cell.split("!")[1] else []
        if g.rpt_row:
            cells = [make_addr(g.rpt_row, 1), *cells]
        ev = []
        if g.rpt_value is not None and g.rpt_cell and "!" in g.rpt_cell and g.rpt_cell.split("!")[1]:
            ev.append(Evidence(label="RPT note", ref=CellRef(sheet=g.rpt_cell.split("!")[0], addr=g.rpt_cell.split("!")[1]), value=g.rpt_value))
        if g.tb_value is not None:
            ev.append(Evidence(label=f"Trial balance ({', '.join(g.tb_accounts[:4])})", value=g.tb_value))
        if g.fs_value is not None and g.fs_cell:
            ev.append(Evidence(label="FS note line", ref=CellRef(sheet=g.fs_cell.split("!")[0], addr=g.fs_cell.split("!")[1]), value=g.fs_value))
        if g.status == "Omitted":
            if not cells and note is not None:
                # nothing to point at in the note: cite the heading of the section where it belongs (explainability)
                fam = family(g.nature)
                head = next((r for r, nat, _t in note.nature_headings if family(nat) == fam), None)
                cells = [make_addr(head if head is not None else note.r1, 1)]
            ctx.emit("RPT-09", sheet=sheet, cells=cells, severity=Severity.HIGH,
                     explanation=f"{g.counterparty} — {g.nature} {fmt(g.tb_value)} ({g.period}) per the trial balance is not disclosed in the RPT note.",
                     expected=fmt(g.tb_value), period=g.period, key=f"omit:{g.counterparty}:{g.nature}:{g.period}", evidence=ev,
                     caption=g.counterparty, tags=["omitted balance" if g.nature in ("trade_payable", "trade_receivable", "loan_payable", "recoverable") else "omitted transaction"])
        elif g.status == "Wrong counterparty":
            ctx.emit("RPT-09", sheet=sheet, cells=cells, severity=Severity.HIGH,
                     explanation=f"{g.nature} {fmt(g.rpt_value)} ({g.period}) is shown against '{g.rpt_party_as_written or g.counterparty}' in the RPT note, but {'; '.join(g.flags)}.",
                     period=g.period, key=f"cp:{g.counterparty}:{g.nature}:{g.period}", evidence=ev, caption=g.counterparty, tags=["wrong counterparty"])
        elif g.status == "Difference":
            ctx.emit("RPT-09", sheet=sheet, cells=cells, severity=Severity.HIGH,
                     explanation=f"{g.counterparty} — {g.nature} ({g.period}): RPT note {fmt(g.rpt_value)} vs trial balance {fmt(g.tb_value)}"
                                 + (f" vs FS {fmt(g.fs_value)}" if g.fs_value is not None else "") + (f". {'; '.join(g.flags)}" if g.flags else "."),
                     printed=fmt(g.rpt_value), expected=fmt(g.tb_value), difference=g.variance, period=g.period,
                     key=f"diff:{g.counterparty}:{g.nature}:{g.period}", evidence=ev, caption=g.counterparty, tags=list(g.flags))
        elif g.status == "No TB support" and g.nature not in ("trade_payable", "trade_receivable", "loan_payable", "recoverable", "loan_receivable") \
                and g.nature in ledger_families:
            ctx.emit("RPT-09", sheet=sheet, cells=cells, severity=Severity.MEDIUM, certainty=Certainty.REVIEW,
                     explanation=f"{g.counterparty} — {g.nature} {fmt(g.rpt_value)} ({g.period}) in the RPT note has no support in the trial balance.",
                     period=g.period, key=f"nosup:{g.counterparty}:{g.nature}:{g.period}", evidence=ev, caption=g.counterparty)
        if g.status == "Presentation" or any(f.startswith(("debit balance", "negative asset")) for f in g.flags):
            ctx.emit("RPT-09", sheet=sheet, cells=cells, severity=Severity.MEDIUM,
                     explanation=f"{g.counterparty} — {g.nature} ({g.period}): {', '.join(f for f in g.flags if f.startswith(('debit balance', 'negative asset')))}.",
                     period=g.period, key=f"pres:{g.counterparty}:{g.nature}:{g.period}", evidence=ev, caption=g.counterparty)
    if note is not None:
        for row, fy in stale_fy_equals_q4(note, ledger):
            ctx.emit("RPT-09", sheet=row.sheet, cells=[row.cells.get(fy, make_addr(row.row, 1))], severity=Severity.HIGH,
                     explanation=f"'{row.party}' — {row.nature_text or row.nature}: the full-year column ({fmt(row.values[fy])}) repeats the quarter column — a stale copy.",
                     key=f"fyq4:{row.row}:{fy}", evidence=[_ev(row, fy)], caption=row.party, tags=["FY equals Q4"])
        for party, n_cy, n_py, a, b in classification_switches(note):
            ctx.emit("RPT-09", sheet=a.sheet, cells=[a.cells.get("CY", make_addr(a.row, 1)), b.cells.get("PY", make_addr(b.row, 1))],
                     severity=Severity.MEDIUM, certainty=Certainty.REVIEW,
                     explanation=f"'{party}': classified as {n_cy} in the current year but {n_py} in the previous year — use one classification.",
                     key=f"switch:{normalise(party)}:{n_cy}:{n_py}", caption=party, tags=["classification switch"])


# ---------------------------------------------------------------------------- RPT-10 capital transactions


def _rpt10(ctx: CheckContext, note: RPTNote, ledger: RPTLedger) -> None:
    metrics = compute_metrics(ctx)
    for period in ("CY", "PY"):
        inf = metrics.get("equity_infused", period)
        if inf is None or inf.value == 0:
            continue
        rows = [r for r in note.rows if r.nature == "equity_issue" and not r.hidden and r.values.get(period)]
        if not rows:
            # point at the empty "issue of shares" row (or its heading) when the note has one
            blank = [r for r, n, _p in note.blank_rows if n == "equity_issue"] + [r for r, n, _p in note.nature_headings if n == "equity_issue"]
            empty = [r.row for r in note.rows if r.nature == "equity_issue" and not r.values.get(period)]
            at = (empty or blank or [note.r1])[0]
            ctx.emit("RPT-10", sheet=note.sheet, cells=[make_addr(at, 1)], severity=Severity.HIGH,
                     explanation=f"Equity shares of {fmt(inf.value)} were issued during {period} (SOCE) but the RPT note discloses no equity issue to the holding / group company.",
                     expected=fmt(inf.value), period=period, key=f"equity:{period}",
                     evidence=[Evidence(label="SOCE issue of shares", ref=inf.ref, value=inf.value)])
        else:
            total = sum((r.values[period] for r in rows), Decimal(0))
            if abs(abs(total) - abs(inf.value)) > TOL:
                ctx.emit("RPT-10", sheet=note.sheet, cells=[rows[0].cells.get(period, make_addr(rows[0].row, 1))], severity=Severity.MEDIUM,
                         explanation=f"Equity issue in the RPT note ({fmt(total)}) differs from the SOCE ({fmt(inf.value)}) for {period}.",
                         printed=fmt(total), expected=fmt(inf.value), period=period, key=f"equity-diff:{period}")
    for lr in ledger.rows:
        if lr.nature not in ("investment_balance",):
            continue
        cy, py = lr.values.get("CY"), lr.values.get("PY")
        if cy is None or py is None or cy == py:
            continue
        movement = cy - py
        rows = [r for r in note.rows if r.nature == "investment" and not r.hidden and similarity(r.party, lr.counterparty) >= 88]
        if not rows:
            ctx.emit("RPT-10", sheet=note.sheet, cells=[make_addr(note.r1, 1)], severity=Severity.HIGH,
                     explanation=f"Investment in {lr.counterparty} moved by {fmt(movement)} during the year (TB {', '.join(lr.accounts[:3])}) but no investment transaction is disclosed.",
                     expected=fmt(movement), key=f"inv:{normalise(lr.counterparty)}", caption=lr.counterparty)
    for lr in ledger.rows:
        if lr.nature != "loan_payable":
            continue
        cy, py = lr.values.get("CY"), lr.values.get("PY")
        if cy is None or py is None:
            continue
        interest = next((x.values.get("CY", Decimal(0)) for x in ledger.rows if x.nature == "interest_paid" and similarity(x.counterparty, lr.counterparty) >= 90), Decimal(0))
        movement = cy - py - interest * (1 - ctx.config.rpt.tds_rate)
        if abs(movement) <= max(TOL, ctx.config.rpt.loan_tolerance):
            continue
        flows = [r for r in note.rows if r.nature in ("loan_received", "loan_repaid") and not r.hidden and similarity(r.party, lr.counterparty) >= 88
                 and r.values.get("CY")]
        if not flows:
            ctx.emit("RPT-10", sheet=note.sheet, cells=[make_addr(note.r1, 1)], severity=Severity.MEDIUM,
                     explanation=f"The loan from {lr.counterparty} moved by {fmt(movement)} (excluding interest credited) but no loan received / repaid is disclosed.",
                     key=f"loan:{normalise(lr.counterparty)}", caption=lr.counterparty)


# ---------------------------------------------------------------------------- RPT-11 relationships


def _expected_relationship(ctx: CheckContext, party: str) -> str | None:
    gs = ctx.config.rpt.group_structure
    entity = ctx.config.entity or ctx.config.client
    if not gs or not entity:
        return None

    def norm_key(n: str) -> str | None:
        m, _s = best_match(n, list(set(gs) | set(gs.values())), threshold=90)
        return m

    me = norm_key(entity)
    them = norm_key(party)
    if me is None or them is None:
        return None
    ancestors: list[str] = []
    cur = gs.get(me)
    while cur and cur not in ancestors:
        ancestors.append(cur)
        cur = gs.get(cur)
    if them in ancestors:
        return "holding" if ancestors.index(them) == 0 else ("ultimate_holding" if ancestors.index(them) == len(ancestors) - 1 else "intermediate_holding")
    parent_them = gs.get(them)
    if parent_them == me:
        return "subsidiary"
    if parent_them and gs.get(parent_them) == me:
        return "step_down_subsidiary"
    # fellow subsidiary: shares an ancestor
    chain: list[str] = []
    cur = parent_them
    while cur and cur not in chain:
        chain.append(cur)
        cur = gs.get(cur)
    if set(chain) & set(ancestors):
        return "fellow_subsidiary"
    return None


def _rpt11(ctx: CheckContext, note: RPTNote, holding: str | None, holding_src: str) -> None:
    ultimate = ctx.config.rpt.ultimate_holding_company
    listed_holding = [p for p in note.parties if p.relationship == "holding"]
    if holding:
        match = note.find_party(holding)
        if match is None:
            ctx.emit("RPT-11", sheet=note.sheet, cells=[make_addr(note.r1, 1)],
                     explanation=f"The immediate holding company ({holding}, per {holding_src}) is missing from the list of related parties.",
                     expected=holding, key="holding-missing")
        elif match.relationship != "holding":
            ctx.emit("RPT-11", sheet=note.sheet, cells=[make_addr(match.row, 1)],
                     explanation=f"'{match.name}' is described as '{match.relationship_text}' but it is the immediate holding company (per {holding_src}).",
                     caption=match.name, expected="Holding company", key="holding-rel")
        for p in listed_holding:
            if similarity(p.name, holding) < 90:
                extra = " (it is the ultimate holding company)" if ultimate and similarity(p.name, ultimate) >= 90 else ""
                ctx.emit("RPT-11", sheet=note.sheet, cells=[make_addr(p.row, 1)],
                         explanation=f"'{p.name}' is shown as the holding company{extra}, but the immediate holding company is {holding} (per {holding_src}).",
                         caption=p.name, expected=holding, key=f"not-holding:{p.norm}")
    if ultimate:
        up = note.find_party(ultimate)
        if up is not None and up.relationship not in ("ultimate_holding", "holding") and not (holding and similarity(holding, ultimate) >= 90):
            ctx.emit("RPT-11", sheet=note.sheet, cells=[make_addr(up.row, 1)],
                     explanation=f"'{up.name}' is described as '{up.relationship_text}' but it is the ultimate holding company.",
                     caption=up.name, expected="Ultimate holding company", key="ultimate-rel")
    for p in note.parties:
        exp = _expected_relationship(ctx, p.name)
        if exp and p.relationship and exp != p.relationship and not (exp == "ultimate_holding" and p.relationship == "holding" and holding and similarity(holding, p.name) >= 90):
            ctx.emit("RPT-11", sheet=note.sheet, cells=[make_addr(p.row, 1)],
                     explanation=f"'{p.name}' is described as '{p.relationship_text}' but, relative to the reporting entity, it is a {exp.replace('_', ' ')}.",
                     caption=p.name, expected=exp.replace("_", " "), key=f"rel:{p.norm}")
    # direct investees (investments note / TB investment accounts) are subsidiaries of the reporting entity
    investees = _investees(ctx, note)
    for p in note.parties:
        if p.relationship in ("holding", "ultimate_holding", "intermediate_holding", "fellow_subsidiary", "step_down_subsidiary") \
                and any(similarity(p.name, i) >= 90 for i in investees):
            ctx.emit("RPT-11", sheet=note.sheet, cells=[make_addr(p.row, 1)],
                     explanation=f"'{p.name}' is described as '{p.relationship_text}' but the Company holds its equity shares (investments note): it is a subsidiary of the reporting entity.",
                     caption=p.name, expected="Subsidiary", key=f"investee:{p.norm}")
    seen: set[str] = set()
    for row, text in note.captions:
        m = MISSPELLINGS.search(text)
        if m and m.group(0).lower() not in seen:
            seen.add(m.group(0).lower())
            ctx.emit("RPT-11", sheet=note.sheet, cells=[make_addr(row, 1)], severity=Severity.LOW, certainty=Certainty.CERTAIN,
                     explanation=f"Spelling: '{m.group(0)}' in '{text[:80]}'.", printed=m.group(0), key=f"spell:{m.group(0).lower()}")


def _investees(ctx: CheckContext, note: RPTNote) -> list[str]:
    out: list[str] = []
    ledger: RPTLedger | None = ctx.shared.get("rpt_ledger")
    if ledger is not None:
        out += [r.counterparty for r in ledger.rows if r.nature == "investment_balance" and r.counterparty != "Unidentified group company"]
    rx = re.compile(r"^(?:unquoted\s+)?(?:fully\s+paid\s+)?equity\s+shares\s+(?:of|in)\s+(.+)$", re.I)
    for s in ctx.print_sheets:
        if s.name == note.sheet:
            continue
        for c in s.iter_cells():
            if isinstance(c.value, str) and c.col <= 2:
                m = rx.match(c.value.strip())
                if m and looks_like_party(m.group(1)):
                    out.append(m.group(1).strip())
    return out


# ---------------------------------------------------------------------------- RPT-12 period scope


def _rpt12(ctx: CheckContext, note: RPTNote, grid: list[Any]) -> None:
    for row, col, text in out_of_scope_columns(ctx, note):
        ctx.emit("RPT-12", sheet=note.sheet, cells=[make_addr(row, col)],
                 explanation=f"Column '{text}' in the related-party note is outside the reporting periods; hide it (if safe) or remove it.",
                 printed=text, key=f"col:{col}")
    scope_ends = {p.end for p in ctx.config.scope()}
    quarter_in_scope = any(p.months == 3 for p in ctx.config.scope())
    for row, text in note.captions:
        if len(text) > 200:
            continue
        lab = parse_period(text)
        dates = find_dates(text)
        bad_dates = [d for d, _b, _raw in dates if d not in scope_ends]
        if bad_dates and lab is not None and re.search(r"\b(as\s+at|balance|ended|during|for\s+the)\b", text, re.I):
            ctx.emit("RPT-12", sheet=note.sheet, cells=[make_addr(row, 1)],
                     explanation=f"'{text}' refers to {bad_dates[0]:%d-%b-%Y}, outside the reporting periods.", printed=text, key=f"cap:{row}")
        elif not quarter_in_scope and re.search(r"\bduring\s+the\s+quarter\b|\bfor\s+the\s+quarter\b|\bquarter\s+ended\b", text, re.I):
            ctx.emit("RPT-12", sheet=note.sheet, cells=[make_addr(row, 1)],
                     explanation=f"'{text}': the entity reports annual figures; quarter captions are stale.", printed=text, key=f"q:{row}")
    for g in grid:
        if g.status == "No TB support" and g.nature in ("trade_payable", "trade_receivable", "loan_payable", "recoverable", "loan_receivable") and g.period == "CY":
            cell = g.rpt_cell.split("!")[1] if g.rpt_cell and "!" in g.rpt_cell else ""
            ctx.emit("RPT-12", sheet=note.sheet, cells=[cell] if cell else [],
                     explanation=f"Balance with {g.counterparty} ({g.nature}) of {fmt(g.rpt_value)} has no trial-balance support — a stale balance?",
                     printed=fmt(g.rpt_value), key=f"stale-bal:{g.counterparty}:{g.nature}", caption=g.counterparty)


__all__ = ["find_caption_rows", "immediate_holding", "row_values", "rpt_checks"]
