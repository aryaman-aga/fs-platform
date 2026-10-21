"""ENG-01 evaluator self-test / missing cached values; ENG-02 sheet state changed since the last run."""

from __future__ import annotations

import hashlib
from typing import Any

from fscore.checks.base import CheckContext
from fscore.engine.selftest import THRESHOLD
from fscore.io.xlsx_reader import Workbook
from fscore.models import Severity
from fscore.registry import check


def sheet_states(wb: Workbook) -> dict[str, dict[str, Any]]:
    """Fingerprint of each sheet's visibility state (stored with every run)."""
    out: dict[str, dict[str, Any]] = {}
    for s in wb.sheets:
        rows = ",".join(str(r) for r in sorted(s.rows_hidden))
        cols = ",".join(str(c) for c in sorted(s.cols_hidden))
        out[s.name] = {
            "state": s.state,
            "hidden_rows": hashlib.sha256(rows.encode()).hexdigest()[:16],
            "hidden_cols": hashlib.sha256(cols.encode()).hexdigest()[:16],
            "tab_selected": s.tab_selected,
        }
    return out


@check("ENG-01", order=1)
def eng_01(ctx: CheckContext) -> None:
    st = ctx.shared.get("selftest")
    if st is None:
        return
    if st.missing_cache:
        ctx.emit(
            "ENG-01",
            explanation=f"{st.missing_cache} formula cell(s) in the print sheets have no cached value: the workbook was saved without calculation. "
                        "Open it in Excel, recalculate and save, then re-run.",
            severity=Severity.MEDIUM,
            key="missing-cache",
        )
    if st.evaluated and st.match_rate < THRESHOLD:
        ctx.emit(
            "ENG-01",
            explanation=f"Evaluator self-test matched {st.matched} of {st.evaluated} formulas ({st.match_rate:.2%}), below {THRESHOLD:.1%}. "
                        "Automatic fixes are disabled for this workbook.",
            severity=Severity.HIGH,
            cells=[m.split("!")[-1] for m in st.mismatches[:10]],
            key="selftest",
        )


@check("ENG-02", order=2)
def eng_02(ctx: CheckContext) -> None:
    prev = ctx.shared.get("previous_sheet_states")
    if not prev:
        return
    now = sheet_states(ctx.wb)
    for name, cur in now.items():
        old = prev.get(name)
        if old is None:
            continue
        changed = [k for k in ("state", "hidden_rows", "hidden_cols") if old.get(k) != cur.get(k)]
        if changed:
            ctx.emit(
                "ENG-02",
                sheet=name,
                explanation=f"Sheet '{name}' changed since the last run ({', '.join(changed)}): it was opened and saved in Excel; "
                            "all checks were re-run on it.",
                key=name,
            )
