"""Shared pytest configuration for fscore.

Area-specific fixtures live in the test modules themselves or in
``helpers_<area>.py`` files next to them (several builders work in parallel).
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail on any outbound connection (02 §12: the core package is offline)."""

    def guard(*_a: object, **_k: object) -> None:
        raise AssertionError("network access attempted from the offline engine")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    d = tmp_path / "out"
    d.mkdir()
    return d
