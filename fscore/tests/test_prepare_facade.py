"""fscore.api.prepare_statements: TB file + mapping + inputs bundle → statements (Module P via the facade)."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import yaml

from fscore import api
from fscore.config import EngagementConfig
from fscore.prepare.supplementary import dump, load_bundle, template
from fscore.synthetic.ledger import generate_books


def _export(books, d: Path) -> tuple[Path, Path]:  # type: ignore[no-untyped-def]
    tb = d / "tb.csv"
    with tb.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Account", "Account Name", "Level 1", "Level 2", "Level 3", "Level 4", "Level 5", "FS Grouping", "Mar-26", "Mar-25"])
        for a in books.tb.accounts:
            w.writerow([a.code, a.name, *a.levels[:5], a.key, str(a.amount("CY")), str(a.amount("PY"))])
    bundle = d / "inputs.yaml"
    bundle.write_text(yaml.safe_dump(dump(books.years, opening=books.opening, entity=books.entity), sort_keys=False, allow_unicode=True),
                      encoding="utf-8")
    return tb, bundle


def test_bundle_round_trip(tmp_path: Path) -> None:
    books = generate_books(seed=3)
    _tb, bundle = _export(books, tmp_path)
    years, opening, entity = load_bundle(bundle)
    assert years["CY"].far["computers"] == books.years["CY"].far["computers"]
    assert years["PY"].shares == books.years["PY"].shares
    assert opening == books.opening
    assert entity is not None and entity.directors == books.entity.directors and entity.signing_date == books.entity.signing_date
    assert set(template()["years"]["CY"]) >= {"far", "lease", "gratuity", "shares", "tax"}


def test_prepare_from_tb_file_is_clean(tmp_path: Path) -> None:
    books = generate_books(seed=7)
    tb, bundle = _export(books, tmp_path)
    cfg = EngagementConfig(client=books.entity.name, year_end=date(2026, 3, 31))
    res = api.prepare_statements(tb, tmp_path / "prepared.xlsx", config=cfg, supplementary=bundle)
    assert res.ok, res.message
    result = api.check_workbook(tmp_path / "prepared.xlsx", cfg)
    assert [(f.rule_id, f.sheet, f.cells) for f in result.findings if f.severity.value != "Info"] == []


def test_prepare_blocked_without_schedules_or_with_unbalanced_tb(tmp_path: Path) -> None:
    books = generate_books(seed=7)
    tb, _bundle = _export(books, tmp_path)
    cfg = EngagementConfig(client=books.entity.name)
    res = api.prepare_statements(tb, tmp_path / "x.xlsx", config=cfg)
    assert not res.ok and res.missing_supplementary and not (tmp_path / "x.xlsx").exists()
    rows = list(csv.reader(tb.open()))
    rows[1][8] = str(float(rows[1][8]) + 1000)  # break the CY TB
    bad = tmp_path / "bad.csv"
    with bad.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    res2 = api.prepare_statements(bad, tmp_path / "y.xlsx", config=cfg, supplementary=_bundle)
    assert not res2.ok and any(i.code == "TB-NET" for i in res2.tb_validation.issues)
