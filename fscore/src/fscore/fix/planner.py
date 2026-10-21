"""Fix planner (02 §7.1): turns the workbook state into proposed edits.

* auto — applied by ``fix`` under the default policy: totals rewrite, typed
  rounding plug removal, missing total cells, balancing-line adjustments;
* approval — applied only when that item is approved: typed-text narrative
  corrections (GC-06 …), replacing typed totals, removing larger adjustments,
  hiding out-of-scope columns/rows that are safe to hide;
* manual — report only (they stay findings).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fscore.checks.base import CheckContext
from fscore.config import EngagementConfig
from fscore.engine.display import compile_format
from fscore.engine.periods import scope_key
from fscore.engine.refs import make_addr, split_addr
from fscore.engine.statements import column_label_at
from fscore.fix.solver import SolveResult, solve
from fscore.io.ooxml import sha256_file
from fscore.models import CheckResult, Edit, EditGroup, EditKind, FixKind, FixPlan, PrintedMove

ProgressFn = Callable[[str, float], None]
log = logging.getLogger("fscore.fix.planner")


def _render(ctx: CheckContext, sheet: str, row: int, col: int, value: Any) -> str:
    sh = ctx.wb.get_sheet(sheet)
    if sh is None:
        return ""
    c = sh.cell(row, col)
    fmt = sh.number_format(c) if c is not None else "General"
    if value is None:
        return ""
    return compile_format(fmt).render(value, date1904=ctx.wb.date1904).clean


def _period(ctx: CheckContext, sheet: str, row: int, col: int) -> str | None:
    sh = ctx.wb.get_sheet(sheet)
    if sh is None:
        return None
    lab = column_label_at(ctx, sh, row, col)
    return scope_key(lab, ctx.config) if lab is not None else None


def printed_moves(sr: SolveResult) -> list[PrintedMove]:
    ctx = sr.ctx
    key_refs = set()
    try:
        for kf in ctx.key_figures.as_list():
            if kf.ref is not None:
                key_refs.add((kf.ref.sheet, kf.ref.addr))
    except Exception:
        log.debug("key figures unavailable for printed-move highlights")
    out: list[PrintedMove] = []
    for sh in ctx.print_sheets:
        for c in sh.iter_cells():
            key = (sh.name, c.row, c.col)
            if key not in sr.final_values and key not in sr.changes:
                continue
            if not ctx.is_printed(sh, c.row, c.col):
                continue
            before = _render(ctx, sh.name, c.row, c.col, sr.original_value.get(key))
            after = _render(ctx, sh.name, c.row, c.col, sr.final_values.get(key, c.value))
            if before == after:
                continue
            period = _period(ctx, sh.name, c.row, c.col)
            out.append(PrintedMove(
                sheet=sh.name, cell=c.addr, caption=ctx.caption(sh, c.row) or None, before=before, after=after,
                period=period, is_py=bool(period and period.endswith("PY")), is_key=(sh.name, c.addr) in key_refs,
            ))
    return out


def _value_text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, float):
        from fscore.io.ooxml_patch import number_text

        return number_text(v)
    return str(v)


def plan_fixes(
    path: Path,
    config: EngagementConfig,
    *,
    check_result: CheckResult | None = None,
    progress: ProgressFn | None = None,
) -> FixPlan:
    if progress:
        progress("solving", 0.1)
    sr = solve(path, config)
    if progress:
        progress("planning", 0.7)
    sheet_order = {name: i for i, name in enumerate(sr.wb.sheet_names)}
    findings = check_result.findings if check_result is not None else []

    def finding_ids_for(sheet: str, addr: str, rules: list[str]) -> list[str]:
        out = []
        for f in findings:
            if f.sheet == sheet and addr in f.cells and (not rules or f.rule_id in rules or f.rule_id.startswith(("CAST", "PLUG", "BAL", "TYPED"))):
                out.append(f.id)
        return out

    edits: list[Edit] = []
    for key in sorted(sr.changes, key=lambda k: (sheet_order.get(k[0], 999), k[1], k[2])):
        ch = sr.changes[key]
        addr = make_addr(key[1], key[2])
        old_f = sr.original_formula.get(key)
        old_v = sr.original_value.get(key)
        edits.append(Edit(
            sheet=key[0],
            cell=addr,
            kind=EditKind.FORMULA if ch.new_formula is not None else EditKind.VALUE,
            group=ch.group,
            old_formula=old_f,
            old_value=_value_text(old_v),
            new_formula=ch.new_formula,
            new_value=_value_text(sr.final_values.get(key, ch.new_value)),
            reason=ch.reason,
            finding_ids=finding_ids_for(key[0], addr, ch.rules),
            policy=config.policy.casting,
            fix_kind=FixKind.AUTO,
            caption=sr.ctx.caption(sr.wb.sheet(key[0]), key[1]) or None,
        ))
    # approval items -------------------------------------------------------------
    post_fix = _post_fix_text_proposals(sr, config) if sr.changes else []
    post_cells = {(te.sheet, te.cell) for te in post_fix}
    for te in post_fix:
        edits.append(Edit(
            sheet=te.sheet, cell=te.cell, kind=EditKind.TEXT, group=EditGroup.TYPED_TEXT,
            old_value=_value_text(_cell_value(sr, te.sheet, te.cell)), new_value=te.new_text,
            reason=(te.reason or "typed-text correction") + " — restates figures moved by this fix",
            finding_ids=[f.id for f in findings if f.sheet == te.sheet and te.cell in f.cells], fix_kind=FixKind.APPROVAL, policy="typed_text",
        ))
    if check_result is not None:
        for te in check_result.text_proposals:
            if (te.sheet, te.cell) in post_cells:
                continue  # superseded by the proposal computed on the corrected figures
            edits.append(Edit(
                sheet=te.sheet, cell=te.cell, kind=EditKind.TEXT, group=EditGroup.TYPED_TEXT,
                old_value=_value_text(_cell_value(sr, te.sheet, te.cell)), new_value=te.new_text, reason=te.reason or "typed-text correction",
                finding_ids=[f.id for f in findings if f.sheet == te.sheet and te.cell in f.cells], fix_kind=FixKind.APPROVAL, policy="typed_text",
            ))
        for f in findings:
            if f.rule_id == "TYPED-01" and f.cells and f.suggested_fix and "=SUM(" in f.suggested_fix:
                formula = f.suggested_fix.split("=", 1)[1].rstrip(".")
                edits.append(Edit(
                    sheet=f.sheet or "", cell=f.cells[0], kind=EditKind.FORMULA, group=EditGroup.TOTALS_REWRITE,
                    old_value=f.printed, new_formula=formula, reason="replace the hard-typed total with a formula",
                    finding_ids=[f.id], fix_kind=FixKind.APPROVAL, policy="approval",
                ))
            if f.rule_id == "PLUG-02" and f.cells:
                from fscore.fix.rewrite import remove_plug

                c = _cell(sr, f.sheet or "", f.cells[0])
                if c is not None and c.formula:
                    new = remove_plug(c.formula)
                    if new:
                        edits.append(Edit(
                            sheet=f.sheet or "", cell=f.cells[0], kind=EditKind.FORMULA, group=EditGroup.PLUG_REMOVAL,
                            old_formula=c.formula, new_formula=new, reason="remove the larger typed adjustment (after investigation)",
                            finding_ids=[f.id], fix_kind=FixKind.APPROVAL, policy="approval",
                        ))
        edits.extend(_hide_proposals(sr, findings))
    for i, e in enumerate(edits, 1):
        e.id = f"E-{i:04d}"
    plan = FixPlan(
        id="",
        source_file=Path(path).name,
        source_sha256=sha256_file(Path(path)),
        config_hash=config.config_hash(),
        policy=config.policy.casting,
        edits=edits,
        balancing=sr.balancing,
        printed_moves=printed_moves(sr),
        unresolved=sr.unresolved,
        converged=sr.converged,
        passes=sr.passes,
        created_at=datetime.now(UTC),
    )
    plan.id = "P-" + plan.plan_sha256[:12]
    sr.wb.close()
    if progress:
        progress("done", 1.0)
    return plan


def _post_fix_text_proposals(sr: SolveResult, config: EngagementConfig) -> list[Any]:
    """Narrative / Going Concern wording re-derived from the figures *after* the fix (typed text, approval items)."""
    from fscore.checks.base import CheckContext
    from fscore.registry import load_all_checks

    ctx = CheckContext(sr.wb, config)
    for impl in load_all_checks():
        if any(r.startswith(("NARR-", "GC-")) or r == "TXT-06" for r in impl.rule_ids):
            try:
                impl.fn(ctx)
            except Exception:
                log.debug("post-fix narrative pass failed in %s", impl.name)
    return list(ctx.shared.get("text_proposals", []))


def _cell(sr: SolveResult, sheet: str, addr: str) -> Any:
    sh = sr.wb.get_sheet(sheet)
    if sh is None:
        return None
    r, c = split_addr(addr)
    return sh.cell(r, c)


def _cell_value(sr: SolveResult, sheet: str, addr: str) -> Any:
    r, c = split_addr(addr)
    return sr.original_value.get((sheet, r, c))


def _hide_proposals(sr: SolveResult, findings: list[Any]) -> list[Edit]:
    """Out-of-scope columns / intermediate rows that are safe to hide (MULTI-01/03; MULTI-02 safety)."""
    try:
        from fscore.checks import hide_safety  # built by the text/MULTI checks
    except ImportError:
        return []
    out: list[Edit] = []
    ctx = sr.ctx
    try:
        cols = hide_safety.out_of_scope_columns(ctx)
        rows = hide_safety.out_of_scope_rows(ctx)
    except Exception:
        return []
    for sheet, col, label in cols:
        verdict = hide_safety.column_hide_safety(ctx, sheet, col)
        if not verdict.safe:
            continue
        from fscore.engine.refs import index_to_col

        out.append(Edit(sheet=sheet, cell=index_to_col(col), kind=EditKind.HIDE_COL, group=EditGroup.HIDE,
                        reason=f"hide out-of-scope column ({label})", fix_kind=FixKind.APPROVAL, policy="hide",
                        finding_ids=[f.id for f in findings if f.rule_id == "MULTI-01" and f.sheet == sheet]))
    for sheet, row, label in rows:
        verdict = hide_safety.row_hide_safety(ctx, sheet, row)
        if not verdict.safe:
            continue
        out.append(Edit(sheet=sheet, cell=str(row), kind=EditKind.HIDE_ROW, group=EditGroup.HIDE,
                        reason=f"hide intermediate / out-of-scope row ({label})", fix_kind=FixKind.APPROVAL, policy="hide",
                        finding_ids=[f.id for f in findings if f.rule_id == "MULTI-03" and f.sheet == sheet]))
    return out


__all__ = ["Decimal", "plan_fixes", "printed_moves"]
