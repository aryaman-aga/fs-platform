"""API request/response contracts (OpenAPI, consumed by the React UI)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

EmailStr = Annotated[str, StringConstraints(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320, to_lower=True, strip_whitespace=True)]


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ErrorDetail(BaseModel):
    code: str
    message: str
    correlation_id: str | None = None
    details: dict[str, Any] | None = None


class ErrorBody(BaseModel):
    error: ErrorDetail


class OkOut(Out):
    ok: bool = True
    message: str = ""


class Page(Out):
    total: int
    items: list[Any]


# ------------------------------------------------------------------ auth


class LoginIn(In):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class LoginOut(Out):
    status: Literal["mfa_required", "mfa_enrollment_required"]
    csrf_token: str
    expires_at: datetime


class MfaCodeIn(In):
    code: str = Field(min_length=6, max_length=20)


class MfaEnrollOut(Out):
    otpauth_uri: str
    secret: str
    qr_svg_data_uri: str


class RecoveryCodesOut(Out):
    recovery_codes: list[str]
    csrf_token: str
    message: str = "Store these recovery codes safely. They are shown only once."


class AuthOkOut(Out):
    status: Literal["ok"] = "ok"
    csrf_token: str
    expires_at: datetime


class SessionInfo(Out):
    id: str
    auth_method: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    idle_minutes: int
    stepup_valid_until: datetime | None = None
    ip: str | None = None
    user_agent: str | None = None
    current: bool = False


class UserOut(Out):
    id: str
    email: str
    name: str
    status: str
    is_firm_user: bool
    mfa_type: str
    mfa_enrolled_at: datetime | None = None
    last_login_at: datetime | None = None
    created_at: datetime


class RoleAssignmentOut(Out):
    id: str
    user_id: str
    client_id: str | None
    client_name: str | None = None
    role_code: str
    role_name: str
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime


class MeOut(Out):
    user: UserOut
    tenant_id: str
    tenant_name: str
    csrf_token: str | None = None
    session: SessionInfo
    assignments: list[RoleAssignmentOut]
    permissions: dict[str, dict[str, str]]  # client_id ("*" = tenant-wide) -> permission -> value
    ai_policy_version: int | None = None
    ai_policy_accepted: bool = False


class InviteAcceptIn(In):
    token: str
    password: str = Field(min_length=12, max_length=256)


class PasswordForgotIn(In):
    email: EmailStr


class PasswordResetIn(In):
    token: str
    password: str = Field(min_length=12, max_length=256)


class PasswordChangeIn(In):
    current_password: str
    new_password: str = Field(min_length=12, max_length=256)


# ------------------------------------------------------------------ admin


class InviteIn(In):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    is_firm_user: bool = True
    role_code: str | None = None
    client_id: str | None = None


class InviteOut(Out):
    user: UserOut
    invite_link: str | None = None  # returned only outside production (delivered by e-mail otherwise)
    expires_at: datetime


class UserPatchIn(In):
    name: str | None = Field(default=None, max_length=200)
    download_allowed: bool | None = None


class AssignmentIn(In):
    user_id: str
    role_code: str
    client_id: str | None = None


class RoleOut(Out):
    id: str
    code: str
    name: str
    scope: str
    is_system: bool
    is_client_role: bool
    download_allowed: bool
    description: str


class MatrixRowOut(Out):
    label: str
    permission: str
    values: dict[str, str]
    in_05_matrix: bool


class MatrixOut(Out):
    roles: list[RoleOut]
    rows: list[MatrixRowOut]


class RolePermissionsIn(In):
    values: dict[str, str]  # permission -> value (absent = denied)
    download_allowed: bool | None = None


class ElevationIn(In):
    role_code: str
    client_id: str | None = None
    reason: str = Field(min_length=5)
    duration_minutes: int = Field(default=60, ge=5, le=480)


class ElevationOut(Out):
    id: str
    user_id: str
    role_id: str
    client_id: str | None
    reason: str
    duration_minutes: int
    status: str
    approved_by: str | None
    expires_at: datetime | None
    created_at: datetime


# ------------------------------------------------------------------ master data


class ClientIn(In):
    name: str = Field(min_length=1, max_length=200)
    pan: str | None = Field(default=None, pattern=r"^[A-Z]{5}\d{4}[A-Z]$")
    cin: str | None = Field(default=None, pattern=r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
    group_name: str | None = None
    office: str | None = None
    classification: Literal["confidential", "restricted", "internal"] = "confidential"


class ClientPatchIn(In):
    name: str | None = None
    group_name: str | None = None
    office: str | None = None
    status: Literal["active", "inactive"] | None = None
    classification: Literal["confidential", "restricted", "internal"] | None = None


class ClientOut(Out):
    id: str
    name: str
    pan_masked: str | None = None
    cin_masked: str | None = None
    group_name: str | None = None
    status: str
    office: str | None = None
    classification: str
    version: int
    created_at: datetime


class EntityIn(In):
    name: str
    functional_ccy: str = Field(default="INR", min_length=3, max_length=3)
    presentation_ccy: str = Field(default="INR", min_length=3, max_length=3)
    country: str = Field(default="IN", min_length=2, max_length=2)
    parent_entity_id: str | None = None


class EntityOut(Out):
    id: str
    client_id: str
    name: str
    functional_ccy: str
    presentation_ccy: str
    country: str
    parent_entity_id: str | None
    created_at: datetime


class SignatoryIn(In):
    name: str
    role: str
    din: str | None = Field(default=None, pattern=r"^\d{8}$")
    membership_no: str | None = Field(default=None, pattern=r"^\d{6}$")
    frn: str | None = Field(default=None, pattern=r"^\d{6}[A-Z]$")
    valid_from: date | None = None
    valid_to: date | None = None


class SignatoryOut(Out):
    id: str
    entity_id: str
    name: str
    role: str
    din_masked: str | None = None
    membership_no: str | None = None
    frn: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None


# ------------------------------------------------------------------ engagements


class EngagementIn(In):
    entity_id: str
    name: str = Field(min_length=1, max_length=200)
    type: Literal["annual", "interim", "spfs"] = "annual"
    period_end: date
    periods_in_scope: list[dict[str, Any]] = Field(default_factory=list)
    partner_id: str | None = None
    manager_id: str | None = None
    due_date: date | None = None


class EngagementPatchIn(In):
    name: str | None = None
    due_date: date | None = None
    partner_id: str | None = None
    manager_id: str | None = None
    ai_enabled: bool | None = None
    legal_hold: bool | None = None


class EngagementOut(Out):
    id: str
    client_id: str
    client_name: str | None = None
    entity_id: str
    entity_name: str | None = None
    name: str
    type: str
    period_end: date
    periods_in_scope_json: list[Any]
    status: str
    partner_id: str | None
    manager_id: str | None
    due_date: date | None
    locked_at: datetime | None
    ai_enabled: bool
    is_quarantine: bool
    merge_required: bool
    version_line: int
    legal_hold: bool
    version: int
    created_at: datetime
    open_high_findings: int | None = None
    open_review_notes: int | None = None


class TransitionIn(In):
    to: str
    reason: str = ""


class GateOut(Out):
    code: str
    label: str
    ok: bool
    detail: str = ""


class GatesOut(Out):
    final_pdf: list[GateOut]
    final_lock: list[GateOut]


class UnlockRequestIn(In):
    reason: str = Field(min_length=10)


class UnlockRequestOut(Out):
    id: str
    engagement_id: str
    reason: str
    requested_by: str
    partner_approved_by: str | None
    admin_approved_by: str | None
    status: str
    created_at: datetime


class ConfigIn(In):
    yaml_text: str


class ConfigOut(Out):
    engagement_id: str
    yaml_text: str
    version: int
    created_at: datetime | None = None


class ChecklistItemOut(Out):
    id: str
    item_code: str
    question: str
    answer: str | None
    auto: bool
    evidence_ref: str | None
    answered_by: str | None
    answered_at: datetime | None


class ChecklistAnswerIn(In):
    answer: Literal["yes", "no", "na"]
    evidence_ref: str | None = None


class MgmtItemIn(In):
    text: str = Field(min_length=3)


class MgmtItemOut(Out):
    id: str
    engagement_id: str
    text: str
    status: str
    confirmed_by: str | None
    confirmed_at: datetime | None
    comment: str | None


class MgmtConfirmIn(In):
    decision: Literal["confirmed", "disputed"] = "confirmed"
    comment: str | None = None


# ------------------------------------------------------------------ files


class FileOut(Out):
    id: str
    engagement_id: str
    kind: str
    title: str
    created_at: datetime
    latest_version: FileVersionOut | None = None
    checked_out_by: str | None = None


class FileVersionOut(Out):
    id: str
    file_id: str
    version_no: int
    version_line: int
    sha256: str
    size: int
    content_type: str
    original_name: str
    parent_version_id: str | None
    produced_by_run_id: str | None
    change_summary_json: dict[str, Any]
    worm_until: datetime | None
    has_macros: bool
    created_by: str | None
    created_at: datetime


class UploadOut(Out):
    file: FileOut
    version: FileVersionOut
    engagement_status: str
    auto_check_run_id: str | None = None


class CheckoutOut(Out):
    file_id: str
    version_id: str
    user_id: str
    expires_at: datetime


class ShareLinkIn(In):
    recipient_user_id: str | None = None
    hours: int = Field(default=24, ge=1, le=168)


class ShareLinkOut(Out):
    url: str
    expires_at: datetime


class DiffOut(Out):
    a_version_id: str
    b_version_id: str
    diff: dict[str, Any]


# ------------------------------------------------------------------ runs & findings


class RunIn(In):
    kind: Literal["check", "pdf_check", "render", "diff", "prepare"]
    input_version_ids: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=120)


class RunOut(Out):
    id: str
    engagement_id: str
    kind: str
    status: str
    input_version_ids: list[Any]
    input_hashes: list[Any]
    engine_version: str | None
    rule_set_version: str | None
    rule_set_hash: str | None
    config_hash: str | None
    params_json: dict[str, Any]
    result_json: dict[str, Any]
    output_version_id: str | None
    error_code: str | None
    error_message: str | None
    correlation_id: str | None
    created_by: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ArtefactOut(Out):
    id: str
    run_id: str | None
    kind: str
    name: str
    sha256: str
    size: int
    content_type: str
    created_at: datetime


class FindingOut(Out):
    id: str
    run_id: str
    fid: str
    fingerprint: str
    rule_id: str
    rule_version: str
    family: str
    severity: str
    certainty: str
    category: str
    sheet: str | None
    cells: list[Any]
    caption: str | None
    printed: str | None
    expected: str | None
    diff: str | None
    period: str | None
    evidence_json: list[Any]
    explanation: str
    suggested_fix: str | None
    fix_kind: str
    status: str
    status_reason: str | None
    assigned_to: str | None
    comment: str | None
    shared_with_client: bool
    version: int


class FindingPatchIn(In):
    status: Literal["Open", "Accepted", "Not an issue", "Resolved"] | None = None
    status_reason: str | None = None
    assigned_to: str | None = None
    comment: str | None = None
    shared_with_client: bool | None = None


class RunResultOut(Out):
    run_id: str
    key_figures: list[Any]
    statements: dict[str, Any]
    rpt_recon: list[Any]
    narrative: list[Any]
    xref_map: list[Any]
    text_proposals: list[Any] = Field(default_factory=list)  # typed-text corrections the engine proposes (GC-06 …)
    summary: dict[str, Any]


# ------------------------------------------------------------------ fix plans & narrative


class EditOut(Out):
    id: str
    eid: str
    sheet: str
    cell: str
    kind: str
    grp: str
    fix_kind: str
    old_value: str | None
    new_value: str | None
    old_formula: str | None
    new_formula: str | None
    reason: str
    approved: bool
    applied: bool


class FixPlanOut(Out):
    id: str
    engagement_id: str
    run_id: str
    base_version_id: str
    status: str
    plan_sha256: str | None
    proposed_by: str
    approved_by: str | None
    approved_at: datetime | None
    approved_plan_sha256: str | None
    approved_edit_ids: list[Any]
    has_balancing: bool
    balancing_approved_by: str | None
    apply_run_id: str | None
    plan_run_id: str | None
    rejection_reason: str | None
    created_at: datetime
    balancing: list[Any] = Field(default_factory=list)
    printed_moves: list[Any] = Field(default_factory=list)
    unresolved: list[Any] = Field(default_factory=list)
    edits: list[EditOut] = Field(default_factory=list)


class FixPlanApproveIn(In):
    plan_sha256: str
    approved_edit_ids: list[str] = Field(default_factory=list)


class BalancingApproveIn(In):
    plan_sha256: str


class RejectIn(In):
    reason: str = Field(min_length=3)


class NarrativeEditIn(In):
    sheet: str
    cell: str = Field(pattern=r"^[A-Z]{1,3}[1-9]\d{0,6}$")
    new_text: str = Field(min_length=1, max_length=32000)
    old_text: str | None = None
    reason: str = ""
    base_version_id: str | None = None


class NarrativeEditOut(Out):
    id: str
    engagement_id: str
    base_version_id: str
    sheet: str
    cell: str
    old_text: str | None
    new_text: str
    reason: str
    status: str
    proposed_by: str
    approved_by: str | None
    approved_at: datetime | None
    applied_run_id: str | None
    created_at: datetime


class ApplyNarrativeOut(Out):
    run_id: str
    edit_ids: list[str]


# ------------------------------------------------------------------ review notes


class ReviewNoteIn(In):
    target_type: Literal["finding", "cell", "pdf_page", "engagement", "client_query"] = "engagement"
    target_ref: str | None = None
    text: str = Field(min_length=1, max_length=20000)
    assigned_to: str | None = None


class ReviewNoteMsgIn(In):
    text: str = Field(min_length=1, max_length=20000)


class ReviewNoteMsgOut(Out):
    id: str
    user_id: str
    text: str
    created_at: datetime


class ReviewNoteOut(Out):
    id: str
    engagement_id: str
    target_type: str
    target_ref: str | None
    text: str
    status: str
    audience: str
    raised_by: str
    assigned_to: str | None
    cleared_by: str | None
    cleared_at: datetime | None
    version: int
    created_at: datetime
    messages: list[ReviewNoteMsgOut] = Field(default_factory=list)


# ------------------------------------------------------------------ AI


class AIPolicyOut(Out):
    version: int
    body: str
    accepted: bool
    accepted_at: datetime | None = None


class AIPolicyIn(In):
    body: str = Field(min_length=50)


class AIRequestIn(In):
    kind: Literal["triage", "narrative", "xref", "explain"] = "triage"
    run_id: str | None = None
    finding_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=3, max_length=500)
    provider: Literal["anthropic", "bedrock", "vertex", "foundry", "local", "gateway", "mock"] | None = None
    region: str | None = None


class AIRequestOut(Out):
    id: str
    engagement_id: str
    kind: str
    reason: str
    status: str
    payload_sha256: str
    payload_size: int
    token_estimate: int
    provider: str
    model: str
    region: str | None
    prompt_version: str
    requested_by: str
    approved_by: str | None
    approved_at: datetime | None
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: str | None
    created_at: datetime
    payload: dict[str, Any] | None = None
    response: dict[str, Any] | None = None


class AIApproveIn(In):
    payload_sha256: str


# ------------------------------------------------------------------ render & release


class RenderIn(In):
    mode: Literal["draft", "final"] = "draft"
    version_id: str | None = None
    engine: Literal["auto", "excel", "libreoffice", "native"] = "auto"
    idempotency_key: str | None = None


class ReleaseIn(In):
    render_run_id: str
    notes: str = ""


class ReleaseOut(Out):
    id: str
    engagement_id: str
    render_run_id: str
    pdf_version_id: str
    workbook_version_id: str
    released_by: str
    released_at: datetime
    diff_json: dict[str, Any]
    notes: str


# ------------------------------------------------------------------ audit


class AuditEventOut(Out):
    id: str
    seq: int
    ts: datetime
    user_id: str | None
    role_at_time: str | None
    ip: str | None
    client_id: str | None
    engagement_id: str | None
    object_type: str | None
    object_id: str | None
    action: str
    outcome: str
    details: dict[str, Any]
    correlation_id: str | None
    prev_event_hash: str
    event_hash: str


class ChainOut(Out):
    ok: bool
    tenant_id: str
    events: int
    head_seq: int
    head_hash: str
    first_bad_seq: int | None
    problems: list[str]


# ------------------------------------------------------------------ compliance & ops


class AccessReviewCampaignIn(In):
    period: str = Field(pattern=r"^\d{4}Q[1-4]$")


class AccessReviewOut(Out):
    id: str
    period: str
    client_id: str | None
    reviewer_id: str | None
    user_id: str
    role_id: str
    assignment_id: str
    decision: str
    decided_at: datetime | None
    decided_by: str | None
    due_at: datetime


class AccessDecisionIn(In):
    decision: Literal["certify", "revoke"]


class DsrIn(In):
    principal_ref: str = Field(min_length=3, max_length=320)
    kind: Literal["access", "correction", "erasure", "grievance"]
    details: str = ""


class DsrPatchIn(In):
    status: Literal["in_progress", "completed", "rejected", "partially_completed"]
    resolution: str = ""


class DsrOut(Out):
    id: str
    principal_ref: str
    principal_user_id: str | None
    kind: str
    status: str
    due_at: datetime
    details: str
    resolution: str | None
    handled_by: str | None
    completed_at: datetime | None
    created_at: datetime


class IncidentIn(In):
    severity: Literal["low", "medium", "high", "critical"]
    title: str = Field(min_length=3, max_length=300)
    description: str = ""
    detected_at: datetime | None = None
    certin_reportable: bool = False


class IncidentPatchIn(In):
    status: Literal["open", "contained", "resolved", "closed"] | None = None
    reported_at: datetime | None = None
    rca: str | None = None


class IncidentOut(Out):
    id: str
    severity: str
    title: str
    description: str
    detected_at: datetime
    certin_reportable: bool
    report_due_at: datetime | None
    reported_at: datetime | None
    status: str
    rca: str | None
    source: str
    overdue: bool = False
    created_at: datetime


class BackupLogIn(In):
    kind: Literal["pg_full", "pg_diff", "wal", "objects", "config", "restore_drill", "key_recovery_drill", "restore_request"]
    status: Literal["ok", "failed", "running", "pending_approval"] = "ok"
    size: int = 0
    location: str = ""
    verified: bool = False
    started: datetime | None = None
    finished: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class BackupLogOut(Out):
    id: str
    kind: str
    started: datetime
    finished: datetime | None
    status: str
    size: int
    location: str
    verified: bool
    details_json: dict[str, Any]


class ReleaseRecordIn(In):
    version: str
    ticket_ref: str
    description: str = ""
    risk: Literal["low", "medium", "high"] = "low"
    test_evidence: str = Field(min_length=3)
    regression_evidence: str | None = None
    rule_change: bool = False
    emergency: bool = False
    rollback_plan: str = Field(min_length=3)


class ReleaseRecordOut(Out):
    id: str
    version: str
    ticket_ref: str
    description: str
    risk: str
    test_evidence: str
    regression_evidence: str | None
    rule_change: bool
    emergency: bool
    approver_id: str | None
    approved_at: datetime | None
    deployed_at: datetime | None
    review_due_at: datetime | None = None  # emergency changes: deployed first, approved within 48 hours
    review_overdue: bool = False
    rollback_plan: str
    created_by: str | None
    created_at: datetime


class UsageOut(Out):
    month: str
    rows: list[dict[str, Any]]


class HealthOut(Out):
    status: str
    version: str
    engine: str
    engine_version: str
    database: bool
    storage: bool
    queue_depth: dict[str, int]
    render_workers: list[dict[str, Any]]
    last_backup: dict[str, Any] | None
    last_restore_drill: dict[str, Any] | None
    audit_head: dict[str, Any] | None


class RuleOut(Out):
    rules: list[dict[str, Any]]
    rule_set_version: str
    rule_set_hash: str


class PolicyOut(Out):
    name: str
    version: int
    body: str
    active: bool
    created_at: datetime


class PolicyIn(In):
    body: str = Field(min_length=10)


# ------------------------------------------------------------------ worker protocol


class RenderClaimIn(In):
    worker_id: str = Field(min_length=1, max_length=120)
    excel_build: str | None = None


class RenderClaimOut(Out):
    job_id: str
    run_id: str
    mode: str
    sheets: list[str]
    options: dict[str, Any]


class RenderFailIn(In):
    error: str


class HeartbeatIn(In):
    worker_id: str
    excel_build: str | None = None


FileOut.model_rebuild()
