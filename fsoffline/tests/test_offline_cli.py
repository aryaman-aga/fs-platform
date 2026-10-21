from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from fsoffline import __main__ as cli


def test_where_and_env_redacted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "data"
    assert cli.main(["--data-dir", str(data), "where"]) == 0
    assert capsys.readouterr().out.strip() == str(data)
    assert cli.main(["--data-dir", str(data), "env"]) == 0
    out = capsys.readouterr().out
    assert "FSWEB_KMS_MASTER_KEY=***" in out
    assert "FSWEB_ENV=offline" in out


def test_pubkey_and_trust(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "data"
    assert cli.main(["--data-dir", str(data), "pubkey"]) == 0
    key = capsys.readouterr().out.strip()
    assert len(base64.b64decode(key)) == 32
    server_key = base64.b64encode(b"s" * 32).decode()
    assert cli.main(["--data-dir", str(data), "trust", server_key]) == 0
    assert server_key in (data / "trusted_package_keys.txt").read_text()
    assert cli.main(["--data-dir", str(data), "trust", base64.b64encode(b"short").decode()]) == 2


def test_run_rejects_public_host(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--data-dir", str(tmp_path / "d"), "run", "--host", "0.0.0.0"]) == 2
    assert "loopback" in capsys.readouterr().err


def test_export_import_map_to_manage(fake_fsweb: Any, tmp_path: Path) -> None:
    data = str(tmp_path / "d")
    assert cli.main(["--data-dir", data, "export", "--id", "E7", "--out", "e.fspkg"]) == 0
    assert cli.main(["--data-dir", data, "import", "--file", "e.fspkg", "--quarantine"]) == 0
    assert fake_fsweb.manage_calls == [
        ["export-engagement", "--id", "E7", "--out", "e.fspkg"],
        ["import-engagement", "--file", "e.fspkg", "--quarantine"],
    ]
