#!/usr/bin/env python3
"""Shamir's Secret Sharing over GF(256) for key escrow (E-BCP-02, 07 §3).

Splits a secret (the restic repository password, the pgBackRest cipher pass or
the KMS master key) into ``n`` shares so that any ``k`` of them reconstruct it
and ``k - 1`` of them reveal nothing. The firm's default is 2-of-3 partners, so
no single person can decrypt the backups or lose the key.

Share format (one ASCII line)::

    FSSS1:<set_id>:<k>:<n>:<x>:<payload_hex>:<check>

* ``set_id``  16 hex chars, random per split; shares of different splits never mix
* ``k``/``n`` threshold and number of shares
* ``x``       the share index 1..n (the evaluation point of the polynomial)
* ``payload`` share bytes for ``digest || secret`` where digest = SHA-256(secret)[:16]
* ``check``   first 8 hex chars of SHA-256 over the text before it (typo / corruption check)

Combining verifies every share's check, that all shares come from one split, and
finally that SHA-256(secret)[:16] equals the embedded digest. A tampered or wrong
share is therefore *detected* instead of silently producing a wrong key; when more
than ``k`` shares are supplied, the inconsistent ones are identified.

CLI::

    printf '%s' "$SECRET" | tools/shamir.py split --k 2 --n 3 --out-dir shares/
    tools/shamir.py verify shares/share-1-of-3.txt shares/share-3-of-3.txt
    tools/shamir.py combine shares/share-1-of-3.txt shares/share-3-of-3.txt --out secret.bin

Exit codes: 0 ok, 2 usage / format error, 3 integrity failure, 4 not enough shares.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import os
import secrets
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

FORMAT = "FSSS1"
DIGEST_LEN = 16
MAX_SHARES = 255

# --------------------------------------------------------------------------- GF(256)
# AES field: x^8 + x^4 + x^3 + x + 1 (0x11B), generator 0x03.

_EXP = [0] * 512
_LOG = [0] * 256


def _init_tables() -> None:
    value = 1
    for power in range(255):
        _EXP[power] = value
        _LOG[value] = power
        doubled = ((value << 1) ^ 0x1B) & 0xFF if value & 0x80 else value << 1
        value = doubled ^ value  # value * 3
    for power in range(255, 512):
        _EXP[power] = _EXP[power - 255]


_init_tables()


def gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def gf_div(a: int, b: int) -> int:
    if b == 0:
        raise ZeroDivisionError("division by zero in GF(256)")
    if a == 0:
        return 0
    return _EXP[(_LOG[a] - _LOG[b]) % 255]


def _eval_poly(coefficients: Sequence[int], x: int) -> int:
    """Horner evaluation; coefficients[0] is the constant term (the secret byte)."""
    result = 0
    for coefficient in reversed(coefficients):
        result = gf_mul(result, x) ^ coefficient
    return result


def _lagrange_at(points: Sequence[tuple[int, int]], x: int) -> int:
    """Value at ``x`` of the unique polynomial through ``points`` (GF(256) arithmetic)."""
    total = 0
    for j, (xj, yj) in enumerate(points):
        basis = 1
        for m, (xm, _) in enumerate(points):
            if m != j:
                basis = gf_mul(basis, gf_div(x ^ xm, xj ^ xm))
        total ^= gf_mul(yj, basis)
    return total


# --------------------------------------------------------------------------- errors


class ShamirError(ValueError):
    """Base class; carries the CLI exit code."""

    exit_code = 2


class ShareFormatError(ShamirError):
    exit_code = 2


class InsufficientSharesError(ShamirError):
    exit_code = 4


class IntegrityError(ShamirError):
    exit_code = 3


# --------------------------------------------------------------------------- shares


@dataclass(frozen=True)
class Share:
    set_id: str
    k: int
    n: int
    x: int
    payload: bytes

    def body(self) -> str:
        return f"{FORMAT}:{self.set_id}:{self.k}:{self.n}:{self.x}:{self.payload.hex()}"

    def encode(self) -> str:
        return f"{self.body()}:{_check(self.body())}"


def _check(body: str) -> str:
    return hashlib.sha256(body.encode("ascii")).hexdigest()[:8]


def parse_share(text: str, *, label: str = "share") -> Share:
    """Parse and check one share line; raises ShareFormatError naming ``label``."""
    line = text.strip()
    parts = line.split(":")
    if len(parts) != 7 or parts[0] != FORMAT:
        raise ShareFormatError(f"{label}: not an {FORMAT} share")
    _, set_id, k_txt, n_txt, x_txt, payload_hex, check = parts
    body = ":".join(parts[:6])
    if _check(body) != check.lower():
        raise ShareFormatError(f"{label}: checksum mismatch (typo or corrupted share)")
    try:
        k, n, x = int(k_txt), int(n_txt), int(x_txt)
        payload = bytes.fromhex(payload_hex)
    except ValueError as exc:
        raise ShareFormatError(f"{label}: malformed fields") from exc
    if len(set_id) != 16 or any(c not in "0123456789abcdef" for c in set_id):
        raise ShareFormatError(f"{label}: malformed set id")
    if not (2 <= k <= n <= MAX_SHARES) or not (1 <= x <= n):
        raise ShareFormatError(f"{label}: invalid threshold or index")
    if len(payload) <= DIGEST_LEN:
        raise ShareFormatError(f"{label}: payload too short")
    return Share(set_id=set_id, k=k, n=n, x=x, payload=payload)


def split_secret(
    secret: bytes,
    k: int,
    n: int,
    *,
    rng: Callable[[int], bytes] = secrets.token_bytes,
) -> list[str]:
    """Split ``secret`` into ``n`` encoded shares, any ``k`` of which reconstruct it."""
    if not secret:
        raise ShamirError("secret is empty")
    if not (2 <= k <= n <= MAX_SHARES):
        raise ShamirError(f"need 2 <= k <= n <= {MAX_SHARES} (got k={k}, n={n})")
    data = hashlib.sha256(secret).digest()[:DIGEST_LEN] + secret
    set_id = rng(8).hex()
    ys: list[bytearray] = [bytearray() for _ in range(n)]
    for byte in data:
        coefficients = [byte, *rng(k - 1)]
        for index in range(n):
            ys[index].append(_eval_poly(coefficients, index + 1))
    return [Share(set_id, k, n, index + 1, bytes(ys[index])).encode() for index in range(n)]


@dataclass(frozen=True)
class CombineResult:
    secret: bytes
    used: tuple[int, ...]  # share indices (x) used for the reconstruction
    inconsistent: tuple[int, ...]  # supplied shares that do not lie on the polynomial


def _reconstruct(shares: Sequence[Share], x: int = 0) -> bytes:
    length = len(shares[0].payload)
    return bytes(_lagrange_at([(s.x, s.payload[i]) for s in shares], x) for i in range(length))


def _digest_ok(data: bytes) -> bool:
    digest, secret = data[:DIGEST_LEN], data[DIGEST_LEN:]
    return bool(secret) and secrets.compare_digest(hashlib.sha256(secret).digest()[:DIGEST_LEN], digest)


def combine_detailed(share_texts: Sequence[str]) -> CombineResult:
    parsed = [parse_share(text, label=f"share #{i + 1}") for i, text in enumerate(share_texts)]
    if not parsed:
        raise InsufficientSharesError("no shares supplied")
    first = parsed[0]
    by_x: dict[int, Share] = {}
    for i, share in enumerate(parsed):
        if (share.set_id, share.k, share.n, len(share.payload)) != (first.set_id, first.k, first.n, len(first.payload)):
            raise ShareFormatError(f"share #{i + 1} belongs to a different split")
        existing = by_x.get(share.x)
        if existing is not None and existing.payload != share.payload:
            raise IntegrityError(f"two different shares claim index {share.x}")
        by_x[share.x] = share
    unique = [by_x[x] for x in sorted(by_x)]
    if len(unique) < first.k:
        raise InsufficientSharesError(f"{len(unique)} distinct share(s) supplied; {first.k} needed")
    for combo in itertools.combinations(unique, first.k):
        data = _reconstruct(combo)
        if _digest_ok(data):
            used = tuple(s.x for s in combo)
            points = list(combo)
            inconsistent = tuple(
                s.x for s in unique if s.x not in used and _reconstruct(points, s.x) != s.payload
            )
            return CombineResult(secret=data[DIGEST_LEN:], used=used, inconsistent=inconsistent)
    raise IntegrityError("integrity check failed: the shares are tampered, mistyped or from different splits")


def combine_shares(share_texts: Sequence[str]) -> bytes:
    return combine_detailed(share_texts).secret


# --------------------------------------------------------------------------- CLI


def _read_share_lines(sources: Iterable[str]) -> list[str]:
    lines: list[str] = []
    for source in sources:
        text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="ascii")
        lines.extend(
            line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
        )
    return lines


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def _cmd_split(args: argparse.Namespace) -> int:
    if args.secret_file:
        secret = Path(args.secret_file).read_bytes()
    else:
        secret = sys.stdin.buffer.read()
        if not args.raw and secret.endswith(b"\n"):
            secret = secret[:-1]
            if secret.endswith(b"\r"):
                secret = secret[:-1]
    shares = split_secret(secret, args.k, args.n)
    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        for index, share in enumerate(shares, start=1):
            header = (
                f"# FS-Platform key-escrow share {index} of {args.n} (threshold {args.k})\n"
                f"# label: {args.label or '-'} | created: {stamp}\n"
                "# Keep offline and separate from the other shares. Never e-mail or photograph.\n"
            )
            _write_private(out / f"share-{index}-of-{args.n}.txt", (header + share + "\n").encode("ascii"))
        print(f"wrote {args.n} shares to {out} (any {args.k} reconstruct the secret)", file=sys.stderr)
    else:
        sys.stdout.write("\n".join(shares) + "\n")
    return 0


def _cmd_combine(args: argparse.Namespace) -> int:
    result = combine_detailed(_read_share_lines(args.shares))
    if result.inconsistent:
        print(
            "WARNING: share(s) " + ", ".join(map(str, result.inconsistent)) + " are inconsistent; re-issue them",
            file=sys.stderr,
        )
    if args.out:
        _write_private(Path(args.out), result.secret)
        print(f"secret written to {args.out} (mode 0600)", file=sys.stderr)
    else:
        sys.stdout.buffer.write(result.secret)
        sys.stdout.flush()
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    result = combine_detailed(_read_share_lines(args.shares))
    status = "OK" if not result.inconsistent else "OK with inconsistent shares " + str(list(result.inconsistent))
    print(f"{status}: shares {list(result.used)} reconstruct a secret whose integrity digest verifies")
    return 0 if not result.inconsistent else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shamir.py", description="Shamir secret sharing for key escrow (E-BCP-02)")
    sub = parser.add_subparsers(dest="command", required=True)
    split = sub.add_parser("split", help="split a secret read from stdin or --secret-file")
    split.add_argument("--k", type=int, required=True, help="threshold (shares needed)")
    split.add_argument("--n", type=int, required=True, help="number of shares")
    split.add_argument("--secret-file", help="read the secret bytes from this file instead of stdin")
    split.add_argument("--out-dir", help="write one file per share (mode 0600) instead of stdout")
    split.add_argument("--label", help="free-text label written in the share file header")
    split.add_argument("--raw", action="store_true", help="keep a trailing newline read from stdin")
    split.set_defaults(func=_cmd_split)
    combine = sub.add_parser("combine", help="reconstruct the secret from k or more shares")
    combine.add_argument("shares", nargs="+", help="share files, or - for stdin")
    combine.add_argument("--out", help="write the secret to this file (mode 0600) instead of stdout")
    combine.set_defaults(func=_cmd_combine)
    verify = sub.add_parser("verify", help="check shares reconstruct a valid secret without printing it")
    verify.add_argument("shares", nargs="+", help="share files, or - for stdin")
    verify.set_defaults(func=_cmd_verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ShamirError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
