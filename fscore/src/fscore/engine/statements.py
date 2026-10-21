"""Statement role tagging, key lines, period columns and the note index (02 §5 statements.py).

Everything here is derived from the workbook content (sheet names, titles,
captions, headers) so that it works on workbooks prepared elsewhere, not just
on Module P output. Configuration (``config.statements``) overrides detection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fscore.engine.periods import PeriodLabel, parse_period, scope_key
from fscore.io.xlsx_reader import Sheet

if TYPE_CHECKING:
    from fscore.checks.base import CheckContext

ROLE_NAME_RX: dict[str, re.Pattern[str]] = {
    "balance_sheet": re.compile(r"^\s*(bs|b\.s\.?|balance\s*sheet|bal\s*sheet)\s*$", re.I),
    "profit_loss": re.compile(r"^\s*(pl|p&l|p\s*&\s*l|p-pl|pnl|profit.*loss|statement of p.*l|soi)\s*$", re.I),
    "cash_flow": re.compile(r"^\s*(cf|cfs|cash\s*flow.*)\s*$", re.I),
    "soce": re.compile(r"^\s*(soce|socie|sce|changes in equity|equity statement)\s*$", re.I),
    "rpt": re.compile(r"^\s*(rpt|related part.*|note\s*\d*\s*rpt)\s*$", re.I),
    "ratios": re.compile(r"ratio", re.I),
}
ROLE_TITLE_RX: dict[str, re.Pattern[str]] = {
    "balance_sheet": re.compile(r"\bbalance\s+sheet\s+as\s+at\b", re.I),
    "profit_loss": re.compile(r"statement\s+of\s+profit\s+and\s+loss|profit\s+and\s+loss\s+(account|statement)\s+for", re.I),
    "cash_flow": re.compile(r"(statement\s+of\s+)?cash\s+flows?\s+(statement\s+)?for\s+the", re.I),
    "soce": re.compile(r"statement\s+of\s+changes\s+in\s+equity", re.I),
    "rpt": re.compile(r"related\s+party\s+(disclosures|transactions)", re.I),
    "going_concern": re.compile(r"\bgoing\s+concern\b", re.I),
    "ratios": re.compile(r"\b(analytical\s+)?ratios\b", re.I),
}

_ITEM_PREFIX = re.compile(r"^\s*(\(?[ivxlcdm]{1,5}\)|\(?[a-z]\)|[ivxlcdm]{1,5}\.|[a-z]\.|\d{1,2}(\.\d{1,2})*[.)]?)\s+", re.I)


def norm_caption(text: str) -> str:
    t = " ".join((text or "").replace("–", "-").replace("—", "-").split())
    prev = None
    while prev != t:
        prev = t
        t = _ITEM_PREFIX.sub("", t)
    t = re.sub(r"\s*\((a|b|c|i|ii|iii|iv|v|vi|[a-z]\s*[+-]\s*[a-z].*?|i+\s*[+-].*?)\)\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*\(note\s*\d+.*?\)\s*$", "", t, flags=re.I)
    return t.strip().rstrip(":").strip().lower()


LINE_RX: dict[str, dict[str, re.Pattern[str]]] = {
    "balance_sheet": {
        "total_assets": re.compile(r"^total\s+assets$"),
        "total_eq_liab": re.compile(r"^total\s+equity\s+(and|&)\s+liabilities$"),
        "total_equity": re.compile(r"^total\s+equity$|^total\s+shareholders'?\s+funds$"),
        "share_capital": re.compile(r"^(equity\s+)?share\s+capital$"),
        "other_equity": re.compile(r"^other\s+equity$"),
        "total_nca": re.compile(r"^total\s+non[\s-]*current\s+assets$"),
        "total_ca": re.compile(r"^total\s+current\s+assets$"),
        "total_ncl": re.compile(r"^total\s+non[\s-]*current\s+liabilities$"),
        "total_cl": re.compile(r"^total\s+current\s+liabilities$"),
        "total_liabilities": re.compile(r"^total\s+liabilities$"),
        "cash": re.compile(r"^cash\s+and\s+cash\s+equivalents$"),
        "trade_receivables": re.compile(r"^trade\s+receivables$"),
        "ppe": re.compile(r"^property,?\s+plant\s+and\s+equipment$"),
        "rou": re.compile(r"^right[\s-]+of[\s-]+use\s+assets?$"),
        "cwip": re.compile(r"^capital\s+work[\s-]+in[\s-]+progress$"),
        "intangibles": re.compile(r"^(other\s+)?intangible\s+assets$"),
        "dta": re.compile(r"^deferred\s+tax\s+assets?(\s+\(net\))?$"),
        "ctl": re.compile(r"^current\s+tax\s+liabilit(y|ies)(\s+\(net\))?$"),
    },
    "profit_loss": {
        "revenue": re.compile(r"^revenue\s+from\s+operations$"),
        "other_income": re.compile(r"^other\s+income$"),
        "total_income": re.compile(r"^total\s+income"),
        "employee": re.compile(r"^employee\s+benefits?\s+expenses?$"),
        "finance_costs": re.compile(r"^finance\s+costs?$"),
        "depreciation": re.compile(r"^depreciation\s+(and|&)\s+amorti[sz]ation"),
        "other_expenses": re.compile(r"^other\s+expenses$"),
        "total_expenses": re.compile(r"^total\s+expenses"),
        "pbt": re.compile(r"(profit|loss).*before\s+(tax|income\s+tax)"),
        "current_tax": re.compile(r"^current\s+tax"),
        "deferred_tax": re.compile(r"^deferred\s+tax"),
        "total_tax": re.compile(r"^total\s+(income\s+)?tax\s+expense|^tax\s+expense$|^total\s+tax$"),
        "pat": re.compile(r"^(net\s+)?(profit|\(?loss\)?|profit\s*/\s*\(?loss\)?|\(loss\)\s*/\s*profit)\s*(/\s*\(?loss\)?\s*)?(after\s+tax|for\s+the\s+(year|period|quarter))"),
        "oci_total": re.compile(r"^total\s+other\s+comprehensive|^other\s+comprehensive\s+(income|loss).*(net|for the)"),
        "tci": re.compile(r"^total\s+comprehensive\s+(income|loss|\(loss\)|income\s*/\s*\(loss\))"),
        "eps_basic": re.compile(r"^basic"),
        "eps": re.compile(r"earnings\s+per\s+(equity\s+)?share"),
    },
    "cash_flow": {
        "cf_pbt": re.compile(r"(profit|loss).*before\s+(tax|income\s+tax)"),
        "cf_dep": re.compile(r"^depreciation"),
        "cf_op_total": re.compile(r"^net\s+cash.*operating\s+activities"),
        "cf_inv_total": re.compile(r"^net\s+cash.*investing\s+activities"),
        "cf_fin_total": re.compile(r"^net\s+cash.*financing\s+activities"),
        "cf_net_change": re.compile(r"^net\s+(increase|decrease|\(decrease\)|\(increase\)|change).*cash"),
        "cf_opening": re.compile(r"^cash\s+and\s+cash\s+equivalents.*(beginning|opening|start)"),
        "cf_closing": re.compile(r"^cash\s+and\s+cash\s+equivalents.*(end|closing)"),
        "cf_other_cl": re.compile(r"(increase|decrease).*other\s+current\s+liabilit"),
        "cf_components_total": re.compile(r"^(total\s+)?cash\s+and\s+cash\s+equivalents(\s+as\s+per.*|\s+\(note.*\))?$|^total$"),
    },
    "soce": {
        "soce_profit": re.compile(r"^(profit|loss|\(loss\)|profit\s*/\s*\(loss\)).*for\s+the\s+(year|period)"),
        "soce_oci": re.compile(r"other\s+comprehensive"),
        "soce_issue": re.compile(r"issue[ds]?\s+(of\s+)?(equity\s+)?shares|issued\s+during|shares\s+issued"),
    },
}


@dataclass
class LineLoc:
    key: str
    sheet: str
    row: int
    caption: str
    cols: dict[str, int] = field(default_factory=dict)  # scope key -> column


@dataclass
class NoteLoc:
    number: str
    sheet: str
    row: int
    title: str
    end_row: int = 0


@dataclass
class StatementMap:
    roles: dict[str, str] = field(default_factory=dict)  # role -> sheet name
    lines: dict[str, LineLoc] = field(default_factory=dict)
    alternates: dict[str, list[LineLoc]] = field(default_factory=dict)  # every matching row
    notes: dict[str, NoteLoc] = field(default_factory=dict)
    period_cols: dict[str, dict[str, int]] = field(default_factory=dict)  # sheet -> scope key -> col
    note_refs: dict[str, list[tuple[str, int]]] = field(default_factory=dict)  # note no -> [(sheet, row)] on face statements

    def sheet(self, role: str) -> str | None:
        return self.roles.get(role)

    def line(self, key: str) -> LineLoc | None:
        return self.lines.get(key)

    def as_dict(self) -> dict[str, str]:
        return dict(self.roles)


# ---------------------------------------------------------------------------- headers


def header_labels(ctx: CheckContext, sheet: Sheet) -> list[tuple[int, int, PeriodLabel]]:
    """(row, col, label) for every header-like cell of a sheet that parses as a period."""
    key = f"hdr:{sheet.name}"
    if key in ctx.shared:
        return ctx.shared[key]  # type: ignore[no-any-return]
    out: list[tuple[int, int, PeriodLabel]] = []
    # only columns that hold numbers further down are period columns (titles and
    # row captions such as "Balance as at April 1, 2024" are not column headers)
    from fscore.engine.display import is_date_format as _is_date

    num_rows: dict[int, list[int]] = {}
    for nc in sheet.iter_cells():
        if nc.is_number and not _is_date(sheet.number_format(nc)):
            num_rows.setdefault(nc.col, []).append(nc.row)

    def has_numbers_below(col: int, row: int) -> bool:
        return any(row < r <= row + 300 for r in num_rows.get(col, ()))

    for c in sheet.iter_cells():
        text = ""
        if isinstance(c.value, str):
            text = c.value
        elif c.is_number:
            from fscore.engine.display import is_date_format

            if is_date_format(sheet.number_format(c)):
                text = ctx.wb.render_cell(c).clean
        if not text or len(text) > 120:
            continue
        lab = parse_period(text)
        if lab is None:
            continue
        # combine with a short label directly above ("As at" / "Year ended" on its own row)
        if lab.kind == "unknown" or (lab.months is None and lab.kind != "point"):
            above = sheet.cell(c.row - 1, c.col)
            if above is not None and isinstance(above.value, str) and len(above.value) < 40:
                lab2 = parse_period(above.value + " " + text)
                if lab2 is not None:
                    lab = lab2
        if has_numbers_below(c.col, c.row):
            out.append((c.row, c.col, lab))
        for m in sheet.merges:
            if m.r1 == c.row and m.c1 == c.col and m.c2 > m.c1:
                for cc in range(m.c1 + 1, m.c2 + 1):
                    if has_numbers_below(cc, c.row):
                        out.append((c.row, cc, lab))
    ctx.shared[key] = out
    return out


def period_columns_at(ctx: CheckContext, sheet: Sheet, row: int, *, max_up: int = 500) -> dict[str, int]:
    """Scope key -> column for the header block nearest above ``row`` (per column)."""
    best: dict[int, tuple[int, PeriodLabel]] = {}
    for r, c, lab in header_labels(ctx, sheet):
        if r <= row and row - r <= max_up and (c not in best or r > best[c][0]):
            best[c] = (r, lab)
    out: dict[str, int] = {}
    for c, (_r, lab) in sorted(best.items()):
        k = scope_key(lab, ctx.config)
        if k is None:
            continue
        if k in out:
            # prefer the printed (visible) column; keep the left-most visible otherwise
            prev = out[k]
            if sheet.col_hidden(prev) and not sheet.col_hidden(c):
                out[k] = c
            continue
        out[k] = c
    return out


def column_label_at(ctx: CheckContext, sheet: Sheet, row: int, col: int, *, max_up: int = 500) -> PeriodLabel | None:
    best: tuple[int, PeriodLabel] | None = None
    for r, c, lab in header_labels(ctx, sheet):
        if c == col and r <= row and row - r <= max_up and (best is None or r > best[0]):
            best = (r, lab)
    return best[1] if best else None


# ---------------------------------------------------------------------------- detection


def _title_text(sheet: Sheet, max_rows: int = 12) -> str:
    parts = []
    for c in sheet.iter_cells():
        if c.row > max_rows:
            break
        if isinstance(c.value, str):
            parts.append(c.value)
    return " ".join(parts)


def _detect_roles(ctx: CheckContext) -> dict[str, str]:
    cfg = ctx.config.statements
    roles: dict[str, str] = {}
    explicit = {
        "balance_sheet": cfg.balance_sheet, "profit_loss": cfg.profit_loss, "cash_flow": cfg.cash_flow, "soce": cfg.soce,
        "rpt": cfg.rpt, "going_concern": cfg.going_concern, "ratios": cfg.ratios, "trial_balance": cfg.trial_balance,
        "share_capital": cfg.share_capital,
    }
    for role, name in explicit.items():
        if name and ctx.wb.get_sheet(name) is not None:
            roles[role] = ctx.wb.sheet(name).name
    sheets = ctx.print_sheets
    for role, rx in ROLE_NAME_RX.items():
        if role in roles:
            continue
        for s in sheets:
            if rx.search(s.name):
                roles[role] = s.name
                break
    for role, rx in ROLE_TITLE_RX.items():
        if role in roles:
            continue
        for s in sheets:
            if s.name in roles.values() and role not in ("going_concern", "rpt", "ratios"):
                continue
            title = _title_text(s, 12 if role in ("balance_sheet", "profit_loss", "cash_flow", "soce") else 10_000)
            if rx.search(title):
                roles[role] = s.name
                break
    if "trial_balance" not in roles:
        for s in ctx.wb.sheets:
            if re.match(r"^\s*(ns_)?tb|trial\s*balance", s.name, re.I):
                roles["trial_balance"] = s.name
                break
    return roles


_NOTE_HEAD = [
    re.compile(r"^\s*note\s*(?:no\.?\s*)?(\d{1,3}(?:\.\d{1,2})?)\s*[:.\-–]?\s*(.*)$", re.I),
    re.compile(r"^\s*(\d{1,3}(?:\.\d{1,2})?)\s*[.:)\-–]\s*([A-Za-z].{2,})$"),
]


def _detect_notes(ctx: CheckContext) -> dict[str, NoteLoc]:
    notes: dict[str, NoteLoc] = {}
    for s in ctx.print_sheets:
        for c in s.iter_cells():
            if c.col > 3 or not ctx.is_printed(s, c.row, c.col):
                continue
            num: str | None = None
            title = ""
            if isinstance(c.value, str):
                t = c.value.strip()
                if len(t) > 160:
                    continue
                for rx in _NOTE_HEAD:
                    m = rx.match(t)
                    if m:
                        num, title = m.group(1), m.group(2).strip()
                        break
            elif c.is_number and not c.is_formula and float(c.value).is_integer() and 1 <= c.value <= 99:
                nxt = s.cell(c.row, c.col + 1)
                if nxt is not None and isinstance(nxt.value, str) and nxt.value.strip() and s.is_bold(nxt) and len(nxt.value) < 120:
                    num, title = str(int(c.value)), nxt.value.strip()
            if num and title and not re.match(r"^(to|and|of)\b", title, re.I):
                key = num.split(".")[0] if "." in num and num.split(".")[0] not in notes else num
                if key not in notes:
                    notes[key] = NoteLoc(key, s.name, c.row, title)
    # end rows: next note heading on the same sheet
    by_sheet: dict[str, list[NoteLoc]] = {}
    for n in notes.values():
        by_sheet.setdefault(n.sheet, []).append(n)
    for sname, lst in by_sheet.items():
        lst.sort(key=lambda n: n.row)
        sh = ctx.wb.sheet(sname)
        for i, n in enumerate(lst):
            n.end_row = lst[i + 1].row - 1 if i + 1 < len(lst) else sh.max_row
    return notes


def _detect_lines(ctx: CheckContext, roles: dict[str, str], smap: StatementMap) -> None:
    for role, patterns in LINE_RX.items():
        sname = roles.get(role)
        if not sname:
            continue
        sh = ctx.wb.sheet(sname)
        for row in range(1, sh.max_row + 1):
            cap = ctx.caption(sh, row)
            if not cap:
                continue
            nc = norm_caption(cap)
            for key, rx in patterns.items():
                if not rx.search(nc):
                    continue
                if key == "pat" and ("comprehensive" in nc or "before" in nc):
                    continue
                cols = period_columns_at(ctx, sh, row)
                has_num = any(sh.cell(row, c) is not None and sh.cell(row, c).is_number for c in cols.values())  # type: ignore[union-attr]
                loc = LineLoc(key, sname, row, cap, cols)
                smap.alternates.setdefault(key, []).append(loc)
                if key not in smap.lines and (has_num or key.startswith("eps")):
                    smap.lines[key] = loc
        # P&L: PAT must come after PBT
        if role == "profit_loss" and "pat" in smap.lines and "pbt" in smap.lines:
            pbt_row = smap.lines["pbt"].row
            cands = [x for x in smap.alternates.get("pat", []) if x.row > pbt_row]
            if cands:
                smap.lines["pat"] = cands[0]


def _detect_note_refs(ctx: CheckContext, roles: dict[str, str], smap: StatementMap) -> None:
    for role in ("balance_sheet", "profit_loss"):
        sname = roles.get(role)
        if not sname:
            continue
        sh = ctx.wb.sheet(sname)
        note_col = None
        for c in sh.iter_cells():
            if isinstance(c.value, str) and c.value.strip().lower() in ("note", "notes", "note no.", "note no", "notes no."):
                note_col = c.col
                break
        if note_col is None:
            continue
        for c in sh.iter_cells():
            if c.col != note_col or c.value is None:
                continue
            if c.is_number and float(c.value).is_integer():
                smap.note_refs.setdefault(str(int(c.value)), []).append((sname, c.row))
            elif isinstance(c.value, str):
                for part in re.split(r"[,&]|\band\b", c.value):
                    p = part.strip()
                    m = re.fullmatch(r"(\d{1,3})(\.\d+)?(\([a-z]+\))?", p)
                    if m:
                        smap.note_refs.setdefault(m.group(1), []).append((sname, c.row))


def detect_statements(ctx: CheckContext) -> StatementMap:
    roles = _detect_roles(ctx)
    smap = StatementMap(roles=roles)
    for role, sname in roles.items():
        if role in ("balance_sheet", "profit_loss", "cash_flow", "soce"):
            sh = ctx.wb.sheet(sname)
            smap.period_cols[sname] = period_columns_at(ctx, sh, sh.max_row)
    _detect_lines(ctx, roles, smap)
    smap.notes = _detect_notes(ctx)
    _detect_note_refs(ctx, roles, smap)
    return smap
