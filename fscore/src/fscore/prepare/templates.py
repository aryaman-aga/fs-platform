"""Template library loader (E-RULE-02): layouts, note templates and standard wordings as versioned data."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml


@lru_cache(maxsize=1)
def _library() -> tuple[dict[str, Any], str]:
    text = resources.files("fscore.prepare").joinpath("data/templates.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text), hashlib.sha256(text.encode("utf-8")).hexdigest()


def library() -> dict[str, Any]:
    return _library()[0]


def library_version() -> str:
    return str(library()["version"])


def library_sha256() -> str:
    return _library()[1]


def layout(name: str) -> dict[str, Any]:
    return dict(library()["layouts"][name])


def note(name: str) -> dict[str, Any]:
    return dict(library()["notes"][name])


def note_number(name: str) -> int:
    return int(library()["notes"][name]["number"])


def note_title(name: str) -> str:
    n = library()["notes"][name]
    return f"{n['number']}. {n['title']}"


def wording(name: str) -> dict[str, Any]:
    return dict(library()["wordings"][name])


def fill(text: str, **values: Any) -> str:
    return " ".join(text.split()).format(**values)


def versions_used(layouts: list[str], notes: list[str], wordings: list[str]) -> dict[str, str]:
    lib = library()
    out: dict[str, str] = {"library": f"{lib['library']}@{lib['version']}", "library_sha256": library_sha256()}
    for n in layouts:
        out[f"layout.{n}"] = str(lib["layouts"][n]["version"])
    for n in notes:
        out[f"note.{n}"] = str(lib["notes"][n]["version"])
    for n in wordings:
        out[f"wording.{n}"] = str(lib["wordings"][n]["version"])
    return out
