"""OpenID Connect SSO for firm users (E-SEC-01): authorization-code flow with PKCE.

Network calls are isolated in ``discovery``, ``jwks`` and ``exchange`` so tests can
replace them with a mocked IdP. ID tokens are validated with joserfc against the
IdP's JWKS (signature, issuer, audience, expiry, nonce).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.jwk import KeySet

MFA_AMR = {"mfa", "otp", "hwk", "swk", "fpt", "face", "pin", "sc", "rsa", "wia", "ngcmfa"}


class OIDCError(RuntimeError):
    pass


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


class OIDCClient:
    def __init__(self, issuer: str, client_id: str, client_secret: str | None, redirect_uri: str) -> None:
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._discovery: dict[str, Any] | None = None
        self._jwks: dict[str, Any] | None = None

    # ------------------------------------------------------------ network (mockable)
    def discovery(self) -> dict[str, Any]:
        if self._discovery is None:
            r = httpx.get(f"{self.issuer}/.well-known/openid-configuration", timeout=10)
            r.raise_for_status()
            self._discovery = r.json()
        return self._discovery

    def jwks(self) -> dict[str, Any]:
        if self._jwks is None:
            r = httpx.get(self.discovery()["jwks_uri"], timeout=10)
            r.raise_for_status()
            self._jwks = r.json()
        return self._jwks

    def exchange(self, code: str, code_verifier: str) -> dict[str, Any]:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": code_verifier,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        r = httpx.post(self.discovery()["token_endpoint"], data=data, timeout=15)
        if r.status_code >= 400:
            raise OIDCError(f"token endpoint returned {r.status_code}")
        return dict(r.json())

    # ------------------------------------------------------------ protocol
    def authorization_url(self, state: str, nonce: str, code_challenge: str) -> str:
        q = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.discovery()['authorization_endpoint']}?{urlencode(q)}"

    def validate_id_token(self, id_token: str, nonce: str) -> dict[str, Any]:
        keys = KeySet.import_key_set(self.jwks())  # type: ignore[arg-type]
        try:
            tok = jwt.decode(id_token, keys)
        except Exception as exc:
            raise OIDCError(f"invalid ID token signature: {exc}") from exc
        c = dict(tok.claims)
        now = time.time()
        if c.get("iss") != self.discovery().get("issuer", self.issuer):
            raise OIDCError("issuer mismatch")
        aud = c.get("aud")
        if (isinstance(aud, list) and self.client_id not in aud) or (isinstance(aud, str) and aud != self.client_id):
            raise OIDCError("audience mismatch")
        if float(c.get("exp", 0)) < now - 60:
            raise OIDCError("ID token expired")
        if float(c.get("iat", now)) > now + 300:
            raise OIDCError("ID token issued in the future")
        if c.get("nonce") != nonce:
            raise OIDCError("nonce mismatch")
        return c

    @staticmethod
    def mfa_satisfied(claims: dict[str, Any]) -> bool:
        amr = claims.get("amr") or []
        if isinstance(amr, str):
            amr = [amr]
        return any(a in MFA_AMR for a in amr)
