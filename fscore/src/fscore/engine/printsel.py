"""Print-sheet selection (02 §2.1, [CONFIGURE] print_sheets)."""

from __future__ import annotations

import fnmatch

from fscore.config import EngagementConfig
from fscore.io.xlsx_reader import Sheet, Workbook


def _matches(name: str, patterns: list[str]) -> bool:
    n = name.strip().lower()
    return any(fnmatch.fnmatch(n, p.lower()) for p in patterns)


def select_print_sheets(wb: Workbook, config: EngagementConfig) -> list[Sheet]:
    """Return the print sheets in workbook order.

    Precedence: explicit list → first/last block → the grouped (tab-selected)
    sheets when two or more are grouped, as the preparer prints them → all
    visible sheets except the configured backup patterns. Hidden sheets are excluded unless
    ``include_hidden`` (they are never printed, but may still be referenced).
    """
    sel = config.print_sheets
    sheets = wb.sheets
    if sel.explicit:
        wanted = {n.strip().lower() for n in sel.explicit}
        out = [s for s in sheets if s.name.strip().lower() in wanted]
    elif sel.first or sel.last:
        names = [s.name.strip().lower() for s in sheets]
        i0 = names.index(sel.first.strip().lower()) if sel.first and sel.first.strip().lower() in names else 0
        i1 = names.index(sel.last.strip().lower()) if sel.last and sel.last.strip().lower() in names else len(sheets) - 1
        out = sheets[i0 : i1 + 1]
    else:
        grouped = [s for s in sheets if s.visible and s.peek_tab_selected()] if sel.use_tab_group else []
        if len(grouped) >= 2:
            out = grouped
        else:
            out = [s for s in sheets if not _matches(s.name, sel.all_visible_except)]
    if not sel.include_hidden:
        out = [s for s in out if s.visible]
    return out


def is_print_sheet(wb: Workbook, config: EngagementConfig, name: str) -> bool:
    return any(s.name == name for s in select_print_sheets(wb, config))
