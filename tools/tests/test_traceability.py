"""E-ITGC-06: the traceability and control matrices are generated from the code and every link resolves."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _tool():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("traceability", ROOT / "tools" / "traceability.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["traceability"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_matrices_are_current_and_every_link_resolves() -> None:
    tool = _tool()
    text, gaps = tool.build()
    matrix, matrix_gaps = tool.build_control_matrix()
    assert gaps == [] and matrix_gaps == []
    stale = "regenerate with: python tools/traceability.py"
    assert (ROOT / "docs" / "TRACEABILITY.md").read_text(encoding="utf-8") == text, stale
    assert (ROOT / "docs" / "CONTROL_MATRIX.md").read_text(encoding="utf-8") == matrix, stale


def test_broken_links_are_reported() -> None:
    tool = _tool()
    assert tool.resolve("fscore/src/fscore/api.py::check_workbook", test=False) is None
    assert tool.resolve("fscore/src/fscore/api.py::no_such_function", test=False) is not None
    assert tool.resolve("fsweb/tests/test_runs.py::test_rule_versions_in_use_are_recorded", test=True) is None
    assert tool.resolve("fsweb/tests/test_runs.py::check_workbook", test=True) is not None
    assert tool.resolve("fsui/e2e/smoke.spec.ts::staff proposes a fix plan and cannot approve it", test=True) is None
    assert tool.resolve("docs/no-such-file.md", test=False) is not None
