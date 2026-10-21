"""Apply a fix plan to a *copy* of the workbook and verify it (02 §7.4, controls C1/C4/C5)."""

from __future__ import annotations

import contextlib
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from fscore.config import EngagementConfig
from fscore.engine.calc import Recalculator
from fscore.engine.evaluator import values_equal
from fscore.engine.printsel import select_print_sheets
from fscore.engine.refs import col_to_index, split_addr
from fscore.engine.selftest import bounds, name_lookup
from fscore.fix.verify import verify_copy
from fscore.io.ooxml import sha256_file
from fscore.io.ooxml_patch import PatchError, XlsxPatcher
from fscore.io.xlsx_reader import Workbook
from fscore.models import ApplyResult, Edit, EditKind, FixKind, FixPlan, PrintedMove, VerificationReport

Key = tuple[str, int, int]


def select_edits(plan: FixPlan, approved: set[str] | None) -> tuple[list[Edit], list[Edit]]:
    approved = approved or set()
    chosen, skipped = [], []
    for e in plan.edits:
        if e.fix_kind == FixKind.AUTO or (e.fix_kind == FixKind.APPROVAL and (e.id in approved or e.approved)):
            chosen.append(e)
        else:
            skipped.append(e)
    return chosen, skipped


def _recompute(src: Path, edits: list[Edit], config: EngagementConfig) -> tuple[dict[Key, Any], dict[Key, Any], Workbook]:
    """Final cached values for every print-sheet formula after the chosen edits."""
    wb = Workbook.open(src)
    lower = {s.name.strip().lower(): s.name for s in wb.sheets}

    def canon(name: str) -> str:
        return name if name in {s.name for s in wb.sheets} else lower.get(name.strip().lower(), name)

    sheets = select_print_sheets(wb, config)
    formulas: dict[Key, str] = {}
    original: dict[Key, Any] = {}
    for sh in sheets:
        for c in sh.iter_cells():
            if c.formula is not None:
                formulas[(sh.name, c.row, c.col)] = c.formula
                original[(sh.name, c.row, c.col)] = c.value
    const_override: dict[Key, Any] = {}
    for e in edits:
        er, ec = split_addr(e.cell) if e.kind in (EditKind.FORMULA, EditKind.VALUE, EditKind.TEXT) else (0, 0)
        key = (canon(e.sheet), er, ec)
        if e.kind == EditKind.FORMULA and e.new_formula:
            formulas[key] = e.new_formula
        elif e.kind == EditKind.VALUE:
            formulas.pop(key, None)
            const_override[key] = float(e.new_value) if e.new_value not in (None, "") else None
        elif e.kind == EditKind.TEXT:
            formulas.pop(key, None)
            const_override[key] = e.new_value

    def base(key: Key) -> Any:
        if key in const_override:
            return const_override[key]
        sh = wb.get_sheet(key[0])
        if sh is None:
            return None
        cell = sh.cell(key[1], key[2])
        return None if cell is None else cell.value

    rc = Recalculator(formulas, base, name_lookup=name_lookup(wb), bounds=bounds(wb), date1904=wb.date1904,
                      fallback=lambda k: original.get(k, base(k)), canon=canon)
    values = rc.compute_all()
    return values, original, wb


def apply_plan(
    path: Path,
    plan: FixPlan,
    out_path: Path,
    *,
    approved_edit_ids: set[str] | None = None,
    config: EngagementConfig,
    backup_dir: Path | None = None,
    libreoffice: bool | None = None,
) -> ApplyResult:
    path, out_path = Path(path), Path(out_path)
    if out_path.resolve() == path.resolve():
        return ApplyResult(ok=False, message="refusing to write over the source workbook (C1): choose another output path")
    if plan.source_sha256 and sha256_file(path) != plan.source_sha256:
        return ApplyResult(ok=False, message="the workbook changed since the plan was made: plan again")
    chosen, skipped = select_edits(plan, approved_edit_ids)
    values, original, wb = _recompute(path, chosen, config)
    try:
        patcher = XlsxPatcher(path)
        touched: set[Key] = set()
        for e in chosen:
            if e.kind == EditKind.FORMULA and e.new_formula:
                r, c = split_addr(e.cell)
                key = (_canon(wb, e.sheet), r, c)
                patcher.set_formula(e.sheet, e.cell, e.new_formula, values.get(key))
                touched.add(key)
            elif e.kind == EditKind.VALUE:
                patcher.set_value(e.sheet, e.cell, float(e.new_value) if e.new_value not in (None, "") else None)
                touched.add((_canon(wb, e.sheet), *split_addr(e.cell)))
            elif e.kind == EditKind.TEXT:
                if e.new_value is not None and e.new_value.lstrip().startswith(("=", "+", "@")):
                    raise PatchError(f"{e.sheet}!{e.cell}: narrative edits must be typed text, not formulas (C6)")
                patcher.set_text(e.sheet, e.cell, e.new_value or "")
                touched.add((_canon(wb, e.sheet), *split_addr(e.cell)))
            elif e.kind == EditKind.HIDE_ROW:
                patcher.hide_row(e.sheet, int(e.cell))
            elif e.kind == EditKind.HIDE_COL:
                patcher.hide_col(e.sheet, col_to_index(e.cell))
        # refresh cached values of every other formula whose value moved
        for key, v in values.items():
            if key in touched:
                continue
            if not values_equal(original.get(key), v):
                from fscore.engine.refs import make_addr

                patcher.refresh_cached(key[0], make_addr(key[1], key[2]), v)
        backup_path = None
        if out_path.exists():
            bdir = backup_dir or out_path.parent
            bdir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = bdir / f"{out_path.stem} - backup {stamp}{out_path.suffix}"
            shutil.copy2(out_path, backup_path)
        patcher.write(out_path)
    except PatchError as exc:
        wb.close()
        return ApplyResult(ok=False, message=str(exc), skipped_edit_ids=[e.id for e in skipped])
    wb.close()
    report: VerificationReport = verify_copy(path, out_path, plan, chosen, config, libreoffice=libreoffice)
    if not report.ok:
        with contextlib.suppress(OSError):
            out_path.unlink()
        if backup_path is not None and backup_path.exists():
            shutil.copy2(backup_path, out_path)
        return ApplyResult(ok=False, out_path=None, verification=report, applied_edit_ids=[e.id for e in chosen],
                           skipped_edit_ids=[e.id for e in skipped], backup_path=str(backup_path) if backup_path else None,
                           message="post-write verification failed; the output was deleted")
    moves = _moves_for(plan, chosen)
    return ApplyResult(
        ok=True,
        out_path=str(out_path),
        sha256=sha256_file(out_path),
        backup_path=str(backup_path) if backup_path else None,
        applied_edit_ids=[e.id for e in chosen],
        skipped_edit_ids=[e.id for e in skipped],
        verification=report,
        printed_moves=moves,
        message=f"{len(chosen)} edit(s) applied and verified",
    )


def _canon(wb: Workbook, name: str) -> str:
    s = wb.get_sheet(name)
    return s.name if s is not None else name


def _moves_for(plan: FixPlan, chosen: list[Edit]) -> list[PrintedMove]:
    # the plan's list already reflects all auto edits; approval items only add typed text / hides
    return list(plan.printed_moves)
