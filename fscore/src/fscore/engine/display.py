"""Excel number-format renderer (02 §2.2, §4 "Number-format rendering").

Casting is done on *displayed* values. For every numeric cell this module
reproduces what Excel prints, and the number that the printed text represents:

* the stored float is first reduced to 15 significant digits (Excel's own
  precision, which also removes binary artefacts such as 2.67499999…);
* scaling is applied (``%`` ×100 each, trailing ``,`` ÷1000 each);
* the value is rounded half away from zero to the format's decimals;
* the section (positive; negative; zero; text) is chosen from the raw value.

``Rendered.value`` is the printed number expressed back in the cell's own
units (e.g. ``12.35%`` → ``0.1235``) so that printed figures can be added.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache

BUILTIN_FORMATS: dict[int, str] = {
    0: "General",
    1: "0",
    2: "0.00",
    3: "#,##0",
    4: "#,##0.00",
    5: '"₹"#,##0_);\\("₹"#,##0\\)',
    6: '"₹"#,##0_);[Red]\\("₹"#,##0\\)',
    7: '"₹"#,##0.00_);\\("₹"#,##0.00\\)',
    8: '"₹"#,##0.00_);[Red]\\("₹"#,##0.00\\)',
    9: "0%",
    10: "0.00%",
    11: "0.00E+00",
    12: "# ?/?",
    13: "# ??/??",
    14: "dd-mm-yyyy",
    15: "d-mmm-yy",
    16: "d-mmm",
    17: "mmm-yy",
    18: "h:mm AM/PM",
    19: "h:mm:ss AM/PM",
    20: "h:mm",
    21: "h:mm:ss",
    22: "dd-mm-yyyy h:mm",
    37: "#,##0 ;(#,##0)",
    38: "#,##0 ;[Red](#,##0)",
    39: "#,##0.00;(#,##0.00)",
    40: "#,##0.00;[Red](#,##0.00)",
    41: '_(* #,##0_);_(* \\(#,##0\\);_(* "-"_);_(@_)',
    42: '_("₹"* #,##0_);_("₹"* \\(#,##0\\);_("₹"* "-"_);_(@_)',
    43: '_(* #,##0.00_);_(* \\(#,##0.00\\);_(* "-"??_);_(@_)',
    44: '_("₹"* #,##0.00_);_("₹"* \\(#,##0.00\\);_("₹"* "-"??_);_(@_)',
    45: "mm:ss",
    46: "[h]:mm:ss",
    47: "mm:ss.0",
    48: "##0.0E+0",
    49: "@",
}

# characters Excel prints literally without quoting
_LITERAL_CHARS = set("$-+/():!^&'~{}<>= ₹€£¥")
_DATE_TOKEN_RE = re.compile(
    r"(yyyy|yy|mmmmm|mmmm|mmm|mm|m|dddd|ddd|dd|d|hh|h|ss|s|am/pm|a/p|\[h+\]|\[m+\]|\[s+\]|\.0+)", re.I
)
_COND_RE = re.compile(r"^\[(<=|>=|<>|<|>|=)(-?\d+(?:\.\d+)?)\]")


def to_decimal15(value: float | int | Decimal) -> Decimal:
    """Stored float -> Decimal at Excel's 15 significant digits."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    f = float(value)
    if f == 0.0 or not math.isfinite(f):
        return Decimal(0)
    return Decimal(format(f, ".15g"))


def excel_round(value: float | int | Decimal, digits: int) -> Decimal:
    """Excel ROUND(): half away from zero, on the 15-significant-digit value."""
    d = to_decimal15(value)
    q = Decimal(1).scaleb(-digits)
    try:
        return d.quantize(q, rounding=ROUND_HALF_UP)
    except InvalidOperation:  # extremely large values
        return d


@dataclass(frozen=True)
class Token:
    kind: str  # lit, pad, fill, digit, dot, comma, percent, exp, text, date, general
    text: str = ""


@dataclass
class Section:
    tokens: list[Token]
    condition: tuple[str, Decimal] | None = None
    color: str | None = None
    kind: str = "number"  # number, date, text, general, literal
    int_tokens: list[Token] = field(default_factory=list)
    frac_placeholders: str = ""
    grouping: bool = False
    indian: bool = False
    scale_commas: int = 0
    percent: int = 0
    exponent: str | None = None  # "E+" / "E-"
    exp_digits: int = 0
    prefix: list[Token] = field(default_factory=list)
    suffix: list[Token] = field(default_factory=list)
    has_dot: bool = False

    @property
    def decimals(self) -> int:
        return len(self.frac_placeholders)

    def literal_text(self) -> str:
        return "".join(t.text for t in self.tokens if t.kind in ("lit", "pad"))


@dataclass(frozen=True)
class Rendered:
    text: str  # what Excel prints (padding approximated by spaces)
    value: Decimal | None  # printed number in the cell's own units; None for text/dates/errors
    kind: str  # number, percent, date, text, general, error, empty, bool
    section: int = 0  # 0 pos, 1 neg, 2 zero, 3 text

    @property
    def clean(self) -> str:
        return self.text.strip()


@dataclass(frozen=True)
class FormatInfo:
    code: str
    kind: str  # number, percent, date, text, general, scientific
    decimals: int
    value_decimals: int  # decimals in value units (for ROUND(x, d) rewrites)
    grouping: bool
    zero_dash: bool
    negative_style: str  # minus, brackets, none
    scale_commas: int
    percent: int
    is_accounting: bool
    hidden: bool  # ';;;' style formats print nothing


def _tokenize(section: str) -> list[Token]:
    out: list[Token] = []
    i = 0
    n = len(section)
    while i < n:
        ch = section[i]
        if ch == '"':
            j = section.find('"', i + 1)
            if j < 0:
                j = n
            out.append(Token("lit", section[i + 1 : j]))
            i = j + 1
            continue
        if ch == "\\" and i + 1 < n:
            out.append(Token("lit", section[i + 1]))
            i += 2
            continue
        if ch == "_" and i + 1 < n:
            out.append(Token("pad", " "))
            i += 2
            continue
        if ch == "*" and i + 1 < n:
            out.append(Token("fill", section[i + 1]))
            i += 2
            continue
        if ch == "[":
            j = section.find("]", i)
            if j < 0:
                j = n - 1
            body = section[i + 1 : j]
            low = body.lower()
            if re.fullmatch(r"h+|m+|s+", low):
                out.append(Token("date", "[" + low + "]"))
            elif body.startswith("$"):
                cur = body[1:].split("-", 1)[0]
                if cur:
                    out.append(Token("lit", cur))
            # colours, conditions (handled earlier), DBNum etc. are ignored here
            i = j + 1
            continue
        if ch in "0#?":
            out.append(Token("digit", ch))
            i += 1
            continue
        if ch == ".":
            out.append(Token("dot", "."))
            i += 1
            continue
        if ch == ",":
            out.append(Token("comma", ","))
            i += 1
            continue
        if ch == "%":
            out.append(Token("percent", "%"))
            i += 1
            continue
        if ch in "Ee" and i + 1 < n and section[i + 1] in "+-":
            out.append(Token("exp", "E" + section[i + 1]))
            i += 2
            continue
        if ch == "@":
            out.append(Token("text", "@"))
            i += 1
            continue
        if section[i:].lower().startswith("general"):
            out.append(Token("general", "General"))
            i += 7
            continue
        m = _DATE_TOKEN_RE.match(section, i)
        if m and ch.lower() in "ymdhsa.":
            tok = m.group(0)
            if tok.startswith(".") and not any(t.kind == "date" for t in out):
                out.append(Token("dot", "."))
                i += 1
                continue
            out.append(Token("date", tok.lower()))
            i += len(tok)
            continue
        out.append(Token("lit", ch))
        i += 1
    return out


def _split_sections(code: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    in_quote = False
    while i < len(code):
        ch = code[i]
        if ch == '"':
            in_quote = not in_quote
        if ch == "\\" and not in_quote and i + 1 < len(code):
            buf.append(code[i : i + 2])
            i += 2
            continue
        if ch == ";" and not in_quote:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _compile_section(raw: str) -> Section:
    cond: tuple[str, Decimal] | None = None
    color: str | None = None
    s = raw
    while True:
        m = _COND_RE.match(s)
        if m:
            cond = (m.group(1), Decimal(m.group(2)))
            s = s[m.end() :]
            continue
        mc = re.match(r"^\[(black|blue|cyan|green|magenta|red|white|yellow|color\s*\d+)\]", s, re.I)
        if mc:
            color = mc.group(1).lower()
            s = s[mc.end() :]
            continue
        break
    tokens = _tokenize(s)
    sec = Section(tokens=tokens, condition=cond, color=color)
    kinds = {t.kind for t in tokens}
    if "general" in kinds:
        sec.kind = "general"
    elif "date" in kinds:
        sec.kind = "date"
    elif "digit" in kinds:
        sec.kind = "number"
    elif "text" in kinds:
        sec.kind = "text"
    else:
        sec.kind = "literal"
    if sec.kind != "number":
        return sec

    # locate the numeric part: from first digit to last digit/dot/comma/percent/exp
    idx_digits = [i for i, t in enumerate(tokens) if t.kind == "digit"]
    first, last = idx_digits[0], idx_digits[-1]
    # extend last over trailing commas (scaling) that follow the last digit placeholder
    j = last + 1
    while j < len(tokens) and tokens[j].kind == "comma":
        j += 1
    body = tokens[first:j]
    sec.prefix = tokens[:first]
    sec.suffix = tokens[j:]
    sec.percent = sum(1 for t in tokens if t.kind == "percent")
    if any(t.kind == "exp" for t in body):
        # scientific: mantissa placeholders before exp, exponent digits after
        k = next(i for i, t in enumerate(body) if t.kind == "exp")
        sec.exponent = body[k].text
        sec.exp_digits = sum(1 for t in body[k + 1 :] if t.kind == "digit")
        body = body[:k]
    # split integer / fraction at the dot
    dot_at = next((i for i, t in enumerate(body) if t.kind == "dot"), None)
    int_part = body if dot_at is None else body[:dot_at]
    frac_part = [] if dot_at is None else body[dot_at + 1 :]
    sec.has_dot = dot_at is not None
    # trailing commas of the integer part = scaling (only if not followed by digit placeholders)
    trailing = 0
    k = len(int_part) - 1
    while k >= 0 and int_part[k].kind == "comma":
        trailing += 1
        k -= 1
    if dot_at is None:
        sec.scale_commas = trailing
        int_part = int_part[: len(int_part) - trailing]
    else:
        sec.scale_commas = trailing
        int_part = int_part[: len(int_part) - trailing]
    # trailing commas after the fraction also scale (e.g. 0.0,,)
    k = len(frac_part) - 1
    while k >= 0 and frac_part[k].kind == "comma":
        sec.scale_commas += 1
        k -= 1
    frac_part = frac_part[: k + 1]
    commas_inside = [t for t in int_part if t.kind == "comma"]
    sec.grouping = bool(commas_inside)
    # Indian grouping when the pattern itself groups by two: #,##,##0
    pattern = "".join(t.text if t.kind in ("digit", "comma") else "" for t in int_part)
    sec.indian = bool(re.search(r",[#0?]{2},[#0?]{3}$", pattern))
    sec.int_tokens = [t for t in int_part if t.kind != "comma"]
    sec.frac_placeholders = "".join(t.text for t in frac_part if t.kind == "digit")
    return sec


def _group(digits: str, indian: bool) -> str:
    if len(digits) <= 3:
        return digits
    if not indian:
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


def _fill_integer(int_digits: str, tokens: list[Token], grouping: bool, indian: bool) -> str:
    """Fill integer digits into placeholders (with interleaved literals), right to left."""
    placeholders = [t for t in tokens if t.kind == "digit"]
    has_literals = any(t.kind in ("lit", "pad") for t in tokens)
    if int_digits == "0":
        int_digits = ""
    if not has_literals:
        min_zeros = 0
        # '0' placeholders force digits counting from the right
        for i, t in enumerate(reversed(placeholders)):
            if t.text == "0":
                min_zeros = i + 1
        digits = int_digits.rjust(min_zeros, "0") if min_zeros else int_digits
        q_pad = 0
        for i, t in enumerate(reversed(placeholders)):
            if t.text == "?" and i >= len(digits):
                q_pad += 1
        body = _group(digits, indian) if grouping else digits
        return " " * q_pad + body
    # template fill (e.g. ##\,##\,##0 or 000-000)
    out: list[str] = []
    di = len(int_digits) - 1
    for t in reversed(tokens):
        if t.kind == "digit":
            if di >= 0:
                out.append(int_digits[di])
                di -= 1
            elif t.text == "0":
                out.append("0")
            elif t.text == "?":
                out.append(" ")
        else:
            # a literal between placeholders is printed only if digits remain to its left
            if di >= 0:
                out.append(t.text)
    if di >= 0:
        out.append(int_digits[: di + 1])
    s = "".join(reversed(out))
    return s.lstrip(",") if s.startswith(",") else s


def _render_number(sec: Section, scaled_abs: Decimal) -> str:
    decimals = sec.decimals
    q = Decimal(1).scaleb(-decimals)
    rounded = scaled_abs.quantize(q, rounding=ROUND_HALF_UP)
    text = format(rounded, "f")
    int_s, _, frac_s = text.partition(".")
    frac_s = frac_s.ljust(decimals, "0")
    # trim optional fraction placeholders (#, ?)
    if decimals:
        chars = list(frac_s)
        for i in range(decimals - 1, -1, -1):
            ph = sec.frac_placeholders[i]
            if chars[i] == "0" and ph in "#?":
                chars[i] = "" if ph == "#" else " "
            else:
                break
        frac_s = "".join(chars)
    int_txt = _fill_integer(int_s, sec.int_tokens, sec.grouping, sec.indian)
    if not sec.int_tokens:
        int_txt = "" if int_s == "0" else int_s
    out = int_txt
    if sec.has_dot:
        out += "." + frac_s
    return out


def _render_scientific(sec: Section, scaled_abs: Decimal) -> str:
    if scaled_abs == 0:
        exp = 0
        mant = Decimal(0)
    else:
        exp = scaled_abs.adjusted()
        int_ph = max(1, len([t for t in sec.int_tokens if t.kind == "digit"]))
        if int_ph > 1:  # engineering style: exponent multiple of int placeholders
            exp = (exp // int_ph) * int_ph
        mant = scaled_abs.scaleb(-exp)
    q = Decimal(1).scaleb(-sec.decimals)
    mant = mant.quantize(q, rounding=ROUND_HALF_UP)
    if mant >= 10 and len([t for t in sec.int_tokens if t.kind == "digit"]) <= 1:
        mant = (mant / 10).quantize(q, rounding=ROUND_HALF_UP)
        exp += 1
    sign = "-" if exp < 0 else ("+" if sec.exponent == "E+" else "")
    return f"{format(mant, 'f')}E{sign}{str(abs(exp)).rjust(max(sec.exp_digits, 1), '0')}"


def _literal(tokens: list[Token]) -> str:
    out = []
    for t in tokens:
        if t.kind in ("lit", "pad"):
            out.append(t.text)
        elif t.kind == "percent":
            out.append("%")
    return "".join(out)


_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def serial_to_datetime(serial: float, date1904: bool = False) -> datetime | None:
    """Excel serial date -> datetime (handles the 1900 leap-year bug)."""
    try:
        s = float(serial)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(s) or s < 0 or s > 2958465:
        return None
    if date1904:
        base = datetime(1904, 1, 1)
        return base + timedelta(days=s)
    if s < 60:
        base = datetime(1899, 12, 31)
    else:
        base = datetime(1899, 12, 30)
    return base + timedelta(days=s)


def _render_date(sec: Section, value: float, date1904: bool) -> str:
    dt = serial_to_datetime(value, date1904)
    if dt is None:
        return "#" * 8
    tokens = sec.tokens
    has_ampm = any(t.kind == "date" and t.text in ("am/pm", "a/p") for t in tokens)
    out: list[str] = []
    for i, t in enumerate(tokens):
        if t.kind in ("lit", "pad"):
            out.append(t.text)
            continue
        if t.kind == "digit":
            out.append(t.text)
            continue
        if t.kind == "dot":
            out.append(".")
            continue
        if t.kind in ("comma", "percent"):
            out.append(t.text)
            continue
        if t.kind != "date":
            continue
        tok = t.text
        # 'm'/'mm' is minutes when it follows h or precedes s
        is_minute = False
        if tok in ("m", "mm"):
            prev = [x for x in tokens[:i] if x.kind == "date"]
            nxt = [x for x in tokens[i + 1 :] if x.kind == "date"]
            if (prev and prev[-1].text.startswith(("h", "[h"))) or (nxt and nxt[0].text.startswith(("s", "[s"))):
                is_minute = True
        if tok == "yyyy":
            out.append(f"{dt.year:04d}")
        elif tok == "yy":
            out.append(f"{dt.year % 100:02d}")
        elif tok == "mmmmm":
            out.append(_MONTHS[dt.month - 1][0])
        elif tok == "mmmm":
            out.append(_MONTHS[dt.month - 1])
        elif tok == "mmm":
            out.append(_MONTHS[dt.month - 1][:3])
        elif tok == "mm":
            out.append(f"{dt.minute:02d}" if is_minute else f"{dt.month:02d}")
        elif tok == "m":
            out.append(str(dt.minute) if is_minute else str(dt.month))
        elif tok == "dddd":
            out.append(_DAYS[dt.weekday()])
        elif tok == "ddd":
            out.append(_DAYS[dt.weekday()][:3])
        elif tok == "dd":
            out.append(f"{dt.day:02d}")
        elif tok == "d":
            out.append(str(dt.day))
        elif tok in ("hh", "h"):
            h = dt.hour
            if has_ampm:
                h = h % 12 or 12
            out.append(f"{h:02d}" if tok == "hh" else str(h))
        elif tok in ("ss", "s"):
            out.append(f"{dt.second:02d}" if tok == "ss" else str(dt.second))
        elif tok == "am/pm":
            out.append("AM" if dt.hour < 12 else "PM")
        elif tok == "a/p":
            out.append("A" if dt.hour < 12 else "P")
        elif tok.startswith("[h"):
            out.append(str(int(float(value) * 24)))
        elif tok.startswith("[m"):
            out.append(str(int(float(value) * 1440)))
        elif tok.startswith("[s"):
            out.append(str(round(float(value) * 86400)))
        elif tok.startswith("."):
            frac = dt.microsecond / 1_000_000
            out.append(f"{frac:.{len(tok) - 1}f}"[1:])
    return "".join(out)


def _general(value: float) -> tuple[str, Decimal]:
    d = to_decimal15(value)
    if d == d.to_integral_value() and abs(d) < Decimal("1e11"):
        return str(int(d)), d
    a = abs(float(value))
    if a >= 1e11 or (a < 1e-9 and a != 0):
        txt = f"{float(value):.5E}".replace("E+0", "E+").replace("E-0", "E-")
        mant, exp = txt.split("E")
        mant = mant.rstrip("0").rstrip(".")
        return f"{mant}E{exp}", d
    # up to 10 significant digits, limited to 11 characters
    digits_before = max(1, math.floor(math.log10(a)) + 1) if a >= 1 else 1
    max_dec = max(0, 10 - digits_before - (1 if value < 0 else 0))
    r = excel_round(value, max_dec)
    txt = format(r, "f")
    if "." in txt:
        txt = txt.rstrip("0").rstrip(".")
    return txt, Decimal(txt)


class NumberFormat:
    """A compiled Excel number format."""

    def __init__(self, code: str) -> None:
        self.code = code or "General"
        raw_sections = _split_sections(self.code)
        self.sections = [_compile_section(s) for s in raw_sections[:4]]
        self.conditional = any(s.condition for s in self.sections)
        self.info = self._info()

    # ------------------------------------------------------------ selection
    def _pick(self, v: Decimal) -> tuple[Section, int, bool]:
        """Return (section, index, show_minus)."""
        secs = self.sections
        numeric_secs = secs[:3]
        if self.conditional:
            for i, s in enumerate(numeric_secs):
                if s.condition and _cond(v, s.condition):
                    neg_only = s.condition[0] in ("<", "<=") and s.condition[1] <= 0
                    return s, i, v < 0 and not neg_only
            # fall through to the first unconditioned section
            for i, s in enumerate(numeric_secs):
                if not s.condition:
                    return s, i, v < 0
            return secs[0], 0, v < 0
        n = len(numeric_secs)
        if n == 1:
            return secs[0], 0, v < 0
        if v > 0:
            return secs[0], 0, False
        if v < 0:
            return secs[1], 1, False
        if n >= 3:
            return secs[2], 2, False
        return secs[0], 0, False

    def render(self, value: object, *, date1904: bool = False) -> Rendered:
        if value is None or value == "":
            return Rendered("", None, "empty")
        if isinstance(value, bool):
            return Rendered("TRUE" if value else "FALSE", None, "bool")
        if isinstance(value, str):
            if value.startswith("#") and value.endswith(("!", "?", "A")) and len(value) <= 8:
                return Rendered(value, None, "error")
            text_sec = self.sections[3] if len(self.sections) >= 4 else None
            if text_sec is None and self.sections and self.sections[0].kind == "text":
                text_sec = self.sections[0]
            if text_sec is not None:
                out = "".join(value if t.kind == "text" else (t.text if t.kind in ("lit", "pad") else "") for t in text_sec.tokens)
                return Rendered(out, None, "text", 3)
            return Rendered(value, None, "text", 3)
        if isinstance(value, datetime):
            value = (value - datetime(1899, 12, 30)).total_seconds() / 86400.0
        if not isinstance(value, (int, float, Decimal)):
            return Rendered(str(value), None, "text", 3)
        fv = float(value)
        if not math.isfinite(fv):
            return Rendered("#NUM!", None, "error")
        v = to_decimal15(value)
        if abs(v) < Decimal("1e-9"):
            v = Decimal(0)
        sec, idx, show_minus = self._pick(v)
        if sec.kind == "general":
            txt, gv = _general(fv)
            if idx == 1 and not show_minus:
                txt = txt.lstrip("-")
            body = "".join(txt if t.kind == "general" else (t.text if t.kind in ("lit", "pad") else "") for t in sec.tokens)
            return Rendered(body, gv, "general", idx)
        if sec.kind == "date":
            return Rendered(_render_date(sec, fv, date1904), None, "date", idx)
        if sec.kind in ("text", "literal"):
            return Rendered(sec.literal_text(), Decimal(0) if v == 0 else None, "number" if v == 0 else "text", idx)
        scale = Decimal(100) ** sec.percent / (Decimal(1000) ** sec.scale_commas)
        scaled_abs = abs(v) * scale
        body = _render_scientific(sec, scaled_abs) if sec.exponent else _render_number(sec, scaled_abs)
        text = _literal(sec.prefix) + body + _literal(sec.suffix)
        if show_minus:
            text = "-" + text
        # printed number, back in the cell's own units
        q = Decimal(1).scaleb(-sec.decimals)
        if sec.exponent:
            mant_s, _, exp_s = body.partition("E")
            printed_scaled = Decimal(mant_s).scaleb(int(exp_s))
        else:
            printed_scaled = scaled_abs.quantize(q, rounding=ROUND_HALF_UP)
        printed = printed_scaled / scale if scale != 1 else printed_scaled
        if v < 0:
            printed = -printed
        kind = "percent" if sec.percent else "number"
        return Rendered(text, printed if printed != 0 else Decimal(0), kind, idx)

    # ------------------------------------------------------------ metadata
    def _info(self) -> FormatInfo:
        first = self.sections[0]
        kind = first.kind
        if kind == "number" and first.percent:
            kind = "percent"
        if kind == "number" and first.exponent:
            kind = "scientific"
        decimals = first.decimals if first.kind == "number" else 0
        value_decimals = decimals + 2 * first.percent - 3 * first.scale_commas
        if kind == "general":
            decimals, value_decimals = 2, 2
        zero_dash = False
        if len(self.sections) >= 3 and not self.conditional:
            zt = self.sections[2].literal_text().strip()
            zero_dash = zt in ("-", "–", "—") or (self.sections[2].kind == "literal" and zt.strip() in ("-", ""))
        neg = "minus"
        if len(self.sections) >= 2 and not self.conditional:
            lt = _literal(self.sections[1].prefix) + _literal(self.sections[1].suffix)
            neg = "brackets" if "(" in lt else ("minus" if "-" in lt else "none")
        hidden = all(s.kind == "literal" and not s.literal_text().strip() for s in self.sections[:3]) and len(self.sections) >= 3
        return FormatInfo(
            code=self.code,
            kind=kind if kind != "literal" else "text",
            decimals=decimals,
            value_decimals=value_decimals,
            grouping=first.grouping,
            zero_dash=zero_dash,
            negative_style=neg,
            scale_commas=first.scale_commas,
            percent=first.percent,
            is_accounting=any(t.kind == "fill" for t in first.tokens) or ("_(" in self.code),
            hidden=hidden,
        )


def _cond(v: Decimal, cond: tuple[str, Decimal]) -> bool:
    op, x = cond
    return {
        "<": v < x,
        "<=": v <= x,
        ">": v > x,
        ">=": v >= x,
        "=": v == x,
        "<>": v != x,
    }[op]


@lru_cache(maxsize=2048)
def compile_format(code: str) -> NumberFormat:
    return NumberFormat(code)


def render(value: object, code: str = "General", *, date1904: bool = False) -> Rendered:
    """Render one value through one Excel format code."""
    return compile_format(code or "General").render(value, date1904=date1904)


def format_info(code: str) -> FormatInfo:
    return compile_format(code or "General").info


def is_date_format(code: str) -> bool:
    return compile_format(code or "General").sections[0].kind == "date"


_NUM_TOKEN_RE = re.compile(r"^\(?-?[₹$€£]?\s*[\d,]*\.?\d+\)?%?$")


def parse_printed_number(token: str) -> Decimal | None:
    """Parse a printed number such as '(1,253.04)', '12,34,567.89', '-', '5.0%' into a Decimal.

    A lone dash means zero. Percentages are returned in printed units (12.5% -> 12.5).
    """
    t = token.strip().replace("−", "-").replace("–", "-").replace("—", "-")
    if t in ("-", "--"):
        return Decimal(0)
    t = t.replace(" ", "")
    for sym in ("₹", "$", "€", "£", "Rs.", "INR"):
        t = t.replace(sym, "")
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg = True
        t = t[1:-1]
    if t.endswith("%"):
        t = t[:-1]
    if t.startswith("-"):
        neg = not neg if t.count("-") == 1 else neg
        t = t[1:]
    if not t or not re.fullmatch(r"[\d,]*\.?\d+|\d[\d,]*\.?", t):
        return None
    try:
        d = Decimal(t.replace(",", ""))
    except InvalidOperation:
        return None
    return -d if neg else d


def numeric_signature(text: str) -> str | None:
    """Normalised printed number used to match workbook cells against PDF tokens."""
    d = parse_printed_number(text)
    if d is None:
        return None
    return format(d.normalize(), "f") if d != 0 else "0"
