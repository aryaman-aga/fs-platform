"""Excel process watchdog: kill only the EXCEL.EXE processes this worker started.

Excel launched through DCOM is parented by the DCOM launcher, not by this
process, so ownership is established by a *snapshot diff*: a process counts as
ours when it was not running before the job began, was created after the job
started, and runs under the worker's own (dedicated) service account. Anything
else — an administrator's interactive Excel, another worker's instance — is
never touched.
"""

from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

IMAGE_NAME = "EXCEL.EXE"


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    name: str
    create_time: float | None = None
    username: str | None = None


class ProcessBackend(Protocol):
    def list_processes(self, image_name: str) -> list[ProcInfo]: ...

    def kill(self, pid: int) -> bool: ...

    def current_username(self) -> str | None: ...


class PsutilBackend:
    """Preferred backend (psutil gives creation time and owner)."""

    def __init__(self) -> None:
        import psutil

        self._psutil = psutil

    def list_processes(self, image_name: str) -> list[ProcInfo]:
        out: list[ProcInfo] = []
        for proc in self._psutil.process_iter(["pid", "name", "create_time", "username"]):
            info = proc.info
            if (info.get("name") or "").lower() == image_name.lower():
                out.append(ProcInfo(info["pid"], info["name"], info.get("create_time"), info.get("username")))
        return out

    def kill(self, pid: int) -> bool:
        try:
            self._psutil.Process(pid).kill()
            return True
        except (self._psutil.NoSuchProcess, self._psutil.AccessDenied):
            return False

    def current_username(self) -> str | None:
        try:
            return str(self._psutil.Process().username())
        except Exception:  # pragma: no cover - platform specific
            return None


def _system32(exe: str) -> str:
    return os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", exe)


class TasklistBackend:
    """Fallback on Windows without psutil: snapshot diff only (no creation time)."""

    def list_processes(self, image_name: str) -> list[ProcInfo]:
        result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            [_system32("tasklist.exe"), "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        out: list[ProcInfo] = []
        for row in csv.reader(io.StringIO(result.stdout)):
            if len(row) >= 2 and row[0].lower() == image_name.lower() and row[1].isdigit():
                out.append(ProcInfo(int(row[1]), row[0]))
        return out

    def kill(self, pid: int) -> bool:
        result = subprocess.run(  # noqa: S603
            [_system32("taskkill.exe"), "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, check=False
        )
        return result.returncode == 0

    def current_username(self) -> str | None:
        return None


class NullBackend:
    """Non-Windows hosts (tests, macOS dev): there is no Excel to watch."""

    def list_processes(self, image_name: str) -> list[ProcInfo]:
        return []

    def kill(self, pid: int) -> bool:
        return False

    def current_username(self) -> str | None:
        return None


def default_backend() -> ProcessBackend:
    if sys.platform != "win32":
        return NullBackend()
    try:
        return PsutilBackend()
    except ImportError:
        return TasklistBackend()


class ExcelProcessTracker:
    def __init__(
        self,
        backend: ProcessBackend | None = None,
        *,
        image_name: str = IMAGE_NAME,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._backend = backend or default_backend()
        self._image = image_name
        self._clock = clock
        self._sleep = sleep
        self._before: set[int] = set()
        self._started_at: float | None = None
        self._user = self._backend.current_username()

    def begin(self) -> None:
        """Snapshot the Excel processes that already exist before a job starts."""
        self._before = {p.pid for p in self._backend.list_processes(self._image)}
        self._started_at = self._clock()

    def started(self) -> list[ProcInfo]:
        """Excel processes that appeared during the current job and belong to this worker."""
        if self._started_at is None:
            return []
        mine: list[ProcInfo] = []
        for proc in self._backend.list_processes(self._image):
            if proc.pid in self._before:
                continue
            if proc.create_time is not None and proc.create_time < self._started_at - 1.0:
                continue
            if self._user and proc.username and proc.username.lower() != self._user.lower():
                continue
            mine.append(proc)
        return mine

    def kill_started(self) -> list[int]:
        return [proc.pid for proc in self.started() if self._backend.kill(proc.pid)]

    def reap(self, grace_s: float = 10.0, poll_s: float = 0.5) -> list[int]:
        """After a job: give Excel ``grace_s`` to quit on its own, then kill what is left."""
        deadline = self._clock() + grace_s
        while self.started() and self._clock() < deadline:
            self._sleep(poll_s)
        killed = self.kill_started()
        self._started_at = None
        self._before = set()
        return killed
