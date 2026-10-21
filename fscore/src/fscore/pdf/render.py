"""Excel → PDF orchestration (02 §10A): engine choice, draft/final gate, naming,
draft overlay, bookmarks and the mandatory post-conversion verification.

Engines (all local, never online): ``excel`` (COM, Windows) → ``libreoffice``
(headless) → ``native`` (built-in ReportLab renderer). ``auto`` picks the first
available and falls back to the next one when an engine fails to run (Excel not
installed or not startable, a LibreOffice crash). A *final* PDF needs zero High
findings (signatory checks included) and, by default, an Excel or LibreOffice
render; it is deleted again if the verification finds any miss (control C7).
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from fscore.checks.base import CheckContext
from fscore.checks.pdf_recon import ReconState, verify
from fscore.config import EngagementConfig
from fscore.engine.printsel import select_print_sheets
from fscore.io.ooxml import sha256_file
from fscore.io.xlsx_reader import Workbook, WorkbookReadError
from fscore.models import CheckResult, RenderResult, Severity
from fscore.pdf import render_excel_com, render_libreoffice, render_native
from fscore.pdf.naming import never_overwrite, versioned_path
from fscore.pdf.watermark import set_bookmarks, stamp_draft

ENGINES = ("excel", "libreoffice", "native")
_NOTE_LINE = re.compile(r"^\s*(?:note\s*(?:no\.?)?\s*)?(\d{1,3})[.:)]?\s+([A-Z][A-Za-z ,'&()/\-–]{2,70})$", re.I)


def available_engines() -> dict[str, bool]:
    return {"excel": render_excel_com.available(), "libreoffice": render_libreoffice.available(), "native": True}


def allow_native_final(config: EngagementConfig) -> bool:
    """Policy switch: may a final PDF come from the built-in renderer?

    Allowed when the engagement configuration says so (``render.allow_native_final``)
    or the deployment-wide ``FSCORE_ALLOW_NATIVE_FINAL`` policy is set.
    Default: no (final PDFs come from Excel or LibreOffice).
    """
    render_cfg = getattr(config, "render", None)
    if render_cfg is not None and bool(getattr(render_cfg, "allow_native_final", False)):
        return True
    return os.environ.get("FSCORE_ALLOW_NATIVE_FINAL", "").lower() in ("1", "true", "yes")


def resolve_engine(requested: str, mode: str, config: EngagementConfig) -> tuple[str | None, str]:
    """Return (engine, note). ``engine`` is None when the request cannot be met."""
    avail = available_engines()
    req = (requested or "auto").lower()
    if req not in ("auto", *ENGINES):
        return None, f"unknown engine '{requested}'"
    if req == "auto":
        chosen = next(e for e in ENGINES if avail[e])
        note = "" if chosen != "native" else "Excel and LibreOffice are not available; using the built-in renderer"
    else:
        if not avail[req]:
            return None, f"engine '{req}' is not available on this machine"
        chosen, note = req, ""
    if mode == "final" and chosen == "native" and not allow_native_final(config):
        return None, (
            "a final PDF needs Excel or LibreOffice (the built-in renderer is for drafts); "
            "install LibreOffice or allow native finals by policy"
        )
    return chosen, note


def fallback_engines(chosen: str, requested: str, mode: str, config: EngagementConfig) -> list[str]:
    """Engines ``auto`` falls back to, in order, when ``chosen`` fails (an explicit engine never falls back)."""
    if (requested or "auto").lower() != "auto" or chosen not in ENGINES:
        return []
    avail = available_engines()
    later = ENGINES[ENGINES.index(chosen) + 1 :]
    return [e for e in later if avail[e] and (e != "native" or mode != "final" or allow_native_final(config))]


_ENGINE_LABEL = {"excel": "Excel", "libreoffice": "LibreOffice", "native": "the built-in renderer"}


def final_gate(check_result: CheckResult | None) -> str | None:
    """Return the refusal message for a final render, or None when allowed."""
    if check_result is None:
        return "a final PDF needs a current check run (Module R) with zero High findings"
    highs = [f for f in check_result.findings if f.severity == Severity.HIGH]
    sign = [f for f in highs if f.rule_id == "TXT-01"]
    if sign:
        return f"final blocked: {len(sign)} High signatory finding(s) (DIN / name / date / place) are open"
    if highs:
        return f"final blocked: {len(highs)} High finding(s) are open"
    return None


def _period_label(config: EngagementConfig) -> str:
    y = config.year_end
    tag = y.strftime("%b-%y")
    return f"Qtr-{tag}" if config.period_type in ("interim", "spfs") else f"FY-{tag}"


def _destination(out_path: Path, config: EngagementConfig, when: datetime) -> Path:
    if out_path.suffix.lower() != ".pdf" or (out_path.exists() and out_path.is_dir()):
        entity = config.entity or config.client
        sset = "SPFS" if config.period_type == "spfs" else "FS"
        return versioned_path(out_path, entity, sset, _period_label(config), when)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return never_overwrite(out_path)


def _bookmarks_from_state(state: ReconState, titles: dict[str, str]) -> list[tuple[int, str, int]]:
    """Outline for engines that do not produce one: sheet entries + 'Note N' headings found on pages."""
    out: list[tuple[int, str, int]] = []
    seen_notes: set[str] = set()
    for sheet in state.print_sheets:
        pages = state.sheet_pages.get(sheet)
        if not pages:
            continue
        out.append((1, titles.get(sheet, sheet), pages[0]))
        for pno in pages:
            for ln in state.doc.page(pno).lines:
                words = [w for w in ln.words if not w.invisible]
                if not words or not words[0].bold:
                    continue
                m = _NOTE_LINE.match(" ".join(w.text for w in words))
                if m and m.group(1) not in seen_notes and int(m.group(1)) < 200:
                    seen_notes.add(m.group(1))
                    out.append((2, f"Note {m.group(1)} {m.group(2).strip()}", pno))
    return out


def _finish(
    path: Path,
    tmp_pdf: Path,
    dest: Path,
    *,
    engine: str,
    meta: dict[str, object],
    mode: str,
    run_id: str,
    config: EngagementConfig,
    bookmarks: list[tuple[int, str, int]] | None,
    titles: dict[str, str],
    note: str = "",
) -> RenderResult:
    """Common tail of every render: draft overlay, verification, bookmarks, atomic publish."""
    if mode == "draft" and engine != "native":
        stamp_draft(tmp_pdf, tmp_pdf, run_id)
    with Workbook.open(path) as wb:
        ctx = CheckContext(wb, config)
        state, problems = verify(ctx, tmp_pdf)
    if bookmarks is None:
        bookmarks = _bookmarks_from_state(state, titles)
    titles_out = set_bookmarks(tmp_pdf, bookmarks)
    engine_version = str(meta.get("engine_version", "") or "")
    if mode == "final" and problems:
        return RenderResult(
            ok=False, mode="final", engine=engine, engine_version=engine_version, pages=state.result.pages,
            verification=state.result,
            message="final PDF rejected by verification (control C7): " + "; ".join(problems[:6]),
        )
    os.replace(tmp_pdf, dest)
    msg_parts = [p for p in (note,) if p]
    if problems:
        msg_parts.append("verification issues: " + "; ".join(problems[:6]))
    else:
        msg_parts.append(f"verified: {state.result.values_checked} printed values found on {state.result.pages} page(s)")
    if meta.get("excel_build"):
        msg_parts.append(f"Excel build {meta['excel_build']}")
    return RenderResult(
        ok=not problems,
        pdf_path=str(dest),
        sha256=sha256_file(dest),
        engine=engine,
        engine_version=engine_version,
        mode="final" if mode == "final" else "draft",
        pages=state.result.pages,
        bookmarks=titles_out,
        verification=state.result,
        message=" · ".join(msg_parts),
    )


def _print_titles(path: Path, config: EngagementConfig) -> tuple[list[str], dict[str, str]]:
    with Workbook.open(path) as wb:
        sel = select_print_sheets(wb, config)
        return [s.name for s in sel], {s.name: render_native._sheet_title(wb, s) for s in sel}


def finish_external_render(
    xlsx_path: Path,
    produced_pdf: Path,
    out_path: Path,
    *,
    mode: str,
    config: EngagementConfig,
    run_id: str = "",
    engine_meta: dict[str, object] | None = None,
    check_result: CheckResult | None = None,
) -> RenderResult:
    """Post-process a PDF produced elsewhere (the Windows Excel render worker):
    final gate, DRAFT overlay, bookmarks, mandatory verification, versioned publish.
    """
    mode = (mode or "draft").lower()
    if mode == "final":
        refusal = final_gate(check_result)
        if refusal:
            return RenderResult(ok=False, mode="final", message=refusal)
    meta = dict(engine_meta or {"engine": "excel"})
    try:
        _sheets, titles = _print_titles(Path(xlsx_path), config)
    except WorkbookReadError as exc:
        return RenderResult(ok=False, mode="final" if mode == "final" else "draft", message=str(exc))
    dest = _destination(Path(out_path), config, datetime.now())
    tmp_dir = Path(tempfile.mkdtemp(prefix="fs-render-", dir=str(dest.parent)))
    tmp_pdf = tmp_dir / f"external-{uuid.uuid4().hex[:8]}.pdf"
    try:
        shutil.copyfile(produced_pdf, tmp_pdf)
        return _finish(
            Path(xlsx_path), tmp_pdf, dest, engine=str(meta.get("engine", "excel")), meta=meta, mode=mode,
            run_id=run_id, config=config, bookmarks=None, titles=titles,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def render(
    path: Path,
    out_path: Path,
    *,
    mode: str = "draft",
    engine: str = "auto",
    config: EngagementConfig,
    run_id: str = "",
    check_result: CheckResult | None = None,
) -> RenderResult:
    mode = (mode or "draft").lower()
    if mode not in ("draft", "final"):
        return RenderResult(ok=False, mode="draft", message=f"unknown mode '{mode}' (draft | final)")
    if mode == "final":
        refusal = final_gate(check_result)
        if refusal:
            return RenderResult(ok=False, mode="final", message=refusal)
    chosen, note = resolve_engine(engine, mode, config)
    if chosen is None:
        return RenderResult(ok=False, mode=mode, message=note)
    try:
        sheets, titles = _print_titles(Path(path), config)
    except WorkbookReadError as exc:
        return RenderResult(ok=False, mode=mode, engine=chosen, message=str(exc))
    if not sheets:
        return RenderResult(ok=False, mode=mode, engine=chosen, message="no print sheets selected")
    when = datetime.now()
    dest = _destination(Path(out_path), config, when)
    tmp_dir = Path(tempfile.mkdtemp(prefix="fs-render-", dir=str(dest.parent)))
    tmp_pdf = tmp_dir / f"render-{uuid.uuid4().hex[:8]}.pdf"
    draft = mode == "draft"
    failures: list[str] = []
    try:
        # engine="auto" falls back (Excel → LibreOffice → built-in) when an engine cannot run
        for current in [chosen, *fallback_engines(chosen, engine, mode, config)]:
            tmp_pdf.unlink(missing_ok=True)
            bookmarks: list[tuple[int, str, int]] | None = None
            try:
                if current == "native":
                    res = render_native.render_native(Path(path), tmp_pdf, sheets, draft=draft, run_id=run_id, title=Path(path).stem)
                    meta: dict[str, object] = {"engine": "native", "engine_version": render_native.ENGINE_VERSION, "pages": res.pages}
                    bookmarks = res.bookmarks
                elif current == "libreoffice":
                    meta = render_libreoffice.export_pdf(Path(path), tmp_pdf, sheets)
                else:
                    meta = render_excel_com.export_pdf(Path(path), tmp_pdf, sheets)
            except (render_libreoffice.LibreOfficeError, render_excel_com.ExcelComError) as exc:
                failures.append(f"{_ENGINE_LABEL[current]} failed: {exc}")
                continue
            notes = [note] if note and not failures else []
            if failures:
                notes.append("; ".join(failures) + f"; rendered with {_ENGINE_LABEL[current]}")
            return _finish(
                Path(path), tmp_pdf, dest, engine=current, meta=meta, mode=mode, run_id=run_id, config=config,
                bookmarks=bookmarks, titles=titles, note=" · ".join(notes),
            )
        return RenderResult(ok=False, mode=mode, engine=chosen, message="; ".join(failures))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
