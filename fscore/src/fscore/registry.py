"""Rule registry (E-RULE-01): versioned rule metadata + the check functions that implement them.

Metadata lives in ``fscore/rules/rules.yaml`` (data). Implementations register
with ``@check("CAST-01", "PLUG-01", …)``; one function may emit several rule
IDs. A run records ``rule_set_version()`` and ``rule_set_hash()`` (E-RULE-03).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from importlib import resources
from typing import TYPE_CHECKING, Any

import yaml

from fscore.models import Certainty, FixKind, Severity

if TYPE_CHECKING:
    from fscore.checks.base import CheckContext


@dataclass(frozen=True)
class RuleInfo:
    id: str
    family: str
    title: str
    severity: Severity
    certainty: Certainty
    fix_kind: FixKind
    version: str = "1"
    owner: str = "Technical desk"
    frameworks: tuple[str, ...] = ("ind-as-div2",)
    effective_from: date | None = None
    effective_to: date | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def effective(self, on: date) -> bool:
        if self.effective_from and on < self.effective_from:
            return False
        return not (self.effective_to and on > self.effective_to)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "family": self.family, "title": self.title, "severity": self.severity.value,
            "certainty": self.certainty.value, "fix_kind": self.fix_kind.value, "version": self.version,
            "owner": self.owner, "frameworks": list(self.frameworks),
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "params": self.params,
        }


@lru_cache(maxsize=1)
def _load() -> tuple[str, dict[str, RuleInfo], str]:
    text = resources.files("fscore.rules").joinpath("rules.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    rules: dict[str, RuleInfo] = {}
    for r in data["rules"]:
        rules[r["id"]] = RuleInfo(
            id=r["id"],
            family=r["family"],
            title=r["title"],
            severity=Severity(r.get("severity", "Medium")),
            certainty=Certainty(r.get("certainty", "Likely")),
            fix_kind=FixKind(r.get("fix_kind", "manual")),
            version=str(r.get("version", "1")),
            owner=r.get("owner", data.get("owner", "Technical desk")),
            frameworks=tuple(r.get("frameworks", ["ind-as-div2"])),
            effective_from=r.get("effective_from"),
            effective_to=r.get("effective_to"),
            params=r.get("params", {}) or {},
        )
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return str(data["version"]), rules, digest


def rule_set_version() -> str:
    return _load()[0]


def rule_set_hash() -> str:
    return _load()[2]


def get_rule(rule_id: str) -> RuleInfo:
    rules = _load()[1]
    if rule_id not in rules:
        raise KeyError(f"unknown rule {rule_id}")
    return rules[rule_id]


def list_rules() -> list[RuleInfo]:
    return list(_load()[1].values())


def catalogue_json() -> str:
    return json.dumps({"version": rule_set_version(), "hash": rule_set_hash(), "rules": [r.as_dict() for r in list_rules()]}, indent=2)


# ---------------------------------------------------------------- implementations

CheckFn = Callable[["CheckContext"], None]


@dataclass
class CheckImpl:
    name: str
    rule_ids: tuple[str, ...]
    fn: CheckFn
    order: int = 100  # lower runs earlier (e.g. analysis that later checks reuse)
    needs_pdf: bool = False


_IMPLS: dict[str, CheckImpl] = {}


def check(*rule_ids: str, order: int = 100, needs_pdf: bool = False) -> Callable[[CheckFn], CheckFn]:
    """Register a check function implementing one or more rule IDs."""

    def deco(fn: CheckFn) -> CheckFn:
        for rid in rule_ids:
            get_rule(rid)  # fail fast on unknown IDs
        name = f"{fn.__module__}.{fn.__name__}"
        _IMPLS[name] = CheckImpl(name, tuple(rule_ids), fn, order, needs_pdf)
        return fn

    return deco


def implementations() -> list[CheckImpl]:
    return sorted(_IMPLS.values(), key=lambda c: (c.order, c.name))


CHECK_MODULES = (
    "fscore.checks.casting",
    "fscore.checks.crosscast",
    "fscore.checks.plugs",
    "fscore.checks.typed_totals",
    "fscore.checks.hidden",
    "fscore.checks.balance",
    "fscore.checks.rollforward",
    "fscore.checks.xref",
    "fscore.checks.tie_outs",
    "fscore.checks.subledger",
    "fscore.checks.share_capital",
    "fscore.checks.ratios",
    "fscore.checks.identity",
    "fscore.checks.captions",
    "fscore.checks.formats",
    "fscore.checks.narrative",
    "fscore.checks.going_concern",
    "fscore.checks.rpt",
    "fscore.checks.multi",
    "fscore.checks.pdf_recon",
    "fscore.checks.engine_meta",
)


def load_all_checks() -> list[CheckImpl]:
    """Import every check module (missing modules are skipped while the build is in progress)."""
    import importlib

    for mod in CHECK_MODULES:
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError as exc:
            if exc.name != mod:
                raise
    return implementations()
