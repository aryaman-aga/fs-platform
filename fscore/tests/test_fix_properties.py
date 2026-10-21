"""Property tests (02 §13.3): after `fix` with the totals-only policy every linear
total casts on displayed values, and re-running `fix` is a no-op."""

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from fscore import api
from fscore.checks.base import CheckContext
from fscore.checks.casting import casting_failures
from fscore.config import EngagementConfig
from fscore.io.xlsx_reader import Workbook
from fscore.prepare.spec import SheetSpec, WorkbookSpec
from fscore.prepare.writer import write_workbook

CFG = EngagementConfig()

amount = st.decimals(min_value=Decimal("-500"), max_value=Decimal("5000"), places=3, allow_nan=False, allow_infinity=False)


@st.composite
def schedules(draw: st.DrawFn) -> tuple[list[list[list[Decimal]]], list[str]]:
    n_blocks = draw(st.integers(min_value=1, max_value=3))
    blocks = [
        [[draw(amount) for _ in range(2)] for _ in range(draw(st.integers(min_value=2, max_value=5)))]
        for _ in range(n_blocks)
    ]
    plugs = draw(st.lists(st.sampled_from(["", "-0.01", "+0.02", "+0.03"]), min_size=n_blocks, max_size=n_blocks))
    return blocks, plugs


def _build(path: Path, blocks: list[list[list[Decimal]]], plugs: list[str]) -> Path:
    spec = WorkbookSpec()
    s = spec.add(SheetSpec("Note 5", selected=True))
    s.set(1, 1, "5. Other financial assets", style="caption_bold")
    s.set(2, 2, "As at March 31, 2026", style="header")
    s.set(2, 3, "As at March 31, 2025", style="header")
    r = 3
    subtotal_rows = []
    for b, (block, plug) in enumerate(zip(blocks, plugs, strict=True)):
        first = r
        for i, (cy, py) in enumerate(block):
            s.set(r, 1, f"Item {b}.{i}", style="caption")
            s.set(r, 2, float(cy), style="amount")
            s.set(r, 3, float(py), style="amount")
            r += 1
        s.set(r, 1, f"Sub-total ({b})", style="caption_bold")
        s.set(r, 2, formula=f"SUM(B{first}:B{r - 1}){plug}", style="amount_bold")
        s.set(r, 3, formula=f"SUM(C{first}:C{r - 1})", style="amount_bold")
        subtotal_rows.append(r)
        r += 2
    s.set(r, 1, "Total", style="caption_bold")
    s.set(r, 2, formula="+".join(f"B{x}" for x in subtotal_rows), style="amount_total")
    s.set(r, 3, formula="+".join(f"C{x}" for x in subtotal_rows), style="amount_total")
    s.print_area = (1, 1, r, 3)
    return write_workbook(spec, path)


def _failures(path: Path) -> int:
    with Workbook.open(path) as wb:
        ctx = CheckContext(wb, CFG)
        return sum(len(casting_failures(ctx, sh)) for sh in ctx.print_sheets)


@given(schedules())
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_fix_makes_every_total_cast_and_is_idempotent(case: tuple[list[list[list[Decimal]]], list[str]]) -> None:
    blocks, plugs = case
    with tempfile.TemporaryDirectory() as d:
        src = _build(Path(d) / "in.xlsx", blocks, plugs)
        plan = api.propose_fixes(src, CFG)
        out = Path(d) / "out.xlsx"
        res = api.apply_fixes(src, plan, out, config=CFG)
        assert res.ok, (res.message, res.verification.model_dump())
        assert _failures(out) == 0
        again = api.propose_fixes(out, CFG)
        assert [e for e in again.edits if e.fix_kind.value == "auto"] == []
