"""Completeness of the operational documentation and public-repository hygiene."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"

T1_IDS = [
    "E-ARCH-01", "E-ARCH-02", "E-ARCH-05",
    "E-SEC-01", "E-SEC-02", "E-SEC-03", "E-SEC-05", "E-SEC-06", "E-SEC-07", "E-SEC-08", "E-SEC-10",
    "E-ITGC-01", "E-ITGC-02", "E-ITGC-03", "E-ITGC-04",
    "E-DATA-01", "E-DATA-02", "E-DATA-03",
    "E-RULE-01", "E-RULE-02", "E-RULE-03",
    "E-PREP-01", "E-PREP-02",
    "E-OPS-01", "E-OPS-02", "E-OPS-04", "E-OPS-05", "E-OPS-07",
    "E-AI-01", "E-AI-02", "E-AI-03", "E-AI-05",
    "E-BCP-01", "E-BCP-02",
]


def test_all_adrs_present_and_indexed() -> None:
    index = (DOCS / "adr" / "README.md").read_text()
    for number in range(1, 22):
        files = list((DOCS / "adr").glob(f"{number:04d}-*.md"))
        assert len(files) == 1, f"ADR {number:04d} missing"
        assert files[0].name in index
        text = files[0].read_text()
        for section in ("## Context", "## Decision", "## Consequences", "**Status:**"):
            assert section in text, f"{files[0].name}: {section}"


def test_phase0_answers_all_twenty_questions() -> None:
    text = (DOCS / "phase0-decisions.md").read_text()
    for number in range(1, 21):
        assert re.search(rf"^\| {number} \|", text, re.M), f"question {number} missing"


def test_go_live_checklist_covers_every_t1_item() -> None:
    text = (DOCS / "go-live-checklist.md").read_text()
    missing = [rid for rid in T1_IDS if rid not in text]
    assert not missing, missing
    for control in range(1, 15):
        assert f"C{control} " in text


def test_threat_model_covers_components() -> None:
    text = (DOCS / "security" / "threat-model.md").read_text()
    for component in ("Browser SPA", "Caddy", "API", "Authentication", "PostgreSQL", "Object store", "Job workers",
                      "AI gateway", "Windows render worker", "Backups", "Mode B", "Mode C"):
        assert f"## {component}" in text, component
    for category in ("Spoofing", "Tampering", "Repudiation", "Information disclosure", "Denial of service", "Elevation of privilege"):
        assert category in text


def test_user_guides_per_role_and_cli_commands() -> None:
    guide = DOCS / "user-guide"
    for page in ("staff", "manager", "partner", "reviewer", "client-uploader", "client-viewer", "firm-admin", "cli"):
        assert (guide / f"{page}.md").is_file(), page
    cli = (guide / "cli.md").read_text()
    for command in ("init", "check", "xref", "fix", "pdf", "review-ai", "report", "pdf-export", "prepare"):
        assert f"## {command}" in cli or f"fscheck {command}" in cli, command


def test_control_matrix_draft_maps_all_controls() -> None:
    text = (DOCS / "control-matrix-draft.md").read_text()
    for control in range(1, 15):
        assert re.search(rf"^\| C{control} \|", text, re.M), f"C{control}"
    for itgc in range(1, 7):
        assert f"E-ITGC-0{itgc}" in text


# ----------------------------------------------------------------------------- public-repo hygiene
OWNED = [ROOT / "ops", ROOT / "docs", ROOT / "fsrender-win", ROOT / "fsoffline", ROOT / "tools" / "shamir.py", ROOT / ".github"]
ALLOWED_IN_DOMAINS = ("example-firm.in", "nic.in", "cert-in.org.in")
TEXT_SUFFIXES = {".md", ".py", ".yml", ".yaml", ".sh", ".tf", ".ini", ".j2", ".conf", ".alloy", ".json", ".spec", ".wxs", ".example", ".txt", ".tpl", ""}


def _owned_files() -> list[Path]:
    out: list[Path] = []
    for base in OWNED:
        candidates = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
        out += [p for p in candidates if p.suffix in TEXT_SUFFIXES and "__pycache__" not in p.parts]
    return out


@pytest.mark.parametrize("path", _owned_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_confidential_material(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="ignore")
    # Windows client-share paths (the real fixture locations live only in the firm's private map)
    assert not re.search(r"[A-Z]:\\\\?OneDrive", text), "client file path"
    # Indian domains other than placeholders and public authorities
    for host in re.findall(r"(?<![\w.@-])((?:[a-z0-9-]+\.)+in)(?![\w.-])", text):
        assert host.endswith(ALLOWED_IN_DOMAINS), f"unexpected .in domain {host!r}"
    # key material
    pem_header = "-----" + "BEGIN"
    assert pem_header not in text or ("PRIVATE" + " KEY") not in text, "private key material"
    assert not re.search(r"AKIA[0-9A-Z]{16}", text), "AWS access key"
