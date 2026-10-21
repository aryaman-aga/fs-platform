"""Golden real-file fixtures (build pack 10 §2–3): run only on the firm's machine.

The firm copies the real workbooks into ``fixtures/private/`` and writes
``fixtures/private/golden.yaml`` — the acceptance oracle — in the format of
``fixtures/golden.example.yaml``. Both are git-ignored, so in CI (and in this
public repository) every case is skipped.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from fscore import api
from fscore.config import EngagementConfig

PRIVATE = Path(__file__).resolve().parents[2] / "fixtures" / "private"
ORACLE = PRIVATE / "golden.yaml"


def _cases() -> list[dict[str, Any]]:
    if not ORACLE.exists():
        return []
    data = yaml.safe_load(ORACLE.read_text(encoding="utf-8")) or {}
    return list(data.get("cases", []))


CASES = _cases()


@pytest.mark.private
@pytest.mark.skipif(not CASES, reason="no private golden fixtures on this machine (fixtures/private/golden.yaml)")
@pytest.mark.parametrize("case", CASES, ids=[c.get("id", "?") for c in CASES] or None)
def test_golden_case(case: dict[str, Any]) -> None:
    frozen = PRIVATE / "FROZEN.json"
    wb = PRIVATE / case["file"]
    if not wb.exists():
        pytest.skip(f"{case['file']} not present")
    if frozen.exists():
        fp = json.loads(frozen.read_text(encoding="utf-8")).get(case["file"])
        if fp and case.get("sha256_16") and fp != case["sha256_16"]:
            pytest.fail(f"{case['file']} changed since the oracle was written (re-confirm before freezing)")
    cfg = EngagementConfig.model_validate(case.get("config", {}))
    result = api.check_workbook(wb, cfg)
    for exp in case.get("expect", []):
        hits = [f for f in result.findings if f.rule_id == exp["rule"] and (not exp.get("sheet") or f.sheet == exp["sheet"])
                and (not exp.get("cells") or set(exp["cells"]) <= set(f.cells))]
        if exp.get("absent"):
            assert not hits, f"{case['id']}: {exp['rule']} should not be reported"
        else:
            assert hits, f"{case['id']}: expected {exp['rule']} {exp.get('sheet', '')} {exp.get('cells', '')}"
            if "difference" in exp:
                assert any(f.difference == Decimal(str(exp["difference"])) for f in hits)
    for key, want in (case.get("key_figures") or {}).items():
        name, period = key.split("@")
        got = next((k.value for k in result.key_figures if k.key == name and k.period == period), None)
        assert got == Decimal(str(want)), f"{case['id']}: {key} expected {want}, got {got}"
