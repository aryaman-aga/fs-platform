"""Loader for ``narrative_terms.yaml``: phrase → metric dictionary (02 §6.8 step 3)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path

import yaml


@dataclass(frozen=True)
class MetricTerms:
    metric: str
    phrases: tuple[re.Pattern[str], ...]
    negative_words: tuple[re.Pattern[str], ...] = ()
    try_also: tuple[str, ...] = ()
    order: int = 0


@dataclass
class TermDictionary:
    version: str
    metrics: list[MetricTerms] = field(default_factory=list)

    def by_metric(self, metric: str) -> MetricTerms | None:
        return next((m for m in self.metrics if m.metric == metric), None)


def _compile(items: list[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.I) for p in items)


def load_terms(path: str | Path | None = None) -> TermDictionary:
    if path is None:
        text = resources.files("fscore.narrative").joinpath("data/narrative_terms.yaml").read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    out = TermDictionary(version=str(data.get("version", "narrative-terms/0")))
    for i, m in enumerate(data.get("metrics", [])):
        out.metrics.append(
            MetricTerms(
                metric=m["metric"],
                phrases=_compile(m.get("phrases", [])),
                negative_words=_compile(m.get("negative_words", [])),
                try_also=tuple(m.get("try_also", [])),
                order=i,
            )
        )
    return out


@lru_cache(maxsize=1)
def default_terms() -> TermDictionary:
    return load_terms()


@dataclass(frozen=True)
class PhraseHit:
    metric: str
    start: int
    end: int
    text: str
    order: int


def find_phrases(sentence: str, terms: TermDictionary | None = None) -> list[PhraseHit]:
    """Every metric phrase occurring in a sentence (overlaps resolved by dictionary order)."""
    td = terms or default_terms()
    hits: list[PhraseHit] = []
    for mt in td.metrics:
        for rx in mt.phrases:
            for m in rx.finditer(sentence):
                hits.append(PhraseHit(mt.metric, m.start(), m.end(), m.group(0), mt.order))
    # drop hits contained in an earlier-ordered (more specific) hit
    hits.sort(key=lambda h: (h.order, h.start))
    kept: list[PhraseHit] = []
    for h in hits:
        # any overlap with an earlier-ordered (more specific) metric's phrase drops this hit:
        # "expected credit losses of" is a provision, not "losses of" (profit / loss)
        if any(k.start < h.end and h.start < k.end and k.order < h.order for k in kept):
            continue
        if any(k.start <= h.start and h.end <= k.end and k.metric == h.metric for k in kept):
            continue
        kept.append(h)
    return sorted(kept, key=lambda h: h.start)
