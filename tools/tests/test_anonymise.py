"""tools/anonymise.py: identifiers replaced with stable tokens, amounts scaled, formulas and layout kept."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fscore" / "tests"))

from helpers_core import mini_statements

from fscore.io.ooxml_patch import validate_package
from fscore.io.xlsx_reader import Workbook
from fscore.prepare.spec import WorkbookSpec

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from anonymise import anonymise


def _with_signatures(spec: WorkbookSpec) -> None:
    bs = spec.sheet("BS")
    bs.set(19, 1, "For and on behalf of the Board of Directors of Corvus Data Services Private Limited")
    bs.set(20, 1, "CIN: U72900KA2015PTC081234")
    bs.set(22, 1, "Asha Raman")
    bs.set(23, 1, "Director (DIN: 01234567)")
    bs.set(22, 3, "Vikram Sethi")
    bs.set(23, 3, "DIN: 07654321")


def test_anonymise_replaces_identifiers_and_scales_amounts(tmp_path: Path) -> None:
    src = mini_statements(tmp_path / "real.xlsx", _with_signatures)
    out = tmp_path / "anon.xlsx"
    info = anonymise(src, out, ["Corvus Data Services Private Limited", "Asha Raman", "Vikram Sethi"], seed=3)
    assert validate_package(out) == []
    with Workbook.open(src) as a, Workbook.open(out) as b:
        texts = " ".join(str(c.value) for s in b.sheets for c in s.iter_cells() if isinstance(c.value, str))
        for secret in ("Corvus", "Asha", "Vikram", "01234567", "07654321", "U72900KA2015PTC081234"):
            assert secret not in texts
        assert "ENTITY_1" in texts and "PERSON_1" in texts and "DIN_A" in texts and "CIN_A" in texts
        factor = float(info["factor"])
        pa, pb = a.sheet("BS").cell(7, 3), b.sheet("BS").cell(7, 3)
        assert abs(pb.value - pa.value * factor) < 1e-6
        # formulas survive and their cached values are recomputed consistently
        assert b.sheet("BS").cell(10, 3).formula == a.sheet("BS").cell(10, 3).formula
        ta, tl = b.sheet("BS").cell(10, 3).value, b.sheet("BS").cell(17, 3).value
        assert abs(ta - tl) < 1e-6
        assert b.sheet("Note 4").print_area == a.sheet("Note 4").print_area
    mapping = json.loads(Path(str(out) + ".map.json").read_text())
    assert any(k.startswith("DIN:") for k in mapping["tokens"])
