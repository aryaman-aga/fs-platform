"""Typed-text narrative edits (firm preference 3, control C6).

Narrative corrections (Going Concern etc.) are written as typed text in the
statement unit with 2 decimals — never formulas. A target cell that feeds a
formula (e.g. a "heading" cell that actually holds data used by a total) is
never overwritten (lesson 15A).
"""

from __future__ import annotations

import re
from pathlib import Path

from fscore.config import EngagementConfig
from fscore.engine import formula as F
from fscore.engine.refs import split_addr
from fscore.fix.verify import diff_against_source
from fscore.io.ooxml import sha256_file
from fscore.io.ooxml_patch import PatchError, XlsxPatcher, validate_package
from fscore.io.xlsx_reader import Workbook
from fscore.models import (
    ApplyResult,
    Edit,
    EditGroup,
    EditKind,
    FixKind,
    TextEdit,
    VerificationReport,
    VerificationStep,
)

_FORMULA_LIKE = re.compile(r"^\s*[=+@]|^\s*-\s*[A-Za-z(]")


class TextEditRefused(ValueError):
    pass


def validate_text(text: str) -> None:
    if _FORMULA_LIKE.search(text or ""):
        raise TextEditRefused("narrative edits must be typed text, not formulas (C6)")
    if len(text) > 32_767:
        raise TextEditRefused("text exceeds Excel's 32,767-character cell limit")


def dependents_of(wb: Workbook, sheet: str, row: int, col: int, limit: int = 5) -> list[str]:
    """Formula cells anywhere in the workbook that reference (sheet,row,col)."""
    out: list[str] = []
    for s in wb.sheets:
        for c in s.iter_cells():
            if not c.formula:
                continue
            try:
                refs = F.references(F.parse(c.formula))
            except F.FormulaError:
                continue
            for ref in refs:
                sh = ref.sheet or s.name
                if sh.strip().lower() != sheet.strip().lower():
                    continue
                p = ref.parts
                if p.r1 <= row <= p.r2 and p.c1 <= col <= p.c2:
                    out.append(f"{s.name}!{c.addr}")
                    break
            if len(out) >= limit:
                return out
    return out


def apply_text(path: Path, edits: list[TextEdit], out_path: Path, config: EngagementConfig) -> ApplyResult:
    path, out_path = Path(path), Path(out_path)
    if out_path.resolve() == path.resolve():
        return ApplyResult(ok=False, message="refusing to write over the source workbook (C1)")
    try:
        for e in edits:
            validate_text(e.new_text)
    except TextEditRefused as exc:
        return ApplyResult(ok=False, message=str(exc))
    with Workbook.open(path) as wb:
        for e in edits:
            sh = wb.get_sheet(e.sheet)
            if sh is None:
                return ApplyResult(ok=False, message=f"sheet not found: {e.sheet}")
            r, c = split_addr(e.cell)
            cell = sh.cell(r, c)
            if cell is not None and cell.is_number:
                deps = dependents_of(wb, sh.name, r, c)
                if deps:
                    return ApplyResult(ok=False, message=f"{e.sheet}!{e.cell} holds a number used by {', '.join(deps)}: refusing to overwrite it with text")
    patcher = XlsxPatcher(path)
    try:
        for e in edits:
            patcher.set_text(e.sheet, e.cell, e.new_text)
        patcher.write(out_path)
    except PatchError as exc:
        return ApplyResult(ok=False, message=str(exc))
    plan_edits = [Edit(sheet=e.sheet, cell=e.cell, kind=EditKind.TEXT, group=EditGroup.TYPED_TEXT, new_value=e.new_text, fix_kind=FixKind.APPROVAL) for e in edits]
    errs = validate_package(out_path)
    problems = diff_against_source(path, out_path, plan_edits)
    rep = VerificationReport(
        steps=[
            VerificationStep(name="xml", ok=not errs, detail="; ".join(errs[:3]) or "all XML parts parse strictly"),
            VerificationStep(name="diff", ok=not problems, detail="only the edited cells differ" if not problems else "; ".join(problems[:3])),
        ],
        xml_errors=errs,
        unexpected_changes=problems,
    )
    rep.ok = all(s.ok for s in rep.steps)
    if not rep.ok:
        out_path.unlink(missing_ok=True)
        return ApplyResult(ok=False, verification=rep, message="verification failed; output deleted")
    return ApplyResult(ok=True, out_path=str(out_path), sha256=sha256_file(out_path), verification=rep,
                       applied_edit_ids=[f"{e.sheet}!{e.cell}" for e in edits], message=f"{len(edits)} typed-text edit(s) applied")
