"""ORM models for the full data model of 08 §1 (core + enterprise layer).

Conventions:
* every table carries ``tenant_id``, ``created_at`` and ``created_by``;
* every table holding client data also carries a denormalised ``client_id`` so the
  PostgreSQL RLS policies can scope rows to the clients a user is assigned to;
* identifiers (PAN, CIN, DIN, TOTP secrets, contact details) are stored only as
  envelope-encrypted ciphertext (``*_enc`` columns, E-SEC-07);
* mutable records carry ``version`` for optimistic locking (ETag / If-Match, E-DATA-03).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from fsweb.db import Base, JSONType, UTCDateTime, new_id, utcnow

ID = String(36)


def _id() -> Mapped[str]:
    return mapped_column(ID, primary_key=True, default=new_id)


def _tenant() -> Mapped[str]:
    return mapped_column(ID, ForeignKey("tenant.id"), index=True)


def _client() -> Mapped[str]:
    return mapped_column(ID, ForeignKey("client.id"), index=True)


def _created_at() -> Mapped[datetime]:
    return mapped_column(UTCDateTime, default=utcnow)


def _created_by() -> Mapped[str | None]:
    return mapped_column(String(64), nullable=True)


# =============================================================== tenancy & identity


class Tenant(Base):
    __tablename__ = "tenant"
    id: Mapped[str] = _id()
    name: Mapped[str] = mapped_column(String(200))
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()

    @property
    def tenant_id(self) -> str:
        return self.id


class User(Base):
    __tablename__ = "app_user"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="invited")  # invited|active|disabled
    is_firm_user: Mapped[bool] = mapped_column(Boolean, default=True)
    is_break_glass: Mapped[bool] = mapped_column(Boolean, default=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # set with a one-time / generated password: the API refuses everything but sign-in, MFA and the change (migration 0002)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    mfa_type: Mapped[str] = mapped_column(String(16), default="none")  # none|totp
    totp_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_totp_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # replay protection
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    sso_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_policy_accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    download_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # per-user override
    erased_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # DPDP erasure
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()
    __mapper_args__: ClassVar[dict[str, Any]] = {"version_id_col": version}


class RecoveryCode(Base):
    __tablename__ = "recovery_code"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("app_user.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class UserSession(Base):
    __tablename__ = "user_session"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("app_user.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    auth_method: Mapped[str] = mapped_column(String(16), default="password")  # password|oidc|breakglass
    mfa_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_totp_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    stepup_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    idle_minutes: Mapped[int] = mapped_column(Integer, default=60)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class Invite(Base):
    __tablename__ = "invite"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("app_user.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class PasswordReset(Base):
    __tablename__ = "password_reset"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("app_user.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class Role(Base):
    __tablename__ = "role"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(100))
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)
    scope: Mapped[str] = mapped_column(String(10), default="client")  # tenant|client
    is_client_role: Mapped[bool] = mapped_column(Boolean, default=False)
    download_allowed: Mapped[bool] = mapped_column(Boolean, default=True)  # "per role setting"
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class Permission(Base):
    """Global permission catalogue (code-defined, identical in every tenant)."""

    __tablename__ = "permission"
    code: Mapped[str] = mapped_column(String(60), primary_key=True)
    description: Mapped[str] = mapped_column(Text, default="")
    tenant_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class RolePermission(Base):
    __tablename__ = "role_permission"
    role_id: Mapped[str] = mapped_column(ID, ForeignKey("role.id"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(String(60), ForeignKey("permission.code"), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    value: Mapped[str] = mapped_column(String(20), default="allow")
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class Client(Base):
    __tablename__ = "client"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    name: Mapped[str] = mapped_column(String(200))
    pan_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    cin_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    office: Mapped[str | None] = mapped_column(String(100), nullable=True)
    classification: Mapped[str] = mapped_column(String(20), default="confidential")
    contact_email_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()
    __mapper_args__: ClassVar[dict[str, Any]] = {"version_id_col": version}


class UserClientRole(Base):
    """Scoping: who can do what on which client. ``client_id`` NULL = tenant-wide role."""

    __tablename__ = "user_client_role"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("app_user.id"), index=True)
    client_id: Mapped[str | None] = mapped_column(ID, ForeignKey("client.id"), nullable=True, index=True)
    role_id: Mapped[str] = mapped_column(ID, ForeignKey("role.id"))
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # JIT elevation
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class Elevation(Base):
    """Just-in-time privilege elevation (E-SEC-03): time-boxed, approved by another admin."""

    __tablename__ = "elevation"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("app_user.id"))
    role_id: Mapped[str] = mapped_column(ID, ForeignKey("role.id"))
    client_id: Mapped[str | None] = mapped_column(ID, ForeignKey("client.id"), nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|active|rejected|expired
    approved_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    assignment_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class DataKey(Base):
    """Envelope encryption: data-encryption keys wrapped by a KMS key-encryption key."""

    __tablename__ = "data_key"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    wrapped_key: Mapped[str] = mapped_column(Text)
    kek_id: Mapped[str] = mapped_column(String(40))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class EmailOutbox(Base):
    """Notifications (E-INT-03). Never contains client figures."""

    __tablename__ = "email_outbox"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    to_addr: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(40))
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


# =============================================================== master data


class Entity(Base):
    __tablename__ = "entity"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    name: Mapped[str] = mapped_column(String(200))
    functional_ccy: Mapped[str] = mapped_column(String(3), default="INR")
    presentation_ccy: Mapped[str] = mapped_column(String(3), default="INR")
    country: Mapped[str] = mapped_column(String(2), default="IN")
    parent_entity_id: Mapped[str | None] = mapped_column(ID, ForeignKey("entity.id"), nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class Signatory(Base):
    __tablename__ = "signatory"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    entity_id: Mapped[str] = mapped_column(ID, ForeignKey("entity.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(60))
    din_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    membership_no: Mapped[str | None] = mapped_column(String(20), nullable=True)
    frn: Mapped[str | None] = mapped_column(String(20), nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class Engagement(Base):
    __tablename__ = "engagement"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    entity_id: Mapped[str] = mapped_column(ID, ForeignKey("entity.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(16), default="annual")  # annual|interim|spfs
    period_end: Mapped[date] = mapped_column(Date)
    periods_in_scope_json: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT_SETUP")
    partner_id: Mapped[str | None] = mapped_column(ID, ForeignKey("app_user.id"), nullable=True)
    manager_id: Mapped[str | None] = mapped_column(ID, ForeignKey("app_user.id"), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    locked_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # per-engagement AI kill switch
    is_quarantine: Mapped[bool] = mapped_column(Boolean, default=False)
    merge_required: Mapped[bool] = mapped_column(Boolean, default=False)
    source_engagement_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    version_line: Mapped[int] = mapped_column(Integer, default=1)
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()
    __mapper_args__: ClassVar[dict[str, Any]] = {"version_id_col": version}


class EngagementConfigRow(Base):
    """02 §8 configuration, versioned (08: config)."""

    __tablename__ = "config"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    engagement_id: Mapped[str] = mapped_column(ID, ForeignKey("engagement.id"), index=True)
    yaml_text: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class UnlockRequest(Base):
    """Unlocking a FINAL_LOCKED engagement: Partner + Firm Admin (dual) with a reason."""

    __tablename__ = "unlock_request"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    engagement_id: Mapped[str] = mapped_column(ID, ForeignKey("engagement.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    requested_by: Mapped[str] = mapped_column(ID)
    partner_approved_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    admin_approved_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|completed|rejected
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


# =============================================================== files & versions


class File(Base):
    __tablename__ = "file"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    engagement_id: Mapped[str] = mapped_column(ID, ForeignKey("engagement.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # workbook|tb|printed_pdf|prior_year|rendered_pdf|other
    title: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class FileVersion(Base):
    __tablename__ = "file_version"
    __table_args__ = (UniqueConstraint("file_id", "version_no"),)
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    engagement_id: Mapped[str] = mapped_column(ID, ForeignKey("engagement.id"), index=True)
    file_id: Mapped[str] = mapped_column(ID, ForeignKey("file.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    version_line: Mapped[int] = mapped_column(Integer, default=1)
    object_key: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(String(120))
    original_name: Mapped[str] = mapped_column(String(300))
    parent_version_id: Mapped[str | None] = mapped_column(ID, ForeignKey("file_version.id"), nullable=True)
    produced_by_run_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    change_summary_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    worm_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    has_macros: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class FileLock(Base):
    """Check-out / check-in (E-DATA-03): exclusive edit lock with timeout."""

    __tablename__ = "file_lock"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    file_id: Mapped[str] = mapped_column(ID, ForeignKey("file.id"), index=True)
    version_id: Mapped[str] = mapped_column(ID, ForeignKey("file_version.id"))
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("app_user.id"))
    acquired_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class ShareLink(Base):
    """Expiring, single-use share links for client users (E-SEC-10)."""

    __tablename__ = "share_link"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    engagement_id: Mapped[str] = mapped_column(ID, ForeignKey("engagement.id"))
    file_version_id: Mapped[str] = mapped_column(ID, ForeignKey("file_version.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    recipient_user_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


# =============================================================== runs, jobs, findings


class Run(Base):
    __tablename__ = "run"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    engagement_id: Mapped[str] = mapped_column(ID, ForeignKey("engagement.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # check|plan|fix|narrative|render|ai_review|prepare|diff
    input_version_ids: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    input_hashes: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|succeeded|failed
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    engine_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rule_set_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rule_set_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    params_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    output_version_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class Job(Base):
    """Transactional outbox / DB-backed queue (E-ARCH-05)."""

    __tablename__ = "job"
    __table_args__ = (Index("ix_job_claim", "status", "queue", "available_at"),)
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    run_id: Mapped[str] = mapped_column(ID, ForeignKey("run.id"), index=True)
    queue: Mapped[str] = mapped_column(String(20), default="default")  # default|render-win|ai-gateway
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|claimed|succeeded|failed|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class RunEvent(Base):
    __tablename__ = "run_event"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    run_id: Mapped[str] = mapped_column(ID, ForeignKey("run.id"), index=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    kind: Mapped[str] = mapped_column(String(16), default="progress")  # progress|status|log
    message: Mapped[str] = mapped_column(String(300), default="")
    pct: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class Artefact(Base):
    __tablename__ = "artefact"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    engagement_id: Mapped[str] = mapped_column(ID, ForeignKey("engagement.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ID, ForeignKey("run.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(30))  # register|html|plan|diff|payload|response|verification|report
    name: Mapped[str] = mapped_column(String(300))
    object_key: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class FindingRow(Base):
    __tablename__ = "finding"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    engagement_id: Mapped[str] = mapped_column(ID, ForeignKey("engagement.id"), index=True)
    run_id: Mapped[str] = mapped_column(ID, ForeignKey("run.id"), index=True)
    fid: Mapped[str] = mapped_column(String(20))
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    rule_id: Mapped[str] = mapped_column(String(20), index=True)
    rule_version: Mapped[str] = mapped_column(String(20), default="1")
    family: Mapped[str] = mapped_column(String(20), default="")
    severity: Mapped[str] = mapped_column(String(10), index=True)
    certainty: Mapped[str] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(60), default="")
    sheet: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cells: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    printed: Mapped[str | None] = mapped_column(String(120), nullable=True)
    expected: Mapped[str | None] = mapped_column(String(120), nullable=True)
    diff: Mapped[str | None] = mapped_column(String(60), nullable=True)
    period: Mapped[str | None] = mapped_column(String(20), nullable=True)
    evidence_json: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    explanation: Mapped[str] = mapped_column(Text, default="")
    suggested_fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    fix_kind: Mapped[str] = mapped_column(String(10), default="manual")
    status: Mapped[str] = mapped_column(String(20), default="Open")  # Open|Accepted|Not an issue|Resolved
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(ID, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    shared_with_client: Mapped[bool] = mapped_column(Boolean, default=False)
    status_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()
    __mapper_args__: ClassVar[dict[str, Any]] = {"version_id_col": version}


class FixPlanRow(Base):
    __tablename__ = "fix_plan"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    engagement_id: Mapped[str] = mapped_column(ID, ForeignKey("engagement.id"), index=True)
    run_id: Mapped[str] = mapped_column(ID, ForeignKey("run.id"))  # check run the plan is based on
    plan_run_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    apply_run_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    base_version_id: Mapped[str] = mapped_column(ID, ForeignKey("file_version.id"))
    plan_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    plan_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="planning")
    proposed_by: Mapped[str] = mapped_column(ID)
    approved_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    approved_plan_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_edit_ids: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    has_balancing: Mapped[bool] = mapped_column(Boolean, default=False)
    balancing_approved_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    balancing_approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class EditRow(Base):
    __tablename__ = "edit"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    fix_plan_id: Mapped[str] = mapped_column(ID, ForeignKey("fix_plan.id"), index=True)
    eid: Mapped[str] = mapped_column(String(20))
    sheet: Mapped[str] = mapped_column(String(120))
    cell: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(12))
    grp: Mapped[str] = mapped_column(String(20))
    fix_kind: Mapped[str] = mapped_column(String(10))
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    old_formula: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_formula: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    applied_version_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class NarrativeEdit(Base):
    __tablename__ = "narrative_edit"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    engagement_id: Mapped[str] = mapped_column(ID, ForeignKey("engagement.id"), index=True)
    base_version_id: Mapped[str] = mapped_column(ID, ForeignKey("file_version.id"))
    sheet: Mapped[str] = mapped_column(String(120))
    cell: Mapped[str] = mapped_column(String(20))
    old_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_text: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending_approval")
    proposed_by: Mapped[str] = mapped_column(ID)
    approved_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    applied_run_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class ReviewNote(Base):
    __tablename__ = "review_note"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    engagement_id: Mapped[str] = mapped_column(ID, ForeignKey("engagement.id"), index=True)
    target_type: Mapped[str] = mapped_column(String(20))  # finding|cell|pdf_page|engagement|client_query
    target_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="Open")  # Open|Responded|Cleared
    audience: Mapped[str] = mapped_column(String(10), default="team")  # team|client
    raised_by: Mapped[str] = mapped_column(ID)
    assigned_to: Mapped[str | None] = mapped_column(ID, nullable=True)
    cleared_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    cleared_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()
    __mapper_args__: ClassVar[dict[str, Any]] = {"version_id_col": version}


class ReviewNoteMsg(Base):
    __tablename__ = "review_note_msg"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    review_note_id: Mapped[str] = mapped_column(ID, ForeignKey("review_note.id"), index=True)
    user_id: Mapped[str] = mapped_column(ID)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class AIRequest(Base):
    __tablename__ = "ai_request"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    engagement_id: Mapped[str] = mapped_column(ID, ForeignKey("engagement.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    send_run_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    kind: Mapped[str] = mapped_column(String(20))  # triage|narrative|xref|explain
    reason: Mapped[str] = mapped_column(Text, default="")
    payload_object_key: Mapped[str] = mapped_column(String(500))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    payload_size: Mapped[int] = mapped_column(Integer)
    token_estimate: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(60))
    region: Mapped[str | None] = mapped_column(String(40), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(40), default="1")
    status: Mapped[str] = mapped_column(String(16), default="preview")  # preview|approved|queued|sent|completed|rejected|failed
    requested_by: Mapped[str] = mapped_column(ID)
    approved_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    approval_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_object_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class DraftRelease(Base):
    """A draft released to the client (C12: diff vs the previous release attached)."""

    __tablename__ = "draft_release"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    engagement_id: Mapped[str] = mapped_column(ID, ForeignKey("engagement.id"), index=True)
    render_run_id: Mapped[str] = mapped_column(ID, ForeignKey("run.id"))
    pdf_version_id: Mapped[str] = mapped_column(ID, ForeignKey("file_version.id"))
    workbook_version_id: Mapped[str] = mapped_column(ID, ForeignKey("file_version.id"))
    released_by: Mapped[str] = mapped_column(ID)
    released_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    diff_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class MgmtItem(Base):
    """Management-representation items confirmed by the client (client portal)."""

    __tablename__ = "mgmt_item"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    engagement_id: Mapped[str] = mapped_column(ID, ForeignKey("engagement.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending|confirmed|disputed
    confirmed_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class DisclosureChecklistItem(Base):
    """Disclosure checklist engine (E-PREP-02). Unanswered items block FINAL_LOCKED."""

    __tablename__ = "disclosure_checklist"
    __table_args__ = (UniqueConstraint("engagement_id", "item_code"),)
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    engagement_id: Mapped[str] = mapped_column(ID, ForeignKey("engagement.id"), index=True)
    framework_version: Mapped[str] = mapped_column(String(40), default="IndAS-SchIII-DivII-2026")
    item_code: Mapped[str] = mapped_column(String(40))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(String(8), nullable=True)  # yes|no|na
    auto: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    answered_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


# =============================================================== audit


class AuditEvent(Base):
    """Append-only, hash-chained audit trail (06 §4)."""

    __tablename__ = "audit_event"
    __table_args__ = (UniqueConstraint("tenant_id", "seq"), Index("ix_audit_tenant_ts", "tenant_id", "ts"))
    # Client-generated id: an INSERT never needs RETURNING, so an actor can record an event
    # (e.g. a denial) about a client whose rows RLS does not let them read.
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(ID, index=True)
    seq: Mapped[int] = mapped_column(BigInteger)
    ts: Mapped[datetime] = mapped_column(UTCDateTime)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role_at_time: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    client_id: Mapped[str | None] = mapped_column(ID, nullable=True, index=True)
    engagement_id: Mapped[str | None] = mapped_column(ID, nullable=True, index=True)
    object_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    outcome: Mapped[str] = mapped_column(String(10), default="ok")  # ok|denied|failed
    before_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prev_event_hash: Mapped[str] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class AuditHead(Base):
    """Head of each tenant's chain; row-locked to serialise appends."""

    __tablename__ = "audit_head"
    tenant_id: Mapped[str] = mapped_column(ID, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, default=0)
    event_hash: Mapped[str] = mapped_column(String(64), default="0" * 64)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


# =============================================================== operations & compliance


class BackupLog(Base):
    __tablename__ = "backup_log"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    kind: Mapped[str] = mapped_column(String(30))  # pg_full|pg_diff|wal|objects|config|restore_drill|key_recovery_drill
    started: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok|failed|running|pending_approval|approved
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    location: Mapped[str] = mapped_column(String(300), default="")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class BillingUsage(Base):
    __tablename__ = "billing_usage"
    __table_args__ = (UniqueConstraint("client_id", "month"),)
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    month: Mapped[str] = mapped_column(String(7))
    engagements: Mapped[int] = mapped_column(Integer, default=0)
    runs: Mapped[int] = mapped_column(Integer, default=0)
    renders: Mapped[int] = mapped_column(Integer, default=0)
    ai_calls: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class RuleRow(Base):
    __tablename__ = "rule"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    code: Mapped[str] = mapped_column(String(20))
    family: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class RuleVersion(Base):
    __tablename__ = "rule_version"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    rule_id: Mapped[str] = mapped_column(ID, ForeignKey("rule.id"), index=True)
    version: Mapped[str] = mapped_column(String(20))
    params_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    severity: Mapped[str] = mapped_column(String(10))
    frameworks: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    regression_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class RuleSet(Base):
    __tablename__ = "rule_set"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(40))
    hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    frozen_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class RuleSetMember(Base):
    __tablename__ = "rule_set_member"
    rule_set_id: Mapped[str] = mapped_column(ID, ForeignKey("rule_set.id"), primary_key=True)
    rule_version_id: Mapped[str] = mapped_column(ID, ForeignKey("rule_version.id"), primary_key=True)
    tenant_id: Mapped[str] = _tenant()
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class Template(Base):
    __tablename__ = "template"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    kind: Mapped[str] = mapped_column(String(20))  # statement|note|wording
    framework: Mapped[str] = mapped_column(String(40), default="IndAS")
    industry: Mapped[str | None] = mapped_column(String(60), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class TemplateVersion(Base):
    __tablename__ = "template_version"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    template_id: Mapped[str] = mapped_column(ID, ForeignKey("template.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class Mapping(Base):
    __tablename__ = "mapping"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class MappingVersion(Base):
    __tablename__ = "mapping_version"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    mapping_id: Mapped[str] = mapped_column(ID, ForeignKey("mapping.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    rows_object_key: Mapped[str] = mapped_column(String(500))
    diff_from_prev_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    approved_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class FxRate(Base):
    __tablename__ = "fx_rate"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    entity_id: Mapped[str] = mapped_column(ID, ForeignKey("entity.id"), index=True)
    period_end: Mapped[date] = mapped_column(Date)
    rate_type: Mapped[str] = mapped_column(String(12))  # closing|average|historical
    rate: Mapped[Decimal] = mapped_column(Numeric(20, 10))
    source: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class LineageEdge(Base):
    __tablename__ = "lineage_edge"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str] = _client()
    run_id: Mapped[str] = mapped_column(ID, ForeignKey("run.id"), index=True)
    from_ref: Mapped[str] = mapped_column(String(300))
    to_ref: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class Policy(Base):
    """Policies as versioned data: ABAC rules, the AI policy (E-SEC-02, E-AI-01)."""

    __tablename__ = "policy"
    __table_args__ = (UniqueConstraint("tenant_id", "name", "version"),)
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    name: Mapped[str] = mapped_column(String(60))  # abac|ai_policy
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    approved_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class AccessReview(Base):
    """Quarterly access recertification (C14 / E-SEC-04)."""

    __tablename__ = "access_review"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    client_id: Mapped[str | None] = mapped_column(ID, nullable=True, index=True)
    period: Mapped[str] = mapped_column(String(10))  # 2026Q3
    reviewer_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    user_id: Mapped[str] = mapped_column(ID)
    role_id: Mapped[str] = mapped_column(ID)
    assignment_id: Mapped[str] = mapped_column(ID)
    decision: Mapped[str] = mapped_column(String(14), default="pending")  # pending|certify|revoke|auto_revoked
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class DsrRequest(Base):
    """DPDP Act data-principal requests (E-SEC-06)."""

    __tablename__ = "dsr_request"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    principal_ref: Mapped[str] = mapped_column(String(320))
    principal_user_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    kind: Mapped[str] = mapped_column(String(12))  # access|correction|erasure|grievance
    status: Mapped[str] = mapped_column(String(20), default="received")
    due_at: Mapped[datetime] = mapped_column(UTCDateTime)
    details: Mapped[str] = mapped_column(Text, default="")
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    handled_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class Incident(Base):
    """Security incidents incl. CERT-In 6-hour reporting (E-SEC-05)."""

    __tablename__ = "incident"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    severity: Mapped[str] = mapped_column(String(10))  # low|medium|high|critical
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime)
    certin_reportable: Mapped[bool] = mapped_column(Boolean, default=False)
    report_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    reported_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open|contained|resolved|closed
    rca: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="manual")
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class AIPromptVersion(Base):
    __tablename__ = "ai_prompt_version"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    use_case: Mapped[str] = mapped_column(String(40))
    version: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(ID, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class AIEvalRun(Base):
    __tablename__ = "ai_eval_run"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    prompt_version_id: Mapped[str] = mapped_column(ID, ForeignKey("ai_prompt_version.id"))
    model: Mapped[str] = mapped_column(String(60))
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


class Release(Base):
    """ITGC change log (E-ITGC-01): approver ≠ developer, rollback plan, test evidence."""

    __tablename__ = "release"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    version: Mapped[str] = mapped_column(String(40))
    ticket_ref: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(Text, default="")
    risk: Mapped[str] = mapped_column(String(10), default="low")
    test_evidence: Mapped[str] = mapped_column(Text, default="")
    regression_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)  # E-ITGC-02
    rule_change: Mapped[bool] = mapped_column(Boolean, default=False)
    emergency: Mapped[bool] = mapped_column(Boolean, default=False)
    approver_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    deployed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    rollback_plan: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()

    EMERGENCY_REVIEW = timedelta(hours=48)

    @property
    def review_due_at(self) -> datetime | None:
        """An emergency change is deployed first and approved by someone other than the developer within 48 hours."""
        return self.deployed_at + self.EMERGENCY_REVIEW if self.emergency and self.deployed_at else None

    @property
    def review_overdue(self) -> bool:
        due = self.review_due_at
        return due is not None and (self.approved_at or utcnow()) > due


class RenderWorker(Base):
    """Windows render workers seen via heartbeat (E-ARCH-03)."""

    __tablename__ = "render_worker"
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    excel_build: Mapped[str | None] = mapped_column(String(60), nullable=True)
    last_heartbeat_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str | None] = _created_by()


# RLS categories (must match alembic/versions/0001: every table is in exactly one).
AUTH_TABLES = ["app_user", "user_session", "invite", "password_reset", "recovery_code"]
TENANT_TABLES = [
    "role", "role_permission", "user_client_role", "elevation", "email_outbox", "backup_log", "rule", "rule_version",
    "rule_set", "rule_set_member", "template", "template_version", "policy", "access_review", "dsr_request", "incident",
    "ai_prompt_version", "ai_eval_run", "release", "audit_head", "data_key",
]
FIRM_VISIBLE_TABLES = ["entity", "signatory", "engagement", "ai_request", "unlock_request", "billing_usage"]
CLIENT_TABLES = [
    "config", "file", "file_version", "file_lock", "share_link", "run", "job", "run_event", "artefact", "finding", "fix_plan",
    "edit", "narrative_edit", "review_note", "review_note_msg", "draft_release", "mgmt_item", "disclosure_checklist",
    "mapping", "mapping_version", "fx_rate", "lineage_edge",
]
SPECIAL_TABLES = ["tenant", "client", "audit_event"]  # own policies
NO_RLS_TABLES = ["permission", "render_worker"]  # global catalogue / infrastructure
