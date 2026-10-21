"""Render loop: heartbeat → claim → download → Excel export (watchdog) → upload.

Every job runs in its own temporary directory, which is always wiped. Logs carry
job IDs and error *types* only — never workbook content or client names.
"""

from __future__ import annotations

import logging
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fsrender_win.client import ApiError, RenderApiClient, RenderJob
from fsrender_win.config import WorkerConfig
from fsrender_win.watchdog import ExcelProcessTracker

log = logging.getLogger("fsrender_win")

Exporter = Callable[[Path, Path, "list[str] | None", int], dict[str, Any]]

WATCHDOG_GRACE_S = 30


class RenderTimeoutError(RuntimeError):
    pass


class InvalidOutputError(RuntimeError):
    pass


def default_exporter() -> Exporter:
    """The engine's COM exporter (imported lazily: it needs pywin32 + Excel)."""
    from fscore.pdf.render_excel_com import export_pdf

    def _call(src: Path, dst: Path, sheets: list[str] | None, timeout_s: int) -> dict[str, Any]:
        return dict(export_pdf(src, dst, sheets, timeout_s=timeout_s))

    return _call


def detect_excel_build() -> str:
    """Excel build from the Click-to-Run registry key; 'unknown' when unavailable."""
    if sys.platform != "win32":
        return "unknown"
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Office\ClickToRun\Configuration") as key:
            value, _ = winreg.QueryValueEx(key, "VersionToReport")
            return str(value)
    except OSError:
        return "unknown"


def _run_with_com(exporter: Exporter, src: Path, dst: Path, sheets: list[str] | None, timeout_s: int) -> dict[str, Any]:
    """COM must be initialised in the thread that drives Excel."""
    pythoncom: Any = None
    if sys.platform == "win32":  # pragma: no cover - Windows only
        try:
            import pythoncom as _pythoncom

            pythoncom = _pythoncom
            pythoncom.CoInitialize()
        except ImportError:
            pythoncom = None
    try:
        return exporter(src, dst, sheets, timeout_s)
    finally:
        if pythoncom is not None:  # pragma: no cover
            pythoncom.CoUninitialize()


@dataclass(frozen=True)
class JobOutcome:
    job_id: str
    ok: bool
    error: str | None = None
    pages: int | None = None


class RenderWorker:
    def __init__(
        self,
        config: WorkerConfig,
        client: RenderApiClient,
        *,
        exporter: Exporter | None = None,
        tracker: ExcelProcessTracker | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        watchdog_grace_s: float = WATCHDOG_GRACE_S,
    ) -> None:
        self.config = config
        self.watchdog_grace_s = watchdog_grace_s
        self.client = client
        self._exporter = exporter
        self.tracker = tracker or ExcelProcessTracker()
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_heartbeat: float | None = None
        self.excel_build = config.excel_build or detect_excel_build()
        self._failures = 0

    @property
    def exporter(self) -> Exporter:
        if self._exporter is None:
            self._exporter = default_exporter()
        return self._exporter

    # ------------------------------------------------------------------ loop
    def heartbeat_if_due(self) -> None:
        now = self._monotonic()
        if self._last_heartbeat is None or now - self._last_heartbeat >= self.config.heartbeat_interval_s:
            self.client.heartbeat(self.config.worker_id, self.excel_build)
            self._last_heartbeat = now

    def run_once(self) -> JobOutcome | None:
        """One iteration: heartbeat when due, claim, process. Returns the outcome or None."""
        self.heartbeat_if_due()
        job = self.client.claim(self.config.worker_id, self.excel_build)
        if job is None:
            return None
        return self.process(job)

    def run_forever(self, stop: threading.Event) -> None:
        log.info("render worker %s started (excel build %s)", self.config.worker_id, self.excel_build)
        while not stop.is_set():
            try:
                outcome = self.run_once()
                self._failures = 0
                if outcome is None:
                    stop.wait(self.config.poll_interval_s)
            except ApiError as exc:
                self._failures += 1
                delay = min(self.config.max_backoff_s, self.config.poll_interval_s * (2 ** min(self._failures, 10)))
                log.warning("API unavailable (%s); retrying in %.0fs", exc.status_code or "network", delay)
                stop.wait(delay)
            except Exception:  # keep the service alive; the job (if any) was already failed
                self._failures += 1
                log.exception("unexpected error in render loop")
                stop.wait(min(self.config.max_backoff_s, self.config.poll_interval_s * 4))
        log.info("render worker %s stopped", self.config.worker_id)

    # ------------------------------------------------------------------ one job
    def process(self, job: RenderJob) -> JobOutcome:
        self.config.work_dir.mkdir(parents=True, exist_ok=True)
        job_dir = Path(tempfile.mkdtemp(prefix=f"job-{job.job_id[:12]}-", dir=self.config.work_dir))
        log.info("job %s claimed (mode=%s)", job.job_id, job.mode)
        try:
            src = job_dir / "input.xlsx"
            dst = job_dir / "output.pdf"
            self.client.download_input(job.job_id, src, max_bytes=self.config.max_input_bytes)
            info = self._export_with_watchdog(src, dst, job.sheets)
            self._validate_pdf(dst)
            meta = {
                "engine": str(info.get("engine") or "excel-com"),
                "engine_version": str(info.get("engine_version") or ""),
                "excel_build": str(info.get("excel_build") or self.excel_build),
            }
            if info.get("excel_build"):
                self.excel_build = str(info["excel_build"])
            self.client.complete(job.job_id, dst, meta)
            pages = info.get("pages")
            log.info("job %s completed (%s pages)", job.job_id, pages)
            return JobOutcome(job.job_id, ok=True, pages=int(pages) if isinstance(pages, int) else None)
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
            log.warning("job %s failed: %s", job.job_id, type(exc).__name__)
            try:
                self.client.fail(job.job_id, error)
            except ApiError:
                log.warning("job %s: could not report failure; the server will retry it", job.job_id)
            return JobOutcome(job.job_id, ok=False, error=error)
        finally:
            killed = self.tracker.reap(grace_s=5.0)
            if killed:
                log.warning("job %s: killed %d orphaned Excel process(es)", job.job_id, len(killed))
            shutil.rmtree(job_dir, ignore_errors=True)

    def _export_with_watchdog(self, src: Path, dst: Path, sheets: list[str] | None) -> dict[str, Any]:
        timeout = int(self.config.job_timeout_s)
        self.tracker.begin()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="excel-export")
        future = executor.submit(_run_with_com, self.exporter, src, dst, sheets, timeout)
        try:
            return future.result(timeout=timeout + self.watchdog_grace_s)
        except FuturesTimeout as exc:
            killed = self.tracker.kill_started()
            raise RenderTimeoutError(
                f"export exceeded {timeout}s; killed {len(killed)} Excel process(es) started by this job"
            ) from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _validate_pdf(path: Path) -> None:
        if not path.is_file() or path.stat().st_size < 64:
            raise InvalidOutputError("exporter produced no PDF")
        with path.open("rb") as handle:
            if not handle.read(5).startswith(b"%PDF-"):
                raise InvalidOutputError("exporter output is not a PDF")


def build_worker(config: WorkerConfig, **kwargs: Any) -> RenderWorker:
    client = RenderApiClient(
        config.api_url,
        config.worker_token,
        timeout_s=config.http_timeout_s,
        verify=config.verify_tls,
        max_backoff_s=min(60.0, config.max_backoff_s),
    )
    return RenderWorker(config, client, **kwargs)
