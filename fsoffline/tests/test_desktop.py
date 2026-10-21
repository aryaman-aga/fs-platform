"""The installed desktop runtime with the real platform: port, runtime file, first-run token, stop requests,
backups and restore, install configuration. (Windows-only pieces — mutex, stop event, tray — run in the Windows CI.)"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from fsoffline import backup, desktop
from fsoffline.install import DEFAULT_PORT, load_data_config, load_install_config
from fsoffline.launcher import _pid_alive, find_free_port
from fsoffline.settings import OfflineSettings, ensure_layout


@contextmanager
def _db(path: Path) -> Iterator[sqlite3.Connection]:
    """A connection that commits and is closed afterwards (``sqlite3.connect`` as a context manager only commits)."""
    with closing(sqlite3.connect(path)) as conn, conn:
        yield conn


def _settings(tmp_path: Path, port: int = 0) -> OfflineSettings:
    return OfflineSettings(data_dir=tmp_path / "data", port=port, open_browser=False)


def test_bind_socket_moves_past_a_taken_port() -> None:
    base = find_free_port()
    taken = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    taken.bind(("127.0.0.1", base))
    taken.listen(1)
    try:
        sock = desktop.bind_socket("127.0.0.1", base)
        try:
            assert sock.getsockname()[1] != base
        finally:
            sock.close()
    finally:
        taken.close()


def test_runtime_file_round_trip(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    ensure_layout(s)
    assert desktop.read_runtime(s) is None and desktop.request_stop(s, timeout_s=1)  # nothing running
    desktop.write_runtime(s, 8746, "http://127.0.0.1:8746/")
    rt = desktop.read_runtime(s)
    assert rt is not None and rt["port"] == 8746 and rt["url"] == "http://127.0.0.1:8746/"


def test_setup_token_only_while_no_firm_exists(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    ensure_layout(s)
    token = desktop.prepare_setup(s)
    assert token and s.setup_token_path.read_text(encoding="ascii").strip() == token
    with _db(s.db_path) as conn:
        conn.execute("CREATE TABLE tenant (id TEXT, name TEXT, settings_json TEXT)")
        conn.execute("INSERT INTO tenant VALUES ('t1', 'Corvus Advisors', '{}')")
    assert desktop.prepare_setup(s) is None and not s.setup_token_path.exists()


def test_install_config(tmp_path: Path) -> None:
    root = tmp_path / "FS-Platform"
    (root / "python").mkdir(parents=True)
    (root / "fsplatform.ini").write_text("[fsplatform]\nversion = 1.0.0\ndata_dir = C:/ProgramData/FSPlatform\nui_dir = ui\n"
                                         "soffice = LibreOffice/program/soffice.exe\nport = 99999\n", encoding="utf-8")
    cfg = load_install_config(root)
    assert cfg.installed and cfg.version == "1.0.0" and cfg.port == DEFAULT_PORT  # out-of-range port ignored
    assert cfg.ui_dir == root / "ui" and cfg.soffice == root / "LibreOffice" / "program" / "soffice.exe"
    data = tmp_path / "data"
    data.mkdir()
    assert load_data_config(data) == {}
    (data / "fsplatform.ini").write_text("[app]\nai_enabled = true\n", encoding="utf-8")
    assert load_data_config(data)["ai_enabled"] == "true"


def test_runtime_serves_backs_up_and_stops(tmp_path: Path) -> None:
    """Start the real platform in-process, take a backup while it serves, stop it through the stop request."""
    s = _settings(tmp_path, port=find_free_port())
    rt = desktop.Runtime(s, open_browser=False)
    rt.start()
    try:
        assert "/setup#token=" in rt.url  # no firm yet: first-run setup is authorised by a one-time token
        base = rt.url.split("setup#", 1)[0]
        assert httpx.get(base + "api/v1/health", timeout=10).json()["status"] == "ok"
        assert desktop.read_runtime(s)["port"] == rt.port  # type: ignore[index]
        result = rt.backup_now()
        assert result is not None and result.ok and (result.path / "fsweb.sqlite3").is_file()
        assert desktop.request_stop(s, timeout_s=30)
    finally:
        rt.shutdown()
    assert desktop.read_runtime(s) is None


def _seeded(tmp_path: Path) -> OfflineSettings:
    """A data folder with the real schema, one firm and one stored file."""
    from fsweb.db import Database
    from fsweb.manage import migrate_sqlite

    s = _settings(tmp_path)
    ensure_layout(s)
    url = "sqlite:///" + s.db_path.as_posix()
    db = Database(url)
    migrate_sqlite(db, url)
    db.dispose()
    with _db(s.db_path) as conn:
        conn.execute("INSERT INTO tenant (id, name, settings_json, created_at) VALUES ('t1', 'Corvus Advisors', '{}', '2026-10-21 00:00:00')")
    obj = s.objects_dir / "t" / "t1" / "c" / "c1" / "e" / "e1" / "files" / "v1.xlsx"
    obj.parent.mkdir(parents=True)
    obj.write_bytes(b"PK\x03\x04 workbook\n")
    (s.secrets_dir / "kms_master.key").write_text("a2V5\n", encoding="ascii")
    return s


def test_backup_and_restore_round_trip(tmp_path: Path) -> None:
    s = _seeded(tmp_path)
    result = backup.run_backup(s, reason="manual")
    assert result.ok and result.objects_copied == 1
    manifest = json.loads((result.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["objects_total"] == 1 and manifest["schema_revision"]
    assert (result.path / "secrets" / "kms_master.key").is_file()
    with _db(s.db_path) as conn:  # recorded in the backup log and the audit trail
        assert conn.execute("SELECT kind, status FROM backup_log").fetchall() == [("desktop", "ok")]
        assert conn.execute("SELECT COUNT(*) FROM audit_event WHERE action = 'backup.desktop'").fetchone()[0] == 1
        conn.execute("DELETE FROM backup_log")
        conn.execute("UPDATE tenant SET name = 'changed after the backup'")
    (s.objects_dir / "t" / "t1" / "c" / "c1" / "e" / "e1" / "files" / "v1.xlsx").unlink()

    aside = backup.restore_backup(s, result.path)
    with _db(s.db_path) as conn:
        assert conn.execute("SELECT name FROM tenant").fetchone()[0] == "Corvus Advisors"
        assert conn.execute("SELECT kind FROM backup_log WHERE kind = 'restore'").fetchone() is not None
    assert (s.objects_dir / "t" / "t1" / "c" / "c1" / "e" / "e1" / "files" / "v1.xlsx").read_bytes() == b"PK\x03\x04 workbook\n"
    assert (aside / "fsweb.sqlite3").is_file()  # what the restore replaced is kept
    damaged = backup.run_backup(s, reason="manual").path
    (damaged / "fsweb.sqlite3").write_bytes(b"not a database")
    with pytest.raises(backup.BackupError, match="manifest"):
        backup.restore_backup(s, damaged)


def test_backups_are_pruned_and_copied_to_the_second_folder(tmp_path: Path) -> None:
    s = _seeded(tmp_path)
    second = tmp_path / "usb"
    with _db(s.db_path) as conn:
        conn.execute("UPDATE tenant SET settings_json = ?", (json.dumps({"backup_copy_dir": str(second)}),))
    start = datetime(2026, 10, 1, tzinfo=UTC)
    for day in range(4):
        r = backup.run_backup(s, now=start + timedelta(days=day), keep=2)
        assert r.ok and r.copied_to is not None and r.copy_error is None
    kept = [p.name for p in backup.list_backups(s)]
    assert kept == ["20261004-000000", "20261003-000000"]
    copies = sorted(p.name for p in (r.copied_to).iterdir())  # type: ignore[union-attr]
    assert "objects" in copies and "20261004-000000" in copies
    assert not backup.backup_due(s, now=start + timedelta(days=3, hours=23))
    assert backup.backup_due(s, now=start + timedelta(days=4, hours=1))


def test_stale_runtime_file_means_not_running(tmp_path: Path) -> None:
    """A runtime file left by a process that no longer runs (crash, power cut) must not block a new start."""
    s = _settings(tmp_path)
    ensure_layout(s)
    s.runtime_path.write_text(json.dumps({"pid": 2_000_000_000, "port": 8746, "url": "http://127.0.0.1:8746/"}), encoding="utf-8")
    assert desktop.read_runtime(s) is None
    assert not s.runtime_path.exists()


def test_pid_alive_tells_running_from_ended_processes() -> None:
    """On Windows (CI) an ended process stays openable while a handle to it exists: the exit code decides."""
    import subprocess
    import sys

    assert _pid_alive(os.getpid())
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert not _pid_alive(child.pid)


def test_concurrent_shutdown_waits_for_the_first(tmp_path: Path) -> None:
    """The stop watcher and the main thread both call shutdown(): the second must not return before the first has
    checkpointed the database and removed the runtime file (otherwise the process exits half-way)."""
    import threading

    s = _settings(tmp_path, port=find_free_port())
    rt = desktop.Runtime(s, open_browser=False)
    rt.start()
    first = threading.Thread(target=rt.shutdown)
    first.start()
    rt.shutdown()  # returns only when the first call has finished
    assert not s.runtime_path.exists()
    first.join(5)
    assert not first.is_alive()
