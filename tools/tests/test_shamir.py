"""Tests for tools/shamir.py (E-BCP-02 key escrow)."""

from __future__ import annotations

import importlib.util
import itertools
import os
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "shamir.py"
_spec = importlib.util.spec_from_file_location("fs_tools_shamir", _PATH)
assert _spec
assert _spec.loader
shamir = importlib.util.module_from_spec(_spec)
sys.modules["fs_tools_shamir"] = shamir
_spec.loader.exec_module(shamir)


SECRET = b"restic-offsite-repository-password: Kx9#mQ2!vL7@pR4z"


def _tamper(share: str, *, recompute_check: bool) -> str:
    """Flip one payload byte, optionally re-computing the check (a malicious holder)."""
    parts = share.split(":")
    payload = bytearray(bytes.fromhex(parts[5]))
    payload[20] ^= 0x5A
    parts[5] = payload.hex()
    body = ":".join(parts[:6])
    parts[6] = shamir._check(body) if recompute_check else parts[6]
    return ":".join(parts)


def test_gf256_field_axioms() -> None:
    for a in range(1, 256):
        assert shamir.gf_mul(a, shamir.gf_div(1, a)) == 1
        assert shamir.gf_div(shamir.gf_mul(a, 7), 7) == a
    assert shamir.gf_mul(0, 99) == 0
    assert shamir.gf_mul(0x57, 0x83) == 0xC1  # FIPS-197 worked example
    with pytest.raises(ZeroDivisionError):
        shamir.gf_div(5, 0)


@pytest.mark.parametrize(("k", "n"), [(2, 3), (3, 5), (2, 2), (5, 5), (4, 9)])
def test_every_k_subset_reconstructs(k: int, n: int) -> None:
    shares = shamir.split_secret(SECRET, k, n)
    assert len(shares) == n
    assert len(set(shares)) == n
    for combo in itertools.combinations(shares, k):
        assert shamir.combine_shares(list(combo)) == SECRET


def test_more_than_k_shares_also_work_and_order_is_irrelevant() -> None:
    shares = shamir.split_secret(SECRET, 2, 3)
    assert shamir.combine_shares(list(reversed(shares))) == SECRET


def test_binary_secret_roundtrip() -> None:
    secret = os.urandom(32)  # e.g. a KMS master key
    shares = shamir.split_secret(secret, 2, 3)
    assert shamir.combine_shares([shares[0], shares[2]]) == secret


def test_insufficient_shares_rejected() -> None:
    shares = shamir.split_secret(SECRET, 3, 5)
    with pytest.raises(shamir.InsufficientSharesError):
        shamir.combine_shares(shares[:2])
    # the same share twice does not count twice
    with pytest.raises(shamir.InsufficientSharesError):
        shamir.combine_shares([shares[0], shares[0], shares[1]])


def test_single_share_does_not_contain_the_secret() -> None:
    shares = shamir.split_secret(SECRET, 2, 3)
    for share in shares:
        assert SECRET.hex() not in share
        assert SECRET not in bytes.fromhex(share.split(":")[5])


def test_typo_detected_by_checksum() -> None:
    shares = shamir.split_secret(SECRET, 2, 3)
    with pytest.raises(shamir.ShareFormatError, match="checksum"):
        shamir.combine_shares([_tamper(shares[0], recompute_check=False), shares[1]])


def test_malicious_share_detected_with_exactly_k() -> None:
    shares = shamir.split_secret(SECRET, 2, 3)
    bad = _tamper(shares[0], recompute_check=True)
    with pytest.raises(shamir.IntegrityError):
        shamir.combine_shares([bad, shares[1]])


def test_malicious_share_isolated_when_extra_shares_available() -> None:
    shares = shamir.split_secret(SECRET, 2, 3)
    bad = _tamper(shares[1], recompute_check=True)
    result = shamir.combine_detailed([shares[0], bad, shares[2]])
    assert result.secret == SECRET
    assert result.inconsistent == (2,)
    assert 2 not in result.used


def test_shares_from_different_splits_rejected() -> None:
    a = shamir.split_secret(SECRET, 2, 3)
    b = shamir.split_secret(SECRET, 2, 3)
    with pytest.raises(shamir.ShareFormatError, match="different split"):
        shamir.combine_shares([a[0], b[1]])


def test_conflicting_shares_for_same_index_rejected() -> None:
    shares = shamir.split_secret(SECRET, 2, 3)
    forged = _tamper(shares[0], recompute_check=True)
    with pytest.raises(shamir.IntegrityError, match="index 1"):
        shamir.combine_shares([shares[0], forged, shares[1]])


@pytest.mark.parametrize(("k", "n"), [(1, 3), (4, 3), (2, 256), (0, 0)])
def test_invalid_parameters(k: int, n: int) -> None:
    with pytest.raises(shamir.ShamirError):
        shamir.split_secret(SECRET, k, n)


def test_empty_secret_rejected() -> None:
    with pytest.raises(shamir.ShamirError):
        shamir.split_secret(b"", 2, 3)


def test_garbage_input_rejected() -> None:
    with pytest.raises(shamir.ShareFormatError):
        shamir.parse_share("not a share")
    with pytest.raises(shamir.ShareFormatError):
        shamir.parse_share("FSSS1:zz:2:3:1:00:00000000")


def test_cli_split_files_combine_and_verify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsysbinary: pytest.CaptureFixture[bytes]) -> None:
    secret_file = tmp_path / "secret.bin"
    secret_file.write_bytes(SECRET)
    out_dir = tmp_path / "shares"
    assert shamir.main(["split", "--k", "2", "--n", "3", "--secret-file", str(secret_file), "--out-dir", str(out_dir), "--label", "drill"]) == 0
    files = sorted(out_dir.glob("share-*-of-3.txt"))
    assert len(files) == 3
    if os.name == "posix":
        assert all((f.stat().st_mode & 0o777) == 0o600 for f in files)
    assert "threshold 2" in files[0].read_text()

    recovered = tmp_path / "recovered.bin"
    assert shamir.main(["combine", str(files[0]), str(files[2]), "--out", str(recovered)]) == 0
    assert recovered.read_bytes() == SECRET

    capsysbinary.readouterr()
    assert shamir.main(["verify", str(files[1]), str(files[2])]) == 0
    captured = capsysbinary.readouterr()
    assert SECRET not in captured.out
    assert SECRET not in captured.err

    assert shamir.main(["combine", str(files[0])]) == 4  # not enough shares


def test_cli_stdin_strips_one_trailing_newline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import io

    raw = io.BytesIO(b"s3cret-pass\n")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(raw))
    assert shamir.main(["split", "--k", "2", "--n", "2"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert shamir.combine_shares(lines) == b"s3cret-pass"


def test_cli_reports_tampering_with_exit_code(tmp_path: Path) -> None:
    shares = shamir.split_secret(SECRET, 2, 3)
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text(_tamper(shares[0], recompute_check=True) + "\n")
    b.write_text(shares[1] + "\n")
    assert shamir.main(["combine", str(a), str(b), "--out", str(tmp_path / "x")]) == 3
    assert not (tmp_path / "x").exists()
