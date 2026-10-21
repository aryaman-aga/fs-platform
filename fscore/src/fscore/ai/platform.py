"""Bridge between the web platform's AI gate and the engine's AI pipeline.

The platform (fsweb) owns the user-facing controls of C9: it shows the exact
payload, binds its own approval token to the payload hash, refuses
self-approval and lets only the ``ai-gateway`` component call out. These two
functions give it the engine's pseudonymisation, prompt registry, provider
clients, structured-output validation and cited-number re-verification:

``build_payload(kind=, findings=, context=, config=) -> dict``
    ``{"payload": <exact JSON sent>, "mapping": {"preview": …}, "sha256", "instructions_version", "destination"}``.
    ``mapping`` holds the token map and must stay with the firm (never sent).

``send_payload(payload=, config=, mapping=, requester=, approver=) -> dict``
    Re-checks that ``payload`` is the previewed one, re-applies the engine's own
    approval binding (requester ≠ approver, hash-bound, short-lived), sends it and
    returns ``AIResult`` as JSON (``stop_reason``, ``usage``, ``output``, ``proposals``…).
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from fscore.ai.payload import AIRequestPreview, build_preview
from fscore.ai.review import send_request
from fscore.config import AIConfig
from fscore.models import Certainty, Finding, Severity

_KIND_TO_CATALOGUE = {"ENTITY": "entities", "GROUP": "entities", "PERSON": "persons", "PARTY": "parties"}


def _finding(d: Mapping[str, Any]) -> Finding:
    diff = d.get("diff", d.get("difference"))
    try:
        difference = Decimal(str(diff)) if diff not in (None, "") else None
    except InvalidOperation:
        difference = None
    sev = str(d.get("severity") or "Medium")
    cert = str(d.get("certainty") or "Likely")
    return Finding(
        id=str(d.get("fid") or d.get("id") or ""),
        rule_id=str(d.get("rule_id") or "?"),
        severity=Severity(sev) if sev in Severity._value2member_map_ else Severity.MEDIUM,
        certainty=Certainty(cert) if cert in Certainty._value2member_map_ else Certainty.LIKELY,
        sheet=d.get("sheet"),
        cells=list(d.get("cells") or []),
        caption=d.get("caption"),
        printed=None if d.get("printed") is None else str(d.get("printed")),
        expected=None if d.get("expected") is None else str(d.get("expected")),
        difference=difference,
        explanation=str(d.get("explanation") or ""),
        suggested_fix=d.get("suggested_fix"),
    )


def _catalogue(names: Any) -> dict[str, Any]:
    """Accept the engine's catalogue shape or the platform's {value: KIND} map."""
    if not names:
        return {}
    if isinstance(names, Mapping) and any(k in names for k in ("entities", "persons", "parties", "identifiers")):
        return dict(names)
    out: dict[str, Any] = {"entities": [], "persons": [], "parties": [], "identifiers": {}}
    for value, kind in dict(names).items():
        k = str(kind).upper()
        if k in _KIND_TO_CATALOGUE:
            out[_KIND_TO_CATALOGUE[k]].append(str(value))
        else:
            out["identifiers"].setdefault(k.lower(), []).append(str(value))
    return out


def build_payload(*, kind: str, findings: Sequence[Mapping[str, Any] | Finding], context: Mapping[str, Any], config: AIConfig) -> dict[str, Any]:
    ctx = dict(context)
    names = _catalogue(ctx.pop("names", None))
    fs = [f if isinstance(f, Finding) else _finding(f) for f in findings]
    preview = build_preview(fs, purpose=kind, config=config, names=names, context=ctx)
    return {
        "payload": json.loads(preview.payload_json),
        "mapping": {"preview": preview.model_dump(mode="json")},
        "sha256": preview.sha256,
        "instructions_version": f"{preview.payload.prompt.id}@{preview.payload.prompt.version}",
        "destination": preview.destination.model_dump(mode="json"),
        "approval_summary": preview.approval_summary(),
    }


def send_payload(
    *,
    payload: Mapping[str, Any],
    config: AIConfig,
    mapping: Mapping[str, Any] | None = None,
    requester: str | None = None,
    approver: str | None = None,
) -> dict[str, Any]:
    if not mapping or "preview" not in mapping:
        raise ValueError("the locally stored preview (mapping) is required to send and to restore names")
    preview = AIRequestPreview.model_validate(mapping["preview"])
    if json.loads(preview.payload_json) != dict(payload):
        raise ValueError("the payload differs from the one that was previewed and approved")
    who_requested = requester or preview.requested_by or ""
    who_approved = approver or ""
    if not who_approved or who_approved == who_requested:
        raise ValueError("a different user must approve the AI call (segregation of duties)")
    preview = preview.model_copy(update={"requested_by": who_requested})
    secret = secrets.token_bytes(32)  # per-call: the platform's stored approval is the record; this re-binds the hash here
    from fscore.ai.approval import issue_approval

    token, _record = issue_approval(preview.sha256, approver=who_approved, requester=who_requested, secret=secret)
    result = send_request(preview, token, config=config, secret=secret, requester=who_requested)
    return result.model_dump(mode="json")
