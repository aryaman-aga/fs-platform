"""Shared helpers for the Module P / synthetic-fixture tests."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from fscore.engine.display import render
from fscore.io.xlsx_reader import Workbook
from fscore.synthetic.generator import SyntheticResult, make_workbook

_CACHE_DIR = Path(tempfile.mkdtemp(prefix="fs-synth-"))

SOFFICE_CANDIDATES = ("/Applications/LibreOffice.app/Contents/MacOS/soffice", "soffice", "libreoffice")


@lru_cache(maxsize=64)
def cached_workbook(seed: int = 7, exact: bool = False, layout: str = "annual", inject: tuple[str, ...] = ()) -> SyntheticResult:
    name = f"s{seed}_{'x' if exact else 'r'}_{layout}_{'-'.join(inject) or 'clean'}"[:120]
    return make_workbook(_CACHE_DIR / f"{name}.xlsx", seed=seed, exact=exact, layout=layout, inject=list(inject))


def printed(wb: Workbook, sheet: str, row: int, col: int) -> Decimal:
    c = wb.cell(sheet, row, col)
    if c is None or c.value is None or isinstance(c.value, str):
        return Decimal(0)
    v = wb.render_cell(c).value
    return v if v is not None else Decimal(0)


def printed_alias(wb: Workbook, index: dict[str, Any], alias: str, period: str) -> Decimal:
    sheet, row, cols = index[alias]
    return printed(wb, sheet, row, int(cols[period]))


def display(value: Any, fmt: str) -> Decimal | None:
    return render(value, fmt).value


def soffice() -> str | None:
    for cand in SOFFICE_CANDIDATES:
        p = shutil.which(cand) if not cand.startswith("/") else (cand if Path(cand).exists() else None)
        if p:
            return p
    return None


def libreoffice_recalc(src: Path, out_dir: Path) -> Path:
    """Recalculate a workbook with LibreOffice headless (unique profile: other agents run it too)."""
    exe = soffice()
    if exe is None:
        raise RuntimeError("LibreOffice not available")
    profile = out_dir / f"lo-profile-{uuid.uuid4().hex}"
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [exe, f"-env:UserInstallation=file://{profile}", "--headless", "--norestore", "--convert-to", "xlsx:Calc MS Excel 2007 XML",
           "--outdir", str(out_dir / "lo"), str(src)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=240)
    shutil.rmtree(profile, ignore_errors=True)
    return out_dir / "lo" / src.name
