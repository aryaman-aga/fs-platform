from __future__ import annotations

import re
from pathlib import Path

from helpers_report import XSS, make_applied, make_finding, make_plan, make_result

from fscore.models import CellRef, Evidence
from fscore.report.html import render_html, write_html


def test_html_escapes_workbook_text(out_dir: Path) -> None:
    f = make_finding("F-0001", explanation=f"Caption says {XSS}", caption=XSS)
    f.evidence.append(Evidence(label=XSS, ref=CellRef(sheet="BS", addr="A1"), printed=XSS, note=XSS))
    f.suggested_fix = XSS
    result = make_result(findings=[f])
    result.run.warnings.append(XSS)
    html = write_html(result, out_dir / "r.html", plan=make_plan()).read_text(encoding="utf-8")
    assert "<script>" not in html.lower()
    assert "&lt;script&gt;" in html
    # no active content and no network references at all
    assert not re.search(r"<script|<iframe|<object|<embed|<link\b", html, re.I)
    assert not re.search(r"(src|href)\s*=\s*['\"]?(https?:)?//", html, re.I)
    assert "default-src 'none'" in html


def test_html_sections_and_counts() -> None:
    result = make_result()
    html = render_html(result, plan=make_plan(), applied=make_applied())
    for heading in ("Findings by rule", "Key figures", "Findings (5)", "Fix plan", "Printed figures moved (3)", "Re-print list",
                    "PDF reconciliation", "Cross-reference map (1)", "Run information"):
        assert heading in html, heading
    assert "PDF must be re-printed" in html
    assert "The signed PDF must be re-printed." in html
    assert "SUMPRODUCT(ROUND(C20:C21,2))" in html
    assert make_plan().plan_sha256 in html
    # key figure moved by the plan shows its new printed value
    assert "1,000.01" in html


def test_html_clean_run_has_nothing_to_reprint() -> None:
    html = render_html(make_result(findings=[], pdf=False))
    assert "No findings." in html
    assert "Nothing to re-print." in html
