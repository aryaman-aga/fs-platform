"""initial schema: full data model of 08 §1, row-level security, append-only audit trail

Revision ID: 0001
Revises:
Create Date: 2026-10-21 02:33:24.000160
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import fsweb.db

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('audit_event',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('seq', sa.BigInteger(), nullable=False),
    sa.Column('ts', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=True),
    sa.Column('role_at_time', sa.String(length=200), nullable=True),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('user_agent', sa.String(length=300), nullable=True),
    sa.Column('client_id', sa.String(length=36), nullable=True),
    sa.Column('engagement_id', sa.String(length=36), nullable=True),
    sa.Column('object_type', sa.String(length=40), nullable=True),
    sa.Column('object_id', sa.String(length=64), nullable=True),
    sa.Column('action', sa.String(length=60), nullable=False),
    sa.Column('outcome', sa.String(length=10), nullable=False),
    sa.Column('before_hash', sa.String(length=64), nullable=True),
    sa.Column('after_hash', sa.String(length=64), nullable=True),
    sa.Column('details', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('correlation_id', sa.String(length=64), nullable=True),
    sa.Column('prev_event_hash', sa.String(length=64), nullable=False),
    sa.Column('event_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'seq')
    )
    op.create_index(op.f('ix_audit_event_action'), 'audit_event', ['action'], unique=False)
    op.create_index(op.f('ix_audit_event_client_id'), 'audit_event', ['client_id'], unique=False)
    op.create_index(op.f('ix_audit_event_engagement_id'), 'audit_event', ['engagement_id'], unique=False)
    op.create_index(op.f('ix_audit_event_tenant_id'), 'audit_event', ['tenant_id'], unique=False)
    op.create_index('ix_audit_tenant_ts', 'audit_event', ['tenant_id', 'ts'], unique=False)
    op.create_table('audit_head',
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('seq', sa.BigInteger(), nullable=False),
    sa.Column('event_hash', sa.String(length=64), nullable=False),
    sa.Column('updated_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.PrimaryKeyConstraint('tenant_id')
    )
    op.create_table('data_key',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=True),
    sa.Column('wrapped_key', sa.Text(), nullable=False),
    sa.Column('kek_id', sa.String(length=40), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('rotated_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('permission',
    sa.Column('code', sa.String(length=60), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_table('render_worker',
    sa.Column('id', sa.String(length=120), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=True),
    sa.Column('excel_build', sa.String(length=60), nullable=True),
    sa.Column('last_heartbeat_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('tenant',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('settings_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('access_review',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=True),
    sa.Column('period', sa.String(length=10), nullable=False),
    sa.Column('reviewer_id', sa.String(length=36), nullable=True),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('role_id', sa.String(length=36), nullable=False),
    sa.Column('assignment_id', sa.String(length=36), nullable=False),
    sa.Column('decision', sa.String(length=14), nullable=False),
    sa.Column('decided_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('decided_by', sa.String(length=36), nullable=True),
    sa.Column('due_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_access_review_client_id'), 'access_review', ['client_id'], unique=False)
    op.create_index(op.f('ix_access_review_tenant_id'), 'access_review', ['tenant_id'], unique=False)
    op.create_table('ai_prompt_version',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('use_case', sa.String(length=40), nullable=False),
    sa.Column('version', sa.String(length=20), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('approved_by', sa.String(length=36), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_prompt_version_tenant_id'), 'ai_prompt_version', ['tenant_id'], unique=False)
    op.create_table('app_user',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('is_firm_user', sa.Boolean(), nullable=False),
    sa.Column('is_break_glass', sa.Boolean(), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=True),
    sa.Column('password_changed_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('mfa_type', sa.String(length=16), nullable=False),
    sa.Column('totp_secret_enc', sa.Text(), nullable=True),
    sa.Column('mfa_enrolled_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('last_totp_step', sa.BigInteger(), nullable=True),
    sa.Column('failed_logins', sa.Integer(), nullable=False),
    sa.Column('locked_until', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('last_login_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('sso_subject', sa.String(length=255), nullable=True),
    sa.Column('phone_enc', sa.Text(), nullable=True),
    sa.Column('ai_policy_version', sa.Integer(), nullable=True),
    sa.Column('ai_policy_accepted_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('download_allowed', sa.Boolean(), nullable=True),
    sa.Column('erased_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_index(op.f('ix_app_user_tenant_id'), 'app_user', ['tenant_id'], unique=False)
    op.create_table('backup_log',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('started', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('finished', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('size', sa.BigInteger(), nullable=False),
    sa.Column('location', sa.String(length=300), nullable=False),
    sa.Column('verified', sa.Boolean(), nullable=False),
    sa.Column('details_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_backup_log_tenant_id'), 'backup_log', ['tenant_id'], unique=False)
    op.create_table('client',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('pan_enc', sa.Text(), nullable=True),
    sa.Column('cin_enc', sa.Text(), nullable=True),
    sa.Column('group_name', sa.String(length=200), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('office', sa.String(length=100), nullable=True),
    sa.Column('classification', sa.String(length=20), nullable=False),
    sa.Column('contact_email_enc', sa.Text(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_client_tenant_id'), 'client', ['tenant_id'], unique=False)
    op.create_table('dsr_request',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('principal_ref', sa.String(length=320), nullable=False),
    sa.Column('principal_user_id', sa.String(length=36), nullable=True),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('due_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('details', sa.Text(), nullable=False),
    sa.Column('resolution', sa.Text(), nullable=True),
    sa.Column('handled_by', sa.String(length=36), nullable=True),
    sa.Column('completed_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_dsr_request_tenant_id'), 'dsr_request', ['tenant_id'], unique=False)
    op.create_table('email_outbox',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('to_addr', sa.String(length=320), nullable=False),
    sa.Column('subject', sa.String(length=300), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('sent_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_email_outbox_tenant_id'), 'email_outbox', ['tenant_id'], unique=False)
    op.create_table('incident',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('severity', sa.String(length=10), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('detected_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('certin_reportable', sa.Boolean(), nullable=False),
    sa.Column('report_due_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('reported_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('rca', sa.Text(), nullable=True),
    sa.Column('source', sa.String(length=40), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_incident_tenant_id'), 'incident', ['tenant_id'], unique=False)
    op.create_table('policy',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('approved_by', sa.String(length=36), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'name', 'version')
    )
    op.create_index(op.f('ix_policy_tenant_id'), 'policy', ['tenant_id'], unique=False)
    op.create_table('release',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.String(length=40), nullable=False),
    sa.Column('ticket_ref', sa.String(length=60), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('risk', sa.String(length=10), nullable=False),
    sa.Column('test_evidence', sa.Text(), nullable=False),
    sa.Column('regression_evidence', sa.Text(), nullable=True),
    sa.Column('rule_change', sa.Boolean(), nullable=False),
    sa.Column('emergency', sa.Boolean(), nullable=False),
    sa.Column('approver_id', sa.String(length=36), nullable=True),
    sa.Column('approved_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('deployed_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('rollback_plan', sa.Text(), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_release_tenant_id'), 'release', ['tenant_id'], unique=False)
    op.create_table('role',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('is_system', sa.Boolean(), nullable=False),
    sa.Column('scope', sa.String(length=10), nullable=False),
    sa.Column('is_client_role', sa.Boolean(), nullable=False),
    sa.Column('download_allowed', sa.Boolean(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'code')
    )
    op.create_index(op.f('ix_role_tenant_id'), 'role', ['tenant_id'], unique=False)
    op.create_table('rule',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('code', sa.String(length=20), nullable=False),
    sa.Column('family', sa.String(length=20), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'code')
    )
    op.create_index(op.f('ix_rule_tenant_id'), 'rule', ['tenant_id'], unique=False)
    op.create_table('rule_set',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('version', sa.String(length=40), nullable=False),
    sa.Column('hash', sa.String(length=64), nullable=True),
    sa.Column('frozen_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_rule_set_tenant_id'), 'rule_set', ['tenant_id'], unique=False)
    op.create_table('template',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('framework', sa.String(length=40), nullable=False),
    sa.Column('industry', sa.String(length=60), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_template_tenant_id'), 'template', ['tenant_id'], unique=False)
    op.create_table('ai_eval_run',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('prompt_version_id', sa.String(length=36), nullable=False),
    sa.Column('model', sa.String(length=60), nullable=False),
    sa.Column('metrics_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['prompt_version_id'], ['ai_prompt_version.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_eval_run_tenant_id'), 'ai_eval_run', ['tenant_id'], unique=False)
    op.create_table('billing_usage',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('month', sa.String(length=7), nullable=False),
    sa.Column('engagements', sa.Integer(), nullable=False),
    sa.Column('runs', sa.Integer(), nullable=False),
    sa.Column('renders', sa.Integer(), nullable=False),
    sa.Column('ai_calls', sa.Integer(), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('client_id', 'month')
    )
    op.create_index(op.f('ix_billing_usage_client_id'), 'billing_usage', ['client_id'], unique=False)
    op.create_index(op.f('ix_billing_usage_tenant_id'), 'billing_usage', ['tenant_id'], unique=False)
    op.create_table('elevation',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('role_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=True),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('duration_minutes', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('approved_by', sa.String(length=36), nullable=True),
    sa.Column('approved_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('expires_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('assignment_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['role_id'], ['role.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_elevation_tenant_id'), 'elevation', ['tenant_id'], unique=False)
    op.create_table('entity',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('functional_ccy', sa.String(length=3), nullable=False),
    sa.Column('presentation_ccy', sa.String(length=3), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('parent_entity_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['parent_entity_id'], ['entity.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_entity_client_id'), 'entity', ['client_id'], unique=False)
    op.create_index(op.f('ix_entity_tenant_id'), 'entity', ['tenant_id'], unique=False)
    op.create_table('invite',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('used_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_invite_tenant_id'), 'invite', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_invite_user_id'), 'invite', ['user_id'], unique=False)
    op.create_table('mapping',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_mapping_client_id'), 'mapping', ['client_id'], unique=False)
    op.create_index(op.f('ix_mapping_tenant_id'), 'mapping', ['tenant_id'], unique=False)
    op.create_table('password_reset',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('used_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_password_reset_tenant_id'), 'password_reset', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_password_reset_user_id'), 'password_reset', ['user_id'], unique=False)
    op.create_table('recovery_code',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('code_hash', sa.String(length=128), nullable=False),
    sa.Column('used_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_recovery_code_tenant_id'), 'recovery_code', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_recovery_code_user_id'), 'recovery_code', ['user_id'], unique=False)
    op.create_table('role_permission',
    sa.Column('role_id', sa.String(length=36), nullable=False),
    sa.Column('permission_code', sa.String(length=60), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('value', sa.String(length=20), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['permission_code'], ['permission.code'], ),
    sa.ForeignKeyConstraint(['role_id'], ['role.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('role_id', 'permission_code')
    )
    op.create_index(op.f('ix_role_permission_tenant_id'), 'role_permission', ['tenant_id'], unique=False)
    op.create_table('rule_version',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('rule_id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.String(length=20), nullable=False),
    sa.Column('params_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('severity', sa.String(length=10), nullable=False),
    sa.Column('frameworks', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=True),
    sa.Column('effective_to', sa.Date(), nullable=True),
    sa.Column('approved_by', sa.String(length=36), nullable=True),
    sa.Column('regression_run_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['rule_id'], ['rule.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_rule_version_rule_id'), 'rule_version', ['rule_id'], unique=False)
    op.create_index(op.f('ix_rule_version_tenant_id'), 'rule_version', ['tenant_id'], unique=False)
    op.create_table('template_version',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('template_id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=True),
    sa.Column('approved_by', sa.String(length=36), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['template_id'], ['template.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_template_version_template_id'), 'template_version', ['template_id'], unique=False)
    op.create_index(op.f('ix_template_version_tenant_id'), 'template_version', ['tenant_id'], unique=False)
    op.create_table('user_client_role',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=True),
    sa.Column('role_id', sa.String(length=36), nullable=False),
    sa.Column('expires_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('revoked_reason', sa.String(length=200), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['role_id'], ['role.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_client_role_client_id'), 'user_client_role', ['client_id'], unique=False)
    op.create_index(op.f('ix_user_client_role_tenant_id'), 'user_client_role', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_user_client_role_user_id'), 'user_client_role', ['user_id'], unique=False)
    op.create_table('user_session',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('csrf_hash', sa.String(length=64), nullable=False),
    sa.Column('auth_method', sa.String(length=16), nullable=False),
    sa.Column('mfa_verified', sa.Boolean(), nullable=False),
    sa.Column('pending_totp_secret_enc', sa.Text(), nullable=True),
    sa.Column('stepup_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('last_seen_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('expires_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('idle_minutes', sa.Integer(), nullable=False),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('user_agent', sa.String(length=300), nullable=True),
    sa.Column('revoked_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_user_session_tenant_id'), 'user_session', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_user_session_user_id'), 'user_session', ['user_id'], unique=False)
    op.create_table('engagement',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('entity_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('type', sa.String(length=16), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('periods_in_scope_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('partner_id', sa.String(length=36), nullable=True),
    sa.Column('manager_id', sa.String(length=36), nullable=True),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('locked_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('locked_by', sa.String(length=36), nullable=True),
    sa.Column('ai_enabled', sa.Boolean(), nullable=False),
    sa.Column('is_quarantine', sa.Boolean(), nullable=False),
    sa.Column('merge_required', sa.Boolean(), nullable=False),
    sa.Column('source_engagement_id', sa.String(length=36), nullable=True),
    sa.Column('version_line', sa.Integer(), nullable=False),
    sa.Column('legal_hold', sa.Boolean(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['entity_id'], ['entity.id'], ),
    sa.ForeignKeyConstraint(['manager_id'], ['app_user.id'], ),
    sa.ForeignKeyConstraint(['partner_id'], ['app_user.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_engagement_client_id'), 'engagement', ['client_id'], unique=False)
    op.create_index(op.f('ix_engagement_entity_id'), 'engagement', ['entity_id'], unique=False)
    op.create_index(op.f('ix_engagement_tenant_id'), 'engagement', ['tenant_id'], unique=False)
    op.create_table('fx_rate',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('entity_id', sa.String(length=36), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('rate_type', sa.String(length=12), nullable=False),
    sa.Column('rate', sa.Numeric(precision=20, scale=10), nullable=False),
    sa.Column('source', sa.String(length=100), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['entity_id'], ['entity.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_fx_rate_client_id'), 'fx_rate', ['client_id'], unique=False)
    op.create_index(op.f('ix_fx_rate_entity_id'), 'fx_rate', ['entity_id'], unique=False)
    op.create_index(op.f('ix_fx_rate_tenant_id'), 'fx_rate', ['tenant_id'], unique=False)
    op.create_table('mapping_version',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('mapping_id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('rows_object_key', sa.String(length=500), nullable=False),
    sa.Column('diff_from_prev_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('approved_by', sa.String(length=36), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['mapping_id'], ['mapping.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_mapping_version_client_id'), 'mapping_version', ['client_id'], unique=False)
    op.create_index(op.f('ix_mapping_version_mapping_id'), 'mapping_version', ['mapping_id'], unique=False)
    op.create_index(op.f('ix_mapping_version_tenant_id'), 'mapping_version', ['tenant_id'], unique=False)
    op.create_table('rule_set_member',
    sa.Column('rule_set_id', sa.String(length=36), nullable=False),
    sa.Column('rule_version_id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['rule_set_id'], ['rule_set.id'], ),
    sa.ForeignKeyConstraint(['rule_version_id'], ['rule_version.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('rule_set_id', 'rule_version_id')
    )
    op.create_index(op.f('ix_rule_set_member_tenant_id'), 'rule_set_member', ['tenant_id'], unique=False)
    op.create_table('signatory',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('entity_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('role', sa.String(length=60), nullable=False),
    sa.Column('din_enc', sa.Text(), nullable=True),
    sa.Column('membership_no', sa.String(length=20), nullable=True),
    sa.Column('frn', sa.String(length=20), nullable=True),
    sa.Column('valid_from', sa.Date(), nullable=True),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['entity_id'], ['entity.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_signatory_client_id'), 'signatory', ['client_id'], unique=False)
    op.create_index(op.f('ix_signatory_entity_id'), 'signatory', ['entity_id'], unique=False)
    op.create_index(op.f('ix_signatory_tenant_id'), 'signatory', ['tenant_id'], unique=False)
    op.create_table('ai_request',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('engagement_id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=True),
    sa.Column('send_run_id', sa.String(length=36), nullable=True),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('payload_object_key', sa.String(length=500), nullable=False),
    sa.Column('payload_sha256', sa.String(length=64), nullable=False),
    sa.Column('payload_size', sa.Integer(), nullable=False),
    sa.Column('token_estimate', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('model', sa.String(length=60), nullable=False),
    sa.Column('region', sa.String(length=40), nullable=True),
    sa.Column('prompt_version', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('requested_by', sa.String(length=36), nullable=False),
    sa.Column('approved_by', sa.String(length=36), nullable=True),
    sa.Column('approved_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('approval_token_hash', sa.String(length=64), nullable=True),
    sa.Column('response_object_key', sa.String(length=500), nullable=True),
    sa.Column('stop_reason', sa.String(length=30), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cost_usd', sa.Float(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('sent_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('completed_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['engagement_id'], ['engagement.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_request_client_id'), 'ai_request', ['client_id'], unique=False)
    op.create_index(op.f('ix_ai_request_engagement_id'), 'ai_request', ['engagement_id'], unique=False)
    op.create_index(op.f('ix_ai_request_tenant_id'), 'ai_request', ['tenant_id'], unique=False)
    op.create_table('config',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('engagement_id', sa.String(length=36), nullable=False),
    sa.Column('yaml_text', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['engagement_id'], ['engagement.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_config_client_id'), 'config', ['client_id'], unique=False)
    op.create_index(op.f('ix_config_engagement_id'), 'config', ['engagement_id'], unique=False)
    op.create_index(op.f('ix_config_tenant_id'), 'config', ['tenant_id'], unique=False)
    op.create_table('disclosure_checklist',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('engagement_id', sa.String(length=36), nullable=False),
    sa.Column('framework_version', sa.String(length=40), nullable=False),
    sa.Column('item_code', sa.String(length=40), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('answer', sa.String(length=8), nullable=True),
    sa.Column('auto', sa.Boolean(), nullable=False),
    sa.Column('evidence_ref', sa.String(length=300), nullable=True),
    sa.Column('answered_by', sa.String(length=36), nullable=True),
    sa.Column('answered_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['engagement_id'], ['engagement.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('engagement_id', 'item_code')
    )
    op.create_index(op.f('ix_disclosure_checklist_client_id'), 'disclosure_checklist', ['client_id'], unique=False)
    op.create_index(op.f('ix_disclosure_checklist_engagement_id'), 'disclosure_checklist', ['engagement_id'], unique=False)
    op.create_index(op.f('ix_disclosure_checklist_tenant_id'), 'disclosure_checklist', ['tenant_id'], unique=False)
    op.create_table('file',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('engagement_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['engagement_id'], ['engagement.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_file_client_id'), 'file', ['client_id'], unique=False)
    op.create_index(op.f('ix_file_engagement_id'), 'file', ['engagement_id'], unique=False)
    op.create_index(op.f('ix_file_tenant_id'), 'file', ['tenant_id'], unique=False)
    op.create_table('mgmt_item',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('engagement_id', sa.String(length=36), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('confirmed_by', sa.String(length=36), nullable=True),
    sa.Column('confirmed_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['engagement_id'], ['engagement.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_mgmt_item_client_id'), 'mgmt_item', ['client_id'], unique=False)
    op.create_index(op.f('ix_mgmt_item_engagement_id'), 'mgmt_item', ['engagement_id'], unique=False)
    op.create_index(op.f('ix_mgmt_item_tenant_id'), 'mgmt_item', ['tenant_id'], unique=False)
    op.create_table('review_note',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('engagement_id', sa.String(length=36), nullable=False),
    sa.Column('target_type', sa.String(length=20), nullable=False),
    sa.Column('target_ref', sa.String(length=200), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('audience', sa.String(length=10), nullable=False),
    sa.Column('raised_by', sa.String(length=36), nullable=False),
    sa.Column('assigned_to', sa.String(length=36), nullable=True),
    sa.Column('cleared_by', sa.String(length=36), nullable=True),
    sa.Column('cleared_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['engagement_id'], ['engagement.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_review_note_client_id'), 'review_note', ['client_id'], unique=False)
    op.create_index(op.f('ix_review_note_engagement_id'), 'review_note', ['engagement_id'], unique=False)
    op.create_index(op.f('ix_review_note_tenant_id'), 'review_note', ['tenant_id'], unique=False)
    op.create_table('run',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('engagement_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('input_version_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('input_hashes', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('started_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('finished_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('engine_version', sa.String(length=40), nullable=True),
    sa.Column('rule_set_version', sa.String(length=40), nullable=True),
    sa.Column('rule_set_hash', sa.String(length=64), nullable=True),
    sa.Column('config_hash', sa.String(length=64), nullable=True),
    sa.Column('params_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('idempotency_key', sa.String(length=120), nullable=True),
    sa.Column('result_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('output_version_id', sa.String(length=36), nullable=True),
    sa.Column('error_code', sa.String(length=60), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('correlation_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['engagement_id'], ['engagement.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'idempotency_key')
    )
    op.create_index(op.f('ix_run_client_id'), 'run', ['client_id'], unique=False)
    op.create_index(op.f('ix_run_engagement_id'), 'run', ['engagement_id'], unique=False)
    op.create_index(op.f('ix_run_tenant_id'), 'run', ['tenant_id'], unique=False)
    op.create_table('unlock_request',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('engagement_id', sa.String(length=36), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('requested_by', sa.String(length=36), nullable=False),
    sa.Column('partner_approved_by', sa.String(length=36), nullable=True),
    sa.Column('admin_approved_by', sa.String(length=36), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('completed_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['engagement_id'], ['engagement.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_unlock_request_client_id'), 'unlock_request', ['client_id'], unique=False)
    op.create_index(op.f('ix_unlock_request_engagement_id'), 'unlock_request', ['engagement_id'], unique=False)
    op.create_index(op.f('ix_unlock_request_tenant_id'), 'unlock_request', ['tenant_id'], unique=False)
    op.create_table('artefact',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('engagement_id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=True),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('object_key', sa.String(length=500), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('size', sa.BigInteger(), nullable=False),
    sa.Column('content_type', sa.String(length=120), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['engagement_id'], ['engagement.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['run.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_artefact_client_id'), 'artefact', ['client_id'], unique=False)
    op.create_index(op.f('ix_artefact_engagement_id'), 'artefact', ['engagement_id'], unique=False)
    op.create_index(op.f('ix_artefact_run_id'), 'artefact', ['run_id'], unique=False)
    op.create_index(op.f('ix_artefact_tenant_id'), 'artefact', ['tenant_id'], unique=False)
    op.create_table('file_version',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('engagement_id', sa.String(length=36), nullable=False),
    sa.Column('file_id', sa.String(length=36), nullable=False),
    sa.Column('version_no', sa.Integer(), nullable=False),
    sa.Column('version_line', sa.Integer(), nullable=False),
    sa.Column('object_key', sa.String(length=500), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('size', sa.BigInteger(), nullable=False),
    sa.Column('content_type', sa.String(length=120), nullable=False),
    sa.Column('original_name', sa.String(length=300), nullable=False),
    sa.Column('parent_version_id', sa.String(length=36), nullable=True),
    sa.Column('produced_by_run_id', sa.String(length=36), nullable=True),
    sa.Column('change_summary_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('worm_until', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('has_macros', sa.Boolean(), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['engagement_id'], ['engagement.id'], ),
    sa.ForeignKeyConstraint(['file_id'], ['file.id'], ),
    sa.ForeignKeyConstraint(['parent_version_id'], ['file_version.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('file_id', 'version_no')
    )
    op.create_index(op.f('ix_file_version_client_id'), 'file_version', ['client_id'], unique=False)
    op.create_index(op.f('ix_file_version_engagement_id'), 'file_version', ['engagement_id'], unique=False)
    op.create_index(op.f('ix_file_version_file_id'), 'file_version', ['file_id'], unique=False)
    op.create_index(op.f('ix_file_version_sha256'), 'file_version', ['sha256'], unique=False)
    op.create_index(op.f('ix_file_version_tenant_id'), 'file_version', ['tenant_id'], unique=False)
    op.create_table('finding',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('engagement_id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('fid', sa.String(length=20), nullable=False),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('rule_id', sa.String(length=20), nullable=False),
    sa.Column('rule_version', sa.String(length=20), nullable=False),
    sa.Column('family', sa.String(length=20), nullable=False),
    sa.Column('severity', sa.String(length=10), nullable=False),
    sa.Column('certainty', sa.String(length=10), nullable=False),
    sa.Column('category', sa.String(length=60), nullable=False),
    sa.Column('sheet', sa.String(length=120), nullable=True),
    sa.Column('cells', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('caption', sa.Text(), nullable=True),
    sa.Column('printed', sa.String(length=120), nullable=True),
    sa.Column('expected', sa.String(length=120), nullable=True),
    sa.Column('diff', sa.String(length=60), nullable=True),
    sa.Column('period', sa.String(length=20), nullable=True),
    sa.Column('evidence_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=False),
    sa.Column('suggested_fix', sa.Text(), nullable=True),
    sa.Column('fix_kind', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('status_reason', sa.Text(), nullable=True),
    sa.Column('assigned_to', sa.String(length=36), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('shared_with_client', sa.Boolean(), nullable=False),
    sa.Column('status_changed_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['engagement_id'], ['engagement.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['run.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_finding_client_id'), 'finding', ['client_id'], unique=False)
    op.create_index(op.f('ix_finding_engagement_id'), 'finding', ['engagement_id'], unique=False)
    op.create_index(op.f('ix_finding_fingerprint'), 'finding', ['fingerprint'], unique=False)
    op.create_index(op.f('ix_finding_rule_id'), 'finding', ['rule_id'], unique=False)
    op.create_index(op.f('ix_finding_run_id'), 'finding', ['run_id'], unique=False)
    op.create_index(op.f('ix_finding_severity'), 'finding', ['severity'], unique=False)
    op.create_index(op.f('ix_finding_tenant_id'), 'finding', ['tenant_id'], unique=False)
    op.create_table('job',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('queue', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('available_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('claimed_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('heartbeat_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('worker_id', sa.String(length=120), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('correlation_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['run.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_job_claim', 'job', ['status', 'queue', 'available_at'], unique=False)
    op.create_index(op.f('ix_job_client_id'), 'job', ['client_id'], unique=False)
    op.create_index(op.f('ix_job_run_id'), 'job', ['run_id'], unique=False)
    op.create_index(op.f('ix_job_tenant_id'), 'job', ['tenant_id'], unique=False)
    op.create_table('lineage_edge',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('from_ref', sa.String(length=300), nullable=False),
    sa.Column('to_ref', sa.String(length=300), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['run.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_lineage_edge_client_id'), 'lineage_edge', ['client_id'], unique=False)
    op.create_index(op.f('ix_lineage_edge_run_id'), 'lineage_edge', ['run_id'], unique=False)
    op.create_index(op.f('ix_lineage_edge_tenant_id'), 'lineage_edge', ['tenant_id'], unique=False)
    op.create_table('review_note_msg',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('review_note_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['review_note_id'], ['review_note.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_review_note_msg_client_id'), 'review_note_msg', ['client_id'], unique=False)
    op.create_index(op.f('ix_review_note_msg_review_note_id'), 'review_note_msg', ['review_note_id'], unique=False)
    op.create_index(op.f('ix_review_note_msg_tenant_id'), 'review_note_msg', ['tenant_id'], unique=False)
    op.create_table('run_event',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('ts', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('message', sa.String(length=300), nullable=False),
    sa.Column('pct', sa.Float(), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['run.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_run_event_client_id'), 'run_event', ['client_id'], unique=False)
    op.create_index(op.f('ix_run_event_run_id'), 'run_event', ['run_id'], unique=False)
    op.create_index(op.f('ix_run_event_tenant_id'), 'run_event', ['tenant_id'], unique=False)
    op.create_table('draft_release',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('engagement_id', sa.String(length=36), nullable=False),
    sa.Column('render_run_id', sa.String(length=36), nullable=False),
    sa.Column('pdf_version_id', sa.String(length=36), nullable=False),
    sa.Column('workbook_version_id', sa.String(length=36), nullable=False),
    sa.Column('released_by', sa.String(length=36), nullable=False),
    sa.Column('released_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('diff_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['engagement_id'], ['engagement.id'], ),
    sa.ForeignKeyConstraint(['pdf_version_id'], ['file_version.id'], ),
    sa.ForeignKeyConstraint(['render_run_id'], ['run.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.ForeignKeyConstraint(['workbook_version_id'], ['file_version.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_draft_release_client_id'), 'draft_release', ['client_id'], unique=False)
    op.create_index(op.f('ix_draft_release_engagement_id'), 'draft_release', ['engagement_id'], unique=False)
    op.create_index(op.f('ix_draft_release_tenant_id'), 'draft_release', ['tenant_id'], unique=False)
    op.create_table('file_lock',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('file_id', sa.String(length=36), nullable=False),
    sa.Column('version_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('acquired_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('expires_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('released_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['file_id'], ['file.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ),
    sa.ForeignKeyConstraint(['version_id'], ['file_version.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_file_lock_client_id'), 'file_lock', ['client_id'], unique=False)
    op.create_index(op.f('ix_file_lock_file_id'), 'file_lock', ['file_id'], unique=False)
    op.create_index(op.f('ix_file_lock_tenant_id'), 'file_lock', ['tenant_id'], unique=False)
    op.create_table('fix_plan',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('engagement_id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('plan_run_id', sa.String(length=36), nullable=True),
    sa.Column('apply_run_id', sa.String(length=36), nullable=True),
    sa.Column('base_version_id', sa.String(length=36), nullable=False),
    sa.Column('plan_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('plan_sha256', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('proposed_by', sa.String(length=36), nullable=False),
    sa.Column('approved_by', sa.String(length=36), nullable=True),
    sa.Column('approved_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('approved_plan_sha256', sa.String(length=64), nullable=True),
    sa.Column('approved_edit_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('has_balancing', sa.Boolean(), nullable=False),
    sa.Column('balancing_approved_by', sa.String(length=36), nullable=True),
    sa.Column('balancing_approved_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('rejection_reason', sa.Text(), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['base_version_id'], ['file_version.id'], ),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['engagement_id'], ['engagement.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['run.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_fix_plan_client_id'), 'fix_plan', ['client_id'], unique=False)
    op.create_index(op.f('ix_fix_plan_engagement_id'), 'fix_plan', ['engagement_id'], unique=False)
    op.create_index(op.f('ix_fix_plan_tenant_id'), 'fix_plan', ['tenant_id'], unique=False)
    op.create_table('narrative_edit',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('engagement_id', sa.String(length=36), nullable=False),
    sa.Column('base_version_id', sa.String(length=36), nullable=False),
    sa.Column('sheet', sa.String(length=120), nullable=False),
    sa.Column('cell', sa.String(length=20), nullable=False),
    sa.Column('old_text', sa.Text(), nullable=True),
    sa.Column('new_text', sa.Text(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('proposed_by', sa.String(length=36), nullable=False),
    sa.Column('approved_by', sa.String(length=36), nullable=True),
    sa.Column('approved_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('applied_run_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['base_version_id'], ['file_version.id'], ),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['engagement_id'], ['engagement.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_narrative_edit_client_id'), 'narrative_edit', ['client_id'], unique=False)
    op.create_index(op.f('ix_narrative_edit_engagement_id'), 'narrative_edit', ['engagement_id'], unique=False)
    op.create_index(op.f('ix_narrative_edit_tenant_id'), 'narrative_edit', ['tenant_id'], unique=False)
    op.create_table('share_link',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('engagement_id', sa.String(length=36), nullable=False),
    sa.Column('file_version_id', sa.String(length=36), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('recipient_user_id', sa.String(length=36), nullable=True),
    sa.Column('expires_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('used_at', fsweb.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['engagement_id'], ['engagement.id'], ),
    sa.ForeignKeyConstraint(['file_version_id'], ['file_version.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_share_link_client_id'), 'share_link', ['client_id'], unique=False)
    op.create_index(op.f('ix_share_link_tenant_id'), 'share_link', ['tenant_id'], unique=False)
    op.create_table('edit',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.String(length=36), nullable=False),
    sa.Column('fix_plan_id', sa.String(length=36), nullable=False),
    sa.Column('eid', sa.String(length=20), nullable=False),
    sa.Column('sheet', sa.String(length=120), nullable=False),
    sa.Column('cell', sa.String(length=20), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('grp', sa.String(length=20), nullable=False),
    sa.Column('fix_kind', sa.String(length=10), nullable=False),
    sa.Column('old_value', sa.Text(), nullable=True),
    sa.Column('new_value', sa.Text(), nullable=True),
    sa.Column('old_formula', sa.Text(), nullable=True),
    sa.Column('new_formula', sa.Text(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('approved', sa.Boolean(), nullable=False),
    sa.Column('applied', sa.Boolean(), nullable=False),
    sa.Column('applied_version_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', fsweb.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['fix_plan_id'], ['fix_plan.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_edit_client_id'), 'edit', ['client_id'], unique=False)
    op.create_index(op.f('ix_edit_fix_plan_id'), 'edit', ['fix_plan_id'], unique=False)
    op.create_index(op.f('ix_edit_tenant_id'), 'edit', ['tenant_id'], unique=False)

    _security_upgrade()


def downgrade() -> None:
    _security_downgrade()
    op.drop_index(op.f('ix_edit_tenant_id'), table_name='edit')
    op.drop_index(op.f('ix_edit_fix_plan_id'), table_name='edit')
    op.drop_index(op.f('ix_edit_client_id'), table_name='edit')
    op.drop_table('edit')
    op.drop_index(op.f('ix_share_link_tenant_id'), table_name='share_link')
    op.drop_index(op.f('ix_share_link_client_id'), table_name='share_link')
    op.drop_table('share_link')
    op.drop_index(op.f('ix_narrative_edit_tenant_id'), table_name='narrative_edit')
    op.drop_index(op.f('ix_narrative_edit_engagement_id'), table_name='narrative_edit')
    op.drop_index(op.f('ix_narrative_edit_client_id'), table_name='narrative_edit')
    op.drop_table('narrative_edit')
    op.drop_index(op.f('ix_fix_plan_tenant_id'), table_name='fix_plan')
    op.drop_index(op.f('ix_fix_plan_engagement_id'), table_name='fix_plan')
    op.drop_index(op.f('ix_fix_plan_client_id'), table_name='fix_plan')
    op.drop_table('fix_plan')
    op.drop_index(op.f('ix_file_lock_tenant_id'), table_name='file_lock')
    op.drop_index(op.f('ix_file_lock_file_id'), table_name='file_lock')
    op.drop_index(op.f('ix_file_lock_client_id'), table_name='file_lock')
    op.drop_table('file_lock')
    op.drop_index(op.f('ix_draft_release_tenant_id'), table_name='draft_release')
    op.drop_index(op.f('ix_draft_release_engagement_id'), table_name='draft_release')
    op.drop_index(op.f('ix_draft_release_client_id'), table_name='draft_release')
    op.drop_table('draft_release')
    op.drop_index(op.f('ix_run_event_tenant_id'), table_name='run_event')
    op.drop_index(op.f('ix_run_event_run_id'), table_name='run_event')
    op.drop_index(op.f('ix_run_event_client_id'), table_name='run_event')
    op.drop_table('run_event')
    op.drop_index(op.f('ix_review_note_msg_tenant_id'), table_name='review_note_msg')
    op.drop_index(op.f('ix_review_note_msg_review_note_id'), table_name='review_note_msg')
    op.drop_index(op.f('ix_review_note_msg_client_id'), table_name='review_note_msg')
    op.drop_table('review_note_msg')
    op.drop_index(op.f('ix_lineage_edge_tenant_id'), table_name='lineage_edge')
    op.drop_index(op.f('ix_lineage_edge_run_id'), table_name='lineage_edge')
    op.drop_index(op.f('ix_lineage_edge_client_id'), table_name='lineage_edge')
    op.drop_table('lineage_edge')
    op.drop_index(op.f('ix_job_tenant_id'), table_name='job')
    op.drop_index(op.f('ix_job_run_id'), table_name='job')
    op.drop_index(op.f('ix_job_client_id'), table_name='job')
    op.drop_index('ix_job_claim', table_name='job')
    op.drop_table('job')
    op.drop_index(op.f('ix_finding_tenant_id'), table_name='finding')
    op.drop_index(op.f('ix_finding_severity'), table_name='finding')
    op.drop_index(op.f('ix_finding_run_id'), table_name='finding')
    op.drop_index(op.f('ix_finding_rule_id'), table_name='finding')
    op.drop_index(op.f('ix_finding_fingerprint'), table_name='finding')
    op.drop_index(op.f('ix_finding_engagement_id'), table_name='finding')
    op.drop_index(op.f('ix_finding_client_id'), table_name='finding')
    op.drop_table('finding')
    op.drop_index(op.f('ix_file_version_tenant_id'), table_name='file_version')
    op.drop_index(op.f('ix_file_version_sha256'), table_name='file_version')
    op.drop_index(op.f('ix_file_version_file_id'), table_name='file_version')
    op.drop_index(op.f('ix_file_version_engagement_id'), table_name='file_version')
    op.drop_index(op.f('ix_file_version_client_id'), table_name='file_version')
    op.drop_table('file_version')
    op.drop_index(op.f('ix_artefact_tenant_id'), table_name='artefact')
    op.drop_index(op.f('ix_artefact_run_id'), table_name='artefact')
    op.drop_index(op.f('ix_artefact_engagement_id'), table_name='artefact')
    op.drop_index(op.f('ix_artefact_client_id'), table_name='artefact')
    op.drop_table('artefact')
    op.drop_index(op.f('ix_unlock_request_tenant_id'), table_name='unlock_request')
    op.drop_index(op.f('ix_unlock_request_engagement_id'), table_name='unlock_request')
    op.drop_index(op.f('ix_unlock_request_client_id'), table_name='unlock_request')
    op.drop_table('unlock_request')
    op.drop_index(op.f('ix_run_tenant_id'), table_name='run')
    op.drop_index(op.f('ix_run_engagement_id'), table_name='run')
    op.drop_index(op.f('ix_run_client_id'), table_name='run')
    op.drop_table('run')
    op.drop_index(op.f('ix_review_note_tenant_id'), table_name='review_note')
    op.drop_index(op.f('ix_review_note_engagement_id'), table_name='review_note')
    op.drop_index(op.f('ix_review_note_client_id'), table_name='review_note')
    op.drop_table('review_note')
    op.drop_index(op.f('ix_mgmt_item_tenant_id'), table_name='mgmt_item')
    op.drop_index(op.f('ix_mgmt_item_engagement_id'), table_name='mgmt_item')
    op.drop_index(op.f('ix_mgmt_item_client_id'), table_name='mgmt_item')
    op.drop_table('mgmt_item')
    op.drop_index(op.f('ix_file_tenant_id'), table_name='file')
    op.drop_index(op.f('ix_file_engagement_id'), table_name='file')
    op.drop_index(op.f('ix_file_client_id'), table_name='file')
    op.drop_table('file')
    op.drop_index(op.f('ix_disclosure_checklist_tenant_id'), table_name='disclosure_checklist')
    op.drop_index(op.f('ix_disclosure_checklist_engagement_id'), table_name='disclosure_checklist')
    op.drop_index(op.f('ix_disclosure_checklist_client_id'), table_name='disclosure_checklist')
    op.drop_table('disclosure_checklist')
    op.drop_index(op.f('ix_config_tenant_id'), table_name='config')
    op.drop_index(op.f('ix_config_engagement_id'), table_name='config')
    op.drop_index(op.f('ix_config_client_id'), table_name='config')
    op.drop_table('config')
    op.drop_index(op.f('ix_ai_request_tenant_id'), table_name='ai_request')
    op.drop_index(op.f('ix_ai_request_engagement_id'), table_name='ai_request')
    op.drop_index(op.f('ix_ai_request_client_id'), table_name='ai_request')
    op.drop_table('ai_request')
    op.drop_index(op.f('ix_signatory_tenant_id'), table_name='signatory')
    op.drop_index(op.f('ix_signatory_entity_id'), table_name='signatory')
    op.drop_index(op.f('ix_signatory_client_id'), table_name='signatory')
    op.drop_table('signatory')
    op.drop_index(op.f('ix_rule_set_member_tenant_id'), table_name='rule_set_member')
    op.drop_table('rule_set_member')
    op.drop_index(op.f('ix_mapping_version_tenant_id'), table_name='mapping_version')
    op.drop_index(op.f('ix_mapping_version_mapping_id'), table_name='mapping_version')
    op.drop_index(op.f('ix_mapping_version_client_id'), table_name='mapping_version')
    op.drop_table('mapping_version')
    op.drop_index(op.f('ix_fx_rate_tenant_id'), table_name='fx_rate')
    op.drop_index(op.f('ix_fx_rate_entity_id'), table_name='fx_rate')
    op.drop_index(op.f('ix_fx_rate_client_id'), table_name='fx_rate')
    op.drop_table('fx_rate')
    op.drop_index(op.f('ix_engagement_tenant_id'), table_name='engagement')
    op.drop_index(op.f('ix_engagement_entity_id'), table_name='engagement')
    op.drop_index(op.f('ix_engagement_client_id'), table_name='engagement')
    op.drop_table('engagement')
    op.drop_index(op.f('ix_user_session_user_id'), table_name='user_session')
    op.drop_index(op.f('ix_user_session_tenant_id'), table_name='user_session')
    op.drop_table('user_session')
    op.drop_index(op.f('ix_user_client_role_user_id'), table_name='user_client_role')
    op.drop_index(op.f('ix_user_client_role_tenant_id'), table_name='user_client_role')
    op.drop_index(op.f('ix_user_client_role_client_id'), table_name='user_client_role')
    op.drop_table('user_client_role')
    op.drop_index(op.f('ix_template_version_tenant_id'), table_name='template_version')
    op.drop_index(op.f('ix_template_version_template_id'), table_name='template_version')
    op.drop_table('template_version')
    op.drop_index(op.f('ix_rule_version_tenant_id'), table_name='rule_version')
    op.drop_index(op.f('ix_rule_version_rule_id'), table_name='rule_version')
    op.drop_table('rule_version')
    op.drop_index(op.f('ix_role_permission_tenant_id'), table_name='role_permission')
    op.drop_table('role_permission')
    op.drop_index(op.f('ix_recovery_code_user_id'), table_name='recovery_code')
    op.drop_index(op.f('ix_recovery_code_tenant_id'), table_name='recovery_code')
    op.drop_table('recovery_code')
    op.drop_index(op.f('ix_password_reset_user_id'), table_name='password_reset')
    op.drop_index(op.f('ix_password_reset_tenant_id'), table_name='password_reset')
    op.drop_table('password_reset')
    op.drop_index(op.f('ix_mapping_tenant_id'), table_name='mapping')
    op.drop_index(op.f('ix_mapping_client_id'), table_name='mapping')
    op.drop_table('mapping')
    op.drop_index(op.f('ix_invite_user_id'), table_name='invite')
    op.drop_index(op.f('ix_invite_tenant_id'), table_name='invite')
    op.drop_table('invite')
    op.drop_index(op.f('ix_entity_tenant_id'), table_name='entity')
    op.drop_index(op.f('ix_entity_client_id'), table_name='entity')
    op.drop_table('entity')
    op.drop_index(op.f('ix_elevation_tenant_id'), table_name='elevation')
    op.drop_table('elevation')
    op.drop_index(op.f('ix_billing_usage_tenant_id'), table_name='billing_usage')
    op.drop_index(op.f('ix_billing_usage_client_id'), table_name='billing_usage')
    op.drop_table('billing_usage')
    op.drop_index(op.f('ix_ai_eval_run_tenant_id'), table_name='ai_eval_run')
    op.drop_table('ai_eval_run')
    op.drop_index(op.f('ix_template_tenant_id'), table_name='template')
    op.drop_table('template')
    op.drop_index(op.f('ix_rule_set_tenant_id'), table_name='rule_set')
    op.drop_table('rule_set')
    op.drop_index(op.f('ix_rule_tenant_id'), table_name='rule')
    op.drop_table('rule')
    op.drop_index(op.f('ix_role_tenant_id'), table_name='role')
    op.drop_table('role')
    op.drop_index(op.f('ix_release_tenant_id'), table_name='release')
    op.drop_table('release')
    op.drop_index(op.f('ix_policy_tenant_id'), table_name='policy')
    op.drop_table('policy')
    op.drop_index(op.f('ix_incident_tenant_id'), table_name='incident')
    op.drop_table('incident')
    op.drop_index(op.f('ix_email_outbox_tenant_id'), table_name='email_outbox')
    op.drop_table('email_outbox')
    op.drop_index(op.f('ix_dsr_request_tenant_id'), table_name='dsr_request')
    op.drop_table('dsr_request')
    op.drop_index(op.f('ix_client_tenant_id'), table_name='client')
    op.drop_table('client')
    op.drop_index(op.f('ix_backup_log_tenant_id'), table_name='backup_log')
    op.drop_table('backup_log')
    op.drop_index(op.f('ix_app_user_tenant_id'), table_name='app_user')
    op.drop_table('app_user')
    op.drop_index(op.f('ix_ai_prompt_version_tenant_id'), table_name='ai_prompt_version')
    op.drop_table('ai_prompt_version')
    op.drop_index(op.f('ix_access_review_tenant_id'), table_name='access_review')
    op.drop_index(op.f('ix_access_review_client_id'), table_name='access_review')
    op.drop_table('access_review')
    op.drop_table('tenant')
    op.drop_table('render_worker')
    op.drop_table('permission')
    op.drop_table('data_key')
    op.drop_table('audit_head')
    op.drop_index('ix_audit_tenant_ts', table_name='audit_event')
    op.drop_index(op.f('ix_audit_event_tenant_id'), table_name='audit_event')
    op.drop_index(op.f('ix_audit_event_engagement_id'), table_name='audit_event')
    op.drop_index(op.f('ix_audit_event_client_id'), table_name='audit_event')
    op.drop_index(op.f('ix_audit_event_action'), table_name='audit_event')
    op.drop_table('audit_event')


# ---------------------------------------------------------------------------
# Security layer: PostgreSQL row-level security (defence in depth behind the
# API's RBAC / ABAC checks), the append-only audit trail and runtime grants.
# The application sets app.tenant_id / app.user_id / app.scope per transaction.
# ---------------------------------------------------------------------------

AUTH_TABLES = ["app_user", "user_session", "invite", "password_reset", "recovery_code"]
TENANT_TABLES = [
    "role", "role_permission", "user_client_role", "elevation", "email_outbox", "backup_log", "rule", "rule_version",
    "rule_set", "rule_set_member", "template", "template_version", "policy", "access_review", "dsr_request", "incident",
    "ai_prompt_version", "ai_eval_run", "release", "audit_head", "data_key",
]
# Master data (and approval objects) visible tenant-wide to a Firm Admin (scope 'firm') and to assigned users.
FIRM_VISIBLE_TABLES = ["entity", "signatory", "engagement", "ai_request", "unlock_request", "billing_usage"]
# Client content: only users assigned to the client.
CLIENT_TABLES = [
    "config", "file", "file_version", "file_lock", "share_link", "run", "job", "run_event", "artefact", "finding", "fix_plan",
    "edit", "narrative_edit", "review_note", "review_note_msg", "draft_release", "mgmt_item", "disclosure_checklist",
    "mapping", "mapping_version", "fx_rate", "lineage_edge",
]

SYSTEM = "fsweb_scope() = 'system'"
TENANT_OK = "tenant_id = fsweb_tenant()"


def _app_role() -> str:
    import os
    import re

    role = os.environ.get("FSWEB_DB_APP_ROLE", "fsweb_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError("invalid FSWEB_DB_APP_ROLE")
    return role


def _policy(table: str, using: str, check: str | None = None) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY fsweb_rls ON {table} USING ({using}) WITH CHECK ({check or using})")


def _security_upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        op.execute("CREATE TRIGGER IF NOT EXISTS audit_event_no_update BEFORE UPDATE ON audit_event BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END")
        op.execute("CREATE TRIGGER IF NOT EXISTS audit_event_no_delete BEFORE DELETE ON audit_event BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END")
        return
    op.execute("CREATE OR REPLACE FUNCTION fsweb_scope() RETURNS text LANGUAGE sql STABLE AS $$ SELECT coalesce(current_setting('app.scope', true), '') $$")
    op.execute("CREATE OR REPLACE FUNCTION fsweb_tenant() RETURNS text LANGUAGE sql STABLE AS $$ SELECT coalesce(current_setting('app.tenant_id', true), '') $$")
    op.execute("CREATE OR REPLACE FUNCTION fsweb_user() RETURNS text LANGUAGE sql STABLE AS $$ SELECT coalesce(current_setting('app.user_id', true), '') $$")
    op.execute(
        "CREATE OR REPLACE FUNCTION fsweb_member(cid text) RETURNS boolean LANGUAGE sql STABLE AS $$ "
        "SELECT EXISTS (SELECT 1 FROM user_client_role ucr WHERE ucr.client_id = cid AND ucr.user_id = fsweb_user() "
        "AND ucr.revoked_at IS NULL AND (ucr.expires_at IS NULL OR ucr.expires_at > now())) $$"
    )
    _policy("tenant", f"{SYSTEM} OR fsweb_scope() = 'auth' OR id = fsweb_tenant()")
    for t in AUTH_TABLES:
        _policy(t, f"{SYSTEM} OR fsweb_scope() = 'auth' OR {TENANT_OK}")
    for t in TENANT_TABLES:
        _policy(t, f"{SYSTEM} OR {TENANT_OK}")
    _policy("client", f"{SYSTEM} OR ({TENANT_OK} AND (fsweb_scope() = 'firm' OR fsweb_member(id)))", f"{SYSTEM} OR {TENANT_OK}")
    for t in FIRM_VISIBLE_TABLES:
        _policy(t, f"{SYSTEM} OR ({TENANT_OK} AND (fsweb_scope() = 'firm' OR fsweb_member(client_id)))", f"{SYSTEM} OR {TENANT_OK}")
    for t in CLIENT_TABLES:
        _policy(t, f"{SYSTEM} OR ({TENANT_OK} AND fsweb_member(client_id))",
                f"{SYSTEM} OR ({TENANT_OK} AND (fsweb_scope() = 'firm' OR fsweb_member(client_id)))")
    _policy("audit_event", f"{SYSTEM} OR ({TENANT_OK} AND (fsweb_scope() = 'firm' OR client_id IS NULL OR fsweb_member(client_id)))",
            f"{SYSTEM} OR {TENANT_OK}")
    # 06 §4: append-only audit trail, even for the table owner.
    op.execute(
        "CREATE OR REPLACE FUNCTION fsweb_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'audit_event is append-only (06 §4)'; END $$"
    )
    op.execute("CREATE TRIGGER audit_event_no_update BEFORE UPDATE OR DELETE ON audit_event FOR EACH ROW EXECUTE FUNCTION fsweb_audit_immutable()")
    op.execute("CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON audit_event FOR EACH STATEMENT EXECUTE FUNCTION fsweb_audit_immutable()")
    role = _app_role()
    op.execute(
        f"""DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT USAGE ON SCHEMA public TO {role};
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role};
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role};
            REVOKE UPDATE, DELETE, TRUNCATE ON audit_event FROM {role};
            REVOKE ALL ON alembic_version FROM {role};
            GRANT EXECUTE ON FUNCTION fsweb_scope(), fsweb_tenant(), fsweb_user(), fsweb_member(text) TO {role};
          END IF;
        END $$"""  # noqa: S608  # nosec B608 - the role name is validated against a strict pattern above
    )


def _security_downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS audit_event_no_update ON audit_event")
    op.execute("DROP TRIGGER IF EXISTS audit_event_no_truncate ON audit_event")
    for t in ["tenant", "client", "audit_event", *AUTH_TABLES, *TENANT_TABLES, *FIRM_VISIBLE_TABLES, *CLIENT_TABLES]:
        op.execute(f"DROP POLICY IF EXISTS fsweb_rls ON {t}")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    for fn in ("fsweb_member(text)", "fsweb_scope()", "fsweb_tenant()", "fsweb_user()", "fsweb_audit_immutable()"):
        op.execute(f"DROP FUNCTION IF EXISTS {fn} CASCADE")
