"""Self-contained HTML report (02 §10.3): offline, no external assets.

All workbook-derived text is autoescaped by Jinja2; the page carries a strict
Content-Security-Policy (no network, inline style only).
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any

import jinja2

from fscore import ENGINE_VERSION
from fscore.models import SEVERITY_ORDER, ApplyResult, CheckResult, FixPlan, Severity
from fscore.report.common import (
    edit_status,
    edits_by_finding,
    finding_status,
    pdf_needs_reprint,
    printed_moves,
    reprint_sheets,
    sorted_findings,
    statement_label,
)

_TEMPLATE = "templates/report.html.j2"


def _env() -> jinja2.Environment:
    env = jinja2.Environment(autoescape=True, undefined=jinja2.StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    env.filters["amount"] = _amount
    return env


def _amount(d: Decimal | float | None) -> str:
    if d is None:
        return ""
    v = Decimal(str(d))
    s = f"{abs(v):,.2f}"
    return f"({s})" if v < 0 else s


def render_html(result: CheckResult, *, plan: FixPlan | None = None, applied: ApplyResult | None = None, title: str | None = None) -> str:
    text = resources.files("fscore.report").joinpath(_TEMPLATE).read_text(encoding="utf-8")
    template = _env().from_string(text)
    findings = sorted_findings(result.findings)
    by_finding = edits_by_finding(plan)
    counts = result.counts()
    by_rule = result.by_rule()
    max_rule = max(by_rule.values(), default=1)
    moves = printed_moves(plan, applied)
    moved = {(m.sheet, m.cell): m for m in moves}
    key_rows = []
    for k in result.key_figures:
        m = moved.get((k.ref.sheet, k.ref.addr)) if k.ref else None
        key_rows.append({"kf": k, "after": m.after if m else None, "moved": m is not None})
    ctx: dict[str, Any] = {
        "title": title or f"FS-Check – {result.run.file_name}",
        "result": result,
        "run": result.run,
        "plan": plan,
        "applied": applied,
        "findings": [
            {
                "f": f,
                "n": i,
                "statement": statement_label(result, f.sheet),
                "status": finding_status(f, plan, applied, by_finding),
                "sev_class": f.severity.value.lower(),
            }
            for i, f in enumerate(findings, start=1)
        ],
        "severities": [s.value for s in sorted(Severity, key=lambda s: SEVERITY_ORDER[s])],
        "counts": counts,
        "by_rule": [{"rule": r, "n": n, "pct": round(100 * n / max_rule)} for r, n in by_rule.items()],
        "key_rows": key_rows,
        "moves": moves,
        "edits": [{"e": e, "status": edit_status(e, applied)} for e in (plan.edits if plan else [])],
        "reprint": pdf_needs_reprint(result, plan, applied),
        "reprint_sheets": reprint_sheets(plan, applied),
        "reprint_pages": result.pdf.reprint_pages if result.pdf else [],
        "generated": datetime.now(UTC).strftime("%d-%b-%Y %H:%M UTC"),
        "engine_version": ENGINE_VERSION,
        "needs_approval": sum(1 for f in findings if f.fix_kind.value == "approval"),
        "manual": sum(1 for f in findings if f.fix_kind.value == "manual"),
        "auto": sum(1 for f in findings if f.fix_kind.value == "auto"),
    }
    return template.render(**ctx)


def write_html(result: CheckResult, out_path: Path, *, plan: FixPlan | None = None, applied: ApplyResult | None = None) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html = render_html(result, plan=plan, applied=applied)
    fd, tmp = tempfile.mkstemp(suffix=".html", prefix=".report-", dir=str(out_path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(html)
    os.replace(tmp, out_path)
    return out_path
