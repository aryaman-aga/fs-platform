"""PDF naming (02 §10A): ``<Entity>_<Set>_<Period>_v<version>_<yyyymmdd-hhmm>.pdf``; never overwrite."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_SAFE = re.compile(r"[^A-Za-z0-9.\-]+")
_VERSIONED = re.compile(r"^(?P<prefix>.+)_v(?P<v>\d+)_(?P<stamp>\d{8}-\d{4})$")


def safe_part(text: str) -> str:
    out = _SAFE.sub("-", text.strip()).strip("-.")
    return re.sub(r"-{2,}", "-", out) or "x"


def pdf_filename(entity: str, statement_set: str, period: str, version: int, when: datetime | None = None) -> str:
    when = when or datetime.now()
    return f"{safe_part(entity)}_{safe_part(statement_set)}_{safe_part(period)}_v{version}_{when:%Y%m%d-%H%M}.pdf"


def next_version(directory: Path, entity: str, statement_set: str, period: str) -> int:
    prefix = f"{safe_part(entity)}_{safe_part(statement_set)}_{safe_part(period)}_v"
    best = 0
    if directory.exists():
        for p in directory.glob(prefix + "*.pdf"):
            m = re.match(re.escape(prefix) + r"(\d+)_", p.name)
            if m:
                best = max(best, int(m.group(1)))
    return best + 1


def versioned_path(directory: Path, entity: str, statement_set: str, period: str, when: datetime | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    v = next_version(directory, entity, statement_set, period)
    return never_overwrite(directory / pdf_filename(entity, statement_set, period, v, when))


def never_overwrite(path: Path) -> Path:
    """Return ``path`` if free, else the next free versioned sibling (keeps every PDF)."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix or ".pdf"
    m = _VERSIONED.match(stem)
    if m:
        v = int(m.group("v"))
        while True:
            v += 1
            cand = path.with_name(f"{m.group('prefix')}_v{v}_{m.group('stamp')}{suffix}")
            if not cand.exists():
                return cand
    n = 2
    base = re.sub(r"_v\d+$", "", stem)
    while True:
        cand = path.with_name(f"{base}_v{n}{suffix}")
        if not cand.exists():
            return cand
        n += 1
