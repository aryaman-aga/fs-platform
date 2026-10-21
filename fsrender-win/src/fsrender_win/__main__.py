"""Command line: ``fsrender-win run | once | check-config | service ...``."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import signal
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

from fsrender_win.config import ConfigError, load_config


def configure_logging(log_dir: Path | None = None, level: int = logging.INFO) -> None:
    """Console + optional rotating file. Messages carry job IDs only, never client data."""
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger("fsrender_win")
    root.setLevel(level)
    if not root.handlers:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(fmt)
        root.addHandler(console)
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            rotating = logging.handlers.RotatingFileHandler(
                log_dir / "fsrender.log", maxBytes=5 * 1024 * 1024, backupCount=10, encoding="utf-8"
            )
            rotating.setFormatter(fmt)
            root.addHandler(rotating)


def _cmd_run(args: argparse.Namespace) -> int:
    from fsrender_win.worker import build_worker

    config = load_config()
    configure_logging(config.log_dir)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    worker = build_worker(config)
    try:
        worker.run_forever(stop)
    finally:
        worker.client.close()
    return 0


def _cmd_once(args: argparse.Namespace) -> int:
    from fsrender_win.worker import build_worker

    config = load_config()
    configure_logging(config.log_dir)
    worker = build_worker(config)
    try:
        outcome = worker.run_once()
    finally:
        worker.client.close()
    if outcome is None:
        print("no render job queued")
        return 0
    print(f"job {outcome.job_id}: {'ok' if outcome.ok else 'failed: ' + (outcome.error or '')}")
    return 0 if outcome.ok else 1


def _cmd_check_config(args: argparse.Namespace) -> int:
    config = load_config()
    from fsrender_win.worker import detect_excel_build

    print(f"api_url        {config.api_url}")
    print(f"worker_id      {config.worker_id}")
    print(f"job_timeout_s  {config.job_timeout_s}")
    print(f"work_dir       {config.work_dir}")
    print(f"excel_build    {config.excel_build or detect_excel_build()}")
    print("worker_token   (set)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "service":
        from fsrender_win.service import handle_command_line

        return handle_command_line(argv[1:])
    if argv and argv[0] == "service-host":  # started by the SCM (MSI install, see installer/FSRender.wxs)
        from fsrender_win.service import host_service

        return host_service()
    parser = argparse.ArgumentParser(prog="fsrender-win", description="FS-Platform Windows render worker")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="run the render loop in the foreground").set_defaults(func=_cmd_run)
    sub.add_parser("once", help="claim and process at most one job").set_defaults(func=_cmd_once)
    sub.add_parser("check-config", help="validate configuration").set_defaults(func=_cmd_check_config)
    sub.add_parser("service", help="install/start/stop/remove the Windows service (pywin32)")
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
