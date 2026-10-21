from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from rw_fakes import MINIMAL_PDF, TOKEN, FakeRenderServer

from fsrender_win.client import RenderApiClient
from fsrender_win.config import WorkerConfig
from fsrender_win.watchdog import ExcelProcessTracker, ProcInfo
from fsrender_win.worker import RenderWorker


class RecordingTracker(ExcelProcessTracker):
    """Tracker whose 'Excel' processes are simulated by the fake exporter."""

    def __init__(self) -> None:
        self.live: list[ProcInfo] = []
        self.killed: list[int] = []
        self.began = 0

        tracker = self

        class _Backend:
            def list_processes(self, image_name: str) -> list[ProcInfo]:
                return [p for p in tracker.live if p.pid not in tracker.killed]

            def kill(self, pid: int) -> bool:
                tracker.killed.append(pid)
                return True

            def current_username(self) -> str | None:
                return None

        super().__init__(_Backend())

    def begin(self) -> None:
        self.began += 1
        super().begin()


def _config(tmp_path: Path, **kw: Any) -> WorkerConfig:
    values: dict[str, Any] = {
        "api_url": "https://fs-internal.example-firm.in",
        "worker_token": TOKEN,
        "worker_id": "RENDER-01",
        "work_dir": tmp_path / "work",
        "poll_interval_s": 0.01,
        "heartbeat_interval_s": 30.0,
        "excel_build": "16.0.19127.20000",
    }
    values.update(kw)
    return WorkerConfig(**values)


def _worker(server: FakeRenderServer, tmp_path: Path, exporter: Any, **kw: Any) -> RenderWorker:
    client = RenderApiClient(
        "https://fs-internal.example-firm.in", TOKEN, transport=httpx.MockTransport(server.handler), sleep=lambda s: None
    )
    return RenderWorker(_config(tmp_path, **kw), client, exporter=exporter, tracker=RecordingTracker(), watchdog_grace_s=0.2)


def good_exporter(src: Path, dst: Path, sheets: list[str] | None, timeout_s: int) -> dict[str, Any]:
    assert src.read_bytes().startswith(b"PK")
    dst.write_bytes(MINIMAL_PDF)
    return {"engine": "excel-com", "engine_version": "16.0", "excel_build": "16.0.19127.20000", "pages": 7}


def test_happy_path_uploads_pdf_and_cleans_up(fake_server: FakeRenderServer, tmp_path: Path) -> None:
    fake_server.add_job("job-ok")
    worker = _worker(fake_server, tmp_path, good_exporter)
    outcome = worker.run_once()
    assert outcome is not None
    assert outcome.ok
    assert outcome.pages == 7
    assert fake_server.completed["job-ok"]["pdf"] == MINIMAL_PDF
    assert fake_server.completed["job-ok"]["meta"]["engine"] == "excel-com"
    assert fake_server.heartbeats
    assert fake_server.heartbeats[0]["worker_id"] == "RENDER-01"
    assert list((tmp_path / "work").iterdir()) == []  # per-job sandbox wiped


def test_sheets_are_passed_to_exporter(fake_server: FakeRenderServer, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def exporter(src: Path, dst: Path, sheets: list[str] | None, timeout_s: int) -> dict[str, Any]:
        seen["sheets"], seen["timeout"] = sheets, timeout_s
        return good_exporter(src, dst, sheets, timeout_s)

    fake_server.add_job("job-sheets", sheets=["BS", "Notes 1-10"])
    _worker(fake_server, tmp_path, exporter, job_timeout_s=120).run_once()
    assert seen == {"sheets": ["BS", "Notes 1-10"], "timeout": 120}


def test_exporter_error_reports_failure_without_client_data(fake_server: FakeRenderServer, tmp_path: Path) -> None:
    def broken(src: Path, dst: Path, sheets: list[str] | None, timeout_s: int) -> dict[str, Any]:
        raise RuntimeError("Excel COM error 0x800A03EC")

    fake_server.add_job("job-bad")
    outcome = _worker(fake_server, tmp_path, broken).run_once()
    assert outcome is not None
    assert not outcome.ok
    assert fake_server.failed["job-bad"].startswith("RuntimeError: Excel COM error")
    assert "job-bad" not in fake_server.completed
    assert list((tmp_path / "work").iterdir()) == []


def test_non_pdf_output_is_rejected(fake_server: FakeRenderServer, tmp_path: Path) -> None:
    def wrong(src: Path, dst: Path, sheets: list[str] | None, timeout_s: int) -> dict[str, Any]:
        dst.write_bytes(b"<html>not a pdf</html>" * 10)
        return {}

    fake_server.add_job("job-html")
    _worker(fake_server, tmp_path, wrong).run_once()
    assert "InvalidOutputError" in fake_server.failed["job-html"]


def test_timeout_kills_only_our_excel_and_fails_job(fake_server: FakeRenderServer, tmp_path: Path) -> None:
    release = threading.Event()
    worker_box: dict[str, RenderWorker] = {}

    def hanging(src: Path, dst: Path, sheets: list[str] | None, timeout_s: int) -> dict[str, Any]:
        tracker = worker_box["w"].tracker
        assert isinstance(tracker, RecordingTracker)
        tracker.live.append(ProcInfo(4242, "EXCEL.EXE", time.time() + 1, None))
        release.wait(5)
        return {}

    fake_server.add_job("job-hang")
    worker = _worker(fake_server, tmp_path, hanging, job_timeout_s=0)
    worker_box["w"] = worker
    outcome = worker.run_once()
    release.set()
    assert outcome is not None
    assert not outcome.ok
    assert "RenderTimeoutError" in fake_server.failed["job-hang"]
    tracker = worker.tracker
    assert isinstance(tracker, RecordingTracker)
    assert tracker.killed
    assert set(tracker.killed) == {4242}


def test_idle_queue_and_heartbeat_cadence(fake_server: FakeRenderServer, tmp_path: Path) -> None:
    now = [0.0]
    client = RenderApiClient("https://x.example-firm.in", TOKEN, transport=httpx.MockTransport(fake_server.handler))
    worker = RenderWorker(_config(tmp_path, heartbeat_interval_s=30.0), client, exporter=good_exporter, monotonic=lambda: now[0])
    assert worker.run_once() is None
    now[0] = 10.0
    worker.run_once()
    assert len(fake_server.heartbeats) == 1
    now[0] = 31.0
    worker.run_once()
    assert len(fake_server.heartbeats) == 2


def test_run_forever_survives_api_outage_and_stops(fake_server: FakeRenderServer, tmp_path: Path) -> None:
    fake_server.fail_next["heartbeat"] = [503] * 8
    stop = threading.Event()
    worker = _worker(fake_server, tmp_path, good_exporter, max_backoff_s=0.05)
    fake_server.add_job("job-late")
    thread = threading.Thread(target=worker.run_forever, args=(stop,), daemon=True)
    thread.start()
    deadline = time.time() + 5
    while "job-late" not in fake_server.completed and time.time() < deadline:
        time.sleep(0.02)
    stop.set()
    thread.join(2)
    assert not thread.is_alive()
    assert "job-late" in fake_server.completed


def test_default_exporter_is_lazy(tmp_path: Path, fake_server: FakeRenderServer) -> None:
    client = RenderApiClient("https://x.example-firm.in", TOKEN, transport=httpx.MockTransport(fake_server.handler))
    worker = RenderWorker(_config(tmp_path), client)  # no exporter given: must not import COM yet
    assert worker._exporter is None


@pytest.mark.parametrize("pages", [None, "n/a"])
def test_pages_optional(fake_server: FakeRenderServer, tmp_path: Path, pages: Any) -> None:
    def exporter(src: Path, dst: Path, sheets: list[str] | None, timeout_s: int) -> dict[str, Any]:
        dst.write_bytes(MINIMAL_PDF)
        return {"engine": "excel-com", "pages": pages}

    fake_server.add_job("job-p")
    outcome = _worker(fake_server, tmp_path, exporter).run_once()
    assert outcome is not None
    assert outcome.ok
    assert outcome.pages is None
