"""Microsoft Excel via COM (Windows + pywin32): the preferred engine (02 §10A).

Unattended Office automation is not supported by Microsoft (11 §3), so every
call runs a *new* Excel instance with alerts, events, macros and link updates
disabled, under a watchdog that kills only the EXCEL.EXE process this call
started (by PID). The workbook is opened read-only and closed without saving.
The ops Windows render service calls :func:`export_pdf`.

Excel is optional: :func:`available` is true only when Excel's COM class is
registered, and any failure to start or drive Excel raises :class:`ExcelComError`,
so ``engine="auto"`` falls back to LibreOffice and then the built-in renderer.
"""

from __future__ import annotations

import contextlib
import functools
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

ENGINE = "excel"
PROG_ID = "Excel.Application"

XL_TYPE_PDF = 0
XL_QUALITY_STANDARD = 0
MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3
CREATE_NO_WINDOW = 0x08000000


class ExcelComError(RuntimeError):
    pass


def _on_windows() -> bool:
    return sys.platform == "win32"


def _registered_clsid(prog_id: str) -> str | None:
    """CLSID registered for a COM ProgID (``HKEY_CLASSES_ROOT\\<ProgID>\\CLSID``), or None."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id + "\\CLSID") as key:
            value = winreg.QueryValue(key, None)
    except OSError:
        return None
    return value or None


def _pywin32_loads() -> bool:
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except Exception:  # ImportError, or a broken pywin32 install (DLL load failure)
        return False
    return True


@functools.lru_cache(maxsize=1)
def _excel_registered() -> bool:
    return _registered_clsid(PROG_ID) is not None and _pywin32_loads()


def available() -> bool:
    """Windows, pywin32 importable and Excel's COM class registered (checked once per process)."""
    return _on_windows() and _excel_registered()


def _default_dispatch() -> Any:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    return win32com.client.DispatchEx(PROG_ID)


def _default_pid_of(app: Any) -> int | None:
    try:
        import win32process

        return int(win32process.GetWindowThreadProcessId(app.Hwnd)[1])
    except Exception:
        return None


def _taskkill() -> str:
    return os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", "taskkill.exe")


def _child_env() -> dict[str, str]:
    """Environment for child processes: FSWEB_* variables hold secrets and are not passed on."""
    return {k: v for k, v in os.environ.items() if not k.upper().startswith("FSWEB_")}


def _default_kill(pid: int) -> None:
    with contextlib.suppress(OSError):
        subprocess.run(  # noqa: S603 - fixed system binary, no shell
            [_taskkill(), "/F", "/PID", str(pid)], stdin=subprocess.DEVNULL, capture_output=True, check=False,
            env=_child_env(), creationflags=CREATE_NO_WINDOW if _on_windows() else 0,
        )


def _default_alive(pid: int) -> bool:
    try:
        import psutil

        return bool(psutil.pid_exists(pid))
    except ImportError:
        return False


def _page_count(pdf_path: Path) -> int:
    try:
        from fscore.io.pdf_reader import page_count

        return page_count(pdf_path)
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

    Returns ``{engine, engine_version, excel_build, pages}``. Every failure (Excel missing,
    COM errors, timeouts) raises :class:`ExcelComError`. The private keyword arguments
    exist for tests (fake COM objects on non-Windows hosts).
    """
    if _dispatch is None and not available():
        raise ExcelComError("Excel COM automation needs Windows with pywin32 and licensed Microsoft Excel")
    dispatch = _dispatch or _default_dispatch
    pid_of = _pid_of or _default_pid_of
    kill = _kill or _default_kill
    alive = _alive or _default_alive
    try:
        app = dispatch()
    except Exception as exc:  # pywintypes.com_error, OSError, ...: Excel cannot be started
        raise ExcelComError(f"Excel could not be started: {type(exc).__name__}: {exc}") from exc
    pid: int | None = None
    timed_out = threading.Event()
    done = threading.Event()

    def watchdog() -> None:
        if not done.wait(timeout_s):
            timed_out.set()
            if pid:
                kill(pid)  # only the instance this call started

    wb = None
    try:
        pid = pid_of(app)
        threading.Thread(target=watchdog, name="excel-watchdog", daemon=True).start()
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
        with contextlib.suppress(Exception):
            if pid and alive(pid):
                kill(pid)
