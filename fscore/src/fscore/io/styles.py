"""styles.xml + theme parsing: number formats, fonts (bold, colour), fills, borders."""

from __future__ import annotations

import colorsys
import contextlib
from dataclasses import dataclass, field

from lxml import etree

from fscore.engine.display import BUILTIN_FORMATS
from fscore.io.ooxml import NS_A, q

# Default 64-colour indexed palette (ECMA-376 §18.8.27); 64 = system fg, 65 = system bg.
INDEXED = [
    "000000", "FFFFFF", "FF0000", "00FF00", "0000FF", "FFFF00", "FF00FF", "00FFFF",
    "000000", "FFFFFF", "FF0000", "00FF00", "0000FF", "FFFF00", "FF00FF", "00FFFF",
    "800000", "008000", "000080", "808000", "800080", "008080", "C0C0C0", "808080",
    "9999FF", "993366", "FFFFCC", "CCFFFF", "660066", "FF8080", "0066CC", "CCCCFF",
    "000080", "FF00FF", "FFFF00", "00FFFF", "800080", "800000", "008080", "0000FF",
    "00CCFF", "CCFFFF", "CCFFCC", "FFFF99", "99CCFF", "FF99CC", "CC99FF", "FFCC99",
    "3366FF", "33CCCC", "99CC00", "FFCC00", "FF9900", "FF6600", "666699", "969696",
    "003366", "339966", "003300", "333300", "993300", "993366", "333399", "333333",
    "000000", "FFFFFF",
]

DEFAULT_THEME = ["FFFFFF", "000000", "E7E6E6", "44546A", "4472C4", "ED7D31", "A5A5A5", "FFC000", "5B9BD5", "70AD47", "0563C1", "954F72"]


@dataclass(frozen=True, slots=True)
class Font:
    bold: bool = False
    italic: bool = False
    underline: bool = False
    size: float = 11.0
    name: str = "Calibri"
    color: str | None = None  # RRGGBB, None = automatic (black)


@dataclass(frozen=True, slots=True)
class Fill:
    color: str | None = None  # RRGGBB of a solid fill, None = no fill


@dataclass(frozen=True, slots=True)
class Border:
    top: str | None = None
    bottom: str | None = None
    left: str | None = None
    right: str | None = None


@dataclass(frozen=True, slots=True)
class XF:
    num_fmt_id: int = 0
    font_id: int = 0
    fill_id: int = 0
    border_id: int = 0
    horizontal: str | None = None
    indent: int = 0
    wrap: bool = False


@dataclass
class Styles:
    num_fmts: dict[int, str] = field(default_factory=dict)
    fonts: list[Font] = field(default_factory=lambda: [Font()])
    fills: list[Fill] = field(default_factory=lambda: [Fill(), Fill()])
    borders: list[Border] = field(default_factory=lambda: [Border()])
    xfs: list[XF] = field(default_factory=lambda: [XF()])
    theme: list[str] = field(default_factory=lambda: list(DEFAULT_THEME))
    indexed: list[str] = field(default_factory=lambda: list(INDEXED))

    def xf(self, style_id: int) -> XF:
        return self.xfs[style_id] if 0 <= style_id < len(self.xfs) else self.xfs[0]

    def number_format(self, style_id: int) -> str:
        nid = self.xf(style_id).num_fmt_id
        return self.num_fmts.get(nid) or BUILTIN_FORMATS.get(nid, "General")

    def font(self, style_id: int) -> Font:
        fid = self.xf(style_id).font_id
        return self.fonts[fid] if 0 <= fid < len(self.fonts) else self.fonts[0]

    def fill(self, style_id: int) -> Fill:
        fid = self.xf(style_id).fill_id
        return self.fills[fid] if 0 <= fid < len(self.fills) else Fill()

    def border(self, style_id: int) -> Border:
        bid = self.xf(style_id).border_id
        return self.borders[bid] if 0 <= bid < len(self.borders) else Border()


def _apply_tint(rgb: str, tint: float) -> str:
    r, g, b = (int(rgb[i : i + 2], 16) / 255 for i in (0, 2, 4))
    h, lum, s = colorsys.rgb_to_hls(r, g, b)
    lum = lum * (1 + tint) if tint < 0 else lum * (1 - tint) + tint
    r2, g2, b2 = colorsys.hls_to_rgb(h, max(0.0, min(1.0, lum)), s)
    return "".join(f"{round(x * 255):02X}" for x in (r2, g2, b2))


def resolve_color(el: etree._Element | None, styles: Styles) -> str | None:
    if el is None:
        return None
    if el.get("auto") in ("1", "true"):
        return None
    rgb = el.get("rgb")
    out: str | None = None
    if rgb:
        out = rgb[-6:].upper()
    elif el.get("theme") is not None:
        idx = int(el.get("theme", "1"))
        out = styles.theme[idx] if 0 <= idx < len(styles.theme) else None
    elif el.get("indexed") is not None:
        idx = int(el.get("indexed", "64"))
        if idx == 64:
            return None  # system foreground = automatic
        out = styles.indexed[idx] if 0 <= idx < len(styles.indexed) else None
    if out and el.get("tint"):
        with contextlib.suppress(ValueError):
            out = _apply_tint(out, float(el.get("tint", "0")))
    return out


def parse_theme(root: etree._Element) -> list[str]:
    scheme = root.find(".//" + q("clrScheme", NS_A))
    if scheme is None:
        return list(DEFAULT_THEME)
    colours: dict[str, str] = {}
    for child in scheme:
        name = etree.QName(child).localname
        srgb = child.find(q("srgbClr", NS_A))
        sys_clr = child.find(q("sysClr", NS_A))
        if srgb is not None:
            colours[name] = srgb.get("val", "000000").upper()
        elif sys_clr is not None:
            colours[name] = sys_clr.get("lastClr", "000000").upper()
    order = ["lt1", "dk1", "lt2", "dk2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "hlink", "folHlink"]
    return [colours.get(k, DEFAULT_THEME[i]) for i, k in enumerate(order)]


def parse_styles(root: etree._Element | None, theme: list[str] | None = None) -> Styles:
    st = Styles()
    if theme:
        st.theme = theme
    if root is None:
        return st
    colors = root.find(q("colors"))
    if colors is not None:
        idx_el = colors.find(q("indexedColors"))
        if idx_el is not None:
            custom = [c.get("rgb", "FF000000")[-6:].upper() for c in idx_el.findall(q("rgbColor"))]
            if custom:
                st.indexed = custom + INDEXED[len(custom) :]
    nf = root.find(q("numFmts"))
    if nf is not None:
        for el in nf.findall(q("numFmt")):
            st.num_fmts[int(el.get("numFmtId", "0"))] = el.get("formatCode", "General")
    fonts = root.find(q("fonts"))
    if fonts is not None:
        st.fonts = []
        for f in fonts.findall(q("font")):
            b = f.find(q("b"))
            i = f.find(q("i"))
            u = f.find(q("u"))
            sz = f.find(q("sz"))
            name = f.find(q("name"))
            st.fonts.append(
                Font(
                    bold=b is not None and b.get("val", "1") not in ("0", "false"),
                    italic=i is not None and i.get("val", "1") not in ("0", "false"),
                    underline=u is not None and u.get("val", "single") != "none",
                    size=float(sz.get("val", "11")) if sz is not None else 11.0,
                    name=name.get("val", "Calibri") if name is not None else "Calibri",
                    color=resolve_color(f.find(q("color")), st),
                )
            )
        if not st.fonts:
            st.fonts = [Font()]
    fills = root.find(q("fills"))
    if fills is not None:
        st.fills = []
        for fl in fills.findall(q("fill")):
            pf = fl.find(q("patternFill"))
            colour = None
            if pf is not None and pf.get("patternType") not in (None, "none"):
                colour = resolve_color(pf.find(q("fgColor")), st)
                if colour is None and pf.get("patternType") == "solid":
                    colour = None
            st.fills.append(Fill(colour))
    borders = root.find(q("borders"))
    if borders is not None:
        st.borders = []
        for bd in borders.findall(q("border")):
            def side(tag: str, bd: etree._Element = bd) -> str | None:
                el = bd.find(q(tag))
                return el.get("style") if el is not None and el.get("style") else None

            st.borders.append(Border(side("top"), side("bottom"), side("left"), side("right")))
    xfs = root.find(q("cellXfs"))
    if xfs is not None:
        st.xfs = []
        for x in xfs.findall(q("xf")):
            al = x.find(q("alignment"))
            st.xfs.append(
                XF(
                    num_fmt_id=int(x.get("numFmtId", "0")),
                    font_id=int(x.get("fontId", "0")),
                    fill_id=int(x.get("fillId", "0")),
                    border_id=int(x.get("borderId", "0")),
                    horizontal=al.get("horizontal") if al is not None else None,
                    indent=int(al.get("indent", "0")) if al is not None else 0,
                    wrap=al is not None and al.get("wrapText") in ("1", "true"),
                )
            )
        if not st.xfs:
            st.xfs = [XF()]
    return st


def luminance(rgb: str | None, default: str = "000000") -> float:
    c = rgb or default
    r, g, b = (int(c[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(x: float) -> float:
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast_ratio(fg: str | None, bg: str | None) -> float:
    """WCAG contrast between font colour (auto = black) and fill (none = white)."""
    l1 = luminance(fg, "000000")
    l2 = luminance(bg, "FFFFFF")
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)
