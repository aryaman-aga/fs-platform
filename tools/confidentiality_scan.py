"""Pre-push confidentiality scan (06 / 11 §3: client data must never reach a repository).

    python tools/confidentiality_scan.py [--denylist fixtures/private/denylist.txt]

Scans every tracked-or-trackable text file of the repository for terms in a
deny-list kept *outside* version control (``fixtures/private/denylist.txt`` is
git-ignored: it names real clients, paths and figures, so it can never be
committed itself). Also flags generic risks: Windows client-share paths,
e-mail addresses, and binary office files outside the synthetic fixtures.
Exit code 1 when anything is found.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".toml", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".css", ".html",
                 ".txt", ".ini", ".cfg", ".sh", ".j2", ".xml", ".tf", ".conf", ".env", ".example", ".sql", ".service", ".timer"}
SKIP_DIRS = {".git", ".venv", "node_modules", "dist", ".pgdata", "var", "fixtures/private", "__pycache__", ".mypy_cache",
             ".ruff_cache", ".pytest_cache", "test-results", "playwright-report"}
GENERIC = [
    (re.compile(r"[A-Z]:\\\\?OneDrive", re.I), "Windows OneDrive client-share path"),
    (re.compile(r"Clients - Documents", re.I), "client document library path"),
]


def candidate_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.splitlines()
        files = [ROOT / p for p in out]
    except (OSError, subprocess.CalledProcessError):
        files = [p for p in ROOT.rglob("*") if p.is_file()]
    keep = []
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        if any(rel == d or rel.startswith(d + "/") or f"/{d}/" in f"/{rel}" for d in SKIP_DIRS):
            continue
        keep.append(p)
    return keep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--denylist", type=Path, default=ROOT / "fixtures" / "private" / "denylist.txt")
    a = ap.parse_args()
    terms: list[str] = []
    if a.denylist.exists():
        terms = [t.strip() for t in a.denylist.read_text(encoding="utf-8").splitlines() if t.strip() and not t.startswith("#")]
    else:
        print(f"note: no deny-list at {a.denylist}; only generic checks run")
    rx = [(re.compile(re.escape(t), re.I) if not t.startswith("re:") else re.compile(t[3:], re.I), t) for t in terms]
    problems: list[str] = []
    for p in candidate_files():
        rel = p.relative_to(ROOT).as_posix()
        if rel == "tools/confidentiality_scan.py":
            continue  # its own generic patterns
        if p.suffix.lower() in (".xlsx", ".xlsm", ".xls", ".pdf") and not rel.startswith(("fixtures/synthetic/", "fscore/src/fscore/pdf/fonts/")):
            problems.append(f"{rel}: office/PDF file outside fixtures/synthetic")
            continue
        if p.suffix.lower() not in TEXT_SUFFIXES and p.name not in ("Makefile", "Dockerfile", "Caddyfile", ".gitignore"):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for r, label in rx:
            for m in r.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                problems.append(f"{rel}:{line}: deny-listed term ({label[:3]}…)")
        for r, label in GENERIC:
            for m in r.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                problems.append(f"{rel}:{line}: {label}")
    for msg in problems:
        print(msg)
    print(f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
