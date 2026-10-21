"""Microsoft Excel via COM (Windows + pywin32): the preferred engine (02 §10A).

Unattended Office automation is not supported by Microsoft (11 §3), so every
call runs a *new* Excel instance with alerts, events, macros and link updates
disabled, under a watchdog that kills only the EXCEL.EXE process this call
started (by PID). The workbook is opened read-only and closed without saving.
The ops Windows render service calls :func:`export_pdf`.
"""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

ENGINE = "excel"

XL_TYPE_PDF = 0
XL_QUALITY_STANDARD = 0
MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3


class ExcelComError(RuntimeError):
    pass


def available() -> bool:
    return sys.platform == "win32" and importlib.util.find_spec("win32com") is not None


def _default_dispatch() -> Any:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    return win32com.client.DispatchEx("Excel.Application")


def _default_pid_of(app: Any) -> int | None:
    try:
        import win32process

        return int(win32process.GetWindowThreadProcessId(app.Hwnd)[1])
    except Exception:
        return None


def _default_kill(pid: int) -> None:
    import subprocess

    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)  # noqa: S603,S607


def _default_alive(pid: int) -> bool:
    try:
        import psutil

        return bool(psutil.pid_exists(pid))
    except ImportError:
        return False


def _page_count(pdf_path: Path) -> int:
    try:
        pymupdf: Any = importlib.import_module("pymupdf")

        with pymupdf.open(str(pdf_path)) as d:
            return int(d.page_count)
    except Exception:
        return 0


def export_pdf(
    xlsx_path: Path,
    pdf_path: Path,
    sheets: list[str] | None,
    timeout_s: int = 300,
    *,
    _dispatch: Callable[[], Any] | None = None,
    _pid_of: Callable[[Any], int | None] | None = None,
    _kill: Callable[[int], None] | None = None,
    _alive: Callable[[int], bool] | None = None,
) -> dict[str, object]:
    """Export the given sheets (in order) to PDF with a dedicated Excel instance.

    Returns ``{engine, engine_version, excel_build, pages}``. The private
    keyword arguments exist for tests (fake COM objects on non-Windows hosts).
    """
    if _dispatch is None and not available():
        raise ExcelComError("Excel COM automation needs Windows with pywin32 and licensed Microsoft Excel")
    dispatch = _dispatch or _default_dispatch
    pid_of = _pid_of or _default_pid_of
    kill = _kill or _default_kill
    alive = _alive or _default_alive
    app = dispatch()
    pid = pid_of(app)
    timed_out = threading.Event()
    done = threading.Event()

    def watchdog() -> None:
        if not done.wait(timeout_s):
            timed_out.set()
            if pid:
                kill(pid)  # only the instance this call started

    guard = threading.Thread(target=watchdog, name="excel-watchdog", daemon=True)
    guard.start()
    wb = None
    try:
        app.Visible = False
        app.DisplayAlerts = False
        app.ScreenUpdating = False
        app.EnableEvents = False
        app.AskToUpdateLinks = False
        app.AutomationSecurity = MSO_AUTOMATION_SECURITY_FORCE_DISABLE
        wb = app.Workbooks.Open(str(Path(xlsx_path).resolve()), 0, True, None, None, None, True, None, None, False, False, None, False)
        app.CalculateFull()
        names = list(sheets) if sheets else [ws.Name for ws in wb.Worksheets if int(ws.Visible) == -1]
        if not names:
            raise ExcelComError("no print sheets to export")
        wb.Worksheets(names[0]).Select()
        for n in names[1:]:
            wb.Worksheets(n).Select(False)  # extend the selection: sheets are grouped like a manual print
        Path(pdf_path).parent.mkdir(parents=True, exist_ok=True)
        app.ActiveSheet.ExportAsFixedFormat(
            Type=XL_TYPE_PDF,
            Filename=str(Path(pdf_path).resolve()),
            Quality=XL_QUALITY_STANDARD,
            IncludeDocProperties=True,
            IgnorePrintAreas=False,
            OpenAfterPublish=False,
        )
        version = str(app.Version)
        build = f"{app.Version}.{app.Build}"
        if timed_out.is_set():
            raise ExcelComError(f"Excel timed out after {timeout_s}s")
        return {"engine": ENGINE, "engine_version": f"Microsoft Excel {version}", "excel_build": build, "pages": _page_count(Path(pdf_path))}
    except ExcelComError:
        raise
    except Exception as exc:
        if timed_out.is_set():
            raise ExcelComError(f"Excel timed out after {timeout_s}s") from exc
        raise ExcelComError(f"Excel export failed: {type(exc).__name__}: {exc}") from exc
    finally:
        done.set()
        if wb is not None:
            with contextlib.suppress(Exception):
                wb.Close(SaveChanges=False)
        with contextlib.suppress(Exception):
            app.Quit()
        if pid and alive(pid):
            kill(pid)
