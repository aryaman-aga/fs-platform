from __future__ import annotations

import csv
import importlib.util
import json
import sys
import tomllib
import types
from pathlib import Path
from typing import Any

import pytest
import yaml
from helpers_report import make_applied, make_plan, make_result, small_workbook
from typer.testing import CliRunner

from fscore import api, cli
from fscore.audit.store import AuditStore
from fscore.models import PdfMiss, PdfReconResult, RenderResult

runner = CliRunner()


@pytest.fixture(autouse=True)
def audit_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "audit.db"
    monkeypatch.setenv("FSCHECK_AUDIT_DB", str(db))
    monkeypatch.setenv("FSCHECK_USER", "cli-tester")
    return db


@pytest.fixture
def wb(tmp_path: Path) -> Path:
    return small_workbook(tmp_path / "Synthetic FS FY26.xlsx")


def _invoke(*args: str, input: str | None = None) -> Any:
    return runner.invoke(cli.app, list(args), input=input)


def _fake_check(results: list[Any]) -> Any:
    calls: list[Path] = []

    def fake(path: Any, config: Any = None, **_: Any) -> Any:
        calls.append(Path(path))
        return results[min(len(calls) - 1, len(results) - 1)]

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


def _audit(db: Path) -> AuditStore:
    return AuditStore(db)


# --------------------------------------------------------------------------- check


def test_check_writes_outputs_and_exits_1_on_high(wb: Path, monkeypatch: pytest.MonkeyPatch, audit_db_path: Path) -> None:
    monkeypatch.setattr(api, "check_workbook", _fake_check([make_result()]))
    res = _invoke("check", str(wb), "--html", "--json")
    assert res.exit_code == 1, res.output
    out = wb.parent / "fscheck-out"
    names = sorted(p.name for p in out.iterdir())
    assert any(n.endswith(".xlsx") and "FS-Check" in n for n in names)
    assert "Synthetic FS FY26 FS-Check.html" in names
    assert "Synthetic FS FY26.check.json" in names
    assert "High" in res.output
    assert "PDF must be re-printed" in res.output
    with _audit(audit_db_path) as store:
        runs = store.runs()
        assert runs
        assert runs[0]["kind"] == "check"
        assert runs[0]["status"] == "completed"
        assert store.verify_chain().ok


def test_check_clean_exits_0(wb: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "check_workbook", _fake_check([make_result(findings=[], pdf=False)]))
    res = _invoke("check", str(wb), "--no-register")
    assert res.exit_code == 0, res.output
    assert not (wb.parent / "fscheck-out").exists() or not any((wb.parent / "fscheck-out").glob("*.xlsx"))


def test_check_input_errors_exit_2(wb: Path, tmp_path: Path) -> None:
    assert _invoke("check", str(tmp_path / "missing.xlsx")).exit_code == 2
    bad = tmp_path / "bad.yaml"
    bad.write_text("not_a_setting: 1\n", encoding="utf-8")
    res = _invoke("check", str(wb), "--config", str(bad))
    assert res.exit_code == 2
    assert "invalid configuration" in res.output
    junk = tmp_path / "junk.xlsx"
    junk.write_bytes(b"this is not a workbook")
    res = _invoke("check", str(junk))
    assert res.exit_code == 2
    assert "Traceback" not in res.output


# --------------------------------------------------------------------------- fix


def test_fix_dry_run_writes_nothing(wb: Path, monkeypatch: pytest.MonkeyPatch, audit_db_path: Path) -> None:
    monkeypatch.setattr(api, "check_workbook", _fake_check([make_result()]))
    monkeypatch.setattr(api, "propose_fixes", lambda *a, **k: make_plan())

    def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("apply must not run in a dry run")

    monkeypatch.setattr(api, "apply_fixes", boom)
    res = _invoke("fix", str(wb), "--dry-run")
    assert res.exit_code == 1, res.output
    assert "Dry run" in res.output
    assert "E-0001" in res.output
    assert not (wb.parent / "fscheck-out" / f"{wb.stem} - casting corrected.xlsx").exists()
    with _audit(audit_db_path) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 1


def test_fix_applies_verifies_and_reports(wb: Path, monkeypatch: pytest.MonkeyPatch, audit_db_path: Path) -> None:
    monkeypatch.setattr(api, "check_workbook", _fake_check([make_result(), make_result(findings=[], pdf=False)]))
    monkeypatch.setattr(api, "propose_fixes", lambda *a, **k: make_plan())
    seen: dict[str, Any] = {}

    def fake_apply(path: Path, plan: Any, out_path: Path, **kw: Any) -> Any:
        seen.update(kw, out_path=Path(out_path), source=Path(path))
        Path(out_path).write_bytes(Path(path).read_bytes())
        applied = make_applied()
        applied.out_path = str(out_path)
        return applied

    monkeypatch.setattr(api, "apply_fixes", fake_apply)
    res = _invoke("fix", str(wb), "--approve", "E-0003")
    assert res.exit_code == 0, res.output
    assert seen["approved_edit_ids"] == {"E-0003"}
    assert seen["out_path"].name == f"{wb.stem} - casting corrected.xlsx"
    assert seen["out_path"].parent == wb.parent / "fscheck-out"
    assert seen["backup_dir"] == wb.parent / "fscheck-out" / "backups"
    with _audit(audit_db_path) as store:
        actions = [e["action"] for e in store.events()]
        assert "plan.approved" in actions
        assert "plan.applied" in actions
        assert store.verify_chain().ok


def test_fix_verification_failure_exits_3(wb: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "check_workbook", _fake_check([make_result()]))
    monkeypatch.setattr(api, "propose_fixes", lambda *a, **k: make_plan())
    monkeypatch.setattr(api, "apply_fixes", lambda *a, **k: make_applied(ok=False))
    res = _invoke("fix", str(wb))
    assert res.exit_code == 3, res.output
    assert "verification failed" in res.output.lower()


def test_fix_guards(wb: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "check_workbook", _fake_check([make_result()]))
    monkeypatch.setattr(api, "propose_fixes", lambda *a, **k: make_plan())
    assert _invoke("fix", str(wb), "--approve", "E-9999").exit_code == 2
    assert _invoke("fix", str(wb), "--out", str(wb)).exit_code == 2
    assert _invoke("fix", str(wb), "--policy", "round_everything").exit_code == 2


# --------------------------------------------------------------------------- pdf


def test_pdf_reconcile_stale_exits_1(wb: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = tmp_path / "printed.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    rec = PdfReconResult(pdf_sha256="aa" * 32, pages=3, values_checked=10, stale=True, reprint_pages=[2],
                         misses=[PdfMiss(sheet="BS", cell="C8", printed="445.75", caption="Total assets", pages=[1])])
    monkeypatch.setattr(api, "reconcile_pdf", lambda *a, **k: rec)
    out_json = tmp_path / "rec.json"
    res = _invoke("pdf", str(wb), str(pdf), "--json", str(out_json))
    assert res.exit_code == 1, res.output
    assert "older version" in res.output
    assert "Re-print pages: 2" in res.output
    assert json.loads(out_json.read_text())["stale"] is True


def test_pdf_export_outcomes(wb: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes = {
        "ok": RenderResult(ok=True, pdf_path="x.pdf", sha256="bb" * 32, engine="native", engine_version="1", pages=4, verification=PdfReconResult()),
        "miss": RenderResult(ok=True, pdf_path="x.pdf", engine="native", pages=4, verification=PdfReconResult(misses=[PdfMiss(sheet="BS", cell="C8", printed="1")])),
        "high": RenderResult(ok=False, mode="final", message="Final PDF refused: 3 High findings remain"),
        "fail": RenderResult(ok=False, message="LibreOffice timed out"),
    }
    for key, code in (("ok", 0), ("miss", 3), ("high", 1), ("fail", 3)):
        captured: dict[str, Any] = {}

        def fake(path: Any, out_path: Any, key: str = key, captured: dict[str, Any] = captured, **kw: Any) -> RenderResult:
            captured.update(kw, out=Path(out_path))
            return outcomes[key]

        monkeypatch.setattr(api, "render_pdf", fake)
        args = ["pdf-export", str(wb)] + (["--final"] if key == "high" else [])
        res = _invoke(*args)
        assert res.exit_code == code, (key, res.output)
        assert captured["out"].parent == wb.parent / "fscheck-out"
        assert captured["out"].name.startswith("Synthetic-FS-FY26_FS_FY2025-26_v1_")
        assert captured["mode"] == ("final" if key == "high" else "draft")


# --------------------------------------------------------------------------- AI


def _install_fake_ai(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"sent": 0}
    mod = types.ModuleType("fake_ai")

    def prepare_request(findings: list[Any], config: Any = None, use_case: str = "triage") -> dict[str, Any]:
        state["prepared"] = len(findings)
        return {"payload": {"use_case": use_case, "findings": [{"id": f.id, "rule": f.rule_id, "entity": "ENTITY_1"} for f in findings]},
                "provider": config.ai.provider, "model": config.ai.model, "region": config.ai.region, "token_estimate": 321}

    def send_request(request: dict[str, Any], approval_token: str | None = None, approved_by: str | None = None) -> dict[str, Any]:
        state["sent"] += 1
        state["token"] = approval_token
        state["approved_by"] = approved_by
        return {"stop_reason": "end_turn", "items": [{"finding": "F-0001", "classification": "genuine_error"}]}

    mod.prepare_request = prepare_request  # type: ignore[attr-defined]
    mod.send_request = send_request  # type: ignore[attr-defined]
    monkeypatch.setattr(cli, "_optional_module", lambda name: mod if name == "fscore.ai" else None)
    return state


def _results_json(tmp_path: Path) -> Path:
    p = tmp_path / "results.json"
    p.write_text(make_result().model_dump_json(), encoding="utf-8")
    return p


def test_review_ai_requires_explicit_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_db_path: Path) -> None:
    state = _install_fake_ai(monkeypatch)
    results = _results_json(tmp_path)
    res = _invoke("review-ai", "--findings", str(results), "--provider", "anthropic", "--region", "ap-south-1", input="yes\n")
    assert res.exit_code == 0, res.output
    assert "Not sent." in res.output
    assert state["sent"] == 0
    assert "ENTITY_1" in res.output
    assert "ap-south-1" in res.output
    assert "~321 tokens" in res.output
    res = _invoke("review-ai", "--findings", str(results), "--provider", "anthropic", "--region", "ap-south-1", input="APPROVE\n")
    assert res.exit_code == 0, res.output
    assert state["sent"] == 1
    assert len(state["token"]) == 64
    assert state["approved_by"] == "cli-tester"
    assert "genuine_error" in res.output
    with _audit(audit_db_path) as store:
        decisions = [r[0] for r in store.conn.execute("SELECT decision FROM ai_calls ORDER BY ts")]
        assert decisions == ["rejected", "sent"]
        assert all(r[0] is None for r in store.conn.execute("SELECT payload FROM ai_calls"))  # hash_only by default
        assert store.verify_chain().ok


def test_review_ai_sha_preapproval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = _install_fake_ai(monkeypatch)
    results = _results_json(tmp_path)
    res = _invoke("review-ai", "--findings", str(results), "--provider", "bedrock", "--region", "ap-south-1", "--approve-sha256", "0" * 64)
    assert res.exit_code == 2
    assert state["sent"] == 0
    preview = _invoke("review-ai", "--findings", str(results), "--provider", "bedrock", "--region", "ap-south-1", input="no\n")
    sha = preview.output.split("SHA-256 ")[1].split()[0]
    res = _invoke("review-ai", "--findings", str(results), "--provider", "bedrock", "--region", "ap-south-1", "--approve-sha256", sha)
    assert res.exit_code == 0, res.output
    assert state["sent"] == 1


def test_review_ai_provider_none_sends_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = _install_fake_ai(monkeypatch)
    res = _invoke("review-ai", "--findings", str(_results_json(tmp_path)))
    assert res.exit_code == 0
    assert "nothing was sent" in res.output
    assert state["sent"] == 0


def test_review_ai_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_optional_module", lambda name: None)
    res = _invoke("review-ai", "--findings", str(_results_json(tmp_path)), "--provider", "anthropic")
    assert res.exit_code == 2
    assert "not available" in res.output


# --------------------------------------------------------------------------- report / rules / diff / init


def test_report_from_json_and_latest(tmp_path: Path, wb: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    results = _results_json(tmp_path)
    res = _invoke("report", str(results), "--register")
    assert res.exit_code == 0, res.output
    out = tmp_path / "fscheck-out"
    assert any(p.suffix == ".html" for p in out.iterdir())
    assert any(p.suffix == ".xlsx" for p in out.iterdir())
    assert _invoke("report", "latest").exit_code == 2  # nothing stored yet
    monkeypatch.setattr(api, "check_workbook", _fake_check([make_result()]))
    _invoke("check", str(wb), "--no-register")
    res = _invoke("report", "latest", "--out-dir", str(tmp_path / "rep"))
    assert res.exit_code == 0, res.output
    assert any(p.suffix == ".html" for p in (tmp_path / "rep").iterdir())


def test_rules_catalogue() -> None:
    res = _invoke("rules")
    assert res.exit_code == 0
    assert "CAST-01" in res.output
    assert "GC-01" in res.output
    data = json.loads(_invoke("rules", "--json").output)
    assert data["version"]
    assert any(r["id"] == "RPT-09" for r in data["rules"])


def test_diff_command(tmp_path: Path) -> None:
    a = small_workbook(tmp_path / "a.xlsx", variant="a")
    b = small_workbook(tmp_path / "b.xlsx", variant="b")
    out = tmp_path / "d.json"
    res = _invoke("diff", str(a), str(b), "--json", str(out))
    assert res.exit_code == 0, res.output
    assert "Printed figures moved" in res.output
    assert "Sheets added: Notes" in res.output
    assert json.loads(out.read_text())["printed_moves"]


def test_init_writes_config(wb: Path) -> None:
    res = _invoke("init", str(wb), "--client", "Synthetic Co")
    assert res.exit_code == 0, res.output
    cfg_path = wb.parent / "fscheck-out" / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert cfg["client"] == "Synthetic Co"
    assert cfg["print_sheets"]["explicit"] == ["BS"]
    assert cfg["statements"]["balance_sheet"] == "BS"
    assert cfg["statements"]["trial_balance"] == "NS_TB_FT27"
    assert cfg["year_end"] == "2026-03-31"
    assert _invoke("init", str(wb)).exit_code == 2  # exists, no --force
    assert _invoke("init", str(wb), "--force", "--units", "furlongs").exit_code == 2


def test_detect_signatories_and_units(tmp_path: Path) -> None:
    from fscore.prepare.spec import SheetSpec, WorkbookSpec
    from fscore.prepare.writer import write_workbook

    spec = WorkbookSpec()
    s = spec.add(SheetSpec("BS", selected=True))
    s.set(1, 1, "Balance Sheet as at March 31, 2026")
    s.set(2, 1, "(All amounts in ₹ lakhs, unless otherwise stated)")
    s.set(40, 1, "Rohit Mehra")
    s.set(41, 1, "Partner")
    s.set(42, 1, "Membership No.: 234567")
    s.set(40, 3, "Asha Raman")
    s.set(41, 3, "Director")
    s.set(42, 3, "DIN: 01234567")
    path = write_workbook(spec, tmp_path / "sig.xlsx")
    from fscore.io.xlsx_reader import Workbook

    with Workbook.open(path) as w:
        sigs = cli.detect_signatories(w, "BS")
        assert {"name": "Asha Raman", "role": "Director", "din": "01234567"} in sigs
        assert {"name": "Rohit Mehra", "role": "Partner", "membership_no": "234567"} in sigs
        assert cli.detect_units(w) == "lakhs"
        assert str(cli.detect_year_end(w, "BS")) == "2026-03-31"


# --------------------------------------------------------------------------- TB / Module P / synthetic


def _write_tb(path: Path, rows: list[list[Any]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Account", "Account Name", "Grouping", "Mar-26", "Mar-25"])
        w.writerows(rows)
    return path


def test_validate_tb_gate(tmp_path: Path) -> None:
    ok = _write_tb(tmp_path / "tb.csv", [["1000", "Bank", "cash.bank_current", "1500.00", "900"], ["2000", "Share capital", "eq.share_capital", "-1500.00", "-900"]])
    res = _invoke("validate-tb", str(ok), "--periods", "CY=Mar-26,PY=Mar-25")
    assert res.exit_code == 0, res.output
    assert "passed" in res.output
    bad = _write_tb(tmp_path / "tb2.csv", [["1000", "Bank", "cash.bank_current", "1500.00", "900"], ["3000", "Suspense", "", "-1400", "-900"]])
    res = _invoke("validate-tb", str(bad))
    assert res.exit_code == 1
    assert "TB-NET" in res.output
    assert "TB-UNMAPPED" in res.output
    assert _invoke("validate-tb", str(ok), "--periods", "garbage").exit_code == 2


def test_prepare_and_synth_with_fake_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tb = _write_tb(tmp_path / "tb.csv", [["1000", "Bank", "cash.bank_current", "10", "5"], ["2000", "Capital", "eq.share_capital", "-10", "-5"]])
    mapping = tmp_path / "map.yaml"
    mapping.write_text("1000: cash.bank_current\n2000: eq.share_capital\n", encoding="utf-8")
    out = tmp_path / "prepared.xlsx"
    prep = types.ModuleType("fscore.prepare.api")

    def fake_prepare(tb_path: Path, mapping_path: Path, out_path: Path, config: Any = None) -> dict[str, Any]:
        return {"out_path": out_path, "high_count": 0}

    prep.prepare = fake_prepare  # type: ignore[attr-defined]
    gen = types.ModuleType("fscore.synthetic.generator")

    def fake_make(out_path: Path, inject: list[str] | None = None, exact: bool = False, seed: int = 7) -> dict[str, Any]:
        return {"path": out_path, "expected": [{"rule": r} for r in inject or []]}

    gen.make_workbook = fake_make  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fscore.prepare.api", prep)
    monkeypatch.setitem(sys.modules, "fscore.synthetic.generator", gen)
    res = _invoke("prepare", "--tb", str(tb), "--mapping", str(mapping), "--out", str(out))
    assert res.exit_code == 0, res.output
    res = _invoke("synth", str(tmp_path / "s.xlsx"), "--inject", "CAST-01,PLUG-01", "--exact")
    assert res.exit_code == 0, res.output
    assert "2 expected finding(s)" in res.output


def test_prepare_and_synth_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_optional_module", lambda name: None)
    tb = _write_tb(tmp_path / "tb.csv", [["1000", "Bank", "cash.bank_current", "1", "1"]])
    assert _invoke("prepare", "--tb", str(tb), "--mapping", str(tb), "--out", str(tmp_path / "o.xlsx")).exit_code == 2
    assert _invoke("synth", str(tmp_path / "s.xlsx")).exit_code == 2


# --------------------------------------------------------------------------- engine integration


def _engine_marks() -> list[Any]:
    """Apply the `engine` marker only once it is registered in the root pytest config."""
    root = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        markers = tomllib.loads(root.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]["markers"]
    except (OSError, KeyError):
        markers = []
    return [pytest.mark.engine] if any(str(m).startswith("engine") for m in markers) else []


@pytest.mark.skipif(importlib.util.find_spec("fscore.runner") is None, reason="fscore.runner not built yet")
@pytest.mark.parametrize("_", [pytest.param(0, marks=_engine_marks())])
def test_cli_check_with_real_engine(_: int, wb: Path) -> None:
    res = _invoke("check", str(wb), "--json")
    assert res.exit_code in (0, 1), res.output
    out = wb.parent / "fscheck-out"
    assert any(p.suffix == ".xlsx" for p in out.iterdir())
    data = json.loads((out / f"{wb.stem}.check.json").read_text(encoding="utf-8"))
    assert data["run"]["file_name"] == wb.name
