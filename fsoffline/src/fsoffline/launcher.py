"""Start the offline edition: migrate, run the worker in-process, serve on loopback.

``fsweb`` is imported lazily so this module (and its tests) work before the web
package is installed.
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import typing
import webbrowser
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

from fsoffline.settings import (
    OfflineConfigError,
    OfflineSettings,
    build_env,
    ensure_layout,
    url_host,
    validate_bind_host,
)

log = logging.getLogger("fsoffline")


# --------------------------------------------------------------------------- network
def find_free_port(host: str = "127.0.0.1") -> int:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def wait_for_port(host: str, port: int, timeout_s: float = 30.0, sleep: Callable[[float], None] = time.sleep) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            sleep(0.2)
    return False


# --------------------------------------------------------------------------- single instance
def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - Windows
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: it runs, in another Windows account
        try:
            code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == 259  # STILL_ACTIVE (an exited process stays openable while handles to it exist)
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class InstanceLockError(RuntimeError):
    pass


@contextlib.contextmanager
def instance_lock(path: Path) -> Iterator[None]:
    """One offline instance per data directory (two would share one SQLite file)."""
    for _ in range(2):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                pid = int(path.read_text().strip() or "0")
            except (OSError, ValueError):
                pid = 0
            if _pid_alive(pid):
                raise InstanceLockError(f"the offline edition is already running (pid {pid})") from None
            path.unlink(missing_ok=True)  # stale lock from a crashed run
            continue
        with os.fdopen(fd, "w") as handle:
            handle.write(str(os.getpid()))
        break
    else:  # pragma: no cover - only if the stale lock cannot be removed
        raise InstanceLockError(f"cannot acquire {path}")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- fsweb glue
def supported_env_name(preferred: str = "offline") -> str:
    """Use FSWEB_ENV=offline when fsweb supports it; otherwise fall back to 'dev'."""
    try:
        settings_mod = importlib.import_module("fsweb.settings")
    except ImportError:
        return preferred
    env_type = getattr(settings_mod, "Env", None)
    allowed = typing.get_args(env_type) if env_type is not None else ()
    if not allowed or preferred in allowed:
        return preferred
    log.warning("fsweb does not know FSWEB_ENV=%s; using 'dev' settings validation", preferred)
    return "dev"


def run_manage(argv: Sequence[str]) -> int:
    """Run an ``fsweb.manage`` command in-process (the environment from ``build_env`` must already be set)."""
    from fsweb import manage

    try:
        return manage.main(list(argv))
    except SystemExit as exc:  # argparse errors
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 1


class WorkerHandle:
    def __init__(self, thread: threading.Thread | None = None, process: subprocess.Popen[bytes] | None = None) -> None:
        self.thread = thread
        self.process = process
        self.error: BaseException | None = None

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=10)


def _worker_entry(stop: threading.Event, *, queues: Sequence[str] = ("default",), poll_s: float = 1.0) -> Callable[[], Any]:
    """Resolve how to run fsweb's job worker inside this process without touching signal handlers.

    Preference: an explicit ``run_forever(stop_event)`` entry point; otherwise a loop over fsweb's own
    ``run_once(state, queues, worker_id)``. ``fsweb.worker.main`` installs signal handlers, which only
    work in the main thread, so it is used only as a child process (see :func:`start_worker`).
    """
    module = importlib.import_module("fsweb.worker")
    for name in ("run_forever", "run_worker", "serve"):
        func = getattr(module, name, None)
        if callable(func):
            params = inspect.signature(func).parameters
            for pname in ("stop_event", "stop"):
                if pname in params:
                    return lambda: func(**{pname: stop})
    run_once = getattr(module, "run_once", None)
    if callable(run_once):
        settings_mod = importlib.import_module("fsweb.settings")
        state_mod = importlib.import_module("fsweb.state")
        worker_id = f"fsoffline:{os.getpid()}"

        def _loop() -> None:
            state = state_mod.build_state(settings_mod.Settings.from_env(), component="worker")
            try:
                while not stop.is_set():
                    if not run_once(state, list(queues), worker_id):
                        stop.wait(poll_s)
            finally:
                dispose = getattr(getattr(state, "db", None), "dispose", None)
                if callable(dispose):
                    dispose()

        return _loop
    raise RuntimeError("fsweb.worker exposes no in-process entry point (run_forever / run_once)")


def start_worker(stop: threading.Event, env: dict[str, str], *, settle_s: float = 2.0) -> WorkerHandle:
    """Run the job worker in a daemon thread; fall back to a child process if that fails."""
    handle = WorkerHandle()

    def _target() -> None:
        try:
            _worker_entry(stop)()
        except BaseException as exc:  # recorded and inspected by the launcher
            handle.error = exc

    thread = threading.Thread(target=_target, name="fsweb-worker", daemon=True)
    thread.start()
    handle.thread = thread
    thread.join(settle_s)
    if handle.error is None:
        return handle
    log.warning("in-process worker failed (%s); starting it as a child process", type(handle.error).__name__)
    child_env = {**os.environ, **env}
    process = subprocess.Popen([sys.executable, "-m", "fsweb.worker"], env=child_env)
    return WorkerHandle(process=process)


_UVICORN_DEFAULT_LOGGING = object()


def make_server(host: str, port: int, *, log_config: Any = _UVICORN_DEFAULT_LOGGING, **config: Any) -> Any:
    """``log_config=None`` leaves logging to the caller (the desktop runtime logs to a file; uvicorn's own setup would
    probe the console, which pythonw does not have). Other keyword arguments go to ``uvicorn.Config``."""
    import uvicorn

    module = importlib.import_module("fsweb.main")
    factory = getattr(module, "create_app", None)
    app = factory() if callable(factory) else module.app
    kwargs: dict[str, Any] = dict(config) if log_config is _UVICORN_DEFAULT_LOGGING else {"log_config": log_config, **config}
    server_config = uvicorn.Config(app, host=host, port=port, log_level="info", proxy_headers=False, server_header=False, **kwargs)
    return uvicorn.Server(server_config)


def _open_browser_when_ready(host: str, port: int, url: str) -> None:
    if wait_for_port(host, port):
        webbrowser.open(url)


# --------------------------------------------------------------------------- main entry
def run(settings: OfflineSettings) -> int:
    host = validate_bind_host(settings.host)
    ensure_layout(settings)
    with instance_lock(settings.lock_path):
        port = settings.port or find_free_port(host)
        env = build_env(settings, port, env_name=supported_env_name())
        os.environ.update(env)
        rc = run_manage(["migrate"])
        if rc != 0:
            log.error("database migration failed (exit %s); not starting", rc)
            return rc
        stop = threading.Event()
        worker = start_worker(stop, env)
        url = f"http://{url_host(host)}:{port}/"
        log.info("FS-Platform offline edition on %s (data: %s)", url, settings.data_dir)
        if settings.open_browser:
            threading.Thread(target=_open_browser_when_ready, args=(host, port, url), daemon=True).start()
        server = make_server(host, port)
        try:
            server.run()
        except KeyboardInterrupt:  # uvicorn re-raises Ctrl+C after its graceful shutdown
            log.info("stopped")
        finally:
            stop.set()
            worker.stop()
    return 0


def bootstrap(settings: OfflineSettings, tenant: str, admin_email: str) -> int:
    """First run: create the local tenant and its administrator (prints a one-time password + TOTP secret)."""
    ensure_layout(settings)
    os.environ.update(build_env(settings, settings.port or 0, env_name=supported_env_name()))
    rc = run_manage(["migrate"])
    if rc != 0:
        return rc
    return run_manage(["bootstrap", "--tenant", tenant, "--admin-email", admin_email])


def manage_passthrough(settings: OfflineSettings, argv: Sequence[str]) -> int:
    """Package exchange with the firm server: export-engagement / import-engagement."""
    ensure_layout(settings)
    os.environ.update(build_env(settings, settings.port or 0, env_name=supported_env_name()))
    return run_manage(list(argv))


__all__ = [
    "InstanceLockError",
    "OfflineConfigError",
    "bootstrap",
    "find_free_port",
    "instance_lock",
    "make_server",
    "manage_passthrough",
    "run",
    "run_manage",
    "start_worker",
    "supported_env_name",
    "wait_for_port",
]
