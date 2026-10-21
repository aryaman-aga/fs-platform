"""Deterministic extraction of figures, units, periods and claims from narrative text (02 §6.8 step 2).

``extract_mentions(text, config)`` returns one :class:`Mention` per quoted amount
or percentage, normalised to the statement unit with its sign, the period it
refers to (a scope key such as ``CY``/``PY``/``Q4_CY``) and the metric it most
likely describes (``pat``, ``net_worth``, ``nca_ncl_gap`` …).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from fscore.config import UNIT_DIVISORS, EngagementConfig
from fscore.engine.periods import find_dates, parse_period, scope_key
from fscore.narrative.numwords import find_number_words
from fscore.narrative.terms import PhraseHit, TermDictionary, default_terms, find_phrases

# --------------------------------------------------------------------------- units

UNIT_WORDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\s*(lakhs?|lacs?|lakh|lacks|lkhs?)\b", re.I), "lakhs"),
    (re.compile(r"^\s*(crores?|crs?\.(?=\s*\w)|crs?\b|cr\b)", re.I), "crores"),
    (re.compile(r"^\s*(millions?|mn\.?(?=\s*\w)|mn\b|mio\b)", re.I), "millions"),
    (re.compile(r"^\s*(thousands?|'000s?)\b", re.I), "thousands"),
    (re.compile(r"^\s*(billions?|bn\b)", re.I), "billions"),
]
_DIVISORS = dict(UNIT_DIVISORS)
_DIVISORS["billions"] = 1_000_000_000

_CURRENCY = r"(?:rs\.?|inr|₹|rupees|usd|us\$|\$|aud|a\$|gbp|£|aed|sgd|s\$|eur|€)"
# a number: Indian or western grouping, optional decimals, optional brackets / minus sign
_NUM = r"\(?-?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?\)?|\(?-?\d+(?:\.\d+)?\)?|\(?-?\d{1,3},\d{2}\)?"
_AMOUNT_RE = re.compile(
    rf"(?P<cur>{_CURRENCY})?\s*(?P<num>{_NUM})(?P<pct>\s*%)?", re.I
)
_PCT_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*%?\s*(?:to|-|–)\s*(\d+(?:\.\d+)?)\s*%", re.I)
_YEAR_LIKE = re.compile(r"^(19|20)\d{2}$")

# sentence boundaries: '.', ';', ':' or newline followed by space/capital, ignoring abbreviations and decimals
_ABBREV = re.compile(r"\b(rs|no|nos|pvt|ltd|co|inc|vs|viz|i\.e|e\.g|etc|sr|mr|mrs|ms|dr|st|para|paras|sec|cl|fig|approx|ind|as|u/s)\.$", re.I)

# --------------------------------------------------------------------------- periods

_PERIOD_PHRASE = re.compile(
    r"(?P<lead>(?:for\s+the\s+|during\s+the\s+|in\s+the\s+)?(?:(?:three|six|nine|twelve|3|6|9|12)[\s-]*months?(?:\s+period)?|quarter|year|half[\s-]*year|period|financial\s+year)\s+(?:ended|ending|ended\s+on)\s+|as\s+(?:at|on)\s+|balance\s+as\s+at\s+|up\s*to\s+)?"
    r"(?P<date>(?:\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?[A-Za-z]{3,9}\.?,?\s*\d{4})|(?:[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4})|(?:\d{1,2}[./-]\d{1,2}[./-]\d{4})|(?:\d{1,2}-[A-Za-z]{3}-\d{2,4}))",
    re.I,
)
_FY_PHRASE = re.compile(r"\b(?:FY|financial\s+year|F\.Y\.)\s*'?((?:20)?\d{2})\s*[-–/]\s*'?((?:20)?\d{2})\b", re.I)
_REL_PY = re.compile(r"\b(previous|preceding|prior|last|comparative)\s+(financial\s+)?(year|period)\b|\bpy\b|\bcorresponding\s+(quarter|period)\b", re.I)
_REL_CY = re.compile(r"\b(current|this)\s+(financial\s+)?(year|period)\b|\bduring\s+the\s+(year|period)\b|\bfor\s+the\s+year\b", re.I)
_QUARTER_WORD = re.compile(r"\b(quarter|three[\s-]*months?|3\s*months?|qtr|q4)\b", re.I)
_YEAR_WORD = re.compile(r"\b(year|twelve\s+months|12\s+months|annual)\b", re.I)


@dataclass
class PeriodRef:
    start: int
    end: int
    text: str
    end_date: date | None
    months: int | None  # 3/6/9/12, None for a point in time
    relative: str | None = None  # "PY" / "CY" for relative phrases
    malformed: bool = False
    key: str | None = None  # resolved scope key (None when out of scope)


@dataclass
class Mention:
    kind: str  # amount | percent
    start: int
    end: int
    text: str  # quoted fragment ("₹ 4.71 million")
    number_text: str  # as written ("4.71", "23,58", "twelve lakh")
    raw: Decimal  # absolute number as written
    decimals: int
    unit: str | None  # unit word used (lakhs, crores, millions, thousands, rupees) or None
    value: Decimal | None  # signed, in the statement unit (None for percentages)
    negative: bool = False
    words: bool = False
    malformed: bool = False  # e.g. "23,58" (decimal comma)
    metric: str | None = None
    metric_phrase: str | None = None
    alt_metrics: list[str] = field(default_factory=list)
    period: str | None = None
    period_text: str | None = None
    period_end: date | None = None
    period_months: int | None = None
    polarity: str | None = None  # "profit" | "loss" for result metrics
    sentence_index: int = 0
    sentence: str = ""
    sentence_start: int = 0  # offset of the sentence in the source text


@dataclass
class Sentence:
    index: int
    start: int
    end: int
    text: str


def split_sentences(text: str) -> list[Sentence]:
    """Split narrative into sentences without breaking decimals or common abbreviations."""
    out: list[Sentence] = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        boundary = False
        if ch in "\n\r":
            boundary = True
        elif ch in ".;" and i + 1 <= n:
            nxt = text[i + 1] if i + 1 < n else " "
            prev = text[:i + 1]
            if (ch == "." and (i > 0 and text[i - 1].isdigit() and nxt.isdigit())) or (ch == "." and _ABBREV.search(prev[-8:])):
                boundary = False
            elif nxt in " \t\n\r" or i + 1 == n:
                boundary = True
        if boundary:
            seg = text[start:i + 1]
            if seg.strip():
                lead = len(seg) - len(seg.lstrip())
                out.append(Sentence(len(out), start + lead, i + 1, seg.strip()))
            start = i + 1
        i += 1
    if start < n and text[start:].strip():
        seg = text[start:]
        lead = len(seg) - len(seg.lstrip())
        out.append(Sentence(len(out), start + lead, n, seg.strip()))
    return out


def _decimals(num: str) -> int:
    core = num.strip("()-")
    if re.fullmatch(r"\d{1,3},\d{2}", core):
        return 2
    return len(core.split(".")[1]) if "." in core else 0


def _to_decimal(num: str) -> tuple[Decimal | None, bool, bool]:
    """(absolute value, negative, malformed) for a printed number."""
    s = num.strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if s.startswith("-"):
        neg = True
        s = s[1:]
    malformed = False
    if re.fullmatch(r"\d{1,3},\d{2}", s):
        # "23,58": a decimal comma typo (not a valid Indian or western grouping)
        s = s.replace(",", ".")
        malformed = True
    else:
        s = s.replace(",", "")
    try:
        return Decimal(s), neg, malformed
    except InvalidOperation:
        return None, neg, malformed


def _unit_after(text: str, pos: int) -> tuple[str | None, int]:
    tail = text[pos:pos + 20]
    for rx, unit in UNIT_WORDS:
        m = rx.match(tail)
        if m:
            return unit, pos + m.end()
    return None, pos


def _currency_before(text: str, pos: int) -> bool:
    head = text[max(0, pos - 12):pos].lower()
    return bool(re.search(rf"{_CURRENCY}\s*$", head, re.I))


def period_refs(sentence: str, config: EngagementConfig) -> list[PeriodRef]:
    """Explicit dates / FY labels / relative phrases in a sentence, resolved to scope keys."""
    refs: list[PeriodRef] = []
    taken: list[tuple[int, int]] = []
    for m in _PERIOD_PHRASE.finditer(sentence):
        lead = (m.group("lead") or "").lower()
        frag = m.group(0)
        lab = parse_period(frag)
        dates = find_dates(m.group("date"))
        if lab is None or not dates:
            continue
        d, bad, _ = dates[-1]
        months: int | None = lab.months
        if lead.startswith(("as at", "as on", "balance as at", "up")):
            months = None
        elif _QUARTER_WORD.search(lead):
            months = 3
        elif re.search(r"\b(six|6)\b|half", lead):
            months = 6
        elif re.search(r"\b(nine|9)\b", lead):
            months = 9
        elif _YEAR_WORD.search(lead):
            months = 12
        pr = PeriodRef(m.start(), m.end(), frag, d, months, None, bad)
        pr.key = _resolve(pr, config)
        refs.append(pr)
        taken.append((m.start(), m.end()))
    # "quarter and year ended <date>" / "three months and year ended <date>": two periods, quarter first
    for m in re.finditer(r"\b(quarter|three[\s-]*months?|3\s*months?)\s+and\s+(the\s+)?(year|twelve\s+months|financial\s+year)\s+ended\b", sentence, re.I):
        year_ref = next((r for r in refs if r.start >= m.start() and r.end_date is not None and r.start <= m.end() + 5), None)
        if year_ref is None:
            continue
        year_ref.months = 12
        year_ref.key = _resolve(year_ref, config)
        q = PeriodRef(year_ref.start - 1, year_ref.start, m.group(1), year_ref.end_date, 3, None, year_ref.malformed)
        q.key = _resolve(q, config)
        refs.append(q)
    for m in _FY_PHRASE.finditer(sentence):
        if any(a <= m.start() < b for a, b in taken):
            continue
        end_year = int(m.group(2)) + (2000 if len(m.group(2)) == 2 else 0)
        pr = PeriodRef(m.start(), m.end(), m.group(0), date(end_year, config.year_end.month, config.year_end.day), 12)
        pr.key = _resolve(pr, config)
        refs.append(pr)
        taken.append((m.start(), m.end()))
    for rx, rel in ((_REL_PY, "PY"), (_REL_CY, "CY")):
        for m in rx.finditer(sentence):
            if any(a <= m.start() < b for a, b in taken):
                continue
            quarter = bool(_QUARTER_WORD.search(sentence[max(0, m.start() - 30):m.end() + 30])) and "year" not in m.group(0).lower()
            months = 3 if quarter or "quarter" in m.group(0).lower() else 12
            pr = PeriodRef(m.start(), m.end(), m.group(0), None, months, rel)
            pr.key = _relative_key(rel, months, config)
            refs.append(pr)
            taken.append((m.start(), m.end()))
    return sorted(refs, key=lambda r: r.start)


def _scope_has(config: EngagementConfig, key: str) -> bool:
    return any(p.key == key for p in config.scope())


def _relative_key(rel: str, months: int, config: EngagementConfig) -> str | None:
    if months == 3 and _scope_has(config, f"Q4_{rel}"):
        return f"Q4_{rel}"
    return rel if _scope_has(config, rel) else None


def _resolve(pr: PeriodRef, config: EngagementConfig) -> str | None:
    if pr.end_date is None:
        return None
    from fscore.engine.periods import PeriodLabel

    lab = PeriodLabel(pr.end_date, pr.months, "point" if pr.months is None else "duration", pr.text, pr.malformed)
    return scope_key(lab, config)


def _assign_period(m_start: int, m_end: int, sentence: str, refs: list[PeriodRef]) -> PeriodRef | None:
    """Period for a mention: parenthetical comparatives first, then the nearest phrase in the clause."""
    if not refs:
        return None
    # inside "( … )" that starts with a period reference: "(previous year: 61.40)", "(March 31, 2025: 37.15)"
    open_br = sentence.rfind("(", 0, m_start)
    close_br = sentence.find(")", m_end) if open_br >= 0 else -1
    if open_br >= 0 and close_br >= 0 and sentence.rfind(")", open_br, m_start) < 0:
        inside = [r for r in refs if open_br < r.start < m_start]
        if inside:
            return inside[-1]
    # nearest reference outside other parentheses, measured by character distance
    def outside_brackets(r: PeriodRef) -> bool:
        ob = sentence.rfind("(", 0, r.start)
        return ob < 0 or sentence.rfind(")", ob, r.start) >= 0
    cands = [r for r in refs if outside_brackets(r)]
    if not cands:
        return None  # only bracketed comparatives in the sentence: they describe other figures

    def dist(r: PeriodRef) -> int:
        if r.start >= m_end:
            return r.start - m_end
        return max(0, m_start - r.end) + 5  # slight preference for a period stated after the figure
    return min(cands, key=dist)


def _classify(m_start: int, sentence: str, hits: list[PhraseHit], terms: TermDictionary) -> tuple[PhraseHit | None, list[str]]:
    before = [h for h in hits if h.end <= m_start + 1 and m_start - h.end <= 140]
    after = [h for h in hits if h.start >= m_start and h.start - m_start <= 40]
    best: PhraseHit | None = None
    if before:
        # nearest preceding phrase; among equally near ones prefer the more specific (lower order)
        nearest_end = max(h.end for h in before)
        group = [h for h in before if nearest_end - h.end <= 3]
        best = min(group, key=lambda h: h.order)
        # a specific metric phrase further back beats a generic "profit"/"loss" word right before the number
        if best.metric == "pat" and best.text.lower() in ("loss", "losses", "profit", "profits"):
            specific = [h for h in before if h.metric != "pat" and m_start - h.end <= 60 and h.order < best.order]
            if specific:
                best = max(specific, key=lambda h: h.end)
    elif after:
        best = min(after, key=lambda h: (h.start, h.order))
    alts: list[str] = []
    if best is not None:
        mt = terms.by_metric(best.metric)
        if mt is not None:
            alts = list(mt.try_also)
    return best, alts


def _polarity(sentence: str, m_start: int, metric: str | None) -> str | None:
    window = sentence[max(0, m_start - 80):m_start].lower()
    loss_pos = max(window.rfind("loss"), window.rfind("deficit"), window.rfind("negative"), window.rfind("eroded"))
    prof_pos = max(window.rfind("profit"), window.rfind("surplus"), window.rfind("positive"), window.rfind("earned"))
    if loss_pos < 0 and prof_pos < 0:
        return None
    return "loss" if loss_pos > prof_pos else "profit"


def extract_mentions(text: str, config: EngagementConfig, *, terms: TermDictionary | None = None) -> list[Mention]:
    td = terms or default_terms()
    stmt_div = _DIVISORS.get(config.units, 100_000)
    out: list[Mention] = []
    for sent in split_sentences(text):
        s = sent.text
        refs = period_refs(s, config)
        hits = find_phrases(s, td)
        spans_taken: list[tuple[int, int]] = []
        # dates / FY labels / years must not be read as amounts
        for r in refs:
            spans_taken.append((r.start, r.end))
        for m in re.finditer(r"\b(?:19|20)\d{2}\s*[-–/]\s*\d{2,4}\b|\b(?:note|notes|para|paragraph|section|schedule|rule|clause|article|ind\s*as|ias|ifrs|sa)\s+\d+[a-z]?(?:\.\d+)?|\(\s*[a-z]\s*\)|\(\s*[ivx]+\s*\)", s, re.I):
            spans_taken.append((m.start(), m.end()))
        # item numbering at the start of a paragraph ("2.5 Revenue recognition –")
        lead_num = re.match(r"^\s*\(?\d{1,2}(?:\.\d{1,2}){0,2}\)?\s+(?=[A-Z(])", s)
        if lead_num:
            spans_taken.append((lead_num.start(), lead_num.end()))
        # face / par / nominal values ("face value of ₹10 each", "₹10 per share")
        for m in re.finditer(r"(?:face|par|nominal)\s+value\s+(?:of\s+)?(?:rs\.?|inr|₹)?\s*\d+(?:\.\d+)?|(?:rs\.?|inr|₹)\s*\d+(?:\.\d+)?\s+(?:each|per\s+(?:equity\s+)?share)", s, re.I):
            spans_taken.append((m.start(), m.end()))
        # percentage ranges "8.5% to 11.0%"
        for m in _PCT_RANGE.finditer(s):
            for g in (1, 2):
                a, b = m.start(g), m.end(g)
                val = Decimal(m.group(g))
                out.append(Mention("percent", sent.start + a, sent.start + b, m.group(g) + "%", m.group(g), val,
                                   _decimals(m.group(g)), "percent", None, sentence_index=sent.index, sentence=s,
                                   sentence_start=sent.start))
            spans_taken.append((m.start(), m.end()))
        # numbers in words
        for nw in find_number_words(s):
            if any(a <= nw.start < b for a, b in spans_taken):
                continue
            unit, end = _unit_after(s, nw.end)
            scale_unit = None
            if nw.scale_word in ("lakh",):
                scale_unit = "lakhs"
            elif nw.scale_word in ("crore",):
                scale_unit = "crores"
            elif nw.scale_word in ("million",):
                scale_unit = "millions"
            # words already include their scale: the value is in rupees (or the currency's base unit)
            raw = nw.value
            value = raw / Decimal(stmt_div)
            mention = Mention("amount", sent.start + nw.start, sent.start + end, s[nw.start:end], nw.text, raw, 0,
                              unit or scale_unit or "rupees", value, words=True, sentence_index=sent.index, sentence=s,
                              sentence_start=sent.start)
            out.append(mention)
            spans_taken.append((nw.start, end))
        for m in _AMOUNT_RE.finditer(s):
            num = m.group("num")
            a, b = m.start("num"), m.end("num")
            if any(x <= a < y for x, y in spans_taken):
                continue
            core = num.strip("()-")
            if _YEAR_LIKE.match(core):
                continue
            parsed, neg, malformed = _to_decimal(num)
            if parsed is None:
                continue
            raw = parsed
            pct = bool(m.group("pct"))
            cur = bool(m.group("cur")) or _currency_before(s, a)
            unit, end = _unit_after(s, b)
            # counts, not amounts: "10,500,000 equity shares", "45 days", "3 years"
            if not cur and unit is None and re.match(r"\s*(\(.{0,30}\)\s*)?(fully\s+paid\s+)?(equity\s+|preference\s+)?(shares?|units?|employees?|days?|months?|years?|times|nos?\.?|numbers?)\b", s[b:b + 50], re.I):
                spans_taken.append((m.start(), m.end()))
                continue
            dec = _decimals(num)
            # bare small integers without currency/unit are counts, item numbers or days: skip
            if not pct and not cur and unit is None and dec == 0 and not malformed and "," not in core:
                continue
            if pct:
                out.append(Mention("percent", sent.start + m.start(), sent.start + m.end(), s[m.start():m.end()].strip(),
                                   num, raw, dec, "percent", None, negative=neg, sentence_index=sent.index, sentence=s,
                                   sentence_start=sent.start))
                spans_taken.append((m.start(), m.end()))
                continue
            if unit is None and cur and dec == 0 and "," in core and raw >= 100_000:
                unit = "rupees"
            div = _DIVISORS.get(unit, stmt_div) if unit not in (None, "rupees") else (1 if unit == "rupees" else stmt_div)
            value = raw * Decimal(div) / Decimal(stmt_div)
            start = m.start() if m.group("cur") else a
            mention = Mention("amount", sent.start + start, sent.start + end, s[start:end].strip(), num, raw, dec, unit,
                              value, negative=neg, malformed=malformed, sentence_index=sent.index, sentence=s,
                              sentence_start=sent.start)
            out.append(mention)
            spans_taken.append((start, end))
        # classify + period + sign for this sentence's mentions
        sent_mentions = [x for x in out if x.sentence_index == sent.index and x.sentence is s]
        amounts = [x for x in sent_mentions if x.kind == "amount"]
        respectively = bool(re.search(r"\brespectively\b", s, re.I))
        dated = [r for r in refs if r.key is not None or r.end_date is not None]
        prev_amount: Mention | None = None
        for idx, x in enumerate(sorted(sent_mentions, key=lambda z: z.start)):
            rel_start = x.start - sent.start
            rel_end = x.end - sent.start
            hit, alts = _classify(rel_start, s, hits, td)
            if hit is not None:
                x.metric, x.metric_phrase, x.alt_metrics = hit.metric, hit.text, alts
            # a comparative in brackets "(as at March 31, 2025: ₹ X)" / "(previous year: ₹ X)" describes the same metric
            open_br = s.rfind("(", 0, rel_start)
            if x.kind == "amount" and prev_amount is not None and open_br >= 0 and s.rfind(")", open_br, rel_start) < 0 \
                    and prev_amount.start - sent.start < open_br and re.match(r"\(\s*(previous|preceding|prior|py\b|as\s+at|as\s+on|year\s+ended|march|april|june|september|december|\d)", s[open_br:open_br + 25], re.I):
                x.metric, x.metric_phrase, x.alt_metrics = prev_amount.metric, prev_amount.metric_phrase, list(prev_amount.alt_metrics)
            if x.kind == "amount":
                prev_amount = x
            if respectively and x.kind == "amount" and len(amounts) >= 2 and len(dated) >= len(amounts):
                k = amounts.index(x) if x in amounts else idx
                pr: PeriodRef | None = dated[k] if k < len(dated) else None
            else:
                pr = _assign_period(rel_start, rel_end, s, refs)
            if pr is not None:
                x.period, x.period_text, x.period_end, x.period_months = pr.key, pr.text, pr.end_date, pr.months
            if x.kind == "amount" and x.value is not None:
                pol = _polarity(s, rel_start, x.metric)
                x.polarity = pol
                negative = x.negative
                mt = td.by_metric(x.metric) if x.metric else None
                if mt is not None and mt.negative_words:
                    window = s[max(0, rel_start - 80):rel_start]
                    if (any(rx.search(window) for rx in mt.negative_words) and pol == "loss") or (
                        x.metric in ("retained_earnings", "working_capital", "net_worth") and any(rx.search(window) for rx in mt.negative_words)
                    ):
                        negative = True
                if x.metric in ("pat", "pbt", "tci") and pol == "loss":
                    negative = True
                if negative:
                    x.value = -abs(x.value)
    return out


def claims(text: str) -> list[tuple[str, int, int, str]]:
    """Qualitative claims a sentence makes: (claim, start, end, matched text)."""
    rules = [
        ("positive_net_worth", r"\bpositive\s+net\s*-?\s*worth\b"),
        ("net_worth_eroded", r"\bnet\s*-?\s*worth\b[^.]{0,40}\b(fully\s+|completely\s+|substantially\s+)?eroded\b|\berosion\s+of\s+(its\s+)?net\s*-?\s*worth\b"),
        ("negative_net_worth", r"\bnegative\s+net\s*-?\s*worth\b"),
        ("cl_exceed_ca", r"\bcurrent\s+liabilities(?:\s+[\w']+){0,4}?\s+(have\s+|had\s+)?exceed(ed|s)?\s+(its\s+|the\s+|their\s+)?current\s+assets\b|\bnet\s+current\s+liabilities\b|\bnegative\s+working\s+capital\b"),
        ("ca_exceed_cl", r"\bcurrent\s+assets(?:\s+[\w']+){0,4}?\s+(have\s+|had\s+)?exceed(ed|s)?\s+(its\s+|the\s+|their\s+)?current\s+liabilities\b|\bpositive\s+working\s+capital\b"),
        ("earned_profit", r"\b(earned|made|reported|recorded|posted|achieved)\s+(a\s+)?(net\s+)?profits?\b|\bprofit\s+(after\s+tax\s+)?of\b"),
        ("incurred_loss", r"\b(incurred|suffered|reported|recorded|posted|made)\s+(a\s+)?(net\s+|cash\s+)?loss(es)?\b|\bloss(es)?\s+(after\s+tax\s+)?of\b|\bin\s+loss(es)?\b"),
        ("profits_last_n_years", r"\bprofits?\s+(in|for|during)\s+(each\s+of\s+)?the\s+(last|preceding|past)\s+(\w+)\s+(financial\s+)?years\b"),
        ("increase", r"\b(increased|increase|grew|growth|higher)\b"),
        ("decrease", r"\b(decreased|decrease|declined|decline|reduced|reduction|lower)\b"),
        ("material_uncertainty", r"\bmaterial\s+uncertainty\s+(exists|existed|that\s+may\s+cast)\b"),
        ("no_material_uncertainty", r"\bno\s+material\s+uncertainty\b|\bmaterial\s+uncertainty\s+does\s+not\s+exist\b"),
        ("no_default", r"\bno\s+default\s+in\s+(the\s+)?repayment\b"),
    ]
    out = []
    for name, pat in rules:
        for m in re.finditer(pat, text, re.I):
            out.append((name, m.start(), m.end(), m.group(0)))
    return sorted(out, key=lambda t: t[1])
