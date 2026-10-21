"""Public facade of the engine.

The CLI, the web platform's workers and the offline edition call **only** these
functions (decision D3: engine logic lives only in fscore). All functions are
pure with respect to their inputs: they never modify a source file, and they
write only to the explicit output paths they are given.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fscore.config import EngagementConfig
from fscore.models import (
    ApplyResult,
    CheckResult,
    FixPlan,
    PdfReconResult,
    RenderResult,
    TextEdit,
    WorkbookDiff,
)

ProgressFn = Callable[[str, float], None]


def check_workbook(
    path: str | Path,
    config: EngagementConfig | None = None,
    *,
    pdf_path: str | Path | None = None,
    prior_year_path: str | Path | None = None,
    progress: ProgressFn | None = None,
    previous_sheet_states: dict[str, dict[str, Any]] | None = None,
) -> CheckResult:
    """Run every registered rule on a workbook (Module R) and return the findings.

    ``previous_sheet_states`` is ``CheckResult.sheet_states`` from the previous run
    (ENG-02: detects sheets opened and saved in Excel since then).
    """
    from fscore.runner import run_checks

    return run_checks(Path(path), config or EngagementConfig(), pdf_path=pdf_path, prior_year_path=prior_year_path,
                      progress=progress, previous_sheet_states=previous_sheet_states)


def propose_fixes(
    path: str | Path,
    config: EngagementConfig | None = None,
    *,
    check_result: CheckResult | None = None,
    progress: ProgressFn | None = None,
) -> FixPlan:
    """Plan the corrections (totals-only rewrite, plug removal, balancing lines, approvals)."""
    from fscore.fix.planner import plan_fixes

    return plan_fixes(Path(path), config or EngagementConfig(), check_result=check_result, progress=progress)


def apply_fixes(
    path: str | Path,
    plan: FixPlan,
    out_path: str | Path,
    *,
    approved_edit_ids: set[str] | None = None,
    config: EngagementConfig | None = None,
    backup_dir: str | Path | None = None,
) -> ApplyResult:
    """Write a corrected *copy* by surgical OOXML patching, then verify it (control C5)."""
    from fscore.fix.apply import apply_plan

    return apply_plan(
        Path(path),
        plan,
        Path(out_path),
        approved_edit_ids=approved_edit_ids,
        config=config or EngagementConfig(),
        backup_dir=Path(backup_dir) if backup_dir else None,
    )


def apply_text_edits(
    path: str | Path,
    edits: list[TextEdit],
    out_path: str | Path,
    config: EngagementConfig | None = None,
) -> ApplyResult:
    """Apply narrative corrections as typed text (control C6 rejects formulas)."""
    from fscore.fix.text import apply_text

    return apply_text(Path(path), edits, Path(out_path), config or EngagementConfig())


def render_pdf(
    path: str | Path,
    out_path: str | Path,
    *,
    mode: str = "draft",
    engine: str = "auto",
    config: EngagementConfig | None = None,
    run_id: str = "",
    check_result: CheckResult | None = None,
) -> RenderResult:
    """Excel → PDF (02 §10A) followed by the mandatory PDF-vs-workbook verification."""
    from fscore.pdf.render import render

    return render(
        Path(path),
        Path(out_path),
        mode=mode,
        engine=engine,
        config=config or EngagementConfig(),
        run_id=run_id,
        check_result=check_result,
    )


def reconcile_pdf(path: str | Path, pdf_path: str | Path, config: EngagementConfig | None = None) -> PdfReconResult:
    """PDF-01…03: is the printed PDF current, and does every printed number match?"""
    from fscore.checks.pdf_recon import reconcile

    return reconcile(Path(path), Path(pdf_path), config or EngagementConfig())


def prepare_statements(
    tb_path: str | Path,
    out_path: str | Path,
    *,
    config: EngagementConfig | None = None,
    supplementary: str | Path | dict[str, Any] | None = None,
    mapping: str | Path | dict[str, str] | None = None,
    entity: dict[str, Any] | None = None,
    layout: str = "annual",
    checklist_answers: dict[str, Any] | None = None,
    **_ignored: Any,
) -> Any:
    """Module P: trial balance (+ mapping + supplementary schedules) → statements workbook.

    Runs the TB validation gate first (E-DATA-01); returns ``PrepareResult`` with
    ``ok=False`` and the blocking issues (unbalanced TB, unmapped accounts, missing
    schedules) instead of writing anything when the inputs are not fit for use.
    """
    import yaml

    from fscore.prepare.api import prepare
    from fscore.prepare.inputs import EntityInfo
    from fscore.prepare.mapping import MappingVersion
    from fscore.prepare.supplementary import load_bundle
    from fscore.prepare.tb import load_tb

    cfg = config or EngagementConfig()
    tb = load_tb(tb_path, entity=cfg.entity or cfg.client)
    if isinstance(mapping, (str, Path)):
        data = yaml.safe_load(Path(mapping).read_text(encoding="utf-8")) or {}
        rows_map = data.get("rows", data) if isinstance(data, dict) else {}
        mv = MappingVersion(client=cfg.client, rows={str(k): str(v) for k, v in rows_map.items()})
    elif isinstance(mapping, dict):
        mv = MappingVersion(client=cfg.client, rows={str(k): str(v) for k, v in mapping.items()})
    else:
        mv = MappingVersion.from_tb(tb, client=cfg.client, suggest=True)
    years, opening, bundled_entity = load_bundle(supplementary) if supplementary is not None else ({}, {}, None)
    py_end = cfg.year_end.replace(year=cfg.year_end.year - 1)
    ent = bundled_entity or EntityInfo(name=cfg.entity or cfg.client, short=(cfg.entity or cfg.client).split(" ")[0])
    ent.year_end, ent.py_end, ent.standalone = cfg.year_end, py_end, cfg.standalone
    for k, v in (entity or {}).items():
        if hasattr(ent, k):
            setattr(ent, k, v)
    return prepare(tb, mv, years, ent, out_path, opening=opening, layout=layout, units=cfg.units, checklist_answers=checklist_answers)


def check_pdf_only(pdf_path: str | Path, config: EngagementConfig | None = None) -> tuple[list[Any], PdfReconResult]:
    """PDF-04: casting and cross-reference checks on a PDF when no workbook is supplied (lower certainty, no fixes)."""
    from fscore.checks.pdf_recon import check_pdf_only as _pdf_only

    return _pdf_only(Path(pdf_path), config or EngagementConfig())


def diff_workbooks(a: str | Path, b: str | Path, config: EngagementConfig | None = None) -> WorkbookDiff:
    """Cell-level diff and 'printed figures moved' between two versions (06 §5)."""
    from fscore.diff import diff

    return diff(Path(a), Path(b), config or EngagementConfig())


def write_register(
    result: CheckResult,
    out_path: str | Path,
    *,
    plan: FixPlan | None = None,
    applied: ApplyResult | None = None,
) -> Path:
    """Exceptions register workbook (02 §10)."""
    from fscore.report.register import write_register as _write

    return _write(result, Path(out_path), plan=plan, applied=applied)


def write_html_report(result: CheckResult, out_path: str | Path, *, plan: FixPlan | None = None) -> Path:
    from fscore.report.html import write_html

    return write_html(result, Path(out_path), plan=plan)


def watermark_pdf(src: str | Path, out_path: str | Path, text: str) -> Path:
    """Dynamic watermark for viewed / downloaded PDFs (E-SEC-10)."""
    from fscore.pdf.watermark import stamp

    return stamp(Path(src), Path(out_path), text)
