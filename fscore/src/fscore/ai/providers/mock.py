"""Deterministic provider for tests, demos and the evaluation harness.

It reads the approved payload and answers with schema-valid JSON built from
simple, documented heuristics — never from a network. ``mode`` simulates the
outcomes the gateway must handle: ``refusal``, ``max_tokens``, ``invalid_json``,
``wrong_number`` (cites a figure that does not match) and ``error``.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

from fscore.ai.provider import ProviderRequest, RawCompletion
from fscore.ai.settings import ProviderSettings
from fscore.engine.display import parse_printed_number

PRESENTATION_RULES = {"TXT-03", "TXT-04", "TXT-05", "TXT-07"}
_KEYWORDS = [
    ("net_worth", r"net worth|total equity|shareholders'? funds"),
    ("working_capital", r"working capital|current liabilities exceed"),
    ("cash", r"cash and cash equivalents|cash balance|bank balances?"),
    ("revenue", r"revenue|turnover|sales"),
    ("pbt", r"profit before tax|loss before tax"),
    ("pat", r"profit|loss"),
]
_NUM_RE = re.compile(r"\(?-?\d[\d,]*\.\d+\)?|\(?-?\d{1,3}(?:,\d{2,3})+\)?")


def _payload(user: str) -> dict[str, Any]:
    m = re.search(r"<payload>\s*(\{.*\})\s*</payload>", user, re.S)
    return json.loads(m.group(1)) if m else {}


class MockProvider:
    name = "mock"

    def __init__(self, *, mode: str = "normal") -> None:
        self.mode = mode
        self.calls: list[ProviderRequest] = []

    def send(self, request: ProviderRequest, settings: ProviderSettings) -> RawCompletion:
        self.calls.append(request)
        usage = {"input_tokens": max(1, len(request.system + request.user) // 4), "output_tokens": 0}
        if self.mode == "error":
            return RawCompletion(error="APIConnectionError")
        if self.mode == "refusal":
            return RawCompletion(stop_reason="refusal", stop_category="cyber", served_model=settings.model, usage=usage)
        if self.mode == "max_tokens":
            return RawCompletion(stop_reason="max_tokens", served_model=settings.model, usage=usage)
        if self.mode == "invalid_json":
            return RawCompletion(text='{"items": [{"finding_id": 1', stop_reason="end_turn", served_model=settings.model, usage=usage)
        payload = _payload(request.user)
        purpose = payload.get("purpose", "triage")
        out = {
            "triage": self._triage,
            "explain": self._explain,
            "xref": self._xref,
            "narrative": self._narrative,
            "full_statements": self._narrative,
        }[purpose](payload)
        text = json.dumps(out)
        usage["output_tokens"] = max(1, len(text) // 4)
        return RawCompletion(text=text, stop_reason="end_turn", served_model=settings.model, request_id="mock-req", usage=usage)

    # ------------------------------------------------------------------ heuristics
    def _cite(self, f: dict[str, Any]) -> list[dict[str, str]]:
        if not (f.get("sheet") and f.get("cells") and f.get("printed")):
            return []
        value = f["printed"]
        if self.mode == "wrong_number":
            d = parse_printed_number(value)
            value = str((d or Decimal(0)) + Decimal(1))
        return [{"ref": f"{f['sheet']}!{f['cells'][0]}", "value": value}]

    def _triage(self, p: dict[str, Any]) -> dict[str, Any]:
        items = []
        for f in p.get("findings", []):
            if f["rule_id"] in PRESENTATION_RULES:
                cls, reason = "presentation_only", "Formatting or wording only; no printed figure changes."
            elif f["certainty"] == "Review" and f["severity"] != "High":
                cls, reason = "false_positive", "Low-certainty pattern; the figures may legitimately differ."
            else:
                cls, reason = "genuine", "Printed figures disagree and need correction before signing."
            items.append({
                "finding_id": f["id"],
                "classification": cls,
                "reason": reason,
                "suggested_wording": None if cls == "false_positive" else f.get("suggested_fix"),
                "cited_numbers": self._cite(f),
            })
        return {"items": items}

    def _explain(self, p: dict[str, Any]) -> dict[str, Any]:
        return {"items": [
            {"finding_id": f["id"], "explanation": f.get("explanation") or f["rule_id"], "likely_cause": None, "cited_numbers": self._cite(f)}
            for f in p.get("findings", [])
        ]}

    def _xref(self, p: dict[str, Any]) -> dict[str, Any]:
        by_value: dict[str, list[dict[str, Any]]] = {}
        for fig in p.get("figures", []):
            by_value.setdefault(fig.get("printed", ""), []).append(fig)
        out = []
        for value, figs in by_value.items():
            if value and len(figs) >= 2:
                a, b = figs[0], figs[1]
                out.append({"a_ref": a["ref"], "b_ref": b["ref"], "reason": "Same printed value under related captions.",
                            "confidence": "medium", "cited_numbers": [{"ref": a["ref"], "value": value}, {"ref": b["ref"], "value": value}]})
        return {"suggestions": out}

    def _narrative(self, p: dict[str, Any]) -> dict[str, Any]:
        metrics = {(m["key"], m["period"]): m["value"] for m in p.get("metrics", [])}
        sentences = []
        for para in p.get("paragraphs", []):
            text = para["text"]
            low = text.lower()
            metric = next((k for k, rx in _KEYWORDS if re.search(rx, low)), None)
            period = "PY" if re.search(r"previous year|preceding year|prior year", low) else "CY"
            m = _NUM_RE.search(text)
            quoted = m.group(0) if m else None
            actual = metrics.get((metric, period)) if metric else None
            if re.search(r"\bjune\b|\bseptember\b|\bdecember\b", low) and "march" not in low:
                consistent, reason = "no", "Quotes a period outside the reporting dates in scope."
            elif metric is None or quoted is None or actual is None:
                consistent, reason = "unclear", "The sentence could not be mapped to a computed metric."
            else:
                qd, ad = parse_printed_number(quoted), parse_printed_number(actual)
                same = qd is not None and ad is not None and abs(qd) == abs(ad)
                wrong_sign = ad is not None and ad < 0 and "profit" in low and metric in ("pat", "pbt")
                consistent = "yes" if same and not wrong_sign else "no"
                reason = "Agrees with the statements." if consistent == "yes" else "Figure or sign differs from the statements."
            cited = [{"ref": f"{metric}:{period}", "value": actual}] if metric and actual is not None else []
            sentences.append({
                "sentence_id": para["id"], "metric": metric, "quoted_value": quoted, "period": period,
                "consistent": consistent, "reason": reason,
                "suggested_rewording": (text.replace(quoted, actual.strip("()")) if consistent == "no" and quoted and actual else None),
                "cited_numbers": cited,
            })
        gc = None
        if any("going concern" in para["text"].lower() for para in p.get("paragraphs", [])):
            negatives = [f"{k}:{per}" for (k, per), v in metrics.items() if (parse_printed_number(v) or 0) < 0]
            gc = {"conclusion_supported": "unclear" if negatives else "yes",
                  "observations": [f"Negative {n}" for n in negatives],
                  "disclosure_gaps": [], "cited_numbers": [{"ref": n, "value": metrics[tuple(n.split(":"))]} for n in negatives]}  # type: ignore[index]
        return {"sentences": sentences, "going_concern": gc}
