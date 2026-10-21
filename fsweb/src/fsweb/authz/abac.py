"""Attribute-based access control on top of RBAC (E-SEC-02).

Policies are versioned YAML documents (``policy`` table, name ``abac``). A rule
matches when every attribute in ``when`` has one of the listed values and no
attribute in ``unless`` does. Only ``deny`` rules exist: RBAC grants, ABAC
restricts. Every decision carries the policy version and the rule that fired.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml


@dataclass(frozen=True)
class Decision:
    allowed: bool
    rule_id: str | None = None
    reason: str = ""
    policy_version: int = 0


@dataclass
class PolicyDoc:
    name: str
    version: int
    rules: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def parse(cls, text: str) -> PolicyDoc:
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict) or not isinstance(data.get("rules", []), list):
            raise ValueError("policy must be a mapping with a 'rules' list")
        rules = data.get("rules") or []
        for r in rules:
            if r.get("effect") != "deny":
                raise ValueError(f"rule {r.get('id')}: only 'deny' rules are allowed (RBAC grants)")
            if not isinstance(r.get("when"), dict) or not r["when"]:
                raise ValueError(f"rule {r.get('id')}: 'when' must be a non-empty mapping")
        return cls(name=str(data.get("name", "abac")), version=int(data.get("version", 1)), rules=rules)


@lru_cache(maxsize=1)
def default_policy_text() -> str:
    return resources.files("fsweb").joinpath("authz/policies/abac.yaml").read_text(encoding="utf-8")


@lru_cache(maxsize=64)
def _parse_cached(text: str) -> PolicyDoc:
    return PolicyDoc.parse(text)


def _matches(expected: list[Any], actual: Any) -> bool:
    if isinstance(actual, list | set | tuple):
        return any(a in expected for a in actual)
    return actual in expected


def evaluate(policy_text: str, attrs: dict[str, Any]) -> Decision:
    doc = _parse_cached(policy_text)
    for rule in doc.rules:
        when: dict[str, list[Any]] = rule["when"]
        if not all(k in attrs and _matches(v, attrs[k]) for k, v in when.items()):
            continue
        unless: dict[str, list[Any]] = rule.get("unless") or {}
        if any(k in attrs and _matches(v, attrs[k]) for k, v in unless.items()):
            continue
        return Decision(False, rule.get("id"), rule.get("description", ""), doc.version)
    return Decision(True, None, "", doc.version)


def network_of(ip: str | None, office_cidrs: list[str]) -> str:
    if not ip or not office_cidrs:
        return "unknown"
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "remote"
    for c in office_cidrs:
        try:
            if addr in ipaddress.ip_network(c, strict=False):
                return "office"
        except ValueError:
            continue
    return "remote"
