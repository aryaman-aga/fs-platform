"""First run without the CLI (desktop edition): the firm and its first administrator are created in the browser.

Allowed once, while no firm exists, only in the desktop / development editions, only from this PC (loopback) and
rate-limited. When ``FSWEB_SETUP_TOKEN_FILE`` is set (the desktop launcher always sets it) the request must also
carry the one-time token the launcher wrote to that file and opened as ``/setup#token=…``. The administrator
chooses their own password; no authenticator is pre-enrolled, so the first sign-in enrols one with a QR code.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from fsweb import audit, schemas
from fsweb.authz.deps import get_db, get_state
from fsweb.db import SYSTEM, set_rls
from fsweb.errors import bad_request, conflict, forbidden, too_many
from fsweb.security import passwords
from fsweb.services import accounts
from fsweb.services.seed import WORKS_ON_ENGAGEMENTS, first_run

router = APIRouter(tags=["setup"])
log = logging.getLogger("fsweb.security")

SETUP_TOKEN_ENV = "FSWEB_SETUP_TOKEN_FILE"  # noqa: S105 - the name of an environment variable, not a secret
SETUP_RATE_PER_MINUTE = 10
_SETUP_LOCK = 0x4653_5345_5455_5031  # pg_advisory_xact_lock key ("FSSETUP1"): one first run at a time
_FORWARDING_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded")


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host.strip().strip("[]"))
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def _forwarded_for(request: Request) -> list[str]:
    """Client addresses a proxy in front claims (a local proxy must not open setup to the network)."""
    out: list[str] = []
    for name in _FORWARDING_HEADERS:
        for value in request.headers.getlist(name):
            for part in value.split(","):
                part = part.strip()
                if name == "forwarded":
                    fors = [kv.split("=", 1)[1] for kv in part.split(";") if kv.strip().lower().startswith("for=")]
                    part = fors[0].strip().strip('"') if fors else ""
                    if part.startswith("[") and "]" in part:
                        part = part[1:part.index("]")]
                    elif part.count(":") == 1:
                        part = part.split(":", 1)[0]
                if part:
                    out.append(part)
    return out


def request_from_this_pc(request: Request) -> bool:
    """The TCP peer is the loopback interface and no proxy reports a remote client."""
    peer = request.client.host if request.client else None
    return _is_loopback(peer) and all(_is_loopback(h) for h in _forwarded_for(request))


def _lock(db: Session) -> None:
    """Hold the setup lock until commit: the check that no firm exists and the creation are one transaction. SQLite:
    fsweb.db opens every transaction with BEGIN IMMEDIATE, so the write lock is taken by the first statement."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SETUP_LOCK})


def _token_file() -> Path | None:
    value = os.environ.get(SETUP_TOKEN_ENV, "").strip()
    return Path(value) if value else None


def check_setup_token(supplied: str | None) -> Path | None:
    """With FSWEB_SETUP_TOKEN_FILE set, the request must carry the token in that file (constant-time comparison)."""
    path = _token_file()
    if path is None:
        return None
    try:
        expected = path.read_text(encoding="utf-8").strip()
    except OSError:
        expected = ""
    if not expected:
        raise forbidden("this setup link is no longer valid; close the browser and start FS-Platform again", "setup_token_missing")
    if not supplied or not hmac.compare_digest(supplied.strip().encode("utf-8"), expected.encode("utf-8")):
        raise forbidden("the setup link is not valid; use the link FS-Platform opened when it started", "setup_token_invalid")
    return path


@router.post("/setup", response_model=schemas.SetupOut, status_code=201,
             responses={400: {"model": schemas.ErrorBody}, 403: {"model": schemas.ErrorBody}, 409: {"model": schemas.ErrorBody},
                        429: {"model": schemas.ErrorBody}})
def first_run_setup(body: schemas.SetupIn, request: Request, db: Session = Depends(get_db)) -> schemas.SetupOut:
    """Create the firm (roles, permission matrix, policies, audit head) and its Firm Admin. Once only: 409 afterwards."""
    st = get_state(request)
    if not accounts.setup_allowed(st.settings):
        raise forbidden("first-run setup is not available on the firm server; an operator uses `fsweb.manage bootstrap`", "setup_disabled")
    if not request_from_this_pc(request):
        raise forbidden("set up FS-Platform on the PC where it runs", "setup_local_only")
    peer = request.client.host if request.client else "unknown"
    if not st.limiter.hit(f"setup:{peer}", SETUP_RATE_PER_MINUTE, 60):
        raise too_many("too many setup attempts; wait a minute")
    set_rls(db, SYSTEM)  # no firm (tenant context) exists yet
    _lock(db)
    if accounts.tenant_exists(db):
        raise conflict("setup_complete", "FS-Platform is already set up; sign in instead")
    token_file = check_setup_token(body.setup_token)
    try:
        passwords.check_policy(body.password, email=body.admin_email, name=body.admin_name)
    except passwords.PasswordPolicyError as exc:
        raise bad_request("password_policy", str(exc)) from exc
    res = first_run(db, st.cipher, firm_name=body.firm_name, admin_email=body.admin_email, admin_name=body.admin_name,
                    password=body.password, works_on_engagements=body.works_on_engagements)
    actor = audit.Actor(res.tenant.id, None, "setup", peer, request.headers.get("user-agent"), getattr(request.state, "correlation_id", None))
    audit.append(db, actor, "tenant.setup_completed", object_type="tenant", object_id=res.tenant.id,
                 details={"admin_user_id": res.admin.id, "edition": accounts.edition(st.settings), WORKS_ON_ENGAGEMENTS: body.works_on_engagements,
                          "setup_token": token_file is not None, "mfa": "enrol at first sign-in"})
    audit.append(db, actor, "assignment.created", object_type="assignment", object_id=res.assignment.id,
                 details={"user_id": res.admin.id, "role": "firm_admin", "via": "setup"})
    db.commit()
    if token_file is not None:
        try:
            token_file.unlink(missing_ok=True)  # one-time: the link cannot be replayed
        except OSError as exc:  # pragma: no cover - the firm exists now, so setup is refused anyway
            log.warning("could not delete the setup token file", extra={"error": str(exc)})
    return schemas.SetupOut(tenant_id=res.tenant.id, admin_email=res.admin.email)
