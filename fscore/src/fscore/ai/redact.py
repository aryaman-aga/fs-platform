"""Pseudonymisation and optional amount scaling for AI payloads (02 §9.1).

Identity data never leaves the firm: client and entity names, person and party
names, CIN, PAN, GSTIN, DIN, membership numbers, FRNs, Aadhaar-like numbers and
e-mail addresses are replaced by stable tokens (``ENTITY_1``, ``PERSON_3``,
``DIN_A``…). The token map stays local and is applied to the model's response.

Amount scaling multiplies every currency amount by one undisclosed factor per
run. The multiplication is exact (no rounding), so sums, equalities and ratios
between amounts survive; dimensionless figures (ratios, percentages, EPS) are
left alone by the payload builder.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field

# ------------------------------------------------------------------ name catalogue


class NameCatalogue(BaseModel):
    """Names and identifiers the caller knows about (signatory master, party list, client master)."""

    entities: list[str] = Field(default_factory=list)  # client, reporting entity, group companies
    persons: list[str] = Field(default_factory=list)  # directors, KMP, auditors, signatories
    parties: list[str] = Field(default_factory=list)  # related parties / counterparties
    identifiers: dict[str, list[str]] = Field(default_factory=dict)  # kind -> values (din, cin, pan, …)

    @classmethod
    def coerce(cls, names: NameCatalogue | Mapping[str, Any] | None) -> NameCatalogue:
        if names is None:
            return cls()
        if isinstance(names, NameCatalogue):
            return names
        return cls.model_validate(dict(names))


# token prefixes: names use numbers, identifiers use letters (02 §9.1 examples)
_NAME_PREFIX = {"entity": "ENTITY", "person": "PERSON", "party": "PARTY"}
_ID_PREFIX = {
    "din": "DIN",
    "cin": "CIN",
    "pan": "PAN",
    "gstin": "GSTIN",
    "membership": "MEMNO",
    "frn": "FRN",
    "aadhaar": "AADHAAR",
    "email": "EMAIL",
    "udin": "UDIN",
}
_KEY_KINDS = {
    "din": "din",
    "cin": "cin",
    "pan": "pan",
    "gstin": "gstin",
    "membership_no": "membership",
    "membership": "membership",
    "frn": "frn",
    "email": "email",
    "aadhaar": "aadhaar",
    "udin": "udin",
}
TOKEN_RE = re.compile(r"\b(?:ENTITY|PERSON|PARTY)_\d+\b|\b(?:DIN|CIN|PAN|GSTIN|MEMNO|FRN|AADHAAR|EMAIL|UDIN)_[A-Z]+\b")

# identifier patterns (order matters: GSTIN contains a PAN)
_EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_GSTIN_RE = re.compile(r"(?<![A-Z0-9])\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z](?![A-Z0-9])")
_CIN_RE = re.compile(r"(?<![A-Z0-9])[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}(?![A-Z0-9])")
_PAN_RE = re.compile(r"(?<![A-Z0-9])[A-Z]{3}[ABCFGHJLPT][A-Z]\d{4}[A-Z](?![A-Z0-9])")
_UDIN_RE = re.compile(r"(?<![A-Z0-9])\d{8}[A-Z]{6}\d{4}(?![A-Z0-9])")
_AADHAAR_RE = re.compile(r"(?<!\d)[2-9]\d{3}[ -]\d{4}[ -]\d{4}(?!\d)")
_DIN_CTX_RE = re.compile(r"(?i)(\bDIN\s*(?:No\.?|Number)?\s*[:.\-]?\s*)(\d{8})(?!\d)")
_MEMNO_CTX_RE = re.compile(r"(?i)(\b(?:membership\s*(?:no\.?|number)|m\.\s*no\.?|mem\.?\s*no\.?)\s*[:.\-]?\s*)(\d{6})(?!\d)")
_FRN_CTX_RE = re.compile(r"(?i)(\b(?:FRN|firm\s+registration\s*(?:no\.?|number)?)\s*[:.\-]?\s*)(\d{6}[A-Z])(?![A-Z0-9])")

_SUFFIXES = [
    ("private limited", ["pvt. ltd.", "pvt ltd", "pvt. limited", "private ltd", "(p) ltd", "pvt.ltd."]),
    ("limited", ["ltd.", "ltd"]),
    ("pte. ltd.", ["pte ltd", "private limited"]),
    ("incorporated", ["inc.", "inc"]),
    ("inc.", ["inc", "incorporated"]),
    ("llp", ["l.l.p."]),
]
_LEGAL_TAIL = re.compile(
    r"[\s,]+(private limited|pvt\.? ?ltd\.?|limited|ltd\.?|pte\.? ?ltd\.?|inc\.?|incorporated|llp|llc|fz-llc|gmbh|b\.v\.|bv|plc|pty\.? ?ltd\.?|corporation|corp\.?)$",
    re.I,
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip()).lower()


def _letters(n: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


class Pseudonymiser:
    """Stable, reversible replacement of names and identifiers with tokens."""

    def __init__(self, names: NameCatalogue | Mapping[str, Any] | None = None, *, mapping: Mapping[str, str] | None = None) -> None:
        self._token_to_original: dict[str, str] = {}
        self._key_to_token: dict[tuple[str, str], str] = {}
        self._counters: dict[str, int] = {}
        if mapping:
            for token, original in mapping.items():
                self._register_existing(token, original)
        cat = NameCatalogue.coerce(names)
        self._variants: list[tuple[str, str, str]] = []  # (variant, kind, canonical original)
        for kind, items in (("entity", cat.entities), ("party", cat.parties), ("person", cat.persons)):
            for name in items:
                if name and name.strip():
                    self._add_name(kind, name.strip())
        self._add_core_variants()
        self._explicit_ids: list[tuple[str, str]] = []  # (value, kind)
        for kind, values in cat.identifiers.items():
            k = _KEY_KINDS.get(kind.lower(), kind.lower())
            for v in values:
                if v and str(v).strip():
                    self._explicit_ids.append((str(v).strip(), k))
        self._name_re = self._compile_names()

    # ------------------------------------------------------------------ registration
    def _register_existing(self, token: str, original: str) -> None:
        m = re.match(r"^([A-Z]+)_([A-Z0-9]+)$", token)
        if not m:
            return
        prefix, suffix = m.groups()
        kind = next((k for k, p in {**_NAME_PREFIX, **_ID_PREFIX}.items() if p == prefix), prefix.lower())
        self._token_to_original[token] = original
        self._key_to_token[(kind, _norm(original))] = token
        n = int(suffix) if suffix.isdigit() else _letters_to_int(suffix)
        self._counters[prefix] = max(self._counters.get(prefix, 0), n)

    def token_for(self, kind: str, original: str) -> str:
        key = (kind, _norm(original))
        tok = self._key_to_token.get(key)
        if tok is not None:
            return tok
        if kind in _NAME_PREFIX:
            prefix = _NAME_PREFIX[kind]
            n = self._counters.get(prefix, 0) + 1
            self._counters[prefix] = n
            tok = f"{prefix}_{n}"
        else:
            prefix = _ID_PREFIX.get(kind, kind.upper())
            n = self._counters.get(prefix, 0) + 1
            self._counters[prefix] = n
            tok = f"{prefix}_{_letters(n)}"
        self._key_to_token[key] = tok
        self._token_to_original[tok] = original
        return tok

    def _add_name(self, kind: str, name: str) -> None:
        tok = self.token_for(kind, name)
        canonical = self._token_to_original[tok]
        variants = {name}
        low = _norm(name)
        for full, alts in _SUFFIXES:
            if low.endswith(" " + full):
                stem = name[: len(name) - len(full)].rstrip()
                for alt in alts:
                    variants.add(f"{stem} {alt}")
        for v in variants:
            self._variants.append((v, kind, canonical))

    def _add_core_variants(self) -> None:
        """'Corvus Holdings Private Limited' also matches 'Corvus Holdings' when unambiguous."""
        cores: dict[str, list[tuple[str, str]]] = {}
        for v, kind, canonical in list(self._variants):
            if kind == "person":
                continue
            core = _LEGAL_TAIL.sub("", v).strip()
            if core and core != v and len(core.split()) >= 2:
                cores.setdefault(_norm(core), []).append((kind, canonical))
        for core, owners in cores.items():
            distinct = {c for _k, c in owners}
            if len(distinct) == 1:
                kind, canonical = owners[0]
                self._variants.append((core, kind, canonical))

    def _compile_names(self) -> re.Pattern[str] | None:
        if not self._variants:
            return None
        uniq = sorted({v for v, _k, _c in self._variants}, key=len, reverse=True)
        alts = [r"\s+".join(re.escape(part) for part in v.split()) for v in uniq]
        return re.compile(r"(?<![\w])(?:" + "|".join(alts) + r")(?![\w])", re.I)

    # ------------------------------------------------------------------ redaction
    def redact_text(self, text: str) -> str:
        if not text:
            return text
        out = text
        if self._name_re is not None:
            lookup = {_norm(v): (k, c) for v, k, c in self._variants}

            def name_sub(m: re.Match[str]) -> str:
                hit = lookup.get(_norm(m.group(0)))
                if hit is None:
                    return m.group(0)
                kind, canonical = hit
                return self.token_for(kind, canonical)

            out = self._name_re.sub(name_sub, out)
        out = _EMAIL_RE.sub(lambda m: self.token_for("email", m.group(0)), out)
        out = _UDIN_RE.sub(lambda m: self.token_for("udin", m.group(0)), out)
        out = _GSTIN_RE.sub(lambda m: self.token_for("gstin", m.group(0)), out)
        out = _CIN_RE.sub(lambda m: self.token_for("cin", m.group(0)), out)
        out = _PAN_RE.sub(lambda m: self.token_for("pan", m.group(0)), out)
        out = _AADHAAR_RE.sub(lambda m: self.token_for("aadhaar", re.sub(r"\D", "", m.group(0))), out)
        out = _DIN_CTX_RE.sub(lambda m: m.group(1) + self.token_for("din", m.group(2)), out)
        out = _MEMNO_CTX_RE.sub(lambda m: m.group(1) + self.token_for("membership", m.group(2)), out)
        out = _FRN_CTX_RE.sub(lambda m: m.group(1) + self.token_for("frn", m.group(2)), out)
        for value, kind in sorted(self._explicit_ids, key=lambda x: len(x[0]), reverse=True):
            pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])")
            token = self.token_for(kind, value) if pattern.search(out) else None
            if token is not None:
                out = pattern.sub(token, out)
        return out

    def redact(self, obj: Any, *, _key: str | None = None) -> Any:
        """Recursively redact strings inside dicts / lists (identifier keys force a token)."""
        if isinstance(obj, str):
            kind = _KEY_KINDS.get((_key or "").lower())
            if kind and obj.strip() and not TOKEN_RE.fullmatch(obj.strip()):
                return self.token_for(kind, obj.strip())
            return self.redact_text(obj)
        if isinstance(obj, Mapping):
            return {k: self.redact(v, _key=str(k)) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.redact(v) for v in obj]
        return obj

    # ------------------------------------------------------------------ restoration
    def restore_text(self, text: str) -> str:
        if not text:
            return text
        return TOKEN_RE.sub(lambda m: self._token_to_original.get(m.group(0), m.group(0)), text)

    def restore(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.restore_text(obj)
        if isinstance(obj, Mapping):
            return {k: self.restore(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.restore(v) for v in obj]
        return obj

    def export_map(self) -> dict[str, str]:
        """token -> original. LOCAL ONLY: never part of a payload."""
        return dict(self._token_to_original)

    @property
    def originals(self) -> list[str]:
        return list(self._token_to_original.values())


def _letters_to_int(s: str) -> int:
    n = 0
    for ch in s:
        n = n * 26 + (ord(ch) - 64)
    return n


# ------------------------------------------------------------------ amount scaling

# printed amounts: (1,482.17), -12,34,567.89, 1234.5 — not years, not percentages, not note numbers
_AMOUNT_RE = re.compile(
    r"(?<![\w,/-])(?<!\d\.)(\()?(-)?(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+\.\d+|\d{5,})(\))?(?![\w%/]|\.\d|,\d)"
)


def _parse_amount(token: str) -> tuple[Decimal, int, str] | None:
    """Return (value, decimals, style) for a printed amount; style keeps brackets/grouping."""
    t = token.strip()
    neg = False
    style = ""
    if t.startswith("(") and t.endswith(")"):
        neg, style, t = True, "brackets", t[1:-1]
    if t.startswith("-"):
        neg, style, t = True, style or "minus", t[1:]
    if not re.fullmatch(r"\d[\d,]*(\.\d+)?", t):
        return None
    grouping = "none"
    if "," in t:
        int_part = t.split(".")[0]
        groups = int_part.split(",")
        grouping = "indian" if len(groups) > 2 and all(len(g) == 2 for g in groups[1:-1]) else "western"
    decimals = len(t.split(".")[1]) if "." in t else 0
    try:
        d = Decimal(t.replace(",", ""))
    except InvalidOperation:
        return None
    return (-d if neg else d), decimals, f"{style}|{grouping}"


def _group(digits: str, grouping: str) -> str:
    if grouping == "none" or len(digits) <= 3:
        return digits
    if grouping == "western":
        out: list[str] = []
        while len(digits) > 3:
            out.insert(0, digits[-3:])
            digits = digits[:-3]
        out.insert(0, digits)
        return ",".join(out)
    head, tail = digits[:-3], digits[-3:]
    parts: list[str] = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join([*parts, tail])


def _format_amount(value: Decimal, decimals: int, style: str) -> str:
    neg_style, grouping = style.split("|")
    v = value.quantize(Decimal(1).scaleb(-decimals))
    int_s, _, frac = format(abs(v), "f").partition(".")
    body = _group(int_s, grouping) + (("." + frac) if frac else "")
    if v < 0:
        return f"({body})" if neg_style == "brackets" else f"-{body}"
    return body


class AmountScaler:
    """Multiply amounts by one undisclosed factor; exact and reversible."""

    def __init__(self, factor: Decimal | str | None = None, *, rng: random.Random | None = None) -> None:
        if factor is None:
            r = rng or random.SystemRandom()
            n = 100
            while n == 100:
                n = r.randint(111, 999)
            factor = Decimal(n) / Decimal(100)
        self.factor = Decimal(str(factor))
        if self.factor <= 0:
            raise ValueError("scale factor must be positive")
        self._scaled_to_original: dict[str, str] = {}

    def scale_value(self, d: Decimal) -> Decimal:
        return d * self.factor

    def unscale_value(self, d: Decimal) -> Decimal:
        raw = d / self.factor
        q2 = raw.quantize(Decimal("0.01"))
        return q2 if abs(raw - q2) < Decimal("0.000001") else raw

    def _factor_decimals(self) -> int:
        exp = self.factor.as_tuple().exponent
        return -exp if isinstance(exp, int) and exp < 0 else 0

    def scale_number_text(self, token: str) -> str:
        parsed = _parse_amount(token)
        if parsed is None:
            return token
        value, decimals, style = parsed
        if value == 0:
            return token
        out_decimals = decimals + self._factor_decimals()
        text = _format_amount(value * self.factor, out_decimals, style)
        self._scaled_to_original[text] = token.strip()
        return text

    def scale_text(self, text: str) -> str:
        if not text:
            return text

        def sub(m: re.Match[str]) -> str:
            number = (m.group(2) or "") + m.group(3)
            if m.group(1) and not m.group(4):  # unbalanced bracket: scale only the number
                return "(" + self.scale_number_text(number)
            if m.group(4) and not m.group(1):
                return self.scale_number_text(number) + ")"
            return self.scale_number_text(m.group(0))

        return _AMOUNT_RE.sub(sub, text)

    def unscale_number_text(self, token: str) -> str:
        t = token.strip()
        if t in self._scaled_to_original:
            return self._scaled_to_original[t]
        parsed = _parse_amount(t)
        if parsed is None:
            return token
        value, _decimals, style = parsed
        raw = self.unscale_value(value)
        exp = raw.as_tuple().exponent
        dec = min(6, -exp) if isinstance(exp, int) and exp < 0 else 0
        return _format_amount(raw, dec, style)

    def unscale_text(self, text: str) -> str:
        if not text or not self._scaled_to_original:
            return text
        keys = sorted(self._scaled_to_original, key=len, reverse=True)
        pattern = re.compile(r"(?<![\d,])(?<!\d\.)(?:" + "|".join(re.escape(k) for k in keys) + r")(?![\d])")
        return pattern.sub(lambda m: self._scaled_to_original[m.group(0)], text)

    def export(self) -> dict[str, Any]:
        """LOCAL ONLY: factor and the scaled->original map."""
        return {"factor": str(self.factor), "map": dict(self._scaled_to_original)}

    @classmethod
    def from_export(cls, data: Mapping[str, Any]) -> AmountScaler:
        s = cls(Decimal(str(data["factor"])))
        s._scaled_to_original = dict(data.get("map", {}))
        return s


def redact_many(texts: Iterable[str], fn: Callable[[str], str]) -> list[str]:
    return [fn(t) for t in texts]
