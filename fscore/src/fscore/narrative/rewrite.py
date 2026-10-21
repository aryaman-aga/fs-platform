"""Corrected narrative wording (GC-06): same sentence structure, typed text, 2 decimals.

The firm's policy (03 §3 item 3): narrative notes are corrected as **typed text,
never formulas**, in the statement unit and precision — never rounded to one
decimal to "avoid" differences.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class Replacement:
    start: int  # offsets in the original cell text
    end: int
    new: str
    reason: str


def _group_indian(digits: str) -> str:
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts: list[str] = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join([*parts, tail])


def format_amount(value: Decimal, *, decimals: int = 2, style_of: str | None = None, brackets: bool = False) -> str:
    """Format an absolute amount in the style of the original token (Indian or western grouping)."""
    q = Decimal(1).scaleb(-decimals)
    v = abs(value).quantize(q, rounding=ROUND_HALF_UP)
    int_part, _, frac = format(v, "f").partition(".")
    indian = bool(style_of and re.search(r"\d,\d{2},\d{3}", style_of))
    grouped = _group_indian(int_part) if indian else f"{int(int_part):,}"
    out = grouped + ("." + frac if decimals else "")
    if brackets:
        out = f"({out})"
    return out


def apply_replacements(text: str, reps: list[Replacement]) -> str:
    """Apply non-overlapping replacements (later ones win when they overlap)."""
    chosen: list[Replacement] = []
    for r in sorted(reps, key=lambda r: (r.start, -r.end)):
        if chosen and r.start < chosen[-1].end:
            continue
        chosen.append(r)
    out = []
    pos = 0
    for r in chosen:
        out.append(text[pos:r.start])
        out.append(r.new)
        pos = r.end
    out.append(text[pos:])
    return "".join(out)


_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def format_date_like(original: str, d: date) -> str:
    """Write ``d`` in the same style as the original date text."""
    o = original.strip()
    if re.fullmatch(r"\d{1,2}[./-]\d{1,2}[./-]\d{4}", o):
        sep = re.search(r"[./-]", o).group(0)  # type: ignore[union-attr]
        return f"{d.day:02d}{sep}{d.month:02d}{sep}{d.year}"
    if re.match(r"\d", o):  # 31 March 2026 / 31st March, 2026 / 31-Mar-2026
        if re.fullmatch(r"\d{1,2}-[A-Za-z]{3}-\d{2,4}", o):
            yr = str(d.year) if re.search(r"\d{4}$", o) else f"{d.year % 100:02d}"
            return f"{d.day:02d}-{_MONTHS[d.month - 1][:3]}-{yr}"
        suffix = re.match(r"\d{1,2}(st|nd|rd|th)", o)
        day = f"{d.day}{_ordinal(d.day)}" if suffix else str(d.day)
        comma = "," if "," in o else ""
        month = _MONTHS[d.month - 1] if re.search(r"[A-Za-z]{4,}", o) else _MONTHS[d.month - 1][:3]
        return f"{day} {month}{comma} {d.year}"
    month = _MONTHS[d.month - 1] if re.match(r"[A-Za-z]{4,}", o) else _MONTHS[d.month - 1][:3]
    if not re.search(r"\d{1,2}(st|nd|rd|th)?,?\s+\d{4}", o):  # "June 2026"
        return f"{month} {d.year}"
    return f"{month} {d.day}, {d.year}"


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def swap_polarity(text: str, start: int, end: int, *, to: str) -> list[Replacement]:
    """Swap 'profit' ↔ 'loss' wording right before a figure (within its clause)."""
    window_start = max(0, start - 60)
    window = text[window_start:start]
    pats = [(r"\bprofits?\b", "profit"), (r"\blosses\b", "losses"), (r"\bloss\b", "loss")]
    best: tuple[int, int, str] | None = None
    for pat, word in pats:
        for m in re.finditer(pat, window, re.I):
            if best is None or m.start() > best[0]:
                best = (m.start(), m.end(), word)
    if best is None:
        return []
    s, e, word = best
    current = window[s:e]
    if to == "loss" and word.startswith("profit"):
        new = "loss" if not current.lower().endswith("s") else "losses"
    elif to == "profit" and word.startswith("loss"):
        new = "profit"
    else:
        return []
    if current[0].isupper():
        new = new.capitalize()
    reps = [Replacement(window_start + s, window_start + e, new, f"wording follows the sign ({to})")]
    # "earned a loss" / "incurred a profit": fix the verb too
    verb_window = text[max(0, window_start + s - 25):window_start + s]
    vm = re.search(r"\b(earned|incurred)\s+(a\s+)?(net\s+)?$", verb_window, re.I)
    if vm:
        vstart = max(0, window_start + s - 25) + vm.start(1)
        verb = "incurred" if to == "loss" else "earned"
        if vm.group(1)[0].isupper():
            verb = verb.capitalize()
        reps.append(Replacement(vstart, vstart + len(vm.group(1)), verb, "verb follows the sign"))
    return reps
