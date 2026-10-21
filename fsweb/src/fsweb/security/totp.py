"""TOTP multi-factor authentication (05 §1: MFA mandatory for everyone).

RFC 6238 codes (30 s step, 6 digits, ±1 step tolerance) with replay protection:
the last accepted time step is stored per user and a code can never be reused.
Recovery codes are shown once and stored as HMAC-SHA256 digests.
"""

from __future__ import annotations

import base64
import io
import secrets
import time

import pyotp
import qrcode
import qrcode.image.svg

from fsweb.security.crypto import consteq, hmac_hex

ISSUER = "FS-Platform"
STEP = 30


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str, issuer: str = ISSUER) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def qr_svg_data_uri(uri: str) -> str:
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def verify(secret: str, code: str, *, last_step: int | None = None, at: float | None = None) -> int | None:
    """Return the accepted time step, or None. Rejects reuse of ``last_step`` or older."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return None
    now = time.time() if at is None else at
    totp = pyotp.TOTP(secret)
    current = int(now // STEP)
    for step in (current, current - 1, current + 1):
        if last_step is not None and step <= last_step:
            continue
        if consteq(totp.at(step * STEP), code):
            return step
    return None


def current_code(secret: str, at: float | None = None) -> str:
    return pyotp.TOTP(secret).at(time.time() if at is None else at)


def new_recovery_codes(n: int = 10) -> list[str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    out = []
    for _ in range(n):
        raw = "".join(secrets.choice(alphabet) for _ in range(10))
        out.append(f"{raw[:5]}-{raw[5:]}")
    return out


def hash_recovery_code(pepper: str, code: str) -> str:
    return hmac_hex(pepper, "recovery", code.strip().lower().replace(" ", ""))
