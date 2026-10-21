"""Make the src layout importable and provide the fake render API fixture."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rw_fakes import FakeRenderServer


@pytest.fixture
def fake_server() -> FakeRenderServer:
    return FakeRenderServer()
