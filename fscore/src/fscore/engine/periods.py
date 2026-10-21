"""Period labels in headings and column headers (MULTI-01, TXT-02, GC-03).

Parses "As at March 31, 2026", "For the year ended 31 March 2025",
"Three months ended", "Qtr Mar 26", "FY 25-26", "31.03.2026", "Mar-26" … into
an end date plus a duration, and maps them onto the engagement's scope keys.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

from fscore.config import EngagementConfig, PeriodSpec

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9

_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_YEAR = r"((?:19|20)\d{2}|'?\d{2})"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"

_DATE_PATTERNS = [
    re.compile(rf"\b{_MON}\.?\s+{_DAY},?\s*{_YEAR}\b", re.I),  # March 31, 2026
    re.compile(rf"\b{_DAY}\s*(?:of\s+)?{_MON}\.?,?\s*{_YEAR}\b", re.I),  # 31 March 2026 / 31st March, 2026
    re.compile(rf"\b{_DAY}[-/ ]{_MON}[-/ ]{_YEAR}\b", re.I),  # 31-Mar-2026
    re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-]((?:19|20)\d{2})\b"),  # 31.03.2026 (Indian dd.mm.yyyy)
]
_MON_YEAR = re.compile(rf"\b{_MON}[-\s']*{_YEAR}\b", re.I)  # Mar-26 / Mar 26 / March 2026
_FY = re.compile(r"\bFY\s*'?((?:20)?\d{2})\s*[-–/]\s*'?((?:20)?\d{2})\b|\bFY\s*'?((?:20)?\d{2})\b", re.I)
_YEAR_RANGE = re.compile(r"\b((?:19|20)\d{2})\s*[-–/]\s*(\d{2}|(?:19|20)\d{2})\b")
_DURATION = [
    (re.compile(r"\b(three|3)[\s-]*months?\b|\bquarter\b|\bqtr\b|\bq[1-4]\b", re.I), 3),
    (re.compile(r"\b(six|6)[\s-]*months?\b|\bhalf[\s-]*year\b|\bh[12]\b", re.I), 6),
    (re.compile(r"\b(nine|9)[\s-]*months?\b", re.I), 9),
    (re.compile(r"\byear\s+ended\b|\bfor\s+the\s+year\b|\bfy\b|\btwelve months\b|\bannual\b", re.I), 12),
]
_POINT = re.compile(r"\bas\s+at\b|\bas\s+on\b|\bbalance\s+as\s+at\b|\bclosing\b|\bopening\b", re.I)


@dataclass(frozen=True)
class PeriodLabel:
    end: date | None
    months: int | None  # duration; None for a point in time or when unstated
    kind: str  # point | duration | unknown
    text: str
    malformed: bool = False  # e.g. "31st June"

    def describe(self) -> str:
        if self.end is None:
            return self.text
        if self.months:
            return f"{self.months} months ended {self.end:%d-%b-%Y}"
        return f"as at {self.end:%d-%b-%Y}"


def _year(y: str) -> int:
    y = y.strip("'")
    v = int(y)
    return v + 2000 if v < 100 else v


def _mk(y: int, m: int, d: int) -> tuple[date | None, bool]:
    try:
        return date(y, m, d), False
    except ValueError:
        # malformed calendar date such as 31st June → keep the month end, flag it
        last = calendar.monthrange(y, m)[1]
        return date(y, m, last), True


def find_dates(text: str) -> list[tuple[date, bool, str]]:
    """All explicit calendar dates in a text: (date, malformed, matched text)."""
    out: list[tuple[date, bool, str]] = []
    seen: set[tuple[int, int]] = set()
    for i, rx in enumerate(_DATE_PATTERNS):
        for m in rx.finditer(text):
            if any(a <= m.start() < b for a, b in seen):
                continue
            g = m.groups()
            try:
                if i == 0:
                    mon, day, yr = MONTHS[g[0].lower()[:3] if g[0].lower()[:3] in MONTHS else g[0].lower()], int(g[1]), _year(g[2])
                elif i in (1, 2):
                    day, mon, yr = int(g[0]), MONTHS[g[1].lower()[:3] if g[1].lower()[:3] in MONTHS else g[1].lower()], _year(g[2])
                else:
                    day, mon, yr = int(g[0]), int(g[1]), int(g[2])
                    if mon > 12:
                        continue
            except (KeyError, ValueError):
                continue
            if not 1 <= day <= 31:
                continue
            d, bad = _mk(yr, mon, day)
            if d is not None:
                out.append((d, bad, m.group(0)))
                seen.add((m.start(), m.end()))
    return out


def parse_period(text: str, *, fy_end_month: int = 3) -> PeriodLabel | None:
    """Parse a heading / column header into a period label."""
    if not text or not text.strip():
        return None
    t = " ".join(str(text).split())
    months = None
    for rx, n in _DURATION:
        if rx.search(t):
            months = n
            break
    point = bool(_POINT.search(t))
    dates = find_dates(t)
    if dates:
        d, bad, _ = dates[-1]
        kind = "duration" if months and not point else ("point" if point or not months else "duration")
        return PeriodLabel(d, None if kind == "point" else months, kind, t, bad)
    m = _FY.search(t)
    if m:
        if m.group(2):
            end_year = _year(m.group(2))
        else:
            end_year = _year(m.group(3))
        last = calendar.monthrange(end_year, fy_end_month)[1]
        return PeriodLabel(date(end_year, fy_end_month, last), months if months and months != 12 else 12, "duration", t)
    m = _YEAR_RANGE.search(t)
    if m and (int(m.group(2)[-2:]) == (int(m.group(1)) + 1) % 100):
        end_year = int(m.group(1)) + 1
        last = calendar.monthrange(end_year, fy_end_month)[1]
        return PeriodLabel(date(end_year, fy_end_month, last), months or 12, "duration", t)
    m = _MON_YEAR.search(t)
    if m:
        mon = MONTHS.get(m.group(1).lower()[:3], MONTHS.get(m.group(1).lower(), 0))
        yr = _year(m.group(2))
        if mon:
            d = date(yr, mon, calendar.monthrange(yr, mon)[1])
            kind = "duration" if months else ("point" if point else "unknown")
            return PeriodLabel(d, months, kind, t)
    return None


def scope_key(label: PeriodLabel, config: EngagementConfig) -> str | None:
    """Map a parsed label onto a configured scope key (CY, PY, Q4_CY, …) or None if out of scope."""
    if label.end is None:
        return None
    candidates = [p for p in config.scope() if p.end == label.end]
    if not candidates:
        return None
    if label.months is None:
        # point in time or unstated duration: prefer the year (12-month) key
        for p in candidates:
            if p.months == 12:
                return p.key
        return candidates[0].key
    for p in candidates:
        if p.months == label.months:
            return p.key
    return None


def in_scope(label: PeriodLabel, config: EngagementConfig) -> bool:
    if label.end is None:
        return True
    ends = {p.end for p in config.scope()}
    if label.end not in ends:
        return False
    if label.months is None:
        return True
    return any(p.end == label.end and p.months == label.months for p in config.scope()) or (label.months == 12 and any(p.end == label.end for p in config.scope()))


def scope_dates(config: EngagementConfig) -> list[PeriodSpec]:
    return config.scope()
