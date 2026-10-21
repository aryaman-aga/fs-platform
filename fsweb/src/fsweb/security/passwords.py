"""Password hashing and policy (05 §1).

* argon2id (argon2-cffi defaults, RFC 9106 low-memory profile);
* minimum 12 characters, no forced periodic change;
* checked against an offline breached-password list (SHA-1 hashes, the same
  representation as the Pwned Passwords dataset). ``FSWEB_BREACHED_DIR`` may point
  to a k-anonymity range directory (``ABCDE.txt`` files holding hash suffixes) for
  the full dataset; the bundled list is used otherwise.
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from importlib import resources
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 12
MAX_LENGTH = 256

_hasher = PasswordHasher()


class PasswordPolicyError(ValueError):
    pass


@lru_cache(maxsize=1)
def _bundled() -> frozenset[str]:
    text = resources.files("fsweb").joinpath("data/breached_sha1.txt").read_text(encoding="utf-8")
    return frozenset(line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#"))


def is_breached(password: str) -> bool:
    digest = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()  # the breached-password dataset's key format
    range_dir = os.environ.get("FSWEB_BREACHED_DIR")
    if range_dir:
        f = Path(range_dir) / f"{digest[:5]}.txt"
        if f.exists():
            suffix = digest[5:]
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.split(":", 1)[0].strip().upper() == suffix:
                    return True
    return digest in _bundled()


def check_policy(password: str, *, email: str | None = None, name: str | None = None) -> None:
    if len(password) < MIN_LENGTH:
        raise PasswordPolicyError(f"password must be at least {MIN_LENGTH} characters")
    if len(password) > MAX_LENGTH:
        raise PasswordPolicyError("password is too long")
    if is_breached(password):
        raise PasswordPolicyError("password appears in a list of breached or common passwords")
    low = password.lower()
    if email:
        local = email.split("@", 1)[0].lower()
        if len(local) >= 4 and local in low:
            raise PasswordPolicyError("password must not contain your e-mail name")
    if name:
        for part in name.lower().split():
            if len(part) >= 4 and part in low:
                raise PasswordPolicyError("password must not contain your name")
    if len(set(password)) < 5:
        raise PasswordPolicyError("password is too repetitive")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    if not stored_hash:
        # Spend comparable time to blunt user-enumeration by timing.
        _hasher.hash(password)
        return False
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True
