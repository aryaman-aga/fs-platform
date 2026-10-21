from __future__ import annotations

from fsrender_win.watchdog import ExcelProcessTracker, NullBackend, ProcInfo


class FakeBackend:
    def __init__(self, user: str = "FIRM\\svc-render") -> None:
        self.procs: list[ProcInfo] = []
        self.killed: list[int] = []
        self.user = user

    def list_processes(self, image_name: str) -> list[ProcInfo]:
        return [p for p in self.procs if p.name.lower() == image_name.lower() and p.pid not in self.killed]

    def kill(self, pid: int) -> bool:
        self.killed.append(pid)
        return True

    def current_username(self) -> str | None:
        return self.user


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.now += s


def test_only_processes_started_by_this_job_and_user_are_killed() -> None:
    backend = FakeBackend()
    clock = Clock()
    backend.procs = [ProcInfo(100, "EXCEL.EXE", 500.0, "FIRM\\svc-render")]  # pre-existing
    tracker = ExcelProcessTracker(backend, clock=clock, sleep=clock.sleep)
    tracker.begin()
    clock.now += 5
    backend.procs += [
        ProcInfo(200, "EXCEL.EXE", 1002.0, "FIRM\\svc-render"),  # ours
        ProcInfo(300, "EXCEL.EXE", 1003.0, "FIRM\\admin"),  # someone else's interactive Excel
        ProcInfo(400, "WINWORD.EXE", 1003.0, "FIRM\\svc-render"),  # not Excel
    ]
    assert [p.pid for p in tracker.started()] == [200]
    assert tracker.kill_started() == [200]
    assert backend.killed == [200]


def test_old_process_not_in_snapshot_is_left_alone() -> None:
    backend = FakeBackend()
    clock = Clock()
    tracker = ExcelProcessTracker(backend, clock=clock, sleep=clock.sleep)
    tracker.begin()
    backend.procs = [ProcInfo(150, "EXCEL.EXE", 10.0, "FIRM\\svc-render")]  # created long before the job
    assert tracker.started() == []


def test_reap_waits_for_grace_then_kills() -> None:
    backend = FakeBackend()
    clock = Clock()
    tracker = ExcelProcessTracker(backend, clock=clock, sleep=clock.sleep)
    tracker.begin()
    backend.procs = [ProcInfo(201, "EXCEL.EXE", clock.now + 1, "FIRM\\svc-render")]
    killed = tracker.reap(grace_s=3.0, poll_s=1.0)
    assert killed == [201]
    assert clock.now >= 1003.0
    assert tracker.started() == []  # state reset after reaping


def test_reap_without_leftovers_does_not_wait() -> None:
    backend = FakeBackend()
    clock = Clock()
    tracker = ExcelProcessTracker(backend, clock=clock, sleep=clock.sleep)
    tracker.begin()
    assert tracker.reap(grace_s=30.0) == []
    assert clock.now == 1000.0


def test_null_backend_is_inert() -> None:
    tracker = ExcelProcessTracker(NullBackend())
    tracker.begin()
    assert tracker.started() == []
    assert tracker.kill_started() == []
