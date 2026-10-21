from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fsrender_win import __main__ as cli
from fsrender_win import service


def test_check_config_hides_token(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    monkeypatch.setenv("FSRENDER_API_URL", "https://fs-internal.example-firm.in")
    monkeypatch.setenv("FSRENDER_WORKER_TOKEN", "super-secret-token")
    monkeypatch.setenv("FSRENDER_CONFIG", str(tmp_path / "none.ini"))
    assert cli.main(["check-config"]) == 0
    out = capsys.readouterr().out
    assert "fs-internal.example-firm.in" in out
    assert "super-secret-token" not in out


def test_config_error_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in list(__import__("os").environ):
        if key.startswith("FSRENDER_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("FSRENDER_CONFIG", str(tmp_path / "none.ini"))
    assert cli.main(["check-config"]) == 2


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_service_commands_need_pywin32() -> None:
    assert service.HAVE_PYWIN32 is False
    with pytest.raises(SystemExit, match="pywin32"):
        cli.main(["service", "install"])
    with pytest.raises(SystemExit, match="pywin32"):
        cli.main(["service-host"])
