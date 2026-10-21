"""Labelled evaluation set (E-AI-02), generated deterministically from synthetic data.

No client data: every caption, figure and sentence is invented. 76 triage
items (genuine / presentation-only / false positive, including deliberately
hard cases) and 40 narrative sentences (consistent or not with a metrics
table) — 116 labelled items in total.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from fscore.models import Certainty, Evidence, Finding, Severity

DATASET_VERSION = "ai-eval/1.0"

TriageLabel = Literal["genuine", "presentation_only", "false_positive"]


class TriageCase(BaseModel):
    id: str
    finding: Finding
    label: TriageLabel


class NarrativeCase(BaseModel):
    id: str
    sentence: str
    metrics: dict[str, str]  # "pat:CY" -> printed value
    label: Literal["yes", "no"]  # is the sentence consistent with the metrics?


def _f(i: int, rule: str, sev: Severity, cert: Certainty, caption: str, printed: str | None, expected: str | None,
       diff: str | None, expl: str, sheet: str = "Note 14-19", cell: str | None = None) -> Finding:
    cell = cell or f"D{20 + i % 40}"
    return Finding(
        id=f"EV-{i:03d}", rule_id=rule, severity=sev, certainty=cert, sheet=sheet, cells=[cell], caption=caption,
        printed=printed, expected=expected, difference=None if diff is None else Decimal(diff), explanation=expl,
        evidence=[Evidence(label=caption, printed=printed)] if printed else [],
    )


def triage_cases() -> list[TriageCase]:
    H, M, L = Severity.HIGH, Severity.MEDIUM, Severity.LOW
    C, LK, R = Certainty.CERTAIN, Certainty.LIKELY, Certainty.REVIEW
    out: list[TriageCase] = []
    i = 0

    def add(label: TriageLabel, *args: object, **kw: object) -> None:
        nonlocal i
        i += 1
        out.append(TriageCase(id=f"T{i:03d}", finding=_f(i, *args, **kw), label=label))  # type: ignore[arg-type]

    captions = ["Total current liabilities", "Total other expenses", "Total trade payables", "Total non-current assets",
                "Net block", "Total employee benefits expense", "Total revenue from operations", "Total borrowings",
                "Total provisions", "Total other financial assets"]
    for k, cap in enumerate(captions):  # 10 genuine casting differences
        base = Decimal(500 + 37 * k) + Decimal(k) / 100
        add("genuine", "CAST-01", H, C, cap, f"{base:,.2f}", f"{base + Decimal('0.01'):,.2f}", "-0.01",
            f"Printed total {base:,.2f} does not equal the sum of the printed lines {base + Decimal('0.01'):,.2f}.")
    for k in range(4):
        add("genuine", "CAST-02", H, C, f"Ageing row {k + 1} total", f"{300 + k:,.2f}", f"{300 + k:,.2f}1"[:-1], "0.01",
            "Row total computed downward does not cross-cast across the printed columns.")
    for k in range(6):
        add("genuine", "PLUG-01", H, C, captions[k], f"{812 + k * 3:,.2f}", None, None,
            f"Total formula contains a typed constant of -0.0{k + 1} that hides a rounding difference.")
    for k in range(3):
        add("genuine", "BAL-01", H, C, "Total equity and liabilities", f"{9_876 + k:,.2f}", f"{9_876 + k:,.2f}", "0.02",
            "Balance Sheet does not tally on print by 0.02.", sheet="BS")
    for k in range(4):
        add("genuine", "XREF-01", H, C, "Trade receivables", f"{1_204 + k:,.2f}", f"{1_204 + k:,.2f}", "0.01",
            "Balance Sheet line differs from the note total it links to.", sheet="BS")
    for _k in range(4):
        add("genuine", "TXT-01", H, C, "Signature block", None, None, None,
            "Director B's block shows the DIN of director A; two different people share one DIN.", sheet="PL")
    for k in range(4):
        add("genuine", "GC-01", H, LK, "Going concern", f"{64 + k}.27", f"{64 + k}.72", "0.45",
            "The note quotes a loss that differs from the statement of profit and loss.", sheet="Note 29-34")
    for k in range(3):
        add("genuine", "RPT-09", H, LK, "Payable to holding company", None, f"{33 + k}.19", None,
            "The payable appears in the trade-payables note and the TB but not in the related-party note.", sheet="RPT")
    for k in range(2):
        add("genuine", "HID-01", H, C, "Total lease liabilities", f"{210 + k:,.2f}", f"{208 + k:,.2f}", "2.00",
            "The printed total includes a hidden row carrying 2.00.")
    for k in range(4):  # hard: genuine although the engine was unsure
        add("genuine", "PLUG-02", M, R, "Net cash from operating activities", f"{(450 + k):,.2f}", None, None,
            f"A typed adjustment of -0.{80 + k} sits inside a link to the cash-flow working.", sheet="CFS")
    for k in range(6):
        add("presentation_only", "TXT-07", L, C, captions[k], f"{120 + k:,.2f}", None, None,
            "Zero shown as 0.00 instead of a dash in an accounting-format column.")
    for k in range(4):
        add("presentation_only", "TXT-04", L, R, "Proceeds from borrowings", f"({30 + k:,.2f})", None, None,
            "Caption 'Proceeds' carries an outflow.", sheet="CFS")
    for _k in range(3):
        add("presentation_only", "TXT-05", L, LK, "Units line", None, None, None,
            "The unit line is missing on this note page.")
    for k in range(3):
        add("presentation_only", "TXT-03", M, LK, "Note numbering", None, None, None,
            f"Sub-item ({['iv', 'v', 'vi'][k]}) is numbered twice.")
    for k in range(6):
        add("false_positive", "XREF-02", M, R, "Total", f"{75 + k:,.2f}", f"{75 + k:,.2f}", "0.03",
            "Two figures captioned 'Total' in different notes differ by 0.03.")
    for k in range(4):  # hard: certain-looking but explained elsewhere
        add("false_positive", "RATIO-03", M, C, "Debt-equity ratio", f"{1 + k / 10:.2f}", None, None,
            "Variance above 25 % has no explanation in the reason column (explained in the note below the table).", sheet="Note 29-34")
    for k in range(4):
        add("false_positive", "SUB-05", M, R, "Revenue by geography", f"{2_000 + k:,.2f}", f"{2_000 + k:,.2f}", None,
            "Disaggregation total may be on a different basis than the revenue note.", sheet="Note 20-27")
    for k in range(2):
        add("false_positive", "TXT-06", M, R, "Contingent liabilities narrative", f"{12 + k}.50", None, None,
            "A number in the narrative was not found in the table of the same note.")
    return out


_METRIC_SETS: list[dict[str, str]] = [
    {"pat:CY": "(64.27)", "pat:PY": "(51.90)", "pbt:CY": "(85.90)", "net_worth:CY": "318.44", "net_worth:PY": "302.15",
     "working_capital:CY": "(22.81)", "working_capital:PY": "17.36", "cash:CY": "45.62", "cash:PY": "39.08",
     "revenue:CY": "2,145.73", "revenue:PY": "1,876.20"},
    {"pat:CY": "112.08", "pat:PY": "(18.45)", "pbt:CY": "149.77", "net_worth:CY": "1,042.66", "net_worth:PY": "930.58",
     "working_capital:CY": "286.10", "working_capital:PY": "241.94", "cash:CY": "171.35", "cash:PY": "120.07",
     "revenue:CY": "5,310.02", "revenue:PY": "4,622.81"},
]

_TEMPLATES: list[tuple[str, str, Literal["yes", "no"]]] = [
    # (template, how to fill, label)
    ("The Company has incurred a loss of ₹ {pat_cy_abs} lakhs during the year ended March 31, 2026.", "loss_cy", "yes"),
    ("The net worth of the Company as at March 31, 2026 is ₹ {nw_cy} lakhs.", "", "yes"),
    ("Cash and cash equivalents stood at ₹ {cash_cy} lakhs as at March 31, 2026.", "", "yes"),
    ("Revenue from operations for the year ended March 31, 2026 was ₹ {rev_cy} lakhs.", "", "yes"),
    ("In the previous year the Company reported a loss of ₹ {pat_py_abs} lakhs.", "loss_py", "yes"),
    ("The net worth of the Company as at March 31, 2026 is ₹ {nw_py} lakhs.", "", "no"),  # stale PY figure
    ("The Company has incurred a loss of ₹ {pat_cy_typo} lakhs during the year ended March 31, 2026.", "loss_cy", "no"),
    ("The loss for the quarter ended June 30, 2026 was ₹ {pat_cy_abs} lakhs.", "", "no"),  # out-of-scope period
    ("Cash and cash equivalents stood at ₹ {cash_cy_1dp} lakhs as at March 31, 2026.", "", "no"),  # one-decimal precision
    ("Revenue from operations for the year ended March 31, 2026 was ₹ {rev_cy_x100} lakhs.", "", "no"),  # unit slip
    ("The holding company has infused equity of ₹ 50.00 lakhs during the year to support operations.", "", "yes"),
    ("The Company has earned profits in each of the last three years.", "", "no"),
    ("The Company has earned a profit of ₹ {pat_cy_abs} lakhs during the year ended March 31, 2026.", "profit_cy", "no"),
    ("The Company's turnover for the year was ₹ {rev_cy} lakhs, an increase over the previous year.", "", "yes"),
    ("Net worth as at March 31, 2025 was ₹ {nw_py} lakhs in the previous year.", "", "yes"),
    ("The financial statements have been prepared on a going concern basis.", "", "yes"),
    ("Bank balances of ₹ {cash_cy} lakhs are available to meet obligations.", "", "yes"),
    ("Working capital as at March 31, 2026 is ₹ {wc_py} lakhs.", "", "no"),
    ("{pbt_word} before tax for the year was ₹ {pbt_cy_abs} lakhs.", "", "yes"),
    ("The Company has incurred a loss of ₹ {pat_cy_abs} lakhs in the previous year.", "", "no"),
]


def _abs(v: str) -> str:
    return v.strip("()")


def narrative_cases() -> list[NarrativeCase]:
    out: list[NarrativeCase] = []
    n = 0
    for ms in _METRIC_SETS:
        pat_cy, pat_py = ms["pat:CY"], ms["pat:PY"]
        fill = {
            "pat_cy_abs": _abs(pat_cy),
            "pat_py_abs": _abs(pat_py),
            "pat_cy_typo": _abs(pat_cy)[:-2] + _abs(pat_cy)[-1] + _abs(pat_cy)[-2],
            "nw_cy": ms["net_worth:CY"],
            "nw_py": ms["net_worth:PY"],
            "cash_cy": ms["cash:CY"],
            "cash_cy_1dp": f"{Decimal(ms['cash:CY'].replace(',', '')):.1f}",
            "rev_cy": ms["revenue:CY"],
            "rev_cy_x100": f"{Decimal(ms['revenue:CY'].replace(',', '')) * 100:,.2f}",
            "wc_py": _abs(ms["working_capital:PY"]),
            "pbt_cy_abs": _abs(ms["pbt:CY"]),
            "pbt_word": "Loss" if ms["pbt:CY"].startswith("(") else "Profit",
        }
        cy_loss = pat_cy.startswith("(")
        py_loss = pat_py.startswith("(")
        for template, kind, label in _TEMPLATES:
            text = template.format(**fill)
            lab: Literal["yes", "no"] = label
            # the sign wording must follow the metric set
            if kind == "loss_cy" and not cy_loss and label == "yes":
                text = text.replace("incurred a loss", "earned a profit")
            if kind == "loss_py" and not py_loss:
                text = text.replace("reported a loss", "reported a profit")
            if kind == "profit_cy":
                lab = "yes" if not cy_loss else "no"
            if template.startswith("The Company has earned profits in each"):
                lab = "no" if (cy_loss or py_loss) else "yes"
            if template.startswith("The Company has incurred a loss of ₹ {pat_cy_abs} lakhs in the previous year"):
                lab = "yes" if (py_loss and _abs(pat_py) == _abs(pat_cy)) else "no"
            n += 1
            out.append(NarrativeCase(id=f"N{n:02d}", sentence=text, metrics=dict(ms), label=lab))
    return out


def dataset_sha256() -> str:
    body = {
        "version": DATASET_VERSION,
        "triage": [c.model_dump(mode="json") for c in triage_cases()],
        "narrative": [c.model_dump(mode="json") for c in narrative_cases()],
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
