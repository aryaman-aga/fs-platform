"""The installed desktop runtime: one server per PC on 127.0.0.1, a tray icon, logs and backups in the data folder.

``python -m fsoffline desktop`` is what the Start-menu shortcut runs (``pythonw.exe``, no console); ``serve`` is the
same without the tray (CI, troubleshooting). A second launch — from any Windows account — finds the running server
and only opens the browser. The installer and the Recovery tool stop a running server through a named stop event.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import secrets
import socket
import sqlite3
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from fsoffline import backup as backups
from fsoffline.install import DEFAULT_PORT, load_install_config
from fsoffline.launcher import (
    InstanceLockError,
    WorkerHandle,
    _pid_alive,
    instance_lock,
    make_server,
    run_manage,
    start_worker,
    supported_env_name,
    wait_for_port,
)
from fsoffline.settings import (
    OfflineSettings,
    ai_enabled,
    build_env,
    ensure_layout,
    url_host,
    validate_bind_host,
)

log = logging.getLogger("fsoffline.desktop")

PORT_ATTEMPTS = 20
BACKUP_CHECK_EVERY = timedelta(minutes=30)
_EVERYONE_SDDL = "D:(A;;GA;;;WD)"  # any account may see the server is running and ask it to stop (installer, Recovery)


# --------------------------------------------------------------------------- identity and logging
def instance_id(settings: OfflineSettings) -> str:
    return hashlib.sha256(str(settings.data_dir.resolve()).lower().encode()).hexdigest()[:12]


def configure_file_logging(settings: OfflineSettings, level: int = logging.INFO) -> None:
    """Logs go to ``<data>\\logs`` (fsweb keeps an existing JSON handler). Under pythonw there is no console at all:
    ``sys.stdout``/``sys.stderr`` are None and uvicorn and Alembic write to them, so they are redirected too."""
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(settings.logs_dir / "fsplatform.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    try:
        from fsweb.logging_setup import JsonFormatter

        handler.setFormatter(JsonFormatter())
    except ImportError:  # pragma: no cover - fsweb is always bundled
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    if sys.stdout is None or sys.stderr is None or not sys.stderr.isatty():
        console = open(settings.logs_dir / "console.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - lives as long as the process
        sys.stdout = sys.stderr = console


def show_error(message: str) -> None:
    """A message box when there is no console to print to (Windows); stderr elsewhere."""
    log.error("%s", message)
    if os.name == "nt":  # pragma: no cover - Windows
        with contextlib.suppress(Exception):
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "FS-Platform", 0x10)
            return
    print(message, file=sys.__stderr__ or sys.stderr)


# --------------------------------------------------------------------------- one server per PC, stop requests
def _win32() -> Any:
    try:
        import win32api
        import win32event
        import win32security
    except ImportError:
        return None
    return win32api, win32event, win32security


def _security_attributes(win32security: Any) -> Any:
    sa = win32security.SECURITY_ATTRIBUTES()
    sa.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(_EVERYONE_SDDL, win32security.SDDL_REVISION_1)
    return sa


@contextlib.contextmanager
def single_instance(settings: OfflineSettings) -> Iterator[bool]:
    """Yields True for the one process that may serve this data folder, False if another already does.

    Windows: a Global named mutex (visible across sessions, so two Windows accounts on one PC share one server; an
    access-denied answer means another account owns it). Elsewhere: the PID lock file.
    """
    w = _win32() if os.name == "nt" else None
    if w is not None:  # pragma: no cover - Windows
        win32api, win32event, win32security = w
        import pywintypes

        try:
            handle = win32event.CreateMutex(_security_attributes(win32security), False, f"Global\\FSPlatform-{instance_id(settings)}")
        except pywintypes.error as exc:
            if exc.winerror == 5:  # ERROR_ACCESS_DENIED: created by another account
                yield False
                return
            raise
        if win32api.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            win32api.CloseHandle(handle)
            yield False
            return
        try:
            yield True
        finally:
            win32api.CloseHandle(handle)
        return
    try:
        with instance_lock(settings.lock_path):
            yield True
    except InstanceLockError:
        yield False


class StopSignal:
    """A request to stop the server from another process (installer, uninstaller, Recovery tool, ``fsoffline stop``)."""

    def __init__(self, settings: OfflineSettings) -> None:
        self.settings = settings
        self.name = f"Global\\FSPlatform-Stop-{instance_id(settings)}"
        self.flag = settings.data_dir / "stop.request"
        self._event: Any = None
        w = _win32() if os.name == "nt" else None
        if w is not None:  # pragma: no cover - Windows
            _api, win32event, win32security = w
            self._event = win32event.CreateEvent(_security_attributes(win32security), True, False, self.name)
            win32event.ResetEvent(self._event)
        self.flag.unlink(missing_ok=True)

    def wait(self, timeout_s: float) -> bool:
        if self._event is not None:  # pragma: no cover - Windows
            import win32event

            return bool(win32event.WaitForSingleObject(self._event, int(timeout_s * 1000)) == win32event.WAIT_OBJECT_0)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.flag.exists():
                return True
            time.sleep(min(0.2, timeout_s))
        return self.flag.exists()

    def close(self) -> None:
        self.flag.unlink(missing_ok=True)
        if self._event is not None:  # pragma: no cover - Windows
            import win32api

            win32api.CloseHandle(self._event)
            self._event = None


def request_stop(settings: OfflineSettings, timeout_s: float = 60.0) -> bool:
    """Ask a running server to stop and wait for it; True when nothing is running any more (the server has cleaned
    up and its process has ended, so a new one can start at once)."""
    rt = read_runtime(settings)
    if rt is None:
        return True
    pid = int(rt.get("pid") or 0)
    w = _win32() if os.name == "nt" else None
    if w is not None:  # pragma: no cover - Windows
        import pywintypes

        win32api, win32event, _sec = w
        try:
            handle = win32event.OpenEvent(0x0002 | 0x00100000, False, f"Global\\FSPlatform-Stop-{instance_id(settings)}")  # EVENT_MODIFY_STATE | SYNCHRONIZE
        except pywintypes.error as exc:
            if exc.winerror != 2:  # ERROR_FILE_NOT_FOUND: no server listens for stop requests, the file is stale
                raise
            settings.runtime_path.unlink(missing_ok=True)
            return True
        win32event.SetEvent(handle)
        win32api.CloseHandle(handle)
    else:
        (settings.data_dir / "stop.request").touch()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if read_runtime(settings) is None and (pid == os.getpid() or not _pid_alive(pid)):
            return True
        time.sleep(0.25)
    return False


# --------------------------------------------------------------------------- port and runtime file
def _port_unavailable(exc: OSError) -> bool:
    return exc.errno in (48, 98, 10048) or getattr(exc, "winerror", None) in (10013, 10048)  # in use, or reserved (Hyper-V)


def bind_socket(host: str, preferred: int, attempts: int = PORT_ATTEMPTS) -> socket.socket:
    """Bind the server's socket ourselves (no window in which another program can take the port) and hand it to
    uvicorn; the preferred port first, then the next ones."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    last: OSError | None = None
    for port in range(preferred, preferred + attempts):
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: no other socket may share the port
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:  # POSIX: a restart may reuse the port while the last run's connections linger in TIME_WAIT
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            sock.listen(128)
            sock.setblocking(False)
            return sock
        except OSError as exc:
            sock.close()
            last = exc
            if not _port_unavailable(exc):
                raise
    raise OSError(f"no free port between {preferred} and {preferred + attempts - 1}") from last


def write_runtime(settings: OfflineSettings, port: int, url: str) -> None:
    from fsoffline import __version__

    tmp = settings.runtime_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pid": os.getpid(), "port": port, "url": url, "version": __version__,
                               "started": datetime.now(UTC).isoformat()}), encoding="utf-8")
    os.replace(tmp, settings.runtime_path)


def read_runtime(settings: OfflineSettings) -> dict[str, Any] | None:
    """The running server's address, or None — also when the file was left behind by a process that no longer runs."""
    try:
        data = json.loads(settings.runtime_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("url"):
        return None
    if not _pid_alive(int(data.get("pid") or 0)):
        settings.runtime_path.unlink(missing_ok=True)
        return None
    return data


# --------------------------------------------------------------------------- first run
def firm_exists(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    with contextlib.closing(sqlite3.connect(db_path, timeout=30)) as conn:
        try:
            return bool(conn.execute("SELECT COUNT(*) FROM tenant").fetchone()[0])
        except sqlite3.Error:
            return False


def prepare_setup(settings: OfflineSettings) -> str | None:
    """While no firm exists, a one-time token authorises the setup page, so neither another program on the PC nor a
    web page can create the firm and its administrator before the real user does. Returns the token, if any."""
    if firm_exists(settings.db_path):
        settings.setup_token_path.unlink(missing_ok=True)
        return None
    token = secrets.token_urlsafe(32)
    settings.setup_token_path.unlink(missing_ok=True)
    fd = os.open(settings.setup_token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "w", encoding="ascii", newline="\n") as fh:
        fh.write(token + "\n")
    return token


def _schema_state(settings: OfflineSettings) -> str:
    from fsweb.db import Database
    from fsweb.manage import sqlite_schema_state

    url = "sqlite:///" + settings.db_path.as_posix()
    db = Database(url)
    try:
        return str(sqlite_schema_state(db, url))
    finally:
        db.dispose()


def _checkpoint(db_path: Path) -> None:
    with contextlib.suppress(sqlite3.Error), contextlib.closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


# --------------------------------------------------------------------------- the runtime
class Runtime:
    """API server, job workers, backup schedule and stop watcher for one data folder."""

    def __init__(self, settings: OfflineSettings, *, open_browser: bool = True, notify: Callable[[str, str], None] | None = None) -> None:
        self.settings = settings
        self.open_browser = open_browser
        self.notify = notify or (lambda title, text: log.info("%s: %s", title, text))
        self.stop_event = threading.Event()
        self.threads: list[threading.Thread] = []
        self.server: Any = None
        self.worker: WorkerHandle | None = None
        self.url = ""
        self.port = 0
        self.on_shutdown: list[Callable[[], None]] = []
        self._stop_signal: StopSignal | None = None
        self._shutdown_once = threading.Lock()
        self._stopped = False
        self._shutdown_done = threading.Event()

    # ---- start
    def start(self) -> None:
        s = self.settings
        host = validate_bind_host(s.host)
        ensure_layout(s)
        preferred = s.port or load_install_config().port or DEFAULT_PORT
        sock = bind_socket(host, preferred)
        self.port = int(sock.getsockname()[1])
        env = build_env(s, self.port, env_name=supported_env_name())
        os.environ.update(env)
        if s.db_path.exists() and _schema_state(s) in ("behind", "unversioned"):
            self.notify("FS-Platform", "Updating the database; a backup is taken first.")
            result = backups.run_backup(s, reason="before upgrade", record=False)
            if not result.ok:
                sock.close()
                raise RuntimeError(f"the backup before the database upgrade failed ({result.error}); nothing was changed")
        if run_manage(["migrate"]) != 0:
            sock.close()
            raise RuntimeError("the database could not be prepared; see the log in " + str(s.logs_dir))
        token = prepare_setup(s)
        base = f"http://{url_host(host)}:{self.port}/"
        self.url = base + (f"setup#token={token}" if token else "")
        self.worker = start_worker(self.stop_event, env)
        if ai_enabled(s):
            self._thread("fsweb-ai-gateway", self._ai_worker)
        # connections still open after 10 s no longer hold up a stop (Python 3.12+ waits for every connection)
        self.server = make_server(host, self.port, log_config=None, timeout_graceful_shutdown=10)
        self._thread("fsweb-server", lambda: self.server.run(sockets=[sock]))
        self._stop_signal = StopSignal(s)  # before the runtime file: whoever finds the file can always ask for a stop
        write_runtime(s, self.port, base)
        self._thread("fsoffline-stop-watcher", self._watch_stop)
        self._thread("fsoffline-backups", self._backup_schedule)
        if self.open_browser:
            self._thread("fsoffline-browser", self._open_when_ready)
        log.info("FS-Platform on %s (data: %s)", base, s.data_dir)

    def _thread(self, name: str, target: Callable[[], Any]) -> None:
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        self.threads.append(t)

    def _ai_worker(self) -> None:
        from fsweb.worker import run_forever

        run_forever(self.stop_event, ["ai-gateway"])

    def _open_when_ready(self) -> None:
        if wait_for_port("127.0.0.1" if ":" not in self.settings.host else self.settings.host, self.port):
            self.open_browser_now()

    def open_browser_now(self) -> None:
        url = self.url
        if "#token=" in url and not self.settings.setup_token_path.exists():
            url = url.split("setup#", 1)[0]  # setup already done
        webbrowser.open(url)

    def _watch_stop(self) -> None:
        assert self._stop_signal is not None
        while not self.stop_event.is_set():
            if self._stop_signal.wait(1.0):
                log.info("stop requested by another program")
                self.shutdown()
                return

    def _backup_schedule(self) -> None:
        if self.stop_event.wait(120):  # let the first sign-in happen undisturbed
            return
        while not self.stop_event.is_set():
            if self.settings.db_path.exists() and backups.backup_due(self.settings):
                self.backup_now(reason="daily")
            if self.stop_event.wait(BACKUP_CHECK_EVERY.total_seconds()):
                return

    def backup_now(self, reason: str = "manual") -> backups.BackupResult | None:
        try:
            result = backups.run_backup(self.settings, reason=reason)
        except backups.BackupError as exc:
            self.notify("FS-Platform backup", str(exc))
            return None
        if not result.ok:
            self.notify("FS-Platform backup failed", result.error or "see the log")
        elif result.copy_error:
            self.notify("FS-Platform backup", f"Saved on this PC, but {result.copy_error}")
        elif reason == "manual":
            self.notify("FS-Platform backup", "Backup complete" + (f" (also copied to {result.copied_to})" if result.copied_to else ""))
        return result

    # ---- stop
    def wait(self) -> None:
        """Block until the server stops (stop request, Ctrl+C)."""
        try:
            while not self.stop_event.is_set() and any(t.name == "fsweb-server" and t.is_alive() for t in self.threads):
                time.sleep(0.5)
        except KeyboardInterrupt:
            log.info("interrupted")

    def shutdown(self) -> None:
        """Stop everything and clean up; a second caller (e.g. the main thread while the stop watcher is shutting
        down) waits for the first to finish, so the process never exits half-way through."""
        with self._shutdown_once:
            first = not self._stopped
            self._stopped = True
        if not first:
            self._shutdown_done.wait(timeout=90)
            return
        try:
            self._shutdown()
        finally:
            self._shutdown_done.set()

    def _shutdown(self) -> None:
        self.stop_event.set()
        if self.server is not None:
            self.server.should_exit = True
        for t in self.threads:
            if t is not threading.current_thread() and t.name in ("fsweb-server", "fsweb-ai-gateway"):
                t.join(timeout=15)
        if self.worker is not None:  # the job in hand finishes (or a worker child process is ended)
            if self.worker.thread is not None:
                self.worker.thread.join(timeout=15)
            self.worker.stop()
        _checkpoint(self.settings.db_path)
        self.settings.runtime_path.unlink(missing_ok=True)
        if self._stop_signal is not None:
            self._stop_signal.close()
        for callback in self.on_shutdown:
            with contextlib.suppress(Exception):
                callback()
        log.info("stopped")


def open_running(settings: OfflineSettings) -> bool:
    """A server already runs for this data folder (maybe in another Windows account): just open the browser."""
    rt = read_runtime(settings)
    if rt is None:
        return False
    webbrowser.open(str(rt["url"]))
    return True


def run_desktop(settings: OfflineSettings, *, tray: bool = True, open_browser: bool = True) -> int:
    configure_file_logging(settings)
    with single_instance(settings) as first:
        if not first:
            if open_browser and not open_running(settings):
                show_error("FS-Platform is starting in another window; try again in a moment.")
            return 0
        tray_ui: Any = None
        if tray and os.name == "nt":  # pragma: no cover - Windows
            try:
                from fsoffline.tray import Tray

                tray_ui = Tray(settings)
            except Exception:
                log.exception("tray icon unavailable; running without it")
        runtime = Runtime(settings, open_browser=open_browser, notify=tray_ui.notify if tray_ui else None)
        try:
            runtime.start()
        except Exception as exc:
            log.exception("start failed")
            show_error(f"FS-Platform could not start: {exc}")
            runtime.shutdown()
            return 1
        if tray_ui is not None:  # pragma: no cover - Windows
            runtime.on_shutdown.append(tray_ui.close)
            tray_ui.run(runtime)  # message loop on the main thread until Quit, sign-out or a stop request
        else:
            runtime.wait()
        runtime.shutdown()
    return 0


__all__ = ["Runtime", "StopSignal", "bind_socket", "configure_file_logging", "firm_exists", "instance_id", "open_running",
           "prepare_setup", "read_runtime", "request_stop", "run_desktop", "single_instance", "write_runtime"]
