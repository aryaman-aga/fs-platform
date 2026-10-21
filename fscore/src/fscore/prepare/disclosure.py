"""Disclosure checklist engine (E-PREP-02).

The checklist is data (``prepare/data/disclosure_checklist.yaml``). Items the
engine can detect from the printed workbook are answered automatically, with
evidence; the rest are answered by the preparer. Mandatory items that are
unanswered or answered "no" block the FINAL_LOCKED transition.
"""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from fscore.io.xlsx_reader import Workbook

Answer = Literal["yes", "no", "na"]


class ChecklistItem(BaseModel):
    code: str
    area: str
    question: str
    mandatory: bool = True
    answer: Answer | None = None
    auto: bool = False
    evidence: str | None = None
    answered_by: str | None = None


class ChecklistReport(BaseModel):
    framework: str
    version: str
    items: list[ChecklistItem] = Field(default_factory=list)

    @property
    def answered(self) -> int:
        return sum(1 for i in self.items if i.answer is not None)

    @property
    def completion(self) -> float:
        return 0.0 if not self.items else round(100.0 * self.answered / len(self.items), 1)

    @property
    def blocking(self) -> list[ChecklistItem]:
        return [i for i in self.items if i.mandatory and i.answer in (None, "no")]

    @property
    def complete(self) -> bool:
        return not self.blocking


@lru_cache(maxsize=1)
def _checklist() -> dict[str, Any]:
    text = resources.files("fscore.prepare").joinpath("data/disclosure_checklist.yaml").read_text(encoding="utf-8")
    return dict(yaml.safe_load(text))


def _printed_text(wb: Workbook) -> list[tuple[str, str, str]]:
    grouped = [s for s in wb.sheets if s.visible and s.tab_selected] or [s for s in wb.sheets if s.visible]
    out: list[tuple[str, str, str]] = []
    for s in grouped:
        for c in s.iter_cells():
            if isinstance(c.value, str) and c.value.strip() and s.is_printed(c.row, c.col):
                out.append((s.name, c.addr, c.value))
    return out


def _ratio_reasons(wb: Workbook) -> tuple[bool, str]:
    for s in wb.sheets:
        for c in s.iter_cells():
            if not (isinstance(c.value, str) and c.value.lower().startswith("reason for variance")):
                continue
            header_row, reason_col = c.row, c.col
            var_col = reason_col - 1
            missing = []
            for r in range(header_row + 1, s.max_row + 1):
                v = s.cell(r, var_col)
                if v is None or not v.is_number:
                    if r > header_row + 1 and (v is None or v.value is None):
                        break
                    continue
                reason = s.cell(r, reason_col)
                text = str(reason.value).strip() if reason is not None and reason.value is not None else ""
                if abs(float(v.value)) > 0.25 and text in ("", "-"):
                    missing.append(f"{s.name}!{v.addr}")
            if missing:
                return False, "unexplained variance at " + ", ".join(missing)
            return True, f"{s.name}: every variance above 25 % carries a reason"
    return False, "no ratio table with a 'Reason for variance' column found"


def run_checklist(workbook: str | Path | Workbook, answers: dict[str, Answer] | None = None, *, answered_by: str | None = None) -> ChecklistReport:
    data = _checklist()
    wb = workbook if isinstance(workbook, Workbook) else Workbook.open(workbook)
    try:
        texts = _printed_text(wb)
        sheets = {s.name for s in wb.sheets if s.visible}
        report = ChecklistReport(framework=str(data["framework"]), version=str(data["version"]))
        for raw in data["items"]:
            item = ChecklistItem(code=raw["code"], area=raw["area"], question=raw["question"], mandatory=bool(raw.get("mandatory", True)))
            det = raw.get("detect", "manual")
            if isinstance(det, dict) and "text" in det:
                rx = re.compile(det["text"], re.I)
                hit = next((t for t in texts if rx.search(t[2])), None)
                item.answer, item.auto = ("yes" if hit else "no"), True
                item.evidence = f"{hit[0]}!{hit[1]}" if hit else f"no printed text matches /{det['text']}/"
            elif isinstance(det, dict) and "sheet" in det:
                ok = det["sheet"] in sheets
                item.answer, item.auto, item.evidence = ("yes" if ok else "no"), True, det["sheet"]
            elif isinstance(det, dict) and det.get("ratio_reasons"):
                ok, why = _ratio_reasons(wb)
                item.answer, item.auto, item.evidence = ("yes" if ok else "no"), True, why
            if answers and item.code in answers:
                item.answer, item.auto, item.answered_by = answers[item.code], False, answered_by
            report.items.append(item)
        return report
    finally:
        if not isinstance(workbook, Workbook):
            wb.close()
