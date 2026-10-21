"""FS-Platform Recovery with the real platform: an administrator locked out of the only administrator account gets a
one-time reset link or a fresh authenticator enrolment, both audited; diagnostics never contain client data or keys."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

from fsoffline import recovery
from fsoffline.launcher import bootstrap
from fsoffline.settings import OfflineSettings

EMAIL = "partner@corvus-advisors.example"


@contextmanager
def _db(path: Path) -> Iterator[sqlite3.Connection]:
    """A connection that commits and is closed afterwards (``sqlite3.connect`` as a context manager only commits)."""
    with closing(sqlite3.connect(path)) as conn, conn:
        yield conn


@pytest.fixture
def installed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> OfflineSettings:
    s = OfflineSettings(data_dir=tmp_path / "data", open_browser=False)
    assert bootstrap(s, "Corvus Advisors", EMAIL) == 0
    capsys.readouterr()  # the one-time password printed by bootstrap
    return s


def _audit_actions(s: OfflineSettings) -> list[str]:
    with _db(s.db_path) as conn:
        return [r[0] for r in conn.execute("SELECT action FROM audit_event ORDER BY seq")]


def test_reset_password_issues_a_one_time_link(installed: OfflineSettings) -> None:
    before = len(_audit_actions(installed))
    link, expires = recovery.reset_password(installed, EMAIL.upper())  # e-mail matched case-insensitively
    assert link.startswith("http://127.0.0.1:") and "token=" in link
    assert expires is not None
    assert len(_audit_actions(installed)) > before  # recorded in the audit trail


def test_reset_mfa_removes_the_authenticator(installed: OfflineSettings) -> None:
    with _db(installed.db_path) as conn:
        assert conn.execute("SELECT mfa_type FROM app_user WHERE email = ?", (EMAIL,)).fetchone()[0] == "totp"
    recovery.reset_mfa(installed, EMAIL)
    with _db(installed.db_path) as conn:
        mfa_type, secret = conn.execute("SELECT mfa_type, totp_secret_enc FROM app_user WHERE email = ?", (EMAIL,)).fetchone()
    assert mfa_type != "totp" and secret is None


def test_unknown_account_is_reported(installed: OfflineSettings) -> None:
    with pytest.raises(recovery.RecoveryError, match="no FS-Platform account"):
        recovery.reset_password(installed, "nobody@example.com")


def test_diagnostics_hold_no_client_data(installed: OfflineSettings, tmp_path: Path) -> None:
    installed.logs_dir.mkdir(parents=True, exist_ok=True)
    (installed.logs_dir / "fsplatform.log").write_text('{"msg": "started"}\n', encoding="utf-8")
    out = recovery.diagnostics(installed, tmp_path / "diag.zip")
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        info = json.loads(z.read("info.json"))
        settings_txt = z.read("settings.txt").decode()
    assert "logs/fsplatform.log" in names and "info.json" in names
    assert not any(n.endswith((".sqlite3", ".key", ".token")) or n.startswith("objects/") for n in names)
    assert "fsoffline" in info and info["runtime"] is None
    assert "***" in settings_txt and "FSWEB_SECRET_KEY=***" in settings_txt  # secrets redacted
