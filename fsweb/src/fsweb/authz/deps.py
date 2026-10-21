"""Request authentication and authorisation (05 §3: checks run server-side on every request).

``require_principal`` validates the session cookie (idle/absolute timeouts, MFA,
CSRF for unsafe methods) and binds the RLS context. ``authorize`` applies the RBAC
matrix for the client concerned, segregation-of-duties conditions and the ABAC
policy; every denial is written to the audit trail in its own transaction.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from fsweb import audit
from fsweb.authz import abac
from fsweb.authz.permissions import (
    ACTION_CLASS,
    DENY,
    NOT_OWN,
    POLICY,
    ROLE_SETTING,
    strongest,
)
from fsweb.db import AUTH, RLSContext, set_rls, utcnow
from fsweb.errors import ApiError, bad_request, forbidden, unauthorized
from fsweb.models import Client, Engagement, Policy, Role, RolePermission, User, UserClientRole, UserSession
from fsweb.security.crypto import consteq, hash_token
from fsweb.state import AppState

SESSION_COOKIE = "fs_session"
CSRF_COOKIE = "fs_csrf"
CSRF_HEADER = "X-CSRF-Token"
REASON_HEADER = "X-Action-Reason"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_state(request: Request) -> AppState:
    return request.app.state.fs  # type: ignore[no-any-return]


def get_db(request: Request) -> Iterator[Session]:
    st = get_state(request)
    s = st.db.session(AUTH)
    request.state.db = s
    try:
        yield s
    finally:
        try:
            if s.in_transaction():
                s.rollback()
        finally:
            s.close()


def client_ip(request: Request) -> str | None:
    st = get_state(request)
    if st.settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


@dataclass
class Principal:
    tenant_id: str
    user_id: str
    session_id: str
    email: str
    name: str
    is_firm_user: bool
    is_break_glass: bool
    mfa_verified: bool
    stepup_at: datetime | None
    roles: dict[str | None, set[str]] = field(default_factory=dict)  # client_id -> role codes; None = tenant-wide
    ip: str | None = None
    user_agent: str | None = None
    correlation_id: str | None = None
    download_override: bool | None = None

    @property
    def tenant_roles(self) -> set[str]:
        return set(self.roles.get(None, set()))

    @property
    def all_roles(self) -> set[str]:
        out: set[str] = set()
        for v in self.roles.values():
            out |= v
        return out

    def roles_for(self, client_id: str | None) -> set[str]:
        if client_id is None:
            return self.all_roles
        return self.tenant_roles | self.roles.get(client_id, set())

    def is_member(self, client_id: str) -> bool:
        return bool(self.roles.get(client_id))

    @property
    def client_ids(self) -> list[str]:
        return [c for c in self.roles if c is not None and self.roles[c]]

    @property
    def is_firm_admin(self) -> bool:
        return "firm_admin" in self.tenant_roles

    @property
    def is_system_admin(self) -> bool:
        return "system_admin" in self.tenant_roles

    @property
    def is_client_user(self) -> bool:
        return not self.is_firm_user

    def rls(self) -> RLSContext:
        return RLSContext(self.tenant_id, self.user_id, "firm" if self.is_firm_admin else "user")

    def actor(self) -> audit.Actor:
        roles = ",".join(sorted(self.all_roles)) or None
        return audit.Actor(self.tenant_id, self.user_id, roles, self.ip, self.user_agent, self.correlation_id)


def load_roles(db: Session, user_id: str) -> dict[str | None, set[str]]:
    now = utcnow()
    rows = db.execute(
        select(UserClientRole.client_id, Role.code, UserClientRole.expires_at)
        .join(Role, Role.id == UserClientRole.role_id)
        .where(UserClientRole.user_id == user_id, UserClientRole.revoked_at.is_(None))
    ).all()
    out: dict[str | None, set[str]] = {}
    for client_id, code, expires_at in rows:
        if expires_at is not None and expires_at <= now:
            continue
        out.setdefault(client_id, set()).add(code)
    return out


def _session_from_request(request: Request, db: Session, *, require_mfa: bool) -> tuple[UserSession, User]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise unauthorized()
    sess = db.execute(select(UserSession).where(UserSession.token_hash == hash_token(token))).scalar_one_or_none()
    now = utcnow()
    if sess is None or sess.revoked_at is not None:
        raise unauthorized("session expired or signed out", "session_invalid")
    # From here on the tenant is known: bind it so RLS exposes the tenant's role tables.
    set_rls(db, RLSContext(sess.tenant_id, sess.user_id, "auth"))
    if now >= sess.expires_at:
        raise unauthorized("session reached its absolute limit; sign in again", "session_expired")
    if now - sess.last_seen_at > timedelta(minutes=sess.idle_minutes):
        sess.revoked_at = now
        db.commit()
        raise unauthorized("session timed out after inactivity; sign in again", "session_idle_timeout")
    user = db.get(User, sess.user_id)
    if user is None or user.status != "active":
        raise unauthorized("account is disabled", "account_disabled")
    if require_mfa and not sess.mfa_verified:
        raise unauthorized("multi-factor verification required", "mfa_required")
    if request.method not in SAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        if not header or not consteq(hash_token(header), sess.csrf_hash):
            raise forbidden("missing or invalid CSRF token", "csrf_failed")
    return sess, user


def _principal(request: Request, db: Session, *, require_mfa: bool) -> Principal:
    st = get_state(request)
    sess, user = _session_from_request(request, db, require_mfa=require_mfa)
    roles = load_roles(db, user.id)
    p = Principal(
        tenant_id=user.tenant_id,
        user_id=user.id,
        session_id=sess.id,
        email=user.email,
        name=user.name,
        is_firm_user=user.is_firm_user,
        is_break_glass=user.is_break_glass,
        mfa_verified=sess.mfa_verified,
        stepup_at=sess.stepup_at,
        roles=roles,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        correlation_id=getattr(request.state, "correlation_id", None),
        download_override=user.download_allowed,
    )
    now = utcnow()
    if (now - sess.last_seen_at).total_seconds() > 30:
        sess.last_seen_at = now
    set_rls(db, p.rls())
    db.commit()
    request.state.principal = p
    request.state.user_id = p.user_id
    _ = st
    return p


def require_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    return _principal(request, db, require_mfa=True)


def require_pre_mfa(request: Request, db: Session = Depends(get_db)) -> Principal:
    return _principal(request, db, require_mfa=False)


# ------------------------------------------------------------------ matrix cache

_MATRIX_TTL = 30.0
_matrix_cache: dict[str, tuple[float, dict[str, dict[str, str]], dict[str, bool]]] = {}


def invalidate_matrix(tenant_id: str | None = None) -> None:
    if tenant_id is None:
        _matrix_cache.clear()
    else:
        _matrix_cache.pop(tenant_id, None)


def role_matrix(db: Session, tenant_id: str) -> tuple[dict[str, dict[str, str]], dict[str, bool]]:
    hit = _matrix_cache.get(tenant_id)
    if hit and time.monotonic() - hit[0] < _MATRIX_TTL:
        return hit[1], hit[2]
    matrix: dict[str, dict[str, str]] = {}
    downloads: dict[str, bool] = {}
    for code, dl in db.execute(select(Role.code, Role.download_allowed).where(Role.tenant_id == tenant_id)).all():
        matrix.setdefault(code, {})
        downloads[code] = bool(dl)
    rows = db.execute(
        select(Role.code, RolePermission.permission_code, RolePermission.value)
        .join(Role, Role.id == RolePermission.role_id)
        .where(Role.tenant_id == tenant_id)
    ).all()
    for code, perm, value in rows:
        matrix.setdefault(code, {})[perm] = value
    _matrix_cache[tenant_id] = (time.monotonic(), matrix, downloads)
    return matrix, downloads


def active_policy_text(db: Session, tenant_id: str, name: str = "abac") -> tuple[str, int]:
    row = db.execute(
        select(Policy).where(Policy.tenant_id == tenant_id, Policy.name == name, Policy.active.is_(True)).order_by(Policy.version.desc())
    ).scalars().first()
    if row is None:
        return abac.default_policy_text(), 1
    return row.body, row.version


# ------------------------------------------------------------------ decisions


def deny(request: Request, db: Session, p: Principal, permission: str, reason: str, *, client_id: str | None = None,
         engagement_id: str | None = None, rule: str | None = None, code: str = "forbidden") -> ApiError:
    """Record the denial in its own transaction (it must survive the failed request)."""
    db.rollback()
    set_rls(db, p.rls())
    audit.append(db, p.actor(), "authz.denied", client_id=client_id, engagement_id=engagement_id, outcome="denied",
                 details={"permission": permission, "reason": reason, "rule": rule, "path": request.url.path, "method": request.method})
    db.commit()
    return forbidden(reason, code, permission=permission, rule=rule)


def permission_value(db: Session, p: Principal, permission: str, client_id: str | None) -> str:
    matrix, _ = role_matrix(db, p.tenant_id)
    roles = p.roles_for(client_id)
    return strongest([matrix.get(r, {}).get(permission, DENY) for r in roles])


def authorize(
    request: Request,
    db: Session,
    p: Principal,
    permission: str,
    *,
    client_id: str | None = None,
    engagement: Engagement | None = None,
    owner_id: str | None = None,
    attrs: dict[str, Any] | None = None,
) -> str:
    """Return the matrix value granted (``allow``, ``maker_checker``, …) or raise 403."""
    if engagement is not None:
        client_id = engagement.client_id
    eng_id = engagement.id if engagement is not None else None
    value = permission_value(db, p, permission, client_id)
    if value == DENY:
        raise deny(request, db, p, permission, f"your role does not permit {permission}", client_id=client_id, engagement_id=eng_id)
    if value == NOT_OWN and owner_id is not None and owner_id == p.user_id:
        raise deny(request, db, p, permission, "segregation of duties: you cannot approve your own item", client_id=client_id,
                   engagement_id=eng_id, code="segregation_of_duties")
    st = get_state(request)
    _, downloads = role_matrix(db, p.tenant_id)
    download_allowed = p.download_override if p.download_override is not None else any(downloads.get(r, False) for r in p.roles_for(client_id))
    if value == ROLE_SETTING and not download_allowed:
        raise deny(request, db, p, permission, "downloads are disabled for your role", client_id=client_id, engagement_id=eng_id)
    if value == POLICY:
        tenant_settings = _tenant_settings(db, p.tenant_id)
        if not tenant_settings.get("firm_admin_may_approve_ai", True):
            raise deny(request, db, p, permission, "the AI policy does not allow Firm Admin approvals", client_id=client_id, engagement_id=eng_id)

    a: dict[str, Any] = {
        "action": permission,
        "action_class": ACTION_CLASS.get(permission, "write"),
        "user_type": "firm" if p.is_firm_user else "client",
        "roles": sorted(p.roles_for(client_id)),
        "network": abac.network_of(p.ip, st.settings.office_cidrs),
        "office_networks_configured": bool(st.settings.office_cidrs),
        "download_allowed": bool(download_allowed),
        "client_scoped": client_id is not None,
        "system_admin_only": p.is_system_admin and not (p.all_roles - {"system_admin"}),
    }
    if client_id is not None:
        a["client_assigned"] = p.is_member(client_id) or bool(p.tenant_roles - {"system_admin"})
        client = db.get(Client, client_id)
        if client is not None:
            a["classification"] = client.classification
    if engagement is not None:
        a["engagement_status"] = engagement.status
        a["engagement_quarantine"] = bool(engagement.is_quarantine)
    if attrs:
        a.update(attrs)
    text, version = active_policy_text(db, p.tenant_id)
    decision = abac.evaluate(text, a)
    if not decision.allowed:
        raise deny(request, db, p, permission, decision.reason or f"denied by policy rule {decision.rule_id}", client_id=client_id,
                   engagement_id=eng_id, rule=decision.rule_id, code="policy_denied")
    request.state.policy_version = version
    return value


def _tenant_settings(db: Session, tenant_id: str) -> dict[str, Any]:
    from fsweb.models import Tenant

    t = db.get(Tenant, tenant_id)
    return dict(t.settings_json or {}) if t else {}


def require_stepup(request: Request, p: Principal) -> str:
    """Privileged actions: TOTP re-verified within the step-up window, plus a reason (E-SEC-03)."""
    st = get_state(request)
    if p.stepup_at is None or utcnow() - p.stepup_at > timedelta(minutes=st.settings.stepup_minutes):
        raise forbidden("re-enter your MFA code to confirm this administrative action", "step_up_required")
    reason = (request.headers.get(REASON_HEADER) or "").strip()
    if len(reason) < 5:
        raise bad_request("reason_required", f"administrative actions need a reason in the {REASON_HEADER} header")
    return reason


def check_if_match(request: Request, version: int) -> None:
    """Optimistic locking (E-DATA-03): If-Match must carry the current ETag when supplied."""
    header = request.headers.get("if-match")
    if header is None:
        return
    from fsweb.errors import precondition_failed

    if header.strip().strip('W/').strip('"') != str(version):
        raise precondition_failed()


def etag(version: int) -> str:
    return f'W/"{version}"'
