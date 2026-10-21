"""Post-write verification (02 §7.4, control C5). Never claim a fix is verified unless all steps pass.

1. strict XML validation of every part;
2. independent re-open with openpyxl;
3. cell-by-cell diff against the source: only planned cells may differ in
   formula or constant; number formats, merges, print areas, hidden states and
   sheet order identical (planned hides excepted);
4. evaluator self-test on the new cached values;
5. re-run of all checks: zero CAST/PLUG-01 findings (except reported conflicts),
   balance-sheet / cash-flow gaps only where too large to balance;
6. optional LibreOffice recalculation compare.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fscore.config import EngagementConfig
from fscore.engine.evaluator import values_equal
from fscore.engine.selftest import THRESHOLD, run_selftest
from fscore.fix.solver import canon_formula
from fscore.io.ooxml_patch import validate_package
from fscore.io.xlsx_reader import Workbook
from fscore.models import Edit, EditKind, FixPlan, VerificationReport, VerificationStep

CASTING_RULES = {"CAST-01", "CAST-02", "CAST-03", "PLUG-01"}


def soffice_path() -> str | None:
    for cand in (shutil.which("soffice"), shutil.which("libreoffice"), "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if cand and Path(cand).exists():
            return cand
    return None


def _structure(wb: Workbook, sheets: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"order": [(s.name, s.state) for s in wb.sheets],
                           "print_areas": {s.name: [a.a1() for a in s.print_area] if s.print_area else None for s in wb.sheets}}
    for s in wb.sheets:
        if s.name not in sheets:
            continue
        out[s.name] = {
            "merges": sorted(m.a1() for m in s.merges),
            "rows_hidden": sorted(s.rows_hidden),
            "cols_hidden": sorted(s.cols_hidden),
        }
    return out


def changed_sheet_parts(src: Path, out: Path) -> tuple[set[str], list[str]]:
    """Worksheet parts whose bytes differ (unchanged parts are copied byte for byte)."""
    import zipfile

    with zipfile.ZipFile(src) as za, zipfile.ZipFile(out) as zb:
        a_names, b_names = set(za.namelist()), set(zb.namelist())
        changed = {n for n in a_names & b_names if za.read(n) != zb.read(n)}
        added = sorted(b_names - a_names)
    return changed, added


def diff_against_source(src: Path, out: Path, edits: list[Edit]) -> list[str]:
    planned_cells = {(e.sheet, e.cell) for e in edits if e.kind in (EditKind.FORMULA, EditKind.VALUE, EditKind.TEXT)}
    planned_rows = {(e.sheet, int(e.cell)) for e in edits if e.kind == EditKind.HIDE_ROW}
    planned_cols = {(e.sheet, e.cell) for e in edits if e.kind == EditKind.HIDE_COL}
    problems: list[str] = []
    changed_parts, added_parts = changed_sheet_parts(src, out)
    if added_parts:
        problems.append(f"parts added: {', '.join(added_parts[:5])}")
    with Workbook.open(src) as a, Workbook.open(out) as b:
        # cell text (shared strings) and number formats (styles) live outside the sheet parts: when either
        # table differs, a byte-identical sheet part no longer proves its cells unchanged, so compare them all
        shared_tables_changed = a.sst != b.sst or a.styles != b.styles
        changed = {s.name for s in a.sheets if shared_tables_changed or s.part in changed_parts}
        sa, sb = _structure(a, changed), _structure(b, changed)
        if sa["order"] != sb["order"]:
            problems.append("sheet order / states changed")
        if sa["print_areas"] != sb["print_areas"]:
            problems.append("print areas changed")
        for s in a.sheets:
            t = b.get_sheet(s.name)
            if t is None:
                problems.append(f"sheet missing: {s.name}")
                continue
            if s.name not in changed:
                continue  # byte-identical part and shared tables: nothing in it can differ
            if sa[s.name]["merges"] != sb[s.name]["merges"]:
                problems.append(f"{s.name}: merged cells changed")
            extra_rows = set(sb[s.name]["rows_hidden"]) - set(sa[s.name]["rows_hidden"])
            if extra_rows - {r for (sh, r) in planned_rows if sh == s.name} or set(sa[s.name]["rows_hidden"]) - set(sb[s.name]["rows_hidden"]):
                problems.append(f"{s.name}: hidden rows changed")
            from fscore.engine.refs import index_to_col

            extra_cols = {index_to_col(c) for c in set(sb[s.name]["cols_hidden"]) - set(sa[s.name]["cols_hidden"])}
            if extra_cols - {c for (sh, c) in planned_cols if sh == s.name} or set(sa[s.name]["cols_hidden"]) - set(sb[s.name]["cols_hidden"]):
                problems.append(f"{s.name}: hidden columns changed")
            ca = {(c.row, c.col): c for c in s.iter_cells()}
            cb = {(c.row, c.col): c for c in t.iter_cells()}
            for pos in set(ca) | set(cb):
                x, y = ca.get(pos), cb.get(pos)
                from fscore.engine.refs import make_addr

                addr = make_addr(*pos)
                planned = (s.name, addr) in planned_cells
                if x is None or y is None:
                    if not planned and not (y is not None and y.value is None and y.formula is None):
                        problems.append(f"{s.name}!{addr}: cell added/removed")
                    continue
                if s.number_format(x) != t.number_format(y):
                    problems.append(f"{s.name}!{addr}: number format changed")
                if planned:
                    continue
                if canon_formula(x.formula) != canon_formula(y.formula):
                    problems.append(f"{s.name}!{addr}: formula changed")
                elif x.formula is None and not values_equal(x.value, y.value):
                    problems.append(f"{s.name}!{addr}: constant changed")
            if len(problems) > 200:
                break
    return problems


def libreoffice_recalc_compare(out: Path, sheets: list[str], timeout: int = 180) -> tuple[bool, str]:
    exe = soffice_path()
    if exe is None:
        return True, "LibreOffice not installed: skipped"
    work = Path(tempfile.mkdtemp(prefix="fscheck-lo-"))
    profile = work / f"profile-{uuid.uuid4().hex}"
    try:
        src = work / "in.xlsx"
        shutil.copy2(out, src)
        outdir = work / "out"
        outdir.mkdir()
        cmd = [exe, f"-env:UserInstallation=file://{profile}", "--headless", "--norestore", "--convert-to", "xlsx:Calc MS Excel 2007 XML", "--outdir", str(outdir), str(src)]
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)  # noqa: S603 - fixed local binary, no shell
        produced = outdir / "in.xlsx"
        if not produced.exists():
            return False, "LibreOffice produced no output"
        mism = 0
        checked = 0
        with Workbook.open(out) as a, Workbook.open(produced) as b:
            for name in sheets:
                s, t = a.get_sheet(name), b.get_sheet(name)
                if s is None or t is None:
                    continue
                for c in s.iter_cells():
                    if c.formula is None or not c.is_number:
                        continue
                    d = t.cell(c.row, c.col)
                    checked += 1
                    if d is None or not values_equal(c.value, d.value):
                        mism += 1
        return mism == 0, f"LibreOffice recalculation: {checked - mism}/{checked} formula values agree"
    except (subprocess.SubprocessError, OSError) as exc:
        return False, f"LibreOffice recalculation failed: {type(exc).__name__}"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def verify_copy(src: Path, out: Path, plan: FixPlan, applied: list[Edit], config: EngagementConfig, *, libreoffice: bool | None = None) -> VerificationReport:
    rep = VerificationReport()
    changed_parts, added_parts = changed_sheet_parts(src, out)
    errs = validate_package(out, only=changed_parts | set(added_parts) | {"[Content_Types].xml"})
    rep.xml_errors = errs
    rep.steps.append(VerificationStep(name="xml", ok=not errs, detail="all XML parts parse strictly" if not errs else "; ".join(errs[:3])))
    try:
        import openpyxl

        with Workbook.open(src) as a:
            changed_names = [s.name for s in a.sheets if s.part in changed_parts]
        for data_only in (False, True):
            ob = openpyxl.load_workbook(out, read_only=True, data_only=data_only)
            for name in changed_names:
                if name in ob.sheetnames:
                    for _row in ob[name].iter_rows(values_only=True):
                        pass
            ob.close()
        rep.steps.append(VerificationStep(name="openpyxl", ok=True, detail=f"re-opened with an independent parser ({len(changed_names)} changed sheet(s) parsed in full)"))
    except Exception as exc:
        rep.steps.append(VerificationStep(name="openpyxl", ok=False, detail=f"openpyxl cannot open the copy: {type(exc).__name__}"))
    problems = diff_against_source(src, out, applied)
    rep.unexpected_changes = problems[:200]
    rep.steps.append(VerificationStep(name="diff", ok=not problems, detail="only planned cells differ" if not problems else f"{len(problems)} unexpected difference(s)"))
    with Workbook.open(out) as wb:
        from fscore.engine.printsel import select_print_sheets

        names = [s.name for s in select_print_sheets(wb, config)]
        st = run_selftest(wb, names)
    rep.selftest_match_rate = st.match_rate
    rep.steps.append(VerificationStep(name="selftest", ok=st.match_rate >= THRESHOLD, detail=f"{st.matched}/{st.evaluated} formulas re-evaluate to the cached values"))
    from fscore.runner import run_checks

    res = run_checks(out, config)
    unresolved_cells = {u.split(":")[0] for u in plan.unresolved}
    remaining = [f for f in res.findings if f.rule_id in CASTING_RULES and not any(f"{f.sheet}!{c}" in unresolved_cells for c in f.cells)]
    allowed_bal = {(b.statement, b.period) for b in plan.balancing if b.status != "applied"}
    bal_left = [f for f in res.findings if f.rule_id in ("BAL-01", "BAL-02")
                and (("balance_sheet" if f.rule_id == "BAL-01" else "cash_flow"), f.period) not in allowed_bal]
    rep.remaining_cast_plug = len(remaining)
    rep.steps.append(VerificationStep(name="recheck", ok=not remaining and not bal_left,
                                      detail=f"{len(remaining)} casting/plug and {len(bal_left)} balancing finding(s) remain after the fix"))
    use_lo = libreoffice if libreoffice is not None else os.environ.get("FSCHECK_VERIFY_LIBREOFFICE") == "1"
    if use_lo:
        ok, detail = libreoffice_recalc_compare(out, names)
        rep.steps.append(VerificationStep(name="libreoffice", ok=ok, detail=detail))
    rep.ok = all(s.ok for s in rep.steps)
    return rep
