"""LibreOffice headless engine (fallback when Excel/COM is unavailable, 02 §10A).

The workbook is never round-tripped through a library: a temporary copy is
made with ``xl/workbook.xml`` patched so that non-print sheets are hidden
(print areas, hidden rows/columns and page setup are untouched), plus one page-setup
correction where LibreOffice and Excel disagree (see ``excel_fit_scales``). Each run
uses its own LibreOffice user profile so concurrent conversions do not collide.
"""

from __future__ import annotations

import configparser
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
import zipfile
from functools import lru_cache
from pathlib import Path

from lxml import etree

from fscore.io.ooxml import NS_MAIN, Package, parse_xml, q
from fscore.io.pdf_reader import page_count

ENGINE = "libreoffice"

_CANDIDATES = (
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/lib/libreoffice/program/soffice",
    "/opt/libreoffice/program/soffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)


CREATE_NO_WINDOW = 0x08000000


class LibreOfficeError(RuntimeError):
    pass


def _child_env() -> dict[str, str]:
    """Environment for child processes: FSWEB_* variables hold secrets and are not passed on."""
    return {k: v for k, v in os.environ.items() if not k.upper().startswith("FSWEB_")}


def _no_window() -> int:
    return CREATE_NO_WINDOW if sys.platform == "win32" else 0


def find_soffice() -> str | None:
    env = os.environ.get("FSCORE_SOFFICE")
    if env and Path(env).exists():
        return env
    for name in ("soffice", "libreoffice"):
        hit = shutil.which(name)
        if hit:
            return hit
    for cand in _CANDIDATES:
        if Path(cand).exists():
            return cand
    return None


def available() -> bool:
    return find_soffice() is not None


def _version_ini(binary: str) -> str | None:
    """'LibreOffice <version> <build>' from ``program/version.ini`` (Windows' soffice prints no version)."""
    ini = Path(binary).with_name("version.ini")
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(ini, encoding="utf-8-sig")
    except (OSError, configparser.Error, UnicodeDecodeError):
        return None
    if not parser.has_section("Version"):
        return None
    version = parser.get("Version", "MsiProductVersion", fallback="").strip()
    build = parser.get("Version", "buildid", fallback="").strip()
    if not version:
        return None
    return f"LibreOffice {version} {build}".strip()


@lru_cache(maxsize=4)
def soffice_version(binary: str) -> str:
    unknown = "LibreOffice (unknown version)"
    try:
        out = subprocess.run(  # noqa: S603 - the located soffice binary, no shell
            [binary, "--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, check=False,
            env=_child_env(), creationflags=_no_window(),
        )
        lines = (out.stdout or out.stderr or "").strip().splitlines()
    except (OSError, subprocess.TimeoutExpired):
        lines = []
    if lines:
        return lines[0].strip()
    return _version_ini(binary) or unknown


def excel_fit_scales(src: Path, print_sheets: list[str]) -> dict[str, int]:
    """Explicit print scales (sheet part → percent) for sheets that LibreOffice would not shrink.

    "Fit to N page(s) wide" on a print area that is a single column wider than the paper counts as one
    page wide in LibreOffice (a column is never split), so the text runs off the page; Excel shrinks the
    sheet instead. Those sheets get the scale Excel computes, one percent smaller for font-metric slack.
    """
    from fscore.io.xlsx_reader import Workbook
    from fscore.pdf.render_native import PAPER_SIZES, col_width_pt, plan_sheet

    out: dict[str, int] = {}
    with Workbook.open(src) as wb:  # closed promptly: an open handle blocks deleting the file on Windows
        for name in print_sheets:
            sheet = wb.get_sheet(name)
            if sheet is None:
                continue
            sheet.ensure_loaded()
            ps = sheet.page
            if not ps.fit_to_page or ps.fit_to_width < 1:
                continue
            widths = [w for a in sheet.print_bounds for c in range(a.c1, a.c2 + 1)
                      if not sheet.col_hidden(c) and (w := col_width_pt(sheet.col_width(c))) > 0]
            pw, ph = PAPER_SIZES.get(ps.paper_size, PAPER_SIZES[9])
            avail_w = (ph if ps.orientation == "landscape" else pw) - 72.0 * (ps.margins["left"] + ps.margins["right"])
            if len(widths) != 1 or widths[0] <= avail_w * ps.fit_to_width:
                continue
            out[sheet.part] = max(10, math.floor(plan_sheet(wb, sheet).scale * 100) - 1)
    return out


def _set_scale(data: bytes, percent: int) -> bytes:
    root = parse_xml(data)
    pr = root.find(f"{q('sheetPr')}/{q('pageSetUpPr')}")
    if pr is not None:
        pr.set("fitToPage", "0")
    setup = root.find(q("pageSetup"))
    if setup is None:
        setup = etree.Element(q("pageSetup"))
        margins = root.find(q("pageMargins"))
        if margins is None:
            return data
        margins.addnext(setup)
    setup.set("scale", str(percent))
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def make_print_copy(src: Path, dst: Path, print_sheets: list[str]) -> None:
    """Copy the package, hiding every sheet that is not a print sheet and applying ``excel_fit_scales``."""
    with Package(src) as pkg:
        wb_part = pkg.workbook_part()
    wanted = {s.strip().lower() for s in print_sheets}
    scales = excel_fit_scales(src, print_sheets)
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename in scales:
                data = _set_scale(data, scales[info.filename])
            elif info.filename == wb_part:
                root = parse_xml(data)
                sheets_el = root.find(q("sheets"))
                first_idx: int | None = None
                if sheets_el is not None:
                    for idx, sh in enumerate(sheets_el.findall(q("sheet"))):
                        name = (sh.get("name") or "").strip().lower()
                        if name in wanted and sh.get("state") not in ("hidden", "veryHidden"):
                            if first_idx is None:
                                first_idx = idx
                        elif sh.get("state") != "veryHidden":
                            sh.set("state", "hidden")
                if first_idx is None:
                    raise LibreOfficeError("no visible print sheet to render")
                views = root.find(q("bookViews"))
                if views is not None:
                    for v in views.findall(q("workbookView")):
                        v.set("activeTab", str(first_idx))
                        v.set("firstSheet", str(first_idx))
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            zout.writestr(info, data)


def _taskkill() -> str:
    return os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", "taskkill.exe")


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(  # noqa: S603 - fixed system binary, no shell
                [_taskkill(), "/F", "/T", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL, capture_output=True,
                check=False, env=_child_env(), creationflags=_no_window(),
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


def export_pdf(xlsx_path: Path, pdf_path: Path, sheets: list[str] | None, timeout_s: int = 180) -> dict[str, object]:
    """Convert the print sheets of ``xlsx_path`` to ``pdf_path`` with LibreOffice headless."""
    binary = find_soffice()
    if binary is None:
        raise LibreOfficeError("LibreOffice (soffice) is not installed")
    work = Path(tempfile.mkdtemp(prefix="fs-lo-"))
    profile = Path(tempfile.mkdtemp(prefix=f"lo-profile-{uuid.uuid4().hex[:8]}-"))
    try:
        copy = work / f"print-{uuid.uuid4().hex[:8]}.xlsx"
        if sheets:
            make_print_copy(Path(xlsx_path), copy, sheets)
        else:
            shutil.copyfile(xlsx_path, copy)
        cmd = [
            binary,
            f"-env:UserInstallation={profile.as_uri()}",
            "--headless", "--invisible", "--norestore", "--nolockcheck", "--nodefault", "--nofirststartwizard",
            "--convert-to", "pdf:calc_pdf_Export",
            "--outdir", str(work),
            str(copy),
        ]
        kwargs: dict[str, object] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                                     "env": _child_env()}
        if sys.platform == "win32":
            kwargs["creationflags"] = CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(cmd, **kwargs)  # type: ignore[call-overload]  # noqa: S603
        except OSError as exc:
            raise LibreOfficeError(f"LibreOffice could not be started: {exc}") from exc
        try:
            _out, err = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            _kill_tree(proc)
            proc.communicate()
            raise LibreOfficeError(f"LibreOffice timed out after {timeout_s}s") from exc
        produced = work / (copy.stem + ".pdf")
        if proc.returncode != 0 or not produced.exists():
            msg = (err or b"").decode("utf-8", "replace").strip()[:300]
            raise LibreOfficeError(f"LibreOffice conversion failed (exit {proc.returncode}): {msg}")
        Path(pdf_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(produced), str(pdf_path))
        try:
            pages = page_count(Path(pdf_path))
        except Exception as exc:  # pypdf could not parse what LibreOffice wrote
            raise LibreOfficeError(f"LibreOffice produced an unreadable PDF: {type(exc).__name__}: {exc}") from exc
        return {"engine": ENGINE, "engine_version": soffice_version(binary), "excel_build": None, "pages": pages}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(profile, ignore_errors=True)


__all__ = ["NS_MAIN", "LibreOfficeError", "available", "excel_fit_scales", "export_pdf", "find_soffice", "make_print_copy", "soffice_version"]
