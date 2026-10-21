from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from rw_fakes import MINIMAL_PDF, TOKEN, FakeRenderServer

from fsrender_win.client import ApiError, RenderApiClient


def _client(server: FakeRenderServer, sleeps: list[float] | None = None, token: str = TOKEN) -> RenderApiClient:
    record = sleeps if sleeps is not None else []
    return RenderApiClient(
        "https://fs-internal.example-firm.in",
        token,
        transport=httpx.MockTransport(server.handler),
        sleep=record.append,
        max_attempts=3,
    )


def test_token_header_and_heartbeat(fake_server: FakeRenderServer) -> None:
    with _client(fake_server) as client:
        client.heartbeat("RENDER-01", "16.0.19000.20000")
    assert fake_server.heartbeats == [{"worker_id": "RENDER-01", "excel_build": "16.0.19000.20000"}]
    assert all(r.headers["X-Worker-Token"] == TOKEN for r in fake_server.requests)


def test_claim_empty_queue_returns_none(fake_server: FakeRenderServer) -> None:
    with _client(fake_server) as client:
        assert client.claim("RENDER-01", "b") is None


def test_claim_returns_job(fake_server: FakeRenderServer) -> None:
    fake_server.add_job("job-1", mode="final")
    with _client(fake_server) as client:
        job = client.claim("RENDER-01", "b")
    assert job is not None
    assert (job.job_id, job.run_id, job.mode, job.sheets) == ("job-1", "run-job-1", "final", ["BS", "PL"])


def test_retries_on_5xx_with_backoff(fake_server: FakeRenderServer) -> None:
    fake_server.fail_next["claim"] = [503, 502]
    sleeps: list[float] = []
    with _client(fake_server, sleeps) as client:
        assert client.claim("RENDER-01", "b") is None
    assert sleeps == [1.0, 2.0]


def test_gives_up_after_max_attempts(fake_server: FakeRenderServer) -> None:
    fake_server.fail_next["heartbeat"] = [500, 500, 500]
    with _client(fake_server) as client, pytest.raises(ApiError) as info:
        client.heartbeat("RENDER-01", "b")
    assert info.value.status_code == 500


def test_4xx_is_not_retried(fake_server: FakeRenderServer) -> None:
    sleeps: list[float] = []
    with _client(fake_server, sleeps, token="wrong") as client, pytest.raises(ApiError) as info:
        client.heartbeat("RENDER-01", "b")
    assert info.value.status_code == 401
    assert sleeps == []


def test_download_input_streams_to_file(fake_server: FakeRenderServer, tmp_path: Path) -> None:
    fake_server.add_job("job-2", data=b"PK\x03\x04" + b"x" * 5000)
    dest = tmp_path / "in.xlsx"
    with _client(fake_server) as client:
        written = client.download_input("job-2", dest)
    assert written == 5004
    assert dest.read_bytes().startswith(b"PK\x03\x04")


def test_download_input_size_limit(fake_server: FakeRenderServer, tmp_path: Path) -> None:
    fake_server.add_job("job-3", data=b"x" * 2048)
    with _client(fake_server) as client, pytest.raises(ApiError, match="exceeds"):
        client.download_input("job-3", tmp_path / "in.xlsx", max_bytes=1024)


def test_complete_sends_pdf_and_meta(fake_server: FakeRenderServer, tmp_path: Path) -> None:
    pdf = tmp_path / "out.pdf"
    pdf.write_bytes(MINIMAL_PDF)
    with _client(fake_server) as client:
        client.complete("job-4", pdf, {"engine": "excel-com", "engine_version": "16.0", "excel_build": "16.0.1"})
    record = fake_server.completed["job-4"]
    assert record["pdf"] == MINIMAL_PDF
    assert record["meta"] == {"engine": "excel-com", "engine_version": "16.0", "excel_build": "16.0.1"}


def test_fail_truncates_error(fake_server: FakeRenderServer) -> None:
    with _client(fake_server) as client:
        client.fail("job-5", "E" * 2000)
    assert len(fake_server.failed["job-5"]) == 500
    body = json.loads(fake_server.requests[-1].content)
    assert set(body) == {"error"}
