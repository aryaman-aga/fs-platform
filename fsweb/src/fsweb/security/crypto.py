"""Key management and field-level encryption (E-SEC-07).

Envelope encryption: identifiers (PAN, CIN, DIN, TOTP secrets, contact data) are
encrypted with AES-256-GCM under a per-tenant **data-encryption key** (DEK). DEKs
are stored only *wrapped* by a **key-encryption key** (KEK) held by the KMS:
``LocalKMS`` (keys from ``FSWEB_KMS_MASTER_KEY``) or ``VaultTransitKMS``
(HashiCorp Vault transit engine). Rotation without downtime:

* KEK rotation – add a new master key version (it becomes active) and re-wrap all
  DEKs (``rotate_kek``); ciphertext is untouched, old KEKs can then be retired;
* DEK rotation – create a new active DEK (``rotate_dek``); old DEKs stay
  available for decryption until data is re-encrypted lazily.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from fsweb.db import utcnow
from fsweb.models import DataKey

_DEK_AAD = b"fsweb:dek:v1"


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Tokens (sessions, invites, share links) are stored only as SHA-256."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hmac_hex(key: str | bytes, *parts: str) -> str:
    k = key.encode() if isinstance(key, str) else key
    return hmac.new(k, "\x1f".join(parts).encode("utf-8"), hashlib.sha256).hexdigest()


def consteq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class KMS(Protocol):
    @property
    def active_kek_id(self) -> str: ...

    def wrap(self, dek: bytes) -> tuple[str, str]: ...

    def unwrap(self, kek_id: str, wrapped: str) -> bytes: ...


class LocalKMS:
    """KEKs from configuration; the last configured key is the active one."""

    def __init__(self, master_keys: dict[str, bytes]) -> None:
        if not master_keys:
            raise ValueError("at least one KEK is required")
        self._keys = dict(master_keys)
        self._active = list(master_keys)[-1]

    @property
    def active_kek_id(self) -> str:
        return self._active

    def wrap(self, dek: bytes) -> tuple[str, str]:
        nonce = os.urandom(12)
        ct = AESGCM(self._keys[self._active]).encrypt(nonce, dek, _DEK_AAD)
        return self._active, b64e(nonce + ct)

    def unwrap(self, kek_id: str, wrapped: str) -> bytes:
        if kek_id not in self._keys:
            raise KeyError(f"KEK {kek_id} is not available to this node")
        raw = b64d(wrapped)
        return AESGCM(self._keys[kek_id]).decrypt(raw[:12], raw[12:], _DEK_AAD)


class VaultTransitKMS:  # pragma: no cover - exercised only against a real Vault
    """HashiCorp Vault transit engine as KMS (``FSWEB_KMS=vault``)."""

    def __init__(self, addr: str, token: str, key_name: str = "fsweb-kek") -> None:
        import httpx

        self._http = httpx.Client(base_url=addr.rstrip("/"), headers={"X-Vault-Token": token}, timeout=10)
        self._key = key_name

    @property
    def active_kek_id(self) -> str:
        return f"vault:{self._key}"

    def wrap(self, dek: bytes) -> tuple[str, str]:
        r = self._http.post(f"/v1/transit/encrypt/{self._key}", json={"plaintext": base64.b64encode(dek).decode()})
        r.raise_for_status()
        return self.active_kek_id, r.json()["data"]["ciphertext"]

    def unwrap(self, kek_id: str, wrapped: str) -> bytes:
        r = self._http.post(f"/v1/transit/decrypt/{self._key}", json={"ciphertext": wrapped})
        r.raise_for_status()
        return base64.b64decode(r.json()["data"]["plaintext"])


class FieldCipher:
    """Encrypt/decrypt individual fields. Ciphertext: ``v1.<dek_id>.<b64(nonce|ct)>``."""

    PREFIX = "v1"

    def __init__(self, kms: KMS) -> None:
        self.kms = kms
        self._cache: dict[str, bytes] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------- DEK handling
    def _active_dek(self, session: Session, tenant_id: str) -> tuple[str, bytes]:
        row = session.execute(
            select(DataKey).where(DataKey.tenant_id == tenant_id, DataKey.active.is_(True)).order_by(DataKey.created_at.desc())
        ).scalars().first()
        if row is None:
            dek = AESGCM.generate_key(bit_length=256)
            kek_id, wrapped = self.kms.wrap(dek)
            row = DataKey(tenant_id=tenant_id, wrapped_key=wrapped, kek_id=kek_id, active=True)
            session.add(row)
            session.flush()
            with self._lock:
                self._cache[row.id] = dek
            return row.id, dek
        return row.id, self._dek(session, row.id)

    def _dek(self, session: Session, dek_id: str) -> bytes:
        with self._lock:
            hit = self._cache.get(dek_id)
        if hit is not None:
            return hit
        row = session.get(DataKey, dek_id)
        if row is None:
            raise KeyError(f"data key {dek_id} not found")
        dek = self.kms.unwrap(row.kek_id, row.wrapped_key)
        with self._lock:
            self._cache[dek_id] = dek
        return dek

    # ------------------------------------------------------------------ fields
    def encrypt(self, session: Session, tenant_id: str, plaintext: str | None) -> str | None:
        if plaintext is None or plaintext == "":
            return None
        dek_id, dek = self._active_dek(session, tenant_id)
        nonce = os.urandom(12)
        ct = AESGCM(dek).encrypt(nonce, plaintext.encode("utf-8"), f"{tenant_id}|{dek_id}".encode())
        return f"{self.PREFIX}.{dek_id}.{b64e(nonce + ct)}"

    def decrypt(self, session: Session, tenant_id: str, token: str | None) -> str | None:
        if not token:
            return None
        prefix, dek_id, body = token.split(".", 2)
        if prefix != self.PREFIX:
            raise ValueError("unknown ciphertext version")
        raw = b64d(body)
        dek = self._dek(session, dek_id)
        return AESGCM(dek).decrypt(raw[:12], raw[12:], f"{tenant_id}|{dek_id}".encode()).decode("utf-8")

    @staticmethod
    def mask(value: str | None, keep: int = 4) -> str | None:
        if not value:
            return value
        return "•" * max(0, len(value) - keep) + value[-keep:]

    # ---------------------------------------------------------------- rotation
    def rotate_kek(self, session: Session) -> int:
        """Re-wrap every DEK with the active KEK (no data re-encryption, no downtime)."""
        n = 0
        for row in session.execute(select(DataKey)).scalars():
            if row.kek_id == self.kms.active_kek_id:
                continue
            dek = self.kms.unwrap(row.kek_id, row.wrapped_key)
            row.kek_id, row.wrapped_key = self.kms.wrap(dek)
            row.rotated_at = utcnow()
            n += 1
        session.flush()
        return n

    def rotate_dek(self, session: Session, tenant_id: str) -> str:
        """Start a new active DEK; old DEKs remain for decryption."""
        for row in session.execute(select(DataKey).where(DataKey.tenant_id == tenant_id, DataKey.active.is_(True))).scalars():
            row.active = False
            row.rotated_at = utcnow()
        dek = AESGCM.generate_key(bit_length=256)
        kek_id, wrapped = self.kms.wrap(dek)
        row = DataKey(tenant_id=tenant_id, wrapped_key=wrapped, kek_id=kek_id, active=True)
        session.add(row)
        session.flush()
        with self._lock:
            self._cache[row.id] = dek
        return row.id

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()


def derive_key(secret: str, purpose: str, length: int = 32) -> bytes:
    """HKDF-style derivation of purpose-bound keys from the app secret (dev/test fallbacks)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    return HKDF(algorithm=hashes.SHA256(), length=length, salt=b"fsweb", info=purpose.encode()).derive(secret.encode())
