"""Account recovery and first-run helpers shared by the admin API, the setup endpoint and the recovery CLI.

The desktop edition (``FSWEB_ENV=offline``) has no e-mail: invitations and password-reset links are handed over
in person by an administrator, and the first administrator is created in the browser on the PC itself.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from fsweb import audit
from fsweb.db import utcnow
from fsweb.models import PasswordReset, RecoveryCode, Tenant, User
from fsweb.security import sessions
from fsweb.security.crypto import hash_token, new_token
from fsweb.settings import Settings

# The first administrator is created in the browser only where the server runs on the user's own PC (or in development).
SETUP_ENVS = frozenset({"offline", "dev", "test"})
RESET_PATH = "/reset-password"
MAX_RESET_LINK_HOURS = 7 * 24
# A Firm Admin whose firm's administrators also work on engagements was made Partner on a client they created. A
# distinct audit event because it weakens segregation of duties; access reviews flag these assignments.
AUTO_PARTNER_EVENT = "assignment.firm_admin_auto_partner"


@dataclass
class AdminActor(audit.Actor):
    """An administrator acting after step-up with a stated reason (``X-Action-Reason``): the reason is recorded with
    the audit event of the action. The recovery CLI passes a plain :class:`audit.Actor`."""

    reason: str | None = None


def with_reason(actor: audit.Actor, reason: str) -> AdminActor:
    return AdminActor(**{f.name: getattr(actor, f.name) for f in fields(actor)}, reason=reason)


def _details(actor: audit.Actor, **details: object) -> dict[str, object]:
    reason = getattr(actor, "reason", None)
    return {**details, "reason": reason} if reason else dict(details)


# ------------------------------------------------------------------ edition & capabilities


def edition(settings: Settings) -> str:
    return "desktop" if settings.env == "offline" else "server"


def smtp_configured() -> bool:
    """SMTP delivery of the notification outbox (``FSWEB_SMTP_HOST``, flushed by ``fsweb.manage send-mail``)."""
    return bool(os.environ.get("FSWEB_SMTP_HOST", "").strip())


def email_delivery(settings: Settings) -> bool:
    """Whether invitations and reset links reach users by e-mail. Never on the desktop edition."""
    return settings.env != "offline" and smtp_configured()


def setup_allowed(settings: Settings) -> bool:
    return settings.env in SETUP_ENVS


def signup_allowed(settings: Settings) -> bool:
    """Self-service accounts ("Create an account"): not on the firm server (prod also refuses the setting)."""
    return settings.signup_enabled and settings.env != "prod"


def tenant_exists(db: Session) -> bool:
    return db.execute(select(Tenant.id).limit(1)).first() is not None


# ------------------------------------------------------------------ password reset links


def reset_link(settings: Settings, token: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}{RESET_PATH}?token={token}"


def create_reset_token(db: Session, settings: Settings, user: User, expires_at: datetime) -> str:
    """A single-use reset token, stored only as its SHA-256 (the forgot-password flow and administrators). Returns the
    absolute link to the reset page."""
    token = new_token()
    db.add(PasswordReset(tenant_id=user.tenant_id, user_id=user.id, token_hash=hash_token(token), expires_at=expires_at))
    db.flush()
    return reset_link(settings, token)


def issue_password_reset_link(db: Session, settings: Settings, user: User, actor: audit.Actor, *, hours: int = 24) -> tuple[str, datetime]:
    """An administrator (or the recovery CLI) issues a reset link to hand over in person: the user chooses a new
    password on the reset page, which also signs out their sessions. Audited; the token itself is never recorded."""
    if not 1 <= hours <= MAX_RESET_LINK_HOURS:
        raise ValueError(f"a reset link is valid for 1 to {MAX_RESET_LINK_HOURS} hours")
    if user.status == "disabled":
        raise ValueError("the account is disabled; enable it first")
    expires_at = utcnow() + timedelta(hours=hours)
    link = create_reset_token(db, settings, user, expires_at)
    audit.append(db, actor, "user.password_reset_link_issued", object_type="user", object_id=user.id,
                 details=_details(actor, expires_at=expires_at.isoformat(), hours=hours))
    return link, expires_at


# ------------------------------------------------------------------ MFA reset


def reset_mfa(db: Session, user: User, actor: audit.Actor) -> None:
    """Remove the authenticator and recovery codes and sign the user out everywhere: they enrol a new authenticator
    (QR code) at their next sign-in. Admin-only in the API (step-up + reason)."""
    user.totp_secret_enc = None
    user.mfa_type = "none"
    user.mfa_enrolled_at = None
    user.last_totp_step = None
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    n = sessions.revoke_all(db, user.id)
    audit.append(db, actor, "user.mfa_reset", object_type="user", object_id=user.id, details=_details(actor, sessions_revoked=n))
