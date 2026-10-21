"""The Windows bundle's package list stays in step with the code: every runtime dependency of fscore, fsweb and
fsoffline is bundled (server-only ones excepted), every pin is hash-checked, and nothing copyleft or macOS-only
sneaks in."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "fsoffline" / "packaging"
SERVER_ONLY = {"psycopg", "boto3"}  # imported only for PostgreSQL / S3, never by the desktop edition
OURS = {"fscore", "fsweb", "fsoffline"}
NEVER = {"pymupdf", "pymupdf4llm", "pymupdf-layout", "uvloop", "psycopg", "psycopg-binary", "boto3", "botocore", "numpy", "onnxruntime"}


def _name(req: str) -> str:
    return re.split(r"[\[<>=!~;\s]", req.strip(), maxsplit=1)[0].lower().replace("_", "-")


def _deps(pyproject: Path, extras: tuple[str, ...] = ()) -> set[str]:
    meta = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    reqs = list(meta.get("dependencies", []))
    for extra in extras:
        reqs += meta.get("optional-dependencies", {}).get(extra, [])
    return {_name(r) for r in reqs if ";" not in r or "win32" in r or "Windows" in r}


def _bundled() -> set[str]:
    lines = (PACKAGING / "requirements-windows.txt").read_text(encoding="utf-8").splitlines()
    return {_name(line) for line in lines if line.strip() and not line.lstrip().startswith("#")}


def test_every_runtime_dependency_is_bundled() -> None:
    wanted = (_deps(ROOT / "fscore" / "pyproject.toml", ("ai", "win")) | _deps(ROOT / "fsweb" / "pyproject.toml")
              | _deps(ROOT / "fsoffline" / "pyproject.toml")) - OURS - SERVER_ONLY
    assert wanted - _bundled() == set(), "add these to fsoffline/packaging/requirements-windows.txt and run `make windows-lock`"


def test_lock_is_hashed_windows_only_and_permissive() -> None:
    text = (PACKAGING / "requirements-windows.lock").read_text(encoding="utf-8")
    pins = re.findall(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)", text, re.M)
    names = {n.lower().replace("_", "-") for n, _v in pins}
    assert len(pins) > 40
    assert all(block.count("--hash=sha256:") >= 1 for block in re.split(r"\n(?=[A-Za-z0-9])", text) if "==" in block)
    assert {"pywin32", "colorama", "pypdfium2", "pypdf"} <= names  # Windows-only and PDF packages resolved for the target
    assert names & NEVER == set()
    assert _bundled() - names == set()  # every bundled requirement is resolved in the lock
    assert "/Users/" not in text and "/private/" not in text  # no build-machine paths in a committed file


def test_downloads_are_pinned() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_windows", PACKAGING / "build_windows.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name, item in mod.DOWNLOADS.items():
        assert item["url"].startswith("https://"), name
        assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"]), f"{name}: pin the SHA-256 of the download"


@pytest.mark.parametrize("define", ["VERSION", "STAGE", "REDIST", "LO_MSI", "VCREDIST", "OUTFILE"])
def test_installer_script_uses_the_build_defines(define: str) -> None:
    nsi = (PACKAGING / "installer.nsi").read_text(encoding="utf-8")
    assert "${" + define + "}" in nsi
    build = (PACKAGING / "build_windows.py").read_text(encoding="utf-8")
    assert f"-D{define}=" in build
