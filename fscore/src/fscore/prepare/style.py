"""House style v1 (ADR-0010): fonts, number formats, borders for every style key."""

from __future__ import annotations

from typing import Any

HOUSE_STYLE_VERSION = "house-style/1"

ACCOUNTING = '_(* #,##0.00_);_(* \\(#,##0.00\\);_(* "-"??_);_(@_)'
ACCOUNTING_0 = '_(* #,##0_);_(* \\(#,##0\\);_(* "-"??_);_(@_)'
SHARES = "#,##0"
PCT = "0.00%"
RATIO = "0.00"

_BASE: dict[str, Any] = {"font_name": "Arial", "font_size": 9, "valign": "top"}

STYLES: dict[str, dict[str, Any]] = {
    "text": {},
    "title": {"bold": True, "font_size": 11},
    "subtitle": {"bold": True, "font_size": 10},
    "unit": {"italic": True, "font_size": 8},
    "header": {"bold": True, "align": "right", "text_wrap": True, "bottom": 1},
    "header_left": {"bold": True, "align": "left", "text_wrap": True, "bottom": 1},
    "header_center": {"bold": True, "align": "center", "text_wrap": True, "bottom": 1},
    "section": {"bold": True},
    "caption": {"indent": 1, "text_wrap": True},
    "caption2": {"indent": 2, "text_wrap": True},
    "caption3": {"indent": 3, "text_wrap": True},
    "caption_bold": {"bold": True, "text_wrap": True},
    "note_ref": {"align": "center"},
    "amount": {"num_format": ACCOUNTING},
    "amount_bold": {"num_format": ACCOUNTING, "bold": True, "top": 1},
    "amount_total": {"num_format": ACCOUNTING, "bold": True, "top": 1, "bottom": 6},
    "amount_plain": {"num_format": "0.00"},  # deliberately inconsistent format (TXT-07 tests)
    "amount_0dp": {"num_format": ACCOUNTING_0},
    "shares": {"num_format": SHARES},
    "shares_bold": {"num_format": SHARES, "bold": True, "top": 1},
    "pct": {"num_format": PCT},
    "pct_bold": {"num_format": PCT, "bold": True, "top": 1},
    "ratio": {"num_format": RATIO},
    "narrative": {"text_wrap": True, "align": "left"},  # justify is laid out wider than the cell by some renderers
    "text_wrap": {"text_wrap": True},
    "narrative_bold": {"text_wrap": True, "bold": True},
    "sign": {},
    "sign_bold": {"bold": True},
    "white": {"num_format": ACCOUNTING, "font_color": "#FFFFFF"},
    "white_text": {"font_color": "#FFFFFF"},
    "hidden_fmt": {"num_format": ";;;"},
    "tb_text": {"font_size": 8},
    "tb_amount": {"font_size": 8, "num_format": "#,##0.00;(#,##0.00);-"},
    "tb_header": {"font_size": 8, "bold": True, "bottom": 1, "text_wrap": True},
    "date": {"num_format": "dd-mmm-yyyy", "align": "left"},
}


def xlsxwriter_props(key: str) -> dict[str, Any]:
    props = dict(_BASE)
    props.update(STYLES.get(key, {}))
    return props


def is_amount_style(key: str) -> bool:
    return key.startswith("amount") or key in ("white",)
