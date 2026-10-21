"""Run the evaluation set against a provider and gate releases on it (E-AI-02, E-ITGC-02).

The eval set is synthetic, so the harness calls the provider through the same
code path as production (payload build, pseudonymisation, structured-output
validation, restoration) without the per-payload approval that protects
client data. Reports record the dataset hash, prompt versions and model so a
release can attach them as evidence.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from fscore.ai.eval.dataset import (
    DATASET_VERSION,
    NarrativeCase,
    TriageCase,
    dataset_sha256,
    narrative_cases,
    triage_cases,
)
from fscore.ai.payload import build_preview
from fscore.ai.provider import Provider, get_provider
from fscore.ai.registry import DEFAULT_MODEL, list_prompts
from fscore.ai.review import execute
from fscore.ai.settings import ProviderSettings
from fscore.config import AIConfig


class ClassMetrics(BaseModel):
    precision: float
    recall: float
    f1: float
    support: int


class UseCaseMetrics(BaseModel):
    use_case: str
    n: int
    answered: int
    accuracy: float
    positive_class: str
    precision: float
    recall: float
    f1: float
    per_class: dict[str, ClassMetrics] = Field(default_factory=dict)
    statuses: dict[str, int] = Field(default_factory=dict)
    failures: list[str] = Field(default_factory=list)


class EvalReport(BaseModel):
    dataset_version: str
    dataset_sha256: str
    provider: str
    model: str
    prompt_versions: dict[str, str]
    metrics: dict[str, UseCaseMetrics]
    created_at: datetime

    @property
    def total_items(self) -> int:
        return sum(m.n for m in self.metrics.values())

    def write(self, path: Path) -> Path:
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return round(p, 4), round(r, 4), round(f, 4)


def _metrics(use_case: str, gold: Mapping[str, str], pred: Mapping[str, str], classes: list[str], positive: str, statuses: Counter[str]) -> UseCaseMetrics:
    per: dict[str, ClassMetrics] = {}
    for cls in classes:
        tp = sum(1 for k, g in gold.items() if g == cls and pred.get(k) == cls)
        fp = sum(1 for k, g in gold.items() if g != cls and pred.get(k) == cls)
        fn = sum(1 for k, g in gold.items() if g == cls and pred.get(k) != cls)
        p, r, f = _prf(tp, fp, fn)
        per[cls] = ClassMetrics(precision=p, recall=r, f1=f, support=sum(1 for g in gold.values() if g == cls))
    correct = sum(1 for k, g in gold.items() if pred.get(k) == g)
    pos = per[positive]
    return UseCaseMetrics(
        use_case=use_case,
        n=len(gold),
        answered=sum(1 for k in gold if k in pred),
        accuracy=round(correct / len(gold), 4) if gold else 0.0,
        positive_class=positive,
        precision=pos.precision,
        recall=pos.recall,
        f1=pos.f1,
        per_class=per,
        statuses=dict(statuses),
        failures=sorted(k for k, g in gold.items() if pred.get(k) != g),
    )


def _chunks(items: list[object], size: int) -> list[list[object]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def run_eval(
    provider: Provider | None = None,
    *,
    settings: ProviderSettings | None = None,
    batch: int = 20,
) -> EvalReport:
    s = settings or ProviderSettings(provider="mock", model=DEFAULT_MODEL, region=None)
    prov = provider or get_provider(s)
    cfg = AIConfig(provider="mock" if s.provider == "mock" else s.provider, model=s.model, region=s.region, max_tokens=s.max_tokens)
    # ---------------- triage
    tcases = triage_cases()
    gold_t = {c.finding.id: c.label for c in tcases}
    pred_t: dict[str, str] = {}
    st_t: Counter[str] = Counter()
    for chunk in _chunks(list(tcases), batch):
        tchunk = [c for c in chunk if isinstance(c, TriageCase)]
        preview = build_preview([c.finding for c in tchunk], purpose="triage", config=cfg, settings=s)
        res = execute(preview, prov, s)
        st_t[res.status] += 1
        for item in (res.output or {}).get("items", []):
            pred_t[item["finding_id"]] = item["classification"]
    # ---------------- narrative (one metrics table per request)
    ncases = narrative_cases()
    gold_n = {c.id: c.label for c in ncases}
    pred_n: dict[str, str] = {}
    st_n: Counter[str] = Counter()
    groups: dict[str, list[NarrativeCase]] = {}
    for c in ncases:
        groups.setdefault(json.dumps(c.metrics, sort_keys=True), []).append(c)
    for metrics_key, cases_ in groups.items():
        metrics = json.loads(metrics_key)
        for chunk in _chunks(list(cases_), batch):
            nchunk = [c for c in chunk if isinstance(c, NarrativeCase)]
            ctx = {"paragraphs": [{"id": c.id, "text": c.sentence} for c in nchunk], "metrics": metrics}
            preview = build_preview([], purpose="narrative", config=cfg, context=ctx, settings=s)
            res = execute(preview, prov, s)
            st_n[res.status] += 1
            for sent in (res.output or {}).get("sentences", []):
                pred_n[sent["sentence_id"]] = sent["consistent"]
    return EvalReport(
        dataset_version=DATASET_VERSION,
        dataset_sha256=dataset_sha256(),
        provider=s.provider,
        model=s.platform_model,
        prompt_versions={p.use_case: p.version for p in list_prompts()},
        metrics={
            "triage": _metrics("triage", gold_t, pred_t, ["genuine", "presentation_only", "false_positive"], "genuine", st_t),
            "narrative": _metrics("narrative", gold_n, pred_n, ["yes", "no"], "no", st_n),
        },
        created_at=datetime.now(UTC),
    )


def compare(baseline: EvalReport, candidate: EvalReport, *, max_drop: float = 0.02) -> list[str]:
    """Regressions beyond ``max_drop`` in precision or recall (release gate)."""
    out: list[str] = []
    for uc, b in baseline.metrics.items():
        c = candidate.metrics.get(uc)
        if c is None:
            out.append(f"{uc}: missing in candidate")
            continue
        for name in ("precision", "recall"):
            bv, cv = getattr(b, name), getattr(c, name)
            if cv < bv - max_drop:
                out.append(f"{uc}: {name} fell from {bv:.3f} to {cv:.3f}")
    return out
