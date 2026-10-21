"""Structured outputs the AI reviewer must return (02 §9.2).

Every field is required (optional values are nullable), so the schemas map
1:1 onto structured-output JSON schemas via ``anthropic.transform_schema``.
Outputs are *proposals*: nothing here is ever applied to a workbook
(E-AI-05). Every number the model cites goes into ``cited_numbers`` with the
cell or note it came from, and the engine re-verifies it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CitedNumber(_Out):
    """A figure the model relies on, with where it says the figure comes from."""

    ref: str  # "BS!C12", "Note 12", or a metric key from the payload ("pat:CY")
    value: str  # as written by the model, e.g. "(1,482.17)" or "12.35"


# ------------------------------------------------------------------ triage


class TriageItem(_Out):
    finding_id: str
    classification: Literal["genuine", "presentation_only", "false_positive"]
    reason: str  # one line
    suggested_wording: str | None  # wording for the fix or the review note
    cited_numbers: list[CitedNumber]


class TriageResponse(_Out):
    items: list[TriageItem]


# ------------------------------------------------------------------ narrative


class NarrativeSentenceReview(_Out):
    sentence_id: str
    metric: str | None  # metric key from the payload's metrics table, if the sentence quotes one
    quoted_value: str | None
    period: str | None  # "CY", "PY", "Q4_CY", …
    consistent: Literal["yes", "no", "unclear"]
    reason: str
    suggested_rewording: str | None  # typed text, same unit and 2 decimals as the statements
    cited_numbers: list[CitedNumber]


class GoingConcernReview(_Out):
    conclusion_supported: Literal["yes", "no", "unclear"]
    observations: list[str]  # e.g. negative working capital, overdue borrowings, equity infusion
    disclosure_gaps: list[str]  # against Ind AS 1 paras 25–26 and SA 570
    cited_numbers: list[CitedNumber]


class NarrativeResponse(_Out):
    sentences: list[NarrativeSentenceReview]
    going_concern: GoingConcernReview | None


# ------------------------------------------------------------------ cross-reference


class XrefSuggestion(_Out):
    a_ref: str
    b_ref: str
    reason: str
    confidence: Literal["high", "medium", "low"]
    cited_numbers: list[CitedNumber]


class XrefResponse(_Out):
    suggestions: list[XrefSuggestion]


# ------------------------------------------------------------------ explanation


class Explanation(_Out):
    finding_id: str
    explanation: str  # plain language for the reviewer
    likely_cause: str | None
    cited_numbers: list[CitedNumber]


class ExplainResponse(_Out):
    items: list[Explanation]


OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "triage": TriageResponse,
    "narrative": NarrativeResponse,
    "full_statements": NarrativeResponse,
    "xref": XrefResponse,
    "explain": ExplainResponse,
}


def output_model_for(purpose: str) -> type[BaseModel]:
    try:
        return OUTPUT_MODELS[purpose]
    except KeyError as exc:
        raise ValueError(f"unknown AI purpose {purpose!r}") from exc
