"""DB-backed opaque sessions (E-ARCH-02: API nodes stay stateless).

The cookie carries a random 256-bit token; the database stores only its SHA-256.
Cookies are HttpOnly, SameSite=Strict and Secure (in production). A separate,
JS-readable ``fs_csrf`` cookie carries the CSRF token that the SPA echoes in the
``X-CSRF-Token`` header (double submit, bound to the session server-side).
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from fsweb.authz.deps import CSRF_COOKIE, SESSION_COOKIE
from fsweb.db import utcnow
from fsweb.models import User, UserSession
from fsweb.security.crypto import hash_token, new_token
from fsweb.settings import Settings


def create_session(db: Session, settings: Settings, user: User, *, method: str, mfa_verified: bool, ip: str | None, user_agent: str | None) -> tuple[UserSession, str, str]:
    token, csrf = new_token(), new_token(24)
    idle = settings.session_idle_minutes_firm if user.is_firm_user else settings.session_idle_minutes_client
    now = utcnow()
    s = UserSession(
        tenant_id=user.tenant_id,
        user_id=user.id,
        token_hash=hash_token(token),
        csrf_hash=hash_token(csrf),
        auth_method=method,
        mfa_verified=mfa_verified,
        stepup_at=now if mfa_verified else None,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_absolute_hours),
        idle_minutes=idle,
        ip=ip,
        user_agent=(user_agent or "")[:300] or None,
        created_by=user.id,
    )
    db.add(s)
    db.flush()
    return s, token, csrf


def rotate(db: Session, sess: UserSession) -> tuple[str, str]:
    """New token + CSRF after a privilege change (prevents session fixation)."""
    token, csrf = new_token(), new_token(24)
    sess.token_hash = hash_token(token)
    sess.csrf_hash = hash_token(csrf)
    db.flush()
    return token, csrf


def set_cookies(response: Response, settings: Settings, token: str, csrf: str, sess: UserSession) -> None:
    max_age = int((sess.expires_at - utcnow()).total_seconds())
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=settings.cookie_secure, samesite="strict", path="/")
    response.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, httponly=False, secure=settings.cookie_secure, samesite="strict", path="/")


def clear_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="strict")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=settings.cookie_secure, samesite="strict")


def revoke_all(db: Session, user_id: str, except_session_id: str | None = None) -> int:
    stmt = update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if except_session_id:
        stmt = stmt.where(UserSession.id != except_session_id)
    res = db.execute(stmt.values(revoked_at=utcnow()))
    return int(res.rowcount or 0)  # type: ignore[attr-defined]


def active_sessions(db: Session, user_id: str) -> list[UserSession]:
    now = utcnow()
    rows = db.execute(
        select(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None), UserSession.expires_at > now)
        .order_by(UserSession.created_at.desc())
    ).scalars()
    return [s for s in rows if now - s.last_seen_at <= timedelta(minutes=s.idle_minutes)]
