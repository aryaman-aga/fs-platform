"""Login, MFA, step-up, sessions, invites, password reset and OIDC SSO (05 §1, E-SEC-01/03)."""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from fsweb import audit, schemas
from fsweb.authz.deps import (
    Principal,
    client_ip,
    get_db,
    get_state,
    permission_value,
    require_pre_mfa,
    require_principal,
)
from fsweb.authz.permissions import PERMISSION_CODES
from fsweb.db import RLSContext, set_rls, utcnow
from fsweb.errors import ApiError, bad_request, conflict, not_found, too_many, unauthorized
from fsweb.models import (
    Client,
    Incident,
    Invite,
    PasswordReset,
    Policy,
    RecoveryCode,
    Role,
    Tenant,
    User,
    UserClientRole,
    UserSession,
)
from fsweb.security import passwords, sessions, totp
from fsweb.security.crypto import hash_token, new_token
from fsweb.security.oidc import OIDCClient, OIDCError, pkce_pair
from fsweb.services.common import notify, users_with_role

router = APIRouter(tags=["auth"])
seclog = logging.getLogger("fsweb.security")


def _actor_for(user: User, request: Request) -> audit.Actor:
    return audit.Actor(user.tenant_id, user.id, None, client_ip(request), request.headers.get("user-agent"),
                       getattr(request.state, "correlation_id", None))


def _breakglass_alert(db: Session, user: User, request: Request) -> None:
    """Sealed break-glass account used: audit + incident + alert the Firm Admins (E-SEC-03)."""
    audit.append(db, _actor_for(user, request), "auth.breakglass_used", object_type="user", object_id=user.id, details={"severity": "alert"})
    db.add(Incident(tenant_id=user.tenant_id, severity="high", title="Break-glass account used", detected_at=utcnow(),
                    description="The sealed break-glass account signed in. Confirm the emergency and reseal the account.",
                    source="breakglass", certin_reportable=False))
    for admin in users_with_role(db, user.tenant_id, "firm_admin"):
        notify(db, user.tenant_id, admin.email, "ALERT: break-glass account used", "The break-glass account signed in. Review the audit trail.", "alert")


@router.post("/auth/login", response_model=schemas.LoginOut, responses={401: {"model": schemas.ErrorBody}})
def login(body: schemas.LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> schemas.LoginOut:
    st = get_state(request)
    ip = client_ip(request) or "unknown"
    email = body.email.lower()
    if not st.limiter.hit(f"login:ip:{ip}", st.settings.login_rate_per_minute, 60) or not st.limiter.hit(f"login:email:{email}", 10, 60):
        raise too_many("too many sign-in attempts; wait a minute")
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    now = utcnow()
    if user is not None:
        set_rls(db, RLSContext(user.tenant_id, user.id, "auth"))
    if user is not None and user.locked_until is not None and user.locked_until > now:
        audit.append(db, _actor_for(user, request), "auth.login_failed", object_type="user", object_id=user.id, outcome="denied",
                     details={"reason": "locked"})
        db.commit()
        raise ApiError(401, "account_locked", "account temporarily locked after repeated failures; try again later", headers={"Retry-After": "900"})
    ok = passwords.verify_password(user.password_hash if user else None, body.password)
    if user is None or not ok or user.status != "active":
        if user is not None:
            user.failed_logins += 1
            details = {"reason": "bad_password" if not ok else f"status_{user.status}", "failures": user.failed_logins}
            if user.failed_logins >= st.settings.lockout_threshold:
                user.locked_until = now + timedelta(minutes=st.settings.lockout_minutes)
                user.failed_logins = 0
                audit.append(db, _actor_for(user, request), "auth.locked", object_type="user", object_id=user.id, outcome="denied")
            audit.append(db, _actor_for(user, request), "auth.login_failed", object_type="user", object_id=user.id, outcome="denied", details=details)
            db.commit()
        else:
            seclog.info("login failed for unknown account", extra={"ip": ip})
        raise unauthorized("invalid e-mail or password", "invalid_credentials")
    user.failed_logins = 0
    if user.password_hash and passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(body.password)
    sess, token, csrf = sessions.create_session(db, st.settings, user, method="breakglass" if user.is_break_glass else "password",
                                                mfa_verified=False, ip=ip, user_agent=request.headers.get("user-agent"))
    if user.is_break_glass:
        _breakglass_alert(db, user, request)
    audit.append(db, _actor_for(user, request), "auth.password_ok", object_type="session", object_id=sess.id)
    db.commit()
    sessions.set_cookies(response, st.settings, token, csrf, sess)
    status = "mfa_required" if user.totp_secret_enc else "mfa_enrollment_required"
    return schemas.LoginOut(status=status, csrf_token=csrf, expires_at=sess.expires_at)


def _verify_code(db: Session, request: Request, user: User, code: str, *, allow_recovery: bool) -> str | None:
    st = get_state(request)
    secret = st.cipher.decrypt(db, user.tenant_id, user.totp_secret_enc)
    if secret:
        step = totp.verify(secret, code, last_step=user.last_totp_step)
        if step is not None:
            user.last_totp_step = step
            return "totp"
    if allow_recovery:
        h = totp.hash_recovery_code(st.settings.secret_key, code)
        rc = db.execute(select(RecoveryCode).where(RecoveryCode.user_id == user.id, RecoveryCode.code_hash == h, RecoveryCode.used_at.is_(None))).scalar_one_or_none()
        if rc is not None:
            rc.used_at = utcnow()
            return "recovery_code"
    return None


def _mfa_failure(db: Session, request: Request, user: User, action: str) -> ApiError:
    st = get_state(request)
    user.failed_logins += 1
    if user.failed_logins >= st.settings.lockout_threshold:
        user.locked_until = utcnow() + timedelta(minutes=st.settings.lockout_minutes)
        user.failed_logins = 0
        sessions.revoke_all(db, user.id)
        audit.append(db, _actor_for(user, request), "auth.locked", object_type="user", object_id=user.id, outcome="denied")
    audit.append(db, _actor_for(user, request), action, object_type="user", object_id=user.id, outcome="denied")
    db.commit()
    return unauthorized("the code is not valid", "mfa_invalid")


@router.post("/auth/mfa/verify", response_model=schemas.AuthOkOut, responses={401: {"model": schemas.ErrorBody}})
def mfa_verify(body: schemas.MfaCodeIn, request: Request, response: Response, p: Principal = Depends(require_pre_mfa), db: Session = Depends(get_db)) -> schemas.AuthOkOut:
    st = get_state(request)
    if not st.limiter.hit(f"mfa:{p.user_id}", 10, 60):
        raise too_many()
    user = db.get(User, p.user_id)
    sess = db.get(UserSession, p.session_id)
    assert user is not None
    assert sess is not None
    if not user.totp_secret_enc:
        raise conflict("mfa_not_enrolled", "enrol an authenticator first")
    method = _verify_code(db, request, user, body.code, allow_recovery=True)
    if method is None:
        raise _mfa_failure(db, request, user, "auth.mfa_failed")
    user.failed_logins = 0
    user.last_login_at = utcnow()
    sess.mfa_verified = True
    sess.stepup_at = utcnow()
    token, csrf = sessions.rotate(db, sess)
    audit.append(db, _actor_for(user, request), "auth.login", object_type="session", object_id=sess.id, details={"mfa": method})
    if method == "recovery_code":
        audit.append(db, _actor_for(user, request), "auth.recovery_code_used", object_type="user", object_id=user.id)
    db.commit()
    sessions.set_cookies(response, st.settings, token, csrf, sess)
    return schemas.AuthOkOut(csrf_token=csrf, expires_at=sess.expires_at)


@router.post("/auth/mfa/enroll", response_model=schemas.MfaEnrollOut)
def mfa_enroll_start(request: Request, p: Principal = Depends(require_pre_mfa), db: Session = Depends(get_db)) -> schemas.MfaEnrollOut:
    st = get_state(request)
    user = db.get(User, p.user_id)
    sess = db.get(UserSession, p.session_id)
    assert user is not None
    assert sess is not None
    if user.totp_secret_enc and not p.mfa_verified:
        raise conflict("mfa_already_enrolled", "an authenticator is already enrolled; ask a Firm Admin to reset MFA")
    secret = totp.new_secret()
    sess.pending_totp_secret_enc = st.cipher.encrypt(db, user.tenant_id, secret)
    db.commit()
    uri = totp.provisioning_uri(secret, user.email)
    return schemas.MfaEnrollOut(otpauth_uri=uri, secret=secret, qr_svg_data_uri=totp.qr_svg_data_uri(uri))


@router.post("/auth/mfa/enroll/confirm", response_model=schemas.RecoveryCodesOut)
def mfa_enroll_confirm(body: schemas.MfaCodeIn, request: Request, response: Response, p: Principal = Depends(require_pre_mfa), db: Session = Depends(get_db)) -> schemas.RecoveryCodesOut:
    st = get_state(request)
    user = db.get(User, p.user_id)
    sess = db.get(UserSession, p.session_id)
    assert user is not None
    assert sess is not None
    secret = st.cipher.decrypt(db, user.tenant_id, sess.pending_totp_secret_enc)
    if not secret:
        raise conflict("no_pending_enrolment", "start the enrolment first")
    step = totp.verify(secret, body.code)
    if step is None:
        raise _mfa_failure(db, request, user, "auth.mfa_enrol_failed")
    user.totp_secret_enc = sess.pending_totp_secret_enc
    user.mfa_type = "totp"
    user.mfa_enrolled_at = utcnow()
    user.last_totp_step = step
    user.last_login_at = utcnow()
    sess.pending_totp_secret_enc = None
    sess.mfa_verified = True
    sess.stepup_at = utcnow()
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    codes = totp.new_recovery_codes()
    for c in codes:
        db.add(RecoveryCode(tenant_id=user.tenant_id, user_id=user.id, code_hash=totp.hash_recovery_code(st.settings.secret_key, c)))
    token, csrf = sessions.rotate(db, sess)
    audit.append(db, _actor_for(user, request), "auth.mfa_enrolled", object_type="user", object_id=user.id)
    audit.append(db, _actor_for(user, request), "auth.login", object_type="session", object_id=sess.id, details={"mfa": "enrolment"})
    db.commit()
    sessions.set_cookies(response, st.settings, token, csrf, sess)
    return schemas.RecoveryCodesOut(recovery_codes=codes, csrf_token=csrf)


@router.post("/auth/step-up", response_model=schemas.OkOut)
def step_up(body: schemas.MfaCodeIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.OkOut:
    """Re-verify TOTP for privileged actions (valid for a few minutes, E-SEC-03)."""
    user = db.get(User, p.user_id)
    sess = db.get(UserSession, p.session_id)
    assert user is not None
    assert sess is not None
    if _verify_code(db, request, user, body.code, allow_recovery=False) is None:
        raise _mfa_failure(db, request, user, "auth.stepup_failed")
    sess.stepup_at = utcnow()
    audit.append(db, p.actor(), "auth.step_up", object_type="session", object_id=sess.id)
    db.commit()
    return schemas.OkOut(message="step-up verified")


@router.post("/auth/logout", response_model=schemas.OkOut)
def logout(request: Request, response: Response, p: Principal = Depends(require_pre_mfa), db: Session = Depends(get_db)) -> schemas.OkOut:
    sess = db.get(UserSession, p.session_id)
    if sess is not None:
        sess.revoked_at = utcnow()
    audit.append(db, p.actor(), "auth.logout", object_type="session", object_id=p.session_id)
    db.commit()
    sessions.clear_cookies(response, get_state(request).settings)
    return schemas.OkOut(message="signed out")


@router.post("/auth/logout-all", response_model=schemas.OkOut)
def logout_all(request: Request, response: Response, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.OkOut:
    n = sessions.revoke_all(db, p.user_id)
    audit.append(db, p.actor(), "auth.logout_all", object_type="user", object_id=p.user_id, details={"sessions": n})
    db.commit()
    sessions.clear_cookies(response, get_state(request).settings)
    return schemas.OkOut(message=f"signed out of {n} session(s)")


def assignments_out(db: Session, user_id: str) -> list[schemas.RoleAssignmentOut]:
    rows = db.execute(
        select(UserClientRole, Role, Client.name)
        .join(Role, Role.id == UserClientRole.role_id)
        .outerjoin(Client, Client.id == UserClientRole.client_id)
        .where(UserClientRole.user_id == user_id, UserClientRole.revoked_at.is_(None))
    ).all()
    out = []
    for a, r, cname in rows:
        out.append(schemas.RoleAssignmentOut(id=a.id, user_id=a.user_id, client_id=a.client_id, client_name=cname, role_code=r.code,
                                             role_name=r.name, expires_at=a.expires_at, revoked_at=a.revoked_at, created_at=a.created_at))
    return out


@router.get("/auth/me", response_model=schemas.MeOut)
def me(request: Request, p: Principal = Depends(require_pre_mfa), db: Session = Depends(get_db)) -> schemas.MeOut:
    st = get_state(request)
    user = db.get(User, p.user_id)
    sess = db.get(UserSession, p.session_id)
    tenant = db.get(Tenant, p.tenant_id)
    assert user is not None
    assert sess is not None
    assert tenant is not None
    perms: dict[str, dict[str, str]] = {}
    if p.mfa_verified:
        scopes: list[str | None] = [None, *p.client_ids]
        for cid in scopes:
            vals = {}
            for code in PERMISSION_CODES:
                v = permission_value(db, p, code, cid)
                if v != "deny":
                    vals[code] = v
            perms["*" if cid is None else cid] = vals
    ai_pol = db.execute(select(Policy).where(Policy.tenant_id == p.tenant_id, Policy.name == "ai_policy", Policy.active.is_(True))
                        .order_by(Policy.version.desc())).scalars().first()
    stepup_until = sess.stepup_at + timedelta(minutes=st.settings.stepup_minutes) if sess.stepup_at else None
    return schemas.MeOut(
        user=schemas.UserOut.model_validate(user),
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        session=schemas.SessionInfo(id=sess.id, auth_method=sess.auth_method, created_at=sess.created_at, last_seen_at=sess.last_seen_at,
                                    expires_at=sess.expires_at, idle_minutes=sess.idle_minutes, stepup_valid_until=stepup_until,
                                    ip=sess.ip, user_agent=sess.user_agent, current=True),
        assignments=assignments_out(db, user.id),
        permissions=perms,
        ai_policy_version=ai_pol.version if ai_pol else None,
        ai_policy_accepted=bool(ai_pol and user.ai_policy_version == ai_pol.version),
    )


@router.get("/auth/sessions", response_model=list[schemas.SessionInfo])
def my_sessions(request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[schemas.SessionInfo]:
    out = []
    for s in sessions.active_sessions(db, p.user_id):
        out.append(schemas.SessionInfo(id=s.id, auth_method=s.auth_method, created_at=s.created_at, last_seen_at=s.last_seen_at,
                                       expires_at=s.expires_at, idle_minutes=s.idle_minutes, ip=s.ip, user_agent=s.user_agent,
                                       current=s.id == p.session_id))
    return out


@router.delete("/auth/sessions/{session_id}", response_model=schemas.OkOut)
def revoke_session(session_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.OkOut:
    s = db.get(UserSession, session_id)
    if s is None or s.user_id != p.user_id:
        raise not_found("session")
    s.revoked_at = utcnow()
    audit.append(db, p.actor(), "auth.session_revoked", object_type="session", object_id=session_id)
    db.commit()
    return schemas.OkOut(message="session revoked")


@router.post("/auth/invite/accept", response_model=schemas.OkOut)
def accept_invite(body: schemas.InviteAcceptIn, request: Request, db: Session = Depends(get_db)) -> schemas.OkOut:
    st = get_state(request)
    if not st.limiter.hit(f"invite:{client_ip(request)}", 20, 60):
        raise too_many()
    inv = db.execute(select(Invite).where(Invite.token_hash == hash_token(body.token))).scalar_one_or_none()
    if inv is None or inv.used_at is not None or inv.expires_at <= utcnow():
        raise bad_request("invite_invalid", "the invitation link is invalid, used or expired")
    user = db.get(User, inv.user_id)
    if user is None or user.status == "disabled":
        raise bad_request("invite_invalid", "the invitation is no longer valid")
    try:
        passwords.check_policy(body.password, email=user.email, name=user.name)
    except passwords.PasswordPolicyError as exc:
        raise bad_request("password_policy", str(exc)) from exc
    set_rls(db, RLSContext(user.tenant_id, user.id, "auth"))
    user.password_hash = passwords.hash_password(body.password)
    user.password_changed_at = utcnow()
    user.status = "active"
    inv.used_at = utcnow()
    audit.append(db, _actor_for(user, request), "user.invite_accepted", object_type="user", object_id=user.id)
    db.commit()
    return schemas.OkOut(message="password set; sign in and enrol your authenticator")


@router.post("/auth/password/forgot", response_model=schemas.OkOut)
def password_forgot(body: schemas.PasswordForgotIn, request: Request, db: Session = Depends(get_db)) -> schemas.OkOut:
    st = get_state(request)
    if not st.limiter.hit(f"forgot:{client_ip(request)}", 10, 60):
        raise too_many()
    user = db.execute(select(User).where(User.email == body.email.lower())).scalar_one_or_none()
    if user is not None and user.status == "active" and not user.sso_subject:
        token = new_token()
        db.add(PasswordReset(tenant_id=user.tenant_id, user_id=user.id, token_hash=hash_token(token),
                             expires_at=utcnow() + timedelta(minutes=st.settings.reset_minutes)))
        link = f"{st.settings.public_base_url}/reset-password?token={token}"
        notify(db, user.tenant_id, user.email, "FS-Platform password reset", f"Reset your password within {st.settings.reset_minutes} minutes: {link}", "password_reset")
        audit.append(db, _actor_for(user, request), "auth.password_reset_requested", object_type="user", object_id=user.id)
        db.commit()
    return schemas.OkOut(message="if the account exists, a reset link has been sent")


@router.post("/auth/password/reset", response_model=schemas.OkOut)
def password_reset(body: schemas.PasswordResetIn, request: Request, db: Session = Depends(get_db)) -> schemas.OkOut:
    pr = db.execute(select(PasswordReset).where(PasswordReset.token_hash == hash_token(body.token))).scalar_one_or_none()
    if pr is None or pr.used_at is not None or pr.expires_at <= utcnow():
        raise bad_request("reset_invalid", "the reset link is invalid, used or expired")
    user = db.get(User, pr.user_id)
    assert user is not None
    try:
        passwords.check_policy(body.password, email=user.email, name=user.name)
    except passwords.PasswordPolicyError as exc:
        raise bad_request("password_policy", str(exc)) from exc
    set_rls(db, RLSContext(user.tenant_id, user.id, "auth"))
    user.password_hash = passwords.hash_password(body.password)
    user.password_changed_at = utcnow()
    user.failed_logins = 0
    user.locked_until = None
    pr.used_at = utcnow()
    sessions.revoke_all(db, user.id)
    audit.append(db, _actor_for(user, request), "auth.password_reset", object_type="user", object_id=user.id)
    db.commit()
    return schemas.OkOut(message="password changed; sign in again")


@router.post("/auth/password/change", response_model=schemas.OkOut)
def password_change(body: schemas.PasswordChangeIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.OkOut:
    user = db.get(User, p.user_id)
    assert user is not None
    if not passwords.verify_password(user.password_hash, body.current_password):
        raise bad_request("invalid_password", "current password is not correct")
    try:
        passwords.check_policy(body.new_password, email=user.email, name=user.name)
    except passwords.PasswordPolicyError as exc:
        raise bad_request("password_policy", str(exc)) from exc
    user.password_hash = passwords.hash_password(body.new_password)
    user.password_changed_at = utcnow()
    n = sessions.revoke_all(db, user.id, except_session_id=p.session_id)
    audit.append(db, p.actor(), "auth.password_changed", object_type="user", object_id=user.id, details={"other_sessions_revoked": n})
    db.commit()
    return schemas.OkOut(message="password changed")


# ------------------------------------------------------------------ OIDC SSO


def oidc_client(request: Request) -> OIDCClient:
    st = get_state(request)
    s = st.settings
    if not (s.oidc_issuer and s.oidc_client_id):
        raise not_found("single sign-on")
    cached = st.extras.get("oidc")
    if isinstance(cached, OIDCClient):
        return cached
    client = OIDCClient(s.oidc_issuer, s.oidc_client_id, s.oidc_client_secret,
                        s.oidc_redirect_uri or f"{s.public_base_url}/api/v1/auth/oidc/callback")
    st.extras["oidc"] = client
    return client


def _oidc_serializer(request: Request) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_state(request).settings.secret_key, salt="fsweb-oidc")


@router.get("/auth/oidc/login", include_in_schema=True, response_class=RedirectResponse, status_code=302)
def oidc_login(request: Request) -> RedirectResponse:
    client = oidc_client(request)
    state, nonce = new_token(16), new_token(16)
    verifier, challenge = pkce_pair()
    resp = RedirectResponse(client.authorization_url(state, nonce, challenge), status_code=302)
    cookie = _oidc_serializer(request).dumps({"state": state, "nonce": nonce, "verifier": verifier})
    resp.set_cookie("fs_oidc", cookie, max_age=600, httponly=True, secure=get_state(request).settings.cookie_secure, samesite="lax", path="/api/v1/auth/oidc")
    return resp


@router.get("/auth/oidc/callback", response_class=RedirectResponse, status_code=302)
def oidc_callback(request: Request, code: str = "", state: str = "", db: Session = Depends(get_db)) -> RedirectResponse:
    st = get_state(request)
    client = oidc_client(request)
    try:
        data = _oidc_serializer(request).loads(request.cookies.get("fs_oidc", ""), max_age=600)
    except BadSignature as exc:
        raise bad_request("oidc_state", "sign-in session expired; start again") from exc
    if not state or state != data.get("state") or not code:
        raise bad_request("oidc_state", "state mismatch")
    try:
        tokens = client.exchange(code, data["verifier"])
        claims = client.validate_id_token(str(tokens.get("id_token", "")), data["nonce"])
    except OIDCError as exc:
        raise unauthorized(f"single sign-on failed: {exc}", "oidc_failed") from exc
    email = str(claims.get("email") or claims.get("preferred_username") or "").lower()
    if not email or claims.get("email_verified") is False:
        raise unauthorized("the identity provider did not return a verified e-mail", "oidc_failed")
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or user.status != "active" or not user.is_firm_user:
        raise unauthorized("no active firm account for this identity", "oidc_no_account")
    set_rls(db, RLSContext(user.tenant_id, user.id, "auth"))
    user.sso_subject = str(claims.get("sub"))
    mfa_ok = OIDCClient.mfa_satisfied(claims)
    sess, token, csrf = sessions.create_session(db, st.settings, user, method="oidc", mfa_verified=mfa_ok,
                                                ip=client_ip(request), user_agent=request.headers.get("user-agent"))
    if mfa_ok:
        user.last_login_at = utcnow()
    audit.append(db, _actor_for(user, request), "auth.login" if mfa_ok else "auth.sso_ok", object_type="session", object_id=sess.id,
                 details={"method": "oidc", "mfa_from_idp": mfa_ok})
    db.commit()
    target = "/" if mfa_ok else ("/mfa" if user.totp_secret_enc else "/mfa/enroll")
    resp = RedirectResponse(st.settings.public_base_url.rstrip("/") + target, status_code=302)
    sessions.set_cookies(resp, st.settings, token, csrf, sess)
    resp.delete_cookie("fs_oidc", path="/api/v1/auth/oidc")
    return resp
