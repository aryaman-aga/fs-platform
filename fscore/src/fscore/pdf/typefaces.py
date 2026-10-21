"""Vendored Unicode fonts for the native renderer and PDF stamps.

DejaVu Sans Condensed (Bitstream Vera licence, see fonts/LICENSE-DejaVu.txt)
covers the Indian Rupee sign (U+20B9) and has Arial-like widths.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

FAMILY = {
    (False, False): ("FSDejaVu", "DejaVuSansCondensed.ttf"),
    (True, False): ("FSDejaVu-Bold", "DejaVuSansCondensed-Bold.ttf"),
    (False, True): ("FSDejaVu-Oblique", "DejaVuSansCondensed-Oblique.ttf"),
    (True, True): ("FSDejaVu-BoldOblique", "DejaVuSansCondensed-BoldOblique.ttf"),
}

_registered = False


def font_path(filename: str = "DejaVuSansCondensed.ttf") -> Path:
    return Path(str(resources.files("fscore.pdf").joinpath("fonts", filename)))


def register_reportlab_fonts() -> None:
    """Register the four faces with ReportLab once (subset-embedded on use)."""
    global _registered
    if _registered:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for (_b, _i), (alias, filename) in FAMILY.items():
        pdfmetrics.registerFont(TTFont(alias, str(font_path(filename))))
    pdfmetrics.registerFontFamily(
        "FSDejaVu",
        normal=FAMILY[(False, False)][0],
        bold=FAMILY[(True, False)][0],
        italic=FAMILY[(False, True)][0],
        boldItalic=FAMILY[(True, True)][0],
    )
    _registered = True


def face(bold: bool = False, italic: bool = False) -> str:
    register_reportlab_fonts()
    return FAMILY[(bool(bold), bool(italic))][0]
