"""Related-party name normalisation and matching (RPT-02, RPT-07, counterparty mapping)."""

from __future__ import annotations

import re
from functools import lru_cache

from rapidfuzz import fuzz

# Indian legal forms are ignored when comparing names (Pvt / Private / Ltd / Limited / LLP, 02 §6.9 RPT-02);
# foreign forms distinguish different group entities that share a brand name ("X Pvt Ltd" vs "X Inc" vs "X FZ-LLC").
_INDIAN_FORMS = re.compile(r"\s+(private\s+limited|pvt\s+ltd|pvt|private|limited|ltd|llp|l\s+l\s+p|company|co)$")
_FOREIGN_FORMS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\s+(pte\s+ltd|pte\s+limited|pte)$"), "pte"),
    (re.compile(r"\s+(pty\s+ltd|pty\s+limited|pty)$"), "pty"),
    (re.compile(r"\s+(fz\s*-?\s*llc|fzllc)$"), "fzllc"),
    (re.compile(r"\s+(fze)$"), "fze"),
    (re.compile(r"\s+(inc|incorporated)$"), "inc"),
    (re.compile(r"\s+(corp|corporation)$"), "corp"),
    (re.compile(r"\s+(gmbh)$"), "gmbh"),
    (re.compile(r"\s+(b\s*v|bv)$"), "bv"),
    (re.compile(r"\s+(n\s*v|nv)$"), "nv"),
    (re.compile(r"\s+(s\s*a|sa)$"), "sa"),
    (re.compile(r"\s+(llc)$"), "llc"),
    (re.compile(r"\s+(plc)$"), "plc"),
]
_BRACKETS = re.compile(r"\((?!huf\))[^)]*\)")  # "(UL)", "(Director)" … but keep "(HUF)"
_COMPANY_HINT = re.compile(
    r"\b(private\s+limited|pvt\.?\s*ltd|limited|ltd\.?|llp|inc\.?|incorporated|corp\.?|corporation|pte\.?|pty\.?|gmbh|b\.\s?v\.?|s\.\s?a\.?|fz[\s-]?llc|fze|llc|plc|kk|ag|oy|ab|s\.?r\.?l\.?)(?=$|[\s,.)])",
    re.I,
)
_REL_LABEL = re.compile(
    r"^\s*((ultimate|intermediate)\s+)?(holding|parent)\s+(company|entity)\s*$|^\s*((fellow|step[\s-]*down|wholly[\s-]*owned)\s+)?subsidiar(y|ies)(\s+compan(y|ies))?\s*$|"
    r"^\s*key\s+(management|managerial)\s+personnel(\s*\(kmp\))?\s*$|^\s*(associates?|joint\s+ventures?|directors?)\s*$",
    re.I,
)
_PERSON_ROLE = re.compile(r"\s*\((director|managing director|whole[\s-]*time director|cfo|ceo|company secretary|kmp|chief[^)]*)\)\s*$", re.I)


@lru_cache(maxsize=8192)
def normalise(name: str) -> str:
    """Case / punctuation / Indian-legal-form insensitive key; foreign forms and HUF are kept distinct."""
    n = name or ""
    n = _PERSON_ROLE.sub("", n)
    n = re.sub(r"\s+[\u2013\u2014-]\s+(director|managing director|cfo|ceo|chief .*|company secretary|kmp)\s*$", "", n, flags=re.I)
    n = n.replace("&", " and ")
    huf = bool(re.search(r"\bhuf\b", n, re.I))
    n = _BRACKETS.sub(" ", n)
    n = n.lower()
    n = re.sub(r"fz-llc", "fzllc", n)
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    form = ""
    prev = None
    while prev != n:
        prev = n
        n = _INDIAN_FORMS.sub("", " " + n).strip()
        if not form:
            for rx, token in _FOREIGN_FORMS:
                if rx.search(" " + n):
                    n = rx.sub("", " " + n).strip()
                    form = token
                    break
    if form:
        n = f"{n} {form}".strip()
    if huf and "huf" not in n:
        n += " huf"
    return n


def looks_like_party(text: str) -> bool:
    """A company-like or person-like party name (not a nature heading, relationship label or caption)."""
    t = (text or "").strip()
    if not t or len(t) > 120 or _REL_LABEL.match(t):
        return False
    if _COMPANY_HINT.search(t):
        return True
    return bool(_PERSON_ROLE.search(t))


def similarity(a: str, b: str) -> float:
    na, nb = normalise(a), normalise(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 100.0
    # token_sort (not token_set): "X" must not equal "X FZ-LLC" just because one is a subset of the other
    return float(max(fuzz.ratio(na, nb), fuzz.token_sort_ratio(na, nb) - 2))


def best_match(name: str, candidates: list[str], *, threshold: float = 90.0) -> tuple[str | None, float]:
    best: tuple[str | None, float] = (None, 0.0)
    for c in candidates:
        s = similarity(name, c)
        if s > best[1]:
            best = (c, s)
    if best[1] < threshold:
        return None, best[1]
    return best


def find_party_in_text(text: str, parties: list[str], *, threshold: float = 88.0) -> str | None:
    """The known party named inside a longer text (account description, caption)."""
    nt = normalise(text)
    best: tuple[str | None, float] = (None, 0.0)
    for p in parties:
        npn = normalise(p)
        if not npn:
            continue
        if npn in nt:
            score = 100.0 + len(npn) / 100  # prefer the longest exact containment
        else:
            score = float(fuzz.partial_ratio(npn, nt)) if len(npn) >= 6 else 0.0
        if score > best[1]:
            best = (p, score)
    return best[0] if best[1] >= threshold else None


def extract_party_from_account(text: str) -> str | None:
    """Counterparty named after a dash in an account description ("Intercompany Payable - X Ltd")."""
    parts = re.split(r"\s+[-–—:]\s+", text or "")
    for part in reversed(parts[1:]):
        if looks_like_party(part):
            return part.strip()
    return None
