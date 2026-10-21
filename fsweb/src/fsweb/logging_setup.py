"""Structured JSON logs on stderr (no client figures: IDs and hashes only — 05 §4).

stderr keeps CLI output (JSON results on stdout) machine-readable; container
runtimes collect both streams."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_SAFE_EXTRA = {"correlation_id", "user_id", "path", "method", "status", "duration_ms", "queues", "worker_id", "component", "run_id", "job_id", "ip"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k in _SAFE_EXTRA:
            if hasattr(record, k):
                out[k] = getattr(record, k)
        if record.exc_info:
            out["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(out, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        root.setLevel(level)
        return
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(JsonFormatter())
    root.handlers = [h]
    root.setLevel(level)
