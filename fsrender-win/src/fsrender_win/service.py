"""Windows-service wrapper (pywin32). Importable on any OS; usable only on Windows.

    fsrender-win service install --startup auto
    fsrender-win service start | stop | remove

Run the service under a dedicated, non-admin account that has a licensed Excel
profile (shared-computer activation where Microsoft 365 Apps are used), "Log on
as a service" rights and read access to the token file only.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence

try:  # pragma: no cover - only importable on Windows with pywin32
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    HAVE_PYWIN32 = True
except ImportError:  # macOS / Linux / Windows without pywin32
    HAVE_PYWIN32 = False

SERVICE_NAME = "FSRenderWorker"
SERVICE_DISPLAY_NAME = "FS-Platform Render Worker"
SERVICE_DESCRIPTION = "Exports FS-Platform statement PDFs with Microsoft Excel for the internal render queue."

log = logging.getLogger("fsrender_win")


def run_service_body(stop: threading.Event) -> None:
    """Load configuration and run the render loop until ``stop`` is set."""
    from fsrender_win.__main__ import configure_logging
    from fsrender_win.config import load_config
    from fsrender_win.worker import build_worker

    config = load_config()
    configure_logging(config.log_dir)
    worker = build_worker(config)
    try:
        worker.run_forever(stop)
    finally:
        worker.client.close()


if HAVE_PYWIN32:  # pragma: no cover - exercised on Windows only

    class FSRenderService(win32serviceutil.ServiceFramework):  # type: ignore[misc]
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY_NAME
        _svc_description_ = SERVICE_DESCRIPTION

        def __init__(self, args: Sequence[str]) -> None:
            super().__init__(args)
            self._stop = threading.Event()
            self._wait_handle = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self) -> None:  # pywin32 API name
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._stop.set()
            win32event.SetEvent(self._wait_handle)

        def SvcDoRun(self) -> None:  # pywin32 API name
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE, servicemanager.PYS_SERVICE_STARTED, (self._svc_name_, "")
            )
            run_service_body(self._stop)


def host_service() -> int:
    """Entry point when the Service Control Manager starts a frozen (PyInstaller/MSI) build."""
    if not HAVE_PYWIN32:
        raise SystemExit("the Windows service host needs Windows and pywin32")
    servicemanager.Initialize()  # pragma: no cover
    servicemanager.PrepareToHostSingle(FSRenderService)  # pragma: no cover
    servicemanager.StartServiceCtrlDispatcher()  # pragma: no cover
    return 0  # pragma: no cover


def handle_command_line(argv: Sequence[str]) -> int:
    if not HAVE_PYWIN32:
        raise SystemExit("the Windows service wrapper needs Windows and pywin32 (pip install fsrender-win[win])")
    win32serviceutil.HandleCommandLine(FSRenderService, argv=[SERVICE_NAME, *argv])  # pragma: no cover
    return 0  # pragma: no cover
