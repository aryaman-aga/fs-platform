"""Module R runner: open the workbook, build the context, run every registered check."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from fscore import ENGINE_VERSION
from fscore.checks.base import CheckContext
from fscore.config import EngagementConfig
from fscore.engine.selftest import THRESHOLD, run_selftest
from fscore.io.xlsx_reader import Workbook
from fscore.models import SEVERITY_ORDER, CheckResult, RunInfo
from fscore.registry import load_all_checks, rule_set_hash, rule_set_version

log = logging.getLogger("fscore.runner")

ProgressFn = Callable[[str, float], None]


def build_context(path: Path, config: EngagementConfig, *, pdf_path: str | Path | None = None,
                  prior_year_path: str | Path | None = None) -> CheckContext:
    wb = Workbook.open(path)
    return CheckContext(wb, config, pdf_path=Path(pdf_path) if pdf_path else None,
                        prior_year_path=Path(prior_year_path) if prior_year_path else None)


def run_checks(
    path: Path,
    config: EngagementConfig,
    *,
    pdf_path: str | Path | None = None,
    prior_year_path: str | Path | None = None,
    progress: ProgressFn | None = None,
    context: CheckContext | None = None,
    previous_sheet_states: dict[str, dict[str, object]] | None = None,
) -> CheckResult:
    started = datetime.now(UTC)
    t0 = time.perf_counter()
    ctx = context or build_context(path, config, pdf_path=pdf_path, prior_year_path=prior_year_path)
    wb = ctx.wb
    if previous_sheet_states:
        ctx.shared["previous_sheet_states"] = previous_sheet_states
    try:
        if progress:
            progress("reading", 0.05)
        print_names = [s.name for s in ctx.print_sheets]
        selftest = run_selftest(wb, print_names)
        ctx.shared["selftest"] = selftest
        if selftest.evaluated and selftest.match_rate < THRESHOLD:
            ctx.warnings.append(f"evaluator self-test {selftest.match_rate:.1%} below {THRESHOLD:.1%}: fixes are disabled for this run")
        impls = load_all_checks()
        n = max(len(impls), 1)
        for i, impl in enumerate(impls):
            if impl.needs_pdf and ctx.pdf_path is None:
                continue
            if all(not ctx.enabled(r) for r in impl.rule_ids):
                continue
            try:
                impl.fn(ctx)
            except Exception as exc:  # a failing rule must not stop the run; log type only (no client data)
                log.exception("check %s failed", impl.name)
                ctx.warnings.append(f"check {impl.name} ({', '.join(impl.rule_ids)}) failed: {type(exc).__name__}")
            if progress:
                progress(impl.name, 0.1 + 0.85 * (i + 1) / n)
        findings = sorted(ctx.findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.sheet or "", _cell_key(f.cells), f.rule_id))
        for i, f in enumerate(findings, 1):
            f.id = f"F-{i:04d}"
        try:
            key_figures = ctx.key_figures.as_list()
        except Exception:
            log.exception("key figures failed")
            key_figures = []
        try:
            statements = ctx.statements.as_dict()
        except Exception:
            statements = {}
        finished = datetime.now(UTC)
        run = RunInfo(
            engine_version=ENGINE_VERSION,
            rule_set_version=rule_set_version(),
            rule_set_hash=rule_set_hash(),
            file_name=path.name,
            file_sha256=wb.sha256,
            config_hash=config.config_hash(),
            started_at=started,
            finished_at=finished,
            duration_s=round(time.perf_counter() - t0, 3),
            cells_read=wb.cells_read,
            sheets=len(wb.sheets),
            print_sheets=print_names,
            selftest=selftest,
            warnings=list(ctx.warnings),
        )
        result = CheckResult(
            run=run,
            findings=findings,
            key_figures=key_figures,
            statements=statements,
            xref_map=ctx.shared.get("xref_map", []),
            pdf=ctx.shared.get("pdf_recon"),
            narrative=ctx.shared.get("narrative", []),
            rpt_recon=ctx.shared.get("rpt_recon", []),
            text_proposals=ctx.shared.get("text_proposals", []),
            sheet_states=_sheet_states(wb),
        )
        if progress:
            progress("done", 1.0)
        return result
    finally:
        if context is None:
            wb.close()


def _sheet_states(wb: Workbook) -> dict[str, dict[str, object]]:
    from fscore.checks.engine_meta import sheet_states

    try:
        return dict(sheet_states(wb))
    except Exception:
        return {}


def _cell_key(cells: list[str]) -> tuple[int, int]:
    if not cells:
        return (0, 0)
    from fscore.engine.refs import split_addr

    try:
        r, c = split_addr(cells[0].split(":")[0].split("!")[-1])
        return (r, c)
    except ValueError:
        return (0, 0)
