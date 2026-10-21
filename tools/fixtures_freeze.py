"""Record SHA-256 fingerprints of the private real-file fixtures (``make fixtures-freeze``).

The real workbooks are copied by the firm into ``fixtures/private/`` (git-ignored)
together with ``fixtures/private/golden.yaml`` (the acceptance oracle derived from
the build pack's test-case file). This script writes ``fixtures/private/FROZEN.json``
with the first 16 hex characters of SHA-256 for every file, so a changed
original (e.g. a workbook edited after the review) is detected before tests run.
Nothing in fixtures/private is ever committed.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "private"


def fingerprint(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16].upper()


def main() -> int:
    if not ROOT.exists():
        print("fixtures/private does not exist: copy the real files there first (they stay on this machine).")
        return 1
    out = {p.relative_to(ROOT).as_posix(): fingerprint(p) for p in sorted(ROOT.rglob("*")) if p.is_file() and p.name != "FROZEN.json"}
    previous = {}
    frozen = ROOT / "FROZEN.json"
    if frozen.exists():
        previous = json.loads(frozen.read_text(encoding="utf-8"))
    changed = [k for k, v in out.items() if k in previous and previous[k] != v]
    frozen.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"{len(out)} file(s) fingerprinted into {frozen}")
    for k in changed:
        print(f"CHANGED since last freeze: {k}")
    return 2 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
