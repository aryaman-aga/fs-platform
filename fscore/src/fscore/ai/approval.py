"""Approval tokens bound to the exact payload hash (control C9, 05 §4).

``token = v1.<payload_sha256>.<approver>.<expires_epoch>.<hmac>`` where the
HMAC-SHA256 covers ``payload_sha256 | approver | requester | expiry`` with the
firm's secret. Verification refuses a changed payload, an expired or
tampered token, and a requester approving their own request (segregation of
duties, C10). Every decision returns an auditable record for audit_event.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any

from fscore.ai.errors import ApprovalError

TOKEN_VERSION = "v1"  # noqa: S105 - format marker, not a secret
DEFAULT_TTL = timedelta(minutes=30)


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


def _unb64(s: str) -> str:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad).decode("utf-8")


def _mac(secret: str | bytes, payload_sha256: str, approver: str, requester: str, expires: int) -> str:
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    msg = f"{TOKEN_VERSION}|{payload_sha256}|{approver}|{requester}|{expires}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _record(event: str, **fields: Any) -> dict[str, Any]:
    return {"event": event, "ts": datetime.now(UTC).isoformat(), **fields}


def issue_approval(
    payload_sha256: str,
    *,
    approver: str,
    requester: str,
    secret: str | bytes,
    ttl: timedelta = DEFAULT_TTL,
    now: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return (token, audit record). Raises ApprovalError on self-approval."""
    if not approver or not requester:
        raise ApprovalError("approver and requester are required", _record("ai.approval.rejected", payload_sha256=payload_sha256, reason="missing user"))
    if approver.strip().lower() == requester.strip().lower():
        rec = _record("ai.approval.rejected", payload_sha256=payload_sha256, approver=approver, requester=requester, reason="self-approval")
        raise ApprovalError("a user cannot approve their own AI request (segregation of duties)", rec)
    if len(payload_sha256) != 64:
        raise ApprovalError("payload hash must be a SHA-256 hex digest")
    t = now or datetime.now(UTC)
    expires = int((t + ttl).timestamp())
    mac = _mac(secret, payload_sha256, approver, requester, expires)
    token = ".".join([TOKEN_VERSION, payload_sha256, _b64(approver), _b64(requester), str(expires), mac])
    rec = _record(
        "ai.approval.issued",
        payload_sha256=payload_sha256,
        approver=approver,
        requester=requester,
        expires_at=datetime.fromtimestamp(expires, UTC).isoformat(),
    )
    return token, rec


def verify_approval(
    token: str,
    *,
    payload_sha256: str,
    secret: str | bytes,
    requester: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the audit record of a valid approval; raise ApprovalError otherwise."""

    def reject(reason: str, **extra: Any) -> ApprovalError:
        return ApprovalError(f"AI approval rejected: {reason}", _record("ai.approval.rejected", payload_sha256=payload_sha256, reason=reason, **extra))

    if not token:
        raise reject("no approval token")
    parts = token.split(".")
    if len(parts) != 6 or parts[0] != TOKEN_VERSION:
        raise reject("malformed token")
    _v, bound_hash, approver_b, requester_b, expires_s, mac = parts
    try:
        approver, token_requester, expires = _unb64(approver_b), _unb64(requester_b), int(expires_s)
    except (ValueError, UnicodeDecodeError) as exc:
        raise reject("malformed token") from exc
    expected = _mac(secret, bound_hash, approver, token_requester, expires)
    if not hmac.compare_digest(expected, mac):
        raise reject("signature mismatch (token tampered or issued with another secret)")
    if not hmac.compare_digest(bound_hash, payload_sha256):
        raise reject("payload changed after approval", approved_hash=bound_hash)
    if requester is not None and requester.strip().lower() != token_requester.strip().lower():
        raise reject("token was issued for another requester")
    if approver.strip().lower() == token_requester.strip().lower():
        raise reject("self-approval")
    t = now or datetime.now(UTC)
    if t.timestamp() > expires:
        raise reject("approval expired", expired_at=datetime.fromtimestamp(expires, UTC).isoformat())
    return _record(
        "ai.approval.verified",
        payload_sha256=payload_sha256,
        approver=approver,
        requester=token_requester,
        expires_at=datetime.fromtimestamp(expires, UTC).isoformat(),
    )
