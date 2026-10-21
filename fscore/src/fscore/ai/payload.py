"""Build the exact, minimised, pseudonymised payload a user approves (02 §9.1, C9).

The approval dialog shows ``AIRequestPreview.payload_json`` byte for byte, its
SHA-256, size, token estimate, cost estimate and destination (provider, model,
region, refusal-fallback setting). The approval token is bound to that hash;
any change to the payload invalidates it.

Only findings and small context windows are sent — never the workbook — unless
the purpose is ``full_statements`` *and* the caller sets
``context["allow_full_statements"] = True``. Everything needed to map the
response back (token map, scale factor, original values for re-verification)
lives in ``AIRequestPreview.local`` and is never part of the payload.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fscore.ai.errors import PayloadError
from fscore.ai.redact import AmountScaler, NameCatalogue, Pseudonymiser
from fscore.ai.registry import estimate_cost_usd, prompt_for
from fscore.ai.settings import ProviderSettings, resolve_settings
from fscore.config import AIConfig
from fscore.models import Finding

Purpose = Literal["triage", "narrative", "xref", "explain", "full_statements"]
PURPOSES: tuple[str, ...] = ("triage", "narrative", "xref", "explain", "full_statements")
SCHEMA_VERSION = "fsai/1"
_DIMENSIONLESS = re.compile(r"ratio|pct|percent|%|\beps\b|per share|turnover|times|return on", re.I)


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Destination(_M):
    provider: str
    model: str
    region: str | None = None
    endpoint_host: str | None = None
    refusal_fallback: str = "off"


class PayloadEvidence(_M):
    label: str
    ref: str | None = None
    printed: str | None = None


class PayloadFinding(_M):
    id: str
    rule_id: str
    severity: str
    certainty: str
    sheet: str | None = None
    cells: list[str] = Field(default_factory=list)
    caption: str | None = None
    printed: str | None = None
    expected: str | None = None
    difference: str | None = None
    period: str | None = None
    explanation: str = ""
    suggested_fix: str | None = None
    evidence: list[PayloadEvidence] = Field(default_factory=list)


class ContextRow(_M):
    row: int
    caption: str = ""
    values: dict[str, str] = Field(default_factory=dict)  # column letter -> printed text


class ContextWindow(_M):
    finding_id: str | None = None
    sheet: str
    rows: list[ContextRow] = Field(default_factory=list)


class Paragraph(_M):
    id: str
    text: str
    note: str | None = None
    sheet: str | None = None
    cell: str | None = None


class Metric(_M):
    key: str  # pat, pbt, net_worth, working_capital, cash, revenue, …
    period: str  # CY, PY, Q4_CY, …
    value: str  # printed, in the statement unit
    label: str = ""


class Figure(_M):
    ref: str
    caption: str = ""
    period: str | None = None
    printed: str = ""


class PromptRef(_M):
    id: str
    version: str
    sha256: str


class AIPayload(_M):
    schema_version: str = SCHEMA_VERSION
    purpose: str
    prompt: PromptRef
    destination: Destination
    units: str
    amounts_scaled: bool = False
    findings: list[PayloadFinding] = Field(default_factory=list)
    context: list[ContextWindow] = Field(default_factory=list)
    paragraphs: list[Paragraph] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    figures: list[Figure] = Field(default_factory=list)
    statements: list[ContextWindow] = Field(default_factory=list)

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class LocalState(_M):
    """Kept by the firm only. Never sent, never logged."""

    pseudonyms: dict[str, str] = Field(default_factory=dict)  # token -> original
    scaler: dict[str, Any] | None = None  # factor + scaled->original map
    lookup: dict[str, str] = Field(default_factory=dict)  # normalised ref -> original printed value


class AIRequestPreview(_M):
    purpose: str
    payload: AIPayload
    payload_json: str
    sha256: str
    size_bytes: int
    token_estimate: int
    cost_estimate_usd: str
    destination: Destination
    reason: str = ""
    requested_by: str | None = None
    engagement: str | None = None
    created_at: datetime
    local: LocalState = Field(default_factory=LocalState)

    def approval_summary(self) -> dict[str, Any]:
        """What the approval dialog shows next to the exact payload."""
        return {
            "purpose": self.purpose,
            "reason": self.reason,
            "destination": self.destination.model_dump(),
            "payload_sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "token_estimate": self.token_estimate,
            "cost_estimate_usd": self.cost_estimate_usd,
            "amounts_scaled": self.payload.amounts_scaled,
            "prompt": self.payload.prompt.model_dump(),
            "requested_by": self.requested_by,
        }


# ------------------------------------------------------------------ helpers


def normalise_ref(ref: str) -> str:
    """'BS'!$C$12 -> bs!c12 ; 'pat:CY' -> pat:cy."""
    r = ref.strip().replace("$", "")
    if "!" in r:
        sheet, addr = r.rsplit("!", 1)
        sheet = sheet.strip()
        if sheet.startswith("'") and sheet.endswith("'"):
            sheet = sheet[1:-1].replace("''", "'")
        return f"{sheet.strip().lower()}!{addr.strip().lower()}"
    return r.lower()


def _printed(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, Decimal):
        s = f"{abs(v):,.2f}"
        return f"({s})" if v < 0 else s
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return _printed(Decimal(str(v)))
    return str(v)


def split_sentences(text: str) -> list[str]:
    """Split narrative text into sentences (keeps '₹ 12.34' and 'Rs.' intact)."""
    parts = re.split(r"(?<=[.;])\s+(?=[A-Z(])", text.strip())
    return [p.strip() for p in parts if p.strip()]


def context_windows_from_workbook(workbook: Any, findings: Sequence[Finding], *, neighbours: int = 2, max_cols: int = 10) -> list[ContextWindow]:
    """A few printed rows around each finding's first cell (data minimisation)."""
    from fscore.engine.refs import index_to_col, split_addr

    out: list[ContextWindow] = []
    for f in findings:
        if not f.sheet or not f.cells:
            continue
        sheet = workbook.get_sheet(f.sheet)
        if sheet is None:
            continue
        try:
            r0, _c0 = split_addr(f.cells[0].split(":")[0])
        except ValueError:
            continue
        rows: list[ContextRow] = []
        for r in range(max(1, r0 - neighbours), r0 + neighbours + 1):
            caption_parts: list[str] = []
            values: dict[str, str] = {}
            seen_number = False
            for c in range(1, min(sheet.max_col, 60) + 1):
                cell = sheet.cell(r, c)
                if cell is None or cell.value is None:
                    continue
                if cell.is_number or cell.is_error:
                    seen_number = True
                    if len(values) < max_cols:
                        values[index_to_col(c)] = sheet.rendered(cell).clean
                elif isinstance(cell.value, str) and cell.value.strip() and not seen_number:
                    caption_parts.append(cell.value.strip())
            if caption_parts or values:
                rows.append(ContextRow(row=r, caption=" ".join(caption_parts)[:200], values=values))
        out.append(ContextWindow(finding_id=f.id or None, sheet=sheet.name, rows=rows))
    return out


def _payload_finding(f: Finding, idx: int) -> PayloadFinding:
    return PayloadFinding(
        id=f.id or f"F-{idx:04d}",
        rule_id=f.rule_id,
        severity=f.severity.value,
        certainty=f.certainty.value,
        sheet=f.sheet,
        cells=list(f.cells),
        caption=f.caption,
        printed=f.printed,
        expected=f.expected,
        difference=None if f.difference is None else str(f.difference),
        period=f.period,
        explanation=f.explanation,
        suggested_fix=f.suggested_fix,
        evidence=[PayloadEvidence(label=e.label, ref=str(e.ref) if e.ref else None, printed=e.printed) for e in f.evidence],
    )


def _coerce_windows(items: Iterable[Any]) -> list[ContextWindow]:
    return [w if isinstance(w, ContextWindow) else ContextWindow.model_validate(w) for w in items]


def _coerce_paragraphs(items: Iterable[Any]) -> list[Paragraph]:
    out: list[Paragraph] = []
    for i, p in enumerate(items, start=1):
        if isinstance(p, Paragraph):
            out.append(p)
        elif isinstance(p, str):
            out.append(Paragraph(id=f"S{i}", text=p))
        else:
            d = dict(p)
            d.setdefault("id", f"S{i}")
            out.append(Paragraph.model_validate(d))
    return out


def _coerce_metrics(items: Any) -> list[Metric]:
    if isinstance(items, Mapping):
        out = []
        for k, v in items.items():
            key, _, period = str(k).partition(":")
            out.append(Metric(key=key, period=period or "CY", value=_printed(v)))
        return out
    res = []
    for m in items or []:
        if isinstance(m, Metric):
            res.append(m)
        else:
            d = dict(m)
            d["value"] = _printed(d.get("value"))
            res.append(Metric.model_validate(d))
    return res


def _lookup(payload: AIPayload) -> dict[str, str]:
    """Original printed values by reference, for re-verifying numbers the AI cites (E-AI-05)."""
    lk: dict[str, str] = {}
    for f in payload.findings:
        if f.sheet and f.cells and f.printed:
            lk[normalise_ref(f"{f.sheet}!{f.cells[0]}")] = f.printed
        for e in f.evidence:
            if e.ref and e.printed:
                lk[normalise_ref(e.ref)] = e.printed
    for w in [*payload.context, *payload.statements]:
        for row in w.rows:
            for col, val in row.values.items():
                lk[normalise_ref(f"{w.sheet}!{col}{row.row}")] = val
    for m in payload.metrics:
        lk[normalise_ref(f"{m.key}:{m.period}")] = m.value
    for fig in payload.figures:
        if fig.printed:
            lk[normalise_ref(fig.ref)] = fig.printed
    return lk


def _scale_payload(p: AIPayload, scaler: AmountScaler) -> AIPayload:
    def amt(s: str | None, dimensionless: bool = False) -> str | None:
        if s is None or dimensionless:
            return s
        return scaler.scale_text(s)

    for f in p.findings:
        dimless = f.rule_id.startswith("RATIO")
        f.printed = amt(f.printed, dimless)
        f.expected = amt(f.expected, dimless)
        f.difference = amt(f.difference, dimless)
        if not dimless:
            f.explanation = scaler.scale_text(f.explanation)
            f.suggested_fix = amt(f.suggested_fix)
        for e in f.evidence:
            e.printed = amt(e.printed, dimless or bool(_DIMENSIONLESS.search(e.label)))
    for w in [*p.context, *p.statements]:
        for row in w.rows:
            if _DIMENSIONLESS.search(row.caption):
                continue
            row.values = {k: (scaler.scale_text(v) if v else v) for k, v in row.values.items()}
    for m in p.metrics:
        if not _DIMENSIONLESS.search(f"{m.key} {m.label}"):
            m.value = scaler.scale_text(m.value)
    for para in p.paragraphs:
        para.text = scaler.scale_text(para.text)
    for fig in p.figures:
        if not _DIMENSIONLESS.search(fig.caption):
            fig.printed = scaler.scale_text(fig.printed)
    p.amounts_scaled = True
    return p


def scaling_note(scaled: bool) -> str:
    if not scaled:
        return ""
    return (
        "All currency amounts have been multiplied by one undisclosed constant factor: judge agreement, signs and "
        "relative size, not absolute size. Ratios, percentages and per-share figures are not scaled."
    )


def build_preview(
    findings: Sequence[Finding],
    *,
    purpose: str,
    config: AIConfig,
    names: NameCatalogue | Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    settings: ProviderSettings | None = None,
) -> AIRequestPreview:
    if purpose not in PURPOSES:
        raise PayloadError(f"unknown purpose {purpose!r}")
    ctx = dict(context or {})
    if purpose == "full_statements" and ctx.get("allow_full_statements") is not True:
        raise PayloadError("full statements are sent only when the user explicitly chooses a full-statements review")
    if config.provider == "none":
        dest = Destination(provider="none", model=config.model, region=config.region)
        s = None
    else:
        s = settings or resolve_settings(config)
        dest = Destination(
            provider=s.provider,
            model=s.platform_model,
            region=s.region,
            endpoint_host=s.endpoint_host,
            refusal_fallback=s.fallback_description(),
        )
    prompt = prompt_for(purpose)
    payload = AIPayload(
        purpose=purpose,
        prompt=PromptRef(id=prompt.id, version=prompt.version, sha256=prompt.sha256),
        destination=dest,
        units=str(ctx.get("units", "₹ lakhs")),
        findings=[_payload_finding(f, i) for i, f in enumerate(findings, start=1)],
        context=_coerce_windows(ctx.get("windows", [])),
        paragraphs=_coerce_paragraphs(ctx.get("paragraphs", [])),
        metrics=_coerce_metrics(ctx.get("metrics", [])),
        figures=[x if isinstance(x, Figure) else Figure.model_validate(x) for x in ctx.get("figures", [])],
        statements=_coerce_windows(ctx.get("statements", [])) if purpose == "full_statements" else [],
    )
    if purpose in ("narrative", "full_statements") and not payload.paragraphs and not payload.statements:
        raise PayloadError("a narrative review needs paragraphs (context['paragraphs'])")
    lookup = _lookup(payload)
    local = LocalState(lookup=lookup)
    if config.redact:
        pseudo = Pseudonymiser(names, mapping=ctx.get("pseudonym_map"))
        redacted = pseudo.redact(payload.model_dump(mode="json"))
        # the destination block is never redacted (it names the provider, not the client)
        redacted["destination"] = payload.destination.model_dump(mode="json")
        redacted["prompt"] = payload.prompt.model_dump(mode="json")
        payload = AIPayload.model_validate(redacted)
        local.pseudonyms = pseudo.export_map()
    if config.scale_amounts:
        scaler = AmountScaler(ctx.get("scale_factor"))
        payload = _scale_payload(payload, scaler)
        local.scaler = scaler.export()
    payload_json = payload.canonical_json()
    system_len = len(prompt.body)
    tokens = math.ceil((len(payload_json) + system_len) / 3.5)
    est_output = min(config.max_tokens, 4000)
    cost = estimate_cost_usd(dest.provider, dest.model, tokens, est_output) if s is not None else Decimal(0)
    return AIRequestPreview(
        purpose=purpose,
        payload=payload,
        payload_json=payload_json,
        sha256=hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
        size_bytes=len(payload_json.encode("utf-8")),
        token_estimate=tokens,
        cost_estimate_usd=str(cost),
        destination=dest,
        reason=str(ctx.get("reason", "")),
        requested_by=ctx.get("requested_by"),
        engagement=ctx.get("engagement"),
        created_at=datetime.now(UTC),
        local=local,
    )
