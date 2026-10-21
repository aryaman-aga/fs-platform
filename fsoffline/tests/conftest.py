"""Make the src layout importable; provide fake fsweb modules so tests never need fsweb."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any, Literal

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class FakeFsweb:
    """Installs fake fsweb.{settings,manage,worker,main} modules into sys.modules."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.monkeypatch = monkeypatch
        self.manage_calls: list[list[str]] = []
        self.manage_exit: int | None = 0
        self.worker_started = 0
        self.disposed: list[bool] = []
        self.envs = ("dev", "test", "prod", "offline")

    def install(self) -> FakeFsweb:
        pkg = types.ModuleType("fsweb")
        pkg.__path__ = []  # type: ignore[attr-defined]
        settings = types.ModuleType("fsweb.settings")
        settings.Env = Literal[self.envs]  # type: ignore[attr-defined,valid-type]
        manage = types.ModuleType("fsweb.manage")
        worker = types.ModuleType("fsweb.worker")
        main = types.ModuleType("fsweb.main")
        state = types.ModuleType("fsweb.state")
        fake = self

        class _Settings:
            @classmethod
            def from_env(cls) -> str:
                return "SETTINGS"

        class _State:
            def __init__(self) -> None:
                self.db = types.SimpleNamespace(dispose=lambda: fake.disposed.append(True))

        settings.Settings = _Settings  # type: ignore[attr-defined]
        state.build_state = lambda s, component="worker": _State()  # type: ignore[attr-defined]

        def run_forever(stop_event: Any = None) -> None:
            fake.worker_started += 1
            if stop_event is not None:
                stop_event.wait(5)

        worker.run_forever = run_forever  # type: ignore[attr-defined]
        main.create_app = lambda: "ASGI-APP"  # type: ignore[attr-defined]

        def manage_main(argv: list[str]) -> int:
            fake.manage_calls.append(list(argv))
            return 0 if fake.manage_exit is None else fake.manage_exit

        manage.main = manage_main  # type: ignore[attr-defined]
        for name, module in {"fsweb": pkg, "fsweb.settings": settings, "fsweb.manage": manage, "fsweb.worker": worker,
                             "fsweb.main": main, "fsweb.state": state}.items():
            self.monkeypatch.setitem(sys.modules, name, module)
        return self


@pytest.fixture
def fake_fsweb(monkeypatch: pytest.MonkeyPatch) -> FakeFsweb:
    return FakeFsweb(monkeypatch).install()


@pytest.fixture(autouse=True)
def _isolated_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """The launcher exports FSWEB_* into os.environ; keep tests from leaking it."""
    import os

    for key in list(os.environ):
        if key.startswith(("FSWEB_", "FSOFFLINE_")):
            monkeypatch.delenv(key)
    monkeypatch.setattr(os, "environ", os.environ.copy())
