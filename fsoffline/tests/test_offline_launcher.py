from __future__ import annotations

import os
import socket
import threading
from pathlib import Path
from typing import Any, Literal

import pytest

from fsoffline import launcher
from fsoffline.settings import OfflineConfigError, OfflineSettings


def test_find_free_port_is_bindable_on_loopback() -> None:
    port = launcher.find_free_port("127.0.0.1")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))


def test_wait_for_port() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert launcher.wait_for_port("127.0.0.1", port, timeout_s=2)
    assert not launcher.wait_for_port("127.0.0.1", port, timeout_s=0.3, sleep=lambda s: None)


def test_instance_lock_is_exclusive_and_released(tmp_path: Path) -> None:
    lock = tmp_path / "fsoffline.lock"
    with launcher.instance_lock(lock):
        assert lock.read_text() == str(os.getpid())
        with pytest.raises(launcher.InstanceLockError, match="already running"), launcher.instance_lock(lock):
            pass
    assert not lock.exists()


def test_stale_lock_is_recovered(tmp_path: Path) -> None:
    lock = tmp_path / "fsoffline.lock"
    lock.write_text("999999")  # no such process
    with launcher.instance_lock(lock):
        assert lock.read_text() == str(os.getpid())


def test_run_manage_maps_exit_codes(fake_fsweb: Any) -> None:
    assert launcher.run_manage(["migrate"]) == 0
    fake_fsweb.manage_exit = 3
    assert launcher.run_manage(["verify-audit-chain"]) == 3
    fake_fsweb.manage_exit = None  # module returns normally
    assert launcher.run_manage(["openapi"]) == 0
    assert fake_fsweb.manage_calls == [["migrate"], ["verify-audit-chain"], ["openapi"]]


def test_supported_env_name(fake_fsweb: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    assert launcher.supported_env_name() == "offline"
    monkeypatch.setattr(sys.modules["fsweb.settings"], "Env", Literal["dev", "test", "prod"])
    assert launcher.supported_env_name() == "dev"


def test_worker_runs_in_process_and_stops(fake_fsweb: Any) -> None:
    stop = threading.Event()
    handle = launcher.start_worker(stop, {}, settle_s=0.2)
    assert handle.thread is not None
    assert handle.thread.is_alive()
    assert fake_fsweb.worker_started == 1
    stop.set()
    handle.thread.join(2)
    assert not handle.thread.is_alive()


def test_worker_entry_uses_run_once_loop_without_signals(fake_fsweb: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    worker = sys.modules["fsweb.worker"]
    calls: list[tuple[Any, list[str], str]] = []
    monkeypatch.delattr(worker, "run_forever")

    def run_once(state: Any, queues: list[str], worker_id: str) -> bool:
        calls.append((state, queues, worker_id))
        return False  # queue empty -> loop waits on the stop event

    monkeypatch.setattr(worker, "run_once", run_once, raising=False)
    monkeypatch.setattr(worker, "main", lambda argv=None: pytest.fail("main() installs signal handlers"), raising=False)
    stop = threading.Event()
    thread = threading.Thread(target=launcher._worker_entry(stop, poll_s=0.01), daemon=True)
    thread.start()
    while not calls:
        pass
    stop.set()
    thread.join(2)
    assert not thread.is_alive()
    assert calls[0][1] == ["default"]
    assert calls[0][2].startswith("fsoffline:")
    assert fake_fsweb.disposed == [True]


def test_worker_entry_without_in_process_api_raises(fake_fsweb: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.delattr(sys.modules["fsweb.worker"], "run_forever")
    with pytest.raises(RuntimeError, match="in-process"):
        launcher._worker_entry(threading.Event())


def test_run_orchestration_binds_loopback(fake_fsweb: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    class FakeServer:
        def run(self) -> None:
            seen["env_at_serve"] = dict(os.environ)

    def fake_make_server(host: str, port: int) -> FakeServer:
        seen["bind"] = (host, port)
        return FakeServer()

    monkeypatch.setattr(launcher, "make_server", fake_make_server)
    settings = OfflineSettings(data_dir=tmp_path / "data", port=0, open_browser=False)
    assert launcher.run(settings) == 0
    host, port = seen["bind"]
    assert host == "127.0.0.1"
    assert port > 0
    assert fake_fsweb.manage_calls[0] == ["migrate"]
    assert seen["env_at_serve"]["FSWEB_ENV"] == "offline"
    assert seen["env_at_serve"]["FSWEB_DATABASE_URL"].endswith("fsweb.sqlite3")
    assert not settings.lock_path.exists()  # released after shutdown


def test_run_stops_when_migration_fails(fake_fsweb: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_fsweb.manage_exit = 1
    monkeypatch.setattr(launcher, "make_server", lambda h, p: pytest.fail("server must not start"))
    assert launcher.run(OfflineSettings(data_dir=tmp_path / "d", open_browser=False)) == 1


def test_run_refuses_public_interface(tmp_path: Path) -> None:
    with pytest.raises(OfflineConfigError):
        launcher.run(OfflineSettings(data_dir=tmp_path / "d", host="0.0.0.0"))


def test_bootstrap_and_package_passthrough(fake_fsweb: Any, tmp_path: Path) -> None:
    settings = OfflineSettings(data_dir=tmp_path / "d")
    assert launcher.bootstrap(settings, "Example Firm", "admin@example-firm.in") == 0
    assert launcher.manage_passthrough(settings, ["export-engagement", "--id", "E1", "--out", "e.fspkg"]) == 0
    assert fake_fsweb.manage_calls == [
        ["migrate"],
        ["bootstrap", "--tenant", "Example Firm", "--admin-email", "admin@example-firm.in"],
        ["export-engagement", "--id", "E1", "--out", "e.fspkg"],
    ]
