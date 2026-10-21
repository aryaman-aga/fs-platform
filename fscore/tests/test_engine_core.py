"""Unit tests: number-format rendering, Excel rounding, formula parser/translator, evaluator, linear forms."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fscore.engine import formula as F
from fscore.engine.display import excel_round, format_info, parse_printed_number, render
from fscore.engine.evaluator import Evaluator, values_equal
from fscore.engine.formula import ExcelError
from fscore.engine.linear import linear_form_text
from fscore.engine.periods import parse_period
from fscore.engine.refs import col_to_index, index_to_col, parse_area

ACC = '_(* #,##0.00_);_(* \\(#,##0.00\\);_(* "-"??_);_(@_)'
INDIAN_COND = "[>=10000000]##\\,##\\,##\\,##0.00;[>=100000]##\\,##\\,##0.00;##,##0.00"

RENDER_CASES = [
    (1234.567, ACC, "1,234.57", "1234.57"),
    (-1253.04, ACC, "(1,253.04)", "-1253.04"),
    (0, ACC, "-", "0"),
    (0.001, ACC, "0.00", "0"),
    (-0.001, ACC, "(0.00)", "0"),
    (-0.004, "0.00", "-0.00", "0"),
    (0.005, "0.00", "0.01", "0.01"),
    (2.675, "0.00", "2.68", "2.68"),
    (-2.675, "0.00", "-2.68", "-2.68"),
    (1.005, "0.00", "1.01", "1.01"),
    (0.12345, "0.00%", "12.35%", "0.1235"),
    (0.5, "0%", "50%", "0.5"),
    (1234567, "#,##0,", "1,235", "1235000"),
    (1234567890, "#,##0,,", "1,235", "1235000000"),
    (1234567.891, "#,##,##0.00", "12,34,567.89", "1234567.89"),
    (123456.7, INDIAN_COND, "1,23,456.70", "123456.70"),
    (12345678.9, INDIAN_COND, "1,23,45,678.90", "12345678.90"),
    (5000, INDIAN_COND, "5,000.00", "5000.00"),
    (1234.5678, "General", "1234.5678", "1234.5678"),
    (20200004, "#,##0", "20,200,004", "20200004"),
    (-0.4, "0", "-0", "0"),
    (0.5, "#.00", ".50", "0.50"),
    (-1234.5, "#,##0.00;[Red](#,##0.00)", "(1,234.50)", "-1234.50"),
    (1e-17, ACC, "-", "0"),
    (99.995, "0.00", "100.00", "100.00"),
    (-99.995, ACC, "(100.00)", "-100.00"),
    (0.1 + 0.2, "0.00", "0.30", "0.30"),
    (1234.5, "#,##0", "1,235", "1235"),
    (1234.5, "0.0", "1234.5", "1234.5"),
    (7, "00", "07", "7"),
]


@pytest.mark.parametrize(("value", "fmt", "text", "printed"), RENDER_CASES)
def test_render(value: float, fmt: str, text: str, printed: str) -> None:
    r = render(value, fmt)
    assert r.clean == text
    assert r.value == Decimal(printed)


@pytest.mark.parametrize("decimals", [0, 1, 2, 3])
@pytest.mark.parametrize("value", [0.005, 0.015, 0.025, 1.0049999999999999, 2.675, 1234.5650, -0.125, 5.5, -5.5, 1e15 / 7])
def test_excel_round_is_half_away_from_zero(value: float, decimals: int) -> None:
    got = excel_round(value, decimals)
    exact = Decimal(format(value, ".15g"))
    q = Decimal(1).scaleb(-decimals)
    expect = (abs(exact) / q + Decimal("0.5")).to_integral_value(rounding="ROUND_FLOOR") * q
    assert got == (expect if exact >= 0 else -expect)


@given(st.decimals(min_value=Decimal("-1e9"), max_value=Decimal("1e9"), places=2, allow_nan=False, allow_infinity=False))
@settings(max_examples=300)
def test_two_decimal_values_render_exactly(d: Decimal) -> None:
    r = render(float(d), ACC)
    assert r.value == d.quantize(Decimal("0.01"))
    assert parse_printed_number(r.clean) == d.quantize(Decimal("0.01")) or (d == 0 and r.clean == "-")


def test_format_info() -> None:
    fi = format_info(ACC)
    assert (fi.decimals, fi.zero_dash, fi.negative_style, fi.is_accounting) == (2, True, "brackets", True)
    assert format_info("0.00%").value_decimals == 4
    assert format_info("#,##0,").value_decimals == -3
    assert format_info(";;;").hidden
    assert render(46112, '"As at "mmmm dd, yyyy').clean == "As at March 31, 2026"
    assert render(46112, "dd-mmm-yyyy").clean == "31-Mar-2026"


@pytest.mark.parametrize(("token", "value"), [("(1,253.04)", "-1253.04"), ("12,34,567.89", "1234567.89"), ("-", "0"), ("5.00%", "5.00"), ("₹ 1,000", "1000"), ("abc", None)])
def test_parse_printed_number(token: str, value: str | None) -> None:
    got = parse_printed_number(token)
    assert (got is None and value is None) or got == Decimal(value)  # type: ignore[arg-type]


def test_refs() -> None:
    assert col_to_index("A") == 1 and col_to_index("Z") == 26 and col_to_index("AA") == 27 and col_to_index("XFD") == 16384
    assert index_to_col(28) == "AB"
    a = parse_area("'Note 4-10'!$B$2:C9", "BS")
    assert (a.sheet, a.r1, a.c1, a.r2, a.c2) == ("Note 4-10", 2, 2, 9, 3)
    assert parse_area("O''Brien!A1", "x").sheet == "O''Brien" or parse_area("'O''Brien'!A1", "x").sheet == "O'Brien"


@pytest.mark.parametrize(
    "formula",
    [
        "SUM(F10:F20)-0.01",
        "=A1+B1+0.01",
        "ROUND(SUM(D33:D39),2)-0.03",
        "'Note 4-10'!G23-0.01",
        "J106/100000+0.01",
        "IF(A1>0,\"Profit\",\"Loss\")",
        "\"As at \"&TEXT(BS!C3,\"mmmm dd, yyyy\")",
        "SUMPRODUCT(ROUND(C4:C6,2))",
        "-2^2",
        "50%*A1",
        "SUM(A:A)-SUM(1:1)",
        "'O''Brien & Co'!A1*2",
        "IFERROR(VLOOKUP(A1,$B$2:$C$9,2,FALSE),0)",
        "{1,2;3,4}",
        "#REF!+1",
    ],
)
def test_parse_roundtrip(formula: str) -> None:
    toks = F.tokenize(formula)
    assert F.detokenize(toks) == (formula[1:] if formula.startswith("=") else formula)
    node = F.parse(formula)
    again = F.parse(F.to_text(node))
    assert F.to_text(again) == F.to_text(node)


def test_translate_shared_formula() -> None:
    assert F.translate("SUM(B3:B7)*$C$1+'Note 4'!D3", 2, 1) == "SUM(C5:C9)*$C$1+'Note 4'!E5"
    assert F.translate("A$1+$A2", 3, 3) == "D$1+$A5"
    assert F.translate("A1", -1, 0) == "#REF!"


def _ev(cells: dict[tuple[str, int, int], object]) -> Evaluator:
    return Evaluator(lambda s, r, c: cells.get((s, r, c)), bounds=lambda s: (100, 30))


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        ("SUM(A1:A3)", 6.0),
        ("SUMPRODUCT(ROUND(B1:B3,2))", 3.33),
        ("ROUND(2.675,2)", 2.68),
        ("ROUND(-2.5,0)", -3.0),
        ("ROUNDDOWN(2.679,2)", 2.67),
        ("ROUNDUP(2.671,2)", 2.68),
        ("-2^2", 4.0),
        ("2^3^2", 64.0),
        ("10%", 0.1),
        ("IF(A1>0,\"yes\",\"no\")", "yes"),
        ("IFERROR(1/0,7)", 7.0),
        ("TEXT(46112,\"mmmm dd, yyyy\")", "March 31, 2026"),
        ("\"As at \"&TEXT(46112,\"mmmm dd, yyyy\")", "As at March 31, 2026"),
        ("AND(TRUE,A1=1)", True),
        ("MIN(A1:A3)+MAX(A1:A3)", 4.0),
        ("AVERAGE(A1:A3)", 2.0),
        ("COUNT(A1:C3)", 6.0),
        ("SUMIF(C1:C3,\"x\",A1:A3)", 4.0),
        ("SUMIFS(A1:A3,C1:C3,\"x\")", 4.0),
        ("VLOOKUP(2,A1:B3,2,FALSE)", 1.112),
        ("INDEX(A1:A3,2)", 2.0),
        ("MATCH(3,A1:A3,0)", 3.0),
        ("EOMONTH(DATE(2026,2,10),1)", 46112.0),
        ("YEAR(46112)", 2026.0),
        ("LEFT(\"Balance\",3)&UPPER(\"x\")", "BalX"),
        ("1/0", ExcelError("#DIV/0!")),
        ("ABS(-3)+INT(2.7)+MOD(7,3)", 6.0),
        ("CONCATENATE(\"a\",1.5,TRUE)", "a1.5TRUE"),
    ],
)
def test_evaluator(formula: str, expected: object) -> None:
    cells = {("S", 1, 1): 1.0, ("S", 2, 1): 2.0, ("S", 3, 1): 3.0, ("S", 1, 2): 1.111, ("S", 2, 2): 1.112, ("S", 3, 2): 1.114,
             ("S", 1, 3): "x", ("S", 2, 3): "y", ("S", 3, 3): "x"}
    got = _ev(cells).evaluate(formula, "S", 1, 5)
    assert values_equal(expected, got), (formula, got)


@pytest.mark.parametrize(
    ("formula", "terms", "const", "is_total", "is_link"),
    [
        ("SUM(F10:F20)-0.01", [("F10:F20", 1)], "-0.01", True, False),
        ("A1+B1+0.01", [("A1", 1), ("B1", 1)], "0.01", True, False),
        ("ROUND(SUM(D33:D39),2)-0.03", [("D33:D39", 1)], "-0.03", True, False),
        ("'Note'!G23-0.01", [("G23", 1)], "-0.01", False, True),
        ("J106/100000+0.01", [("J106", Decimal("0.00001"))], "0.01", False, False),
        ("C10-(C11+C12)", [("C10", 1), ("C11", -1), ("C12", -1)], "0", True, False),
        ("SUMPRODUCT(ROUND(C4:C6,2))", [("C4:C6", 1)], "0", True, False),
        ("IF(A1>0,A1,0)", None, None, False, False),
        ("A1*B1", None, None, False, False),
    ],
)
def test_linear_forms(formula: str, terms: list[tuple[str, object]] | None, const: str | None, is_total: bool, is_link: bool) -> None:
    lf = linear_form_text(formula, "S")
    if terms is None:
        assert lf is None
        return
    assert lf is not None
    assert [(t.area.a1(), t.coef) for t in lf.terms] == [(a, Decimal(c) if not isinstance(c, Decimal) else c) for a, c in terms]
    assert lf.constant == Decimal(const)  # type: ignore[arg-type]
    assert lf.is_total is is_total and lf.is_link is is_link


@pytest.mark.parametrize(
    ("text", "end", "months"),
    [
        ("As at March 31, 2026", "2026-03-31", None),
        ("For the year ended 31st March, 2025", "2025-03-31", 12),
        ("Three months ended March 31, 2026", "2026-03-31", 3),
        ("Qtr Mar 26", "2026-03-31", 3),
        ("FY 25-26", "2026-03-31", 12),
        ("31.03.2026", "2026-03-31", None),
        ("Quarter ended June 30, 2026", "2026-06-30", 3),
    ],
)
def test_periods(text: str, end: str, months: int | None) -> None:
    p = parse_period(text)
    assert p is not None and p.end.isoformat() == end and p.months == months  # type: ignore[union-attr]
    assert parse_period("As at 31st June 2026").malformed  # type: ignore[union-attr]
