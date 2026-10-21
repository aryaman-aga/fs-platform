"""Core data contracts of the engine (02 §5.1).

Everything that crosses the engine boundary (CLI, web platform, offline edition,
AI gateway) is one of these pydantic models. Amounts are ``Decimal`` and are
serialised to JSON as strings so no precision is lost on the way to the database.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class Severity(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    INFO = "Info"


class Certainty(str, Enum):
    CERTAIN = "Certain"
    LIKELY = "Likely"
    REVIEW = "Review"


class FixKind(str, Enum):
    """How a finding may be resolved (02 §7.1)."""

    AUTO = "auto"  # applied by `fix` under the default policy
    APPROVAL = "approval"  # applied only when that item is approved
    MANUAL = "manual"  # report only
    NONE = "none"  # informational


SEVERITY_ORDER = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2, Severity.INFO: 3}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class CellRef(_Model):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sheet: str
    addr: str  # "B12"

    def __str__(self) -> str:
        quoted = self.sheet if self.sheet.replace("_", "").isalnum() else "'" + self.sheet.replace("'", "''") + "'"
        return f"{quoted}!{self.addr}"


class Evidence(_Model):
    """One component cell (or fact) supporting a finding."""

    label: str
    ref: CellRef | None = None
    printed: str | None = None
    value: Decimal | None = None
    note: str | None = None

    @field_serializer("value")
    def _ser_value(self, v: Decimal | None) -> str | None:
        return None if v is None else str(v)


class Finding(_Model):
    id: str = ""  # F-0001 within a run; assigned by the runner
    fingerprint: str = ""  # stable across runs (rule + location + key)
    rule_id: str
    rule_version: str = "1"
    family: str = ""
    severity: Severity
    certainty: Certainty = Certainty.CERTAIN
    category: str = ""
    sheet: str | None = None
    cells: list[str] = Field(default_factory=list)
    caption: str | None = None
    printed: str | None = None
    expected: str | None = None
    difference: Decimal | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    explanation: str = ""
    suggested_fix: str | None = None
    fix_kind: FixKind = FixKind.MANUAL
    pages_in_pdf: list[int] = Field(default_factory=list)
    period: str | None = None
    key: str = ""  # disambiguator used for the fingerprint (e.g. column letter, party name)
    tags: list[str] = Field(default_factory=list)

    @field_serializer("difference")
    def _ser_diff(self, v: Decimal | None) -> str | None:
        return None if v is None else str(v)

    def compute_fingerprint(self) -> str:
        raw = "|".join([self.rule_id, self.sheet or "", ",".join(self.cells), self.key])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class SelfTestReport(_Model):
    """Evaluator self-test: re-evaluate print-sheet formulas vs Excel's cached values."""

    formulas: int = 0
    evaluated: int = 0
    matched: int = 0
    unsupported: int = 0
    missing_cache: int = 0
    mismatches: list[str] = Field(default_factory=list)  # "Sheet!A1: cached x, evaluated y"
    unsupported_functions: list[str] = Field(default_factory=list)

    @property
    def match_rate(self) -> float:
        return 1.0 if self.evaluated == 0 else self.matched / self.evaluated


class RunInfo(_Model):
    engine_version: str
    rule_set_version: str
    rule_set_hash: str
    file_name: str
    file_sha256: str
    config_hash: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_s: float = 0.0
    cells_read: int = 0
    sheets: int = 0
    print_sheets: list[str] = Field(default_factory=list)
    selftest: SelfTestReport = Field(default_factory=SelfTestReport)
    warnings: list[str] = Field(default_factory=list)


class KeyFigure(_Model):
    key: str  # e.g. "pat", "pbt", "total_assets", "net_worth", "cash"
    label: str
    period: str  # "CY", "PY", "Q4 CY", "FY CY", ...
    value: Decimal | None = None
    printed: str | None = None
    ref: CellRef | None = None

    @field_serializer("value")
    def _ser_value(self, v: Decimal | None) -> str | None:
        return None if v is None else str(v)


class XrefPair(_Model):
    a: CellRef
    b: CellRef
    caption_a: str = ""
    caption_b: str = ""
    printed_a: str | None = None
    printed_b: str | None = None
    status: Literal["ok", "rounding_mismatch", "mismatch", "proposed"] = "proposed"
    source: Literal["link", "caption", "note_ref", "value", "map", "mandatory"] = "caption"
    confirmed: bool = False


class PdfPageMap(_Model):
    page: int
    sheet: str | None
    score: float = 0.0


class PdfMiss(_Model):
    sheet: str
    cell: str
    printed: str
    caption: str | None = None
    pages: list[int] = Field(default_factory=list)


class PdfReconResult(_Model):
    pdf_sha256: str = ""
    pages: int = 0
    page_map: list[PdfPageMap] = Field(default_factory=list)
    values_checked: int = 0
    misses: list[PdfMiss] = Field(default_factory=list)
    stale: bool = False
    error_tokens: list[str] = Field(default_factory=list)  # "#REF! on page 3"
    text_mismatches: list[str] = Field(default_factory=list)
    reprint_pages: list[int] = Field(default_factory=list)
    blank_pages: list[int] = Field(default_factory=list)


class CheckResult(_Model):
    run: RunInfo
    findings: list[Finding] = Field(default_factory=list)
    key_figures: list[KeyFigure] = Field(default_factory=list)
    statements: dict[str, str] = Field(default_factory=dict)  # role -> sheet name
    xref_map: list[XrefPair] = Field(default_factory=list)
    pdf: PdfReconResult | None = None
    narrative: list[dict[str, Any]] = Field(default_factory=list)  # narrative mentions with statuses
    rpt_recon: list[dict[str, Any]] = Field(default_factory=list)  # three-way RPT grid rows
    text_proposals: list[TextEdit] = Field(default_factory=list)  # typed-text corrections (GC-06 etc.), applied only on approval
    sheet_states: dict[str, dict[str, Any]] = Field(default_factory=dict)  # for ENG-02 on the next run

    def counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in Severity}
        for f in self.findings:
            out[f.severity.value] += 1
        return out

    def by_rule(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.rule_id] = out.get(f.rule_id, 0) + 1
        return dict(sorted(out.items()))

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.HIGH)


# --------------------------------------------------------------------------- fixes


class EditKind(str, Enum):
    FORMULA = "formula"  # set/replace a formula
    VALUE = "value"  # set a constant number
    TEXT = "text"  # typed text (narrative, captions, headings)
    HIDE_ROW = "hide_row"
    HIDE_COL = "hide_col"


class EditGroup(str, Enum):
    TOTALS_REWRITE = "totals_rewrite"
    PLUG_REMOVAL = "plug_removal"
    MISSING_TOTAL = "missing_total"
    BALANCING = "balancing"
    PY_LINE_ADJUST = "py_line_adjust"
    LINK = "link"
    TYPED_TEXT = "typed_text"
    HIDE = "hide"
    OTHER = "other"


class Edit(_Model):
    id: str = ""  # E-0001 within a plan
    sheet: str
    cell: str  # "B12"; for HIDE_ROW the row number as text ("12"); for HIDE_COL the letters ("F")
    kind: EditKind
    group: EditGroup
    old_formula: str | None = None
    old_value: str | None = None
    new_formula: str | None = None
    new_value: str | None = None
    reason: str = ""
    finding_ids: list[str] = Field(default_factory=list)
    policy: str = ""
    fix_kind: FixKind = FixKind.AUTO
    caption: str | None = None
    approved: bool = False


class PrintedMove(_Model):
    sheet: str
    cell: str
    caption: str | None = None
    before: str | None = None
    after: str | None = None
    period: str | None = None
    is_py: bool = False
    is_key: bool = False


class BalancingAdjustment(_Model):
    statement: Literal["balance_sheet", "cash_flow"]
    column: str  # sheet column letter of the statement checked
    period: str | None = None
    gap: Decimal
    target: CellRef | None = None
    adjustment: Decimal = Decimal("0")
    status: Literal["applied", "too_large", "investigate", "no_target", "not_needed"] = "applied"
    note: str = ""

    @field_serializer("gap", "adjustment")
    def _ser(self, v: Decimal) -> str:
        return str(v)


class FixPlan(_Model):
    id: str = ""
    source_file: str = ""
    source_sha256: str = ""
    config_hash: str = ""
    policy: str = "totals_only"
    edits: list[Edit] = Field(default_factory=list)
    balancing: list[BalancingAdjustment] = Field(default_factory=list)
    printed_moves: list[PrintedMove] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    converged: bool = True
    passes: int = 0
    created_at: datetime | None = None

    def canonical_json(self) -> str:
        body = {
            "source_sha256": self.source_sha256,
            "policy": self.policy,
            "edits": [e.model_dump(mode="json", exclude={"approved"}) for e in self.edits],
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":"))

    @property
    def plan_sha256(self) -> str:
        """Hash an approver signs off (control C2): any change to the edits changes it."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class VerificationStep(_Model):
    name: str
    ok: bool
    detail: str = ""


class VerificationReport(_Model):
    ok: bool = False
    steps: list[VerificationStep] = Field(default_factory=list)
    unexpected_changes: list[str] = Field(default_factory=list)
    xml_errors: list[str] = Field(default_factory=list)
    remaining_cast_plug: int = 0
    selftest_match_rate: float = 0.0


class ApplyResult(_Model):
    ok: bool
    out_path: str | None = None
    sha256: str | None = None
    backup_path: str | None = None
    applied_edit_ids: list[str] = Field(default_factory=list)
    skipped_edit_ids: list[str] = Field(default_factory=list)
    verification: VerificationReport = Field(default_factory=VerificationReport)
    printed_moves: list[PrintedMove] = Field(default_factory=list)
    message: str = ""


class TextEdit(_Model):
    """A typed-text narrative edit (control C6: never a formula)."""

    sheet: str
    cell: str
    new_text: str
    reason: str = ""


class RenderResult(_Model):
    ok: bool
    pdf_path: str | None = None
    sha256: str | None = None
    engine: str = ""
    engine_version: str = ""
    mode: Literal["draft", "final"] = "draft"
    pages: int = 0
    bookmarks: list[str] = Field(default_factory=list)
    verification: PdfReconResult | None = None
    message: str = ""


class CellChange(_Model):
    sheet: str
    cell: str
    kind: Literal["formula", "value", "style", "hidden", "added", "removed"]
    before: str | None = None
    after: str | None = None


class WorkbookDiff(_Model):
    a_sha256: str
    b_sha256: str
    changes: list[CellChange] = Field(default_factory=list)
    printed_moves: list[PrintedMove] = Field(default_factory=list)
    hidden_changes: list[str] = Field(default_factory=list)
    sheets_added: list[str] = Field(default_factory=list)
    sheets_removed: list[str] = Field(default_factory=list)
