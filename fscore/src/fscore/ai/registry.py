"""Prompt and model registry (E-AI-03).

Prompts are versioned files in ``fscore/ai/prompts`` with YAML front matter
(id, use case, version, output schema, approver, effective date). Models are an
approved list per provider with the rates used for cost estimates. Changing
either is a change under E-ITGC-02: the evaluation set (E-AI-02) must not
regress before release. Every AI call records the prompt id, version and hash
and the model, platform and region it ran on.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from importlib import resources

import yaml

from fscore.ai.errors import AIConfigError

DEFAULT_MODEL = "claude-opus-5-5"


@dataclass(frozen=True)
class PromptVersion:
    id: str
    use_case: str
    version: str
    output: str
    approved_by: str
    effective_from: str
    body: str
    sha256: str

    def render(self, **values: str) -> str:
        text = self.body
        for k, v in values.items():
            text = text.replace("{" + k + "}", v)
        return text.strip()


def _parse(name: str, text: str) -> PromptVersion:
    if not text.startswith("---"):
        raise AIConfigError(f"prompt {name} has no front matter")
    _, fm, body = text.split("---", 2)
    meta = yaml.safe_load(fm) or {}
    for key in ("id", "use_case", "version", "output"):
        if key not in meta:
            raise AIConfigError(f"prompt {name}: missing {key}")
    return PromptVersion(
        id=str(meta["id"]),
        use_case=str(meta["use_case"]),
        version=str(meta["version"]),
        output=str(meta["output"]),
        approved_by=str(meta.get("approved_by", "")),
        effective_from=str(meta.get("effective_from", "")),
        body=body.strip() + "\n",
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


@lru_cache(maxsize=1)
def _prompts() -> dict[str, PromptVersion]:
    out: dict[str, PromptVersion] = {}
    folder = resources.files("fscore.ai").joinpath("prompts")
    for entry in folder.iterdir():
        if entry.name.endswith(".md"):
            pv = _parse(entry.name, entry.read_text(encoding="utf-8"))
            out[pv.use_case] = pv
    return out


def list_prompts() -> list[PromptVersion]:
    return sorted(_prompts().values(), key=lambda p: p.use_case)


_PURPOSE_TO_USE_CASE = {"full_statements": "narrative"}


def prompt_for(purpose: str) -> PromptVersion:
    use_case = _PURPOSE_TO_USE_CASE.get(purpose, purpose)
    try:
        return _prompts()[use_case]
    except KeyError as exc:
        raise AIConfigError(f"no approved prompt for use case {use_case!r}") from exc


# ------------------------------------------------------------------ models


@dataclass(frozen=True)
class ModelEntry:
    provider: str
    model: str  # the id requested on that platform
    input_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal
    approved: bool = True
    note: str = ""


# Anthropic first-party rates for Claude Opus 5.5 ($4 / $20 per MTok); Foundry is billed at
# the same rates. Bedrock and Vertex are partner-priced: the estimate uses first-party rates.
MODEL_REGISTRY: tuple[ModelEntry, ...] = (
    ModelEntry("anthropic", DEFAULT_MODEL, Decimal(4), Decimal(20)),
    ModelEntry("foundry", DEFAULT_MODEL, Decimal(4), Decimal(20)),
    ModelEntry("bedrock", "anthropic." + DEFAULT_MODEL, Decimal(4), Decimal(20), note="partner pricing; estimate uses first-party rates"),
    ModelEntry("vertex", DEFAULT_MODEL, Decimal(4), Decimal(20), note="partner pricing; estimate uses first-party rates"),
    ModelEntry("gateway", DEFAULT_MODEL, Decimal(4), Decimal(20), note="firm gateway; billing per the gateway contract"),
    ModelEntry("local", "*", Decimal(0), Decimal(0), note="on-premises model, no per-token charge"),
    ModelEntry("mock", "*", Decimal(0), Decimal(0), note="deterministic test provider"),
)


def model_entry(provider: str, model: str) -> ModelEntry | None:
    for e in MODEL_REGISTRY:
        if e.provider == provider and (e.model == model or e.model == "*"):
            return e
    return None


def require_approved_model(provider: str, model: str) -> ModelEntry:
    """Model upgrades go through change control: unknown models are refused."""
    e = model_entry(provider, model)
    if e is None or not e.approved:
        raise AIConfigError(f"model {model!r} is not approved for provider {provider!r} (E-AI-03 change control)")
    return e


def estimate_cost_usd(provider: str, model: str, input_tokens: int, output_tokens: int) -> Decimal:
    e = model_entry(provider, model)
    if e is None:
        return Decimal(0)
    cost = (Decimal(input_tokens) * e.input_usd_per_mtok + Decimal(output_tokens) * e.output_usd_per_mtok) / Decimal(1_000_000)
    return cost.quantize(Decimal("0.000001"))
