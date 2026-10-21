"""English number words, including the Indian scales lakh and crore (02 §6.8).

``find_number_words("rupees twelve lakh thirty-four thousand only")`` →
``[NumberWords(start, end, Decimal(1234000), text)]``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

_UNITS = {
    "zero": 0, "nil": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {
    "thousand": 1_000,
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000, "lacks": 100_000,
    "million": 1_000_000, "millions": 1_000_000,
    "crore": 10_000_000, "crores": 10_000_000,
    "billion": 1_000_000_000, "billions": 1_000_000_000,
}
_ALL = set(_UNITS) | set(_TENS) | set(_SCALES) | {"hundred", "hundreds", "and", "point"}

_WORD_RE = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")


@dataclass(frozen=True)
class NumberWords:
    start: int
    end: int
    value: Decimal
    text: str
    scale_word: str | None  # the largest scale word used ("lakh", "crore", …)


def words_to_number(words: list[str]) -> tuple[Decimal, str | None] | None:
    """Convert a list of lowercase number words to a value (None if not a number)."""
    total = 0
    current = 0
    seen_number = False
    biggest: tuple[int, str] | None = None
    decimals: list[int] = []
    in_decimal = False
    for w in words:
        if w == "and":
            continue
        if w == "point":
            in_decimal = True
            continue
        if in_decimal:
            if w in _UNITS and _UNITS[w] < 10:
                decimals.append(_UNITS[w])
                continue
            return None
        if w in _UNITS:
            current += _UNITS[w]
            seen_number = True
        elif w in _TENS:
            current += _TENS[w]
            seen_number = True
        elif w in ("hundred", "hundreds"):
            current = (current or 1) * 100
            seen_number = True
        elif w in _SCALES:
            scale = _SCALES[w]
            if current == 0 and total == 0:
                current = 1
            if total and total < scale and current == 0:
                # "twelve lakh crore": the scale multiplies everything so far
                total *= scale
            else:
                total += (current or 0) * scale
            current = 0
            seen_number = True
            if biggest is None or scale > biggest[0]:
                biggest = (scale, w.rstrip("s") if w not in ("lacs", "lacks") else "lakh")
        else:
            return None
    if not seen_number:
        return None
    value = Decimal(total + current)
    if decimals:
        value += Decimal("0." + "".join(str(d) for d in decimals))
    return value, (biggest[1] if biggest else None)


def find_number_words(text: str) -> list[NumberWords]:
    """Maximal runs of number words in ``text`` (single words like 'one' are ignored unless scaled)."""
    tokens: list[tuple[int, int, str]] = []
    for m in _WORD_RE.finditer(text):
        for part in m.group(0).lower().split("-"):
            tokens.append((m.start(), m.end(), part))
    out: list[NumberWords] = []
    i = 0
    n = len(tokens)
    while i < n:
        s, _e, w = tokens[i]
        if w not in _ALL or w in ("and", "point"):
            i += 1
            continue
        j = i
        run: list[str] = []
        end = tokens[i][1]
        while j < n and tokens[j][2] in _ALL:
            # a hyphenated word yields several tokens sharing the span
            run.append(tokens[j][2])
            end = tokens[j][1]
            j += 1
        # trim trailing connectors
        while run and run[-1] in ("and", "point"):
            run.pop()
        res = words_to_number(run) if run else None
        # a lone scale word ("million" after a figure) is a unit, not a number
        has_digit_word = any(w in _UNITS or w in _TENS or w in ("hundred", "hundreds") for w in run)
        meaningful = res is not None and has_digit_word and (len(run) >= 2 or (res[1] is not None))
        if res is not None and meaningful:
            out.append(NumberWords(s, end, res[0], text[s:end], res[1]))
        i = max(j, i + 1)
    return out
