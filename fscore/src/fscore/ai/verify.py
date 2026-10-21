"""Re-verification of every number the AI cites (E-AI-05, 02 §6.8 step 6).

The model must cite each figure with its reference; the engine looks the
reference up in the firm's own (unredacted, unscaled) values and flags any
disagreement. A proposal with an unverified citation is shown as such and is
never trusted for a figure.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

from fscore.ai.payload import normalise_ref
from fscore.engine.display import parse_printed_number

Lookup = Mapping[str, str] | Callable[[str], Any]


class CitationCheck(BaseModel):
    ref: str
    cited: str
    actual: str | None = None
    status: Literal["match", "mismatch", "unknown_ref", "unparseable"]


def _to_decimal(v: Any) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return Decimal(str(v))
    return parse_printed_number(str(v))


def _resolve(lookup: Lookup, ref: str) -> Any:
    if callable(lookup):
        return lookup(ref)
    key = normalise_ref(ref)
    if key in lookup:
        return lookup[key]
    # tolerate "Sheet!C12:C14" (first cell) and metric refs without a period ("pat" -> "pat:cy")
    first = key.split(":")[0] if "!" in key else key
    if first in lookup:
        return lookup[first]
    if ":" not in key and f"{key}:cy" in lookup:
        return lookup[f"{key}:cy"]
    return None


def verify_citations(cited: Iterable[Any], lookup: Lookup, *, tolerance: Decimal = Decimal(0)) -> list[CitationCheck]:
    out: list[CitationCheck] = []
    for c in cited:
        ref = c["ref"] if isinstance(c, Mapping) else c.ref
        value = c["value"] if isinstance(c, Mapping) else c.value
        actual = _resolve(lookup, ref)
        cited_d = _to_decimal(value)
        if actual is None:
            out.append(CitationCheck(ref=ref, cited=str(value), status="unknown_ref"))
            continue
        actual_d = _to_decimal(actual)
        if cited_d is None or actual_d is None:
            out.append(CitationCheck(ref=ref, cited=str(value), actual=str(actual), status="unparseable"))
            continue
        ok = abs(cited_d - actual_d) <= tolerance
        out.append(CitationCheck(ref=ref, cited=str(value), actual=str(actual), status="match" if ok else "mismatch"))
    return out


def collect_citations(output: Any) -> list[tuple[str, Any]]:
    """(owner id, CitedNumber) pairs from any structured output."""
    pairs: list[tuple[str, Any]] = []

    def walk(obj: Any, owner: str) -> None:
        if isinstance(obj, BaseModel):
            data = obj
            ident = next((getattr(data, k) for k in ("finding_id", "sentence_id") if hasattr(data, k)), None)
            if ident is None and hasattr(data, "a_ref"):
                ident = f"{data.a_ref}~{data.b_ref}"  # type: ignore[attr-defined]
            own = str(ident) if ident is not None else owner
            for name in type(data).model_fields:
                val = getattr(data, name)
                if name == "cited_numbers":
                    for cn in val:
                        pairs.append((own, cn))
                else:
                    walk(val, own)
        elif isinstance(obj, list):
            for x in obj:
                walk(x, owner)

    walk(output, "")
    return pairs
