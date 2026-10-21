"""Excel → PDF rendering (02 §10A) with mandatory post-conversion verification."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from helpers_pdf import make_workbook

from fscore.config import EngagementConfig
from fscore.io.pdf_reader import fonts_embedded, read_pdf
from fscore.models import CheckResult, Finding, RunInfo, Severity
from fscore.pdf import render_libreoffice
from fscore.pdf.naming import never_overwrite, pdf_filename
from fscore.pdf.render import final_gate, render, resolve_engine

CFG = EngagementConfig(client="Corvus Data Services")


def _check_result(*findings: Finding) -> CheckResult:
    run = RunInfo(
        engine_version="t", rule_set_version="t", rule_set_hash="t", file_name="x.xlsx", file_sha256="0" * 64,
        config_hash="c", started_at=datetime(2026, 10, 21),
    )
    return CheckResult(run=run, findings=list(findings))


def _high(rule: str = "CAST-01") -> Finding:
    return Finding(rule_id=rule, severity=Severity.HIGH, explanation="x")


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    return make_workbook(tmp_path / "statements.xlsx", white_value=123456.78)


def test_native_draft_renders_and_verifies(workbook: Path, tmp_path: Path) -> None:
    res = render(workbook, tmp_path / "out", mode="draft", engine="native", config=CFG, run_id="R-101")
    assert res.ok, res.message
    assert res.engine == "native"
    assert res.mode == "draft"
    assert res.verification is not None
    assert res.verification.misses == []
    assert res.verification.values_checked > 200
    assert res.pages >= 3  # the long note spans pages
    assert res.sha256
    assert len(res.sha256) == 64
    pdf = Path(res.pdf_path or "")
    assert pdf.name.startswith("Corvus-Data-Services_FS_FY-Mar-26_v1_")
    doc = read_pdf(pdf)
    text = "\n".join(p.visible_text for p in doc.pages)
    # hidden row / hidden column values never print; the backup sheet is not printed
    assert "777.77" not in text
    assert "4,248.42" not in text
    assert "4248.42" not in text
    assert "98,765.43" not in text
    # draft watermark and run id on every page (overlay text, excluded from reconciliation)
    for p in doc.pages:
        overlay = " ".join(w.text for w in p.overlay_words)
        assert "DRAFT" in overlay
        assert "R-101" in overlay
    assert "₹" in doc.pages[0].visible_text
    assert fonts_embedded(doc) == []
    # bookmarks: one per sheet and one per note heading
    titles = [t for _lvl, t, _p in doc.toc]
    assert any("Balance Sheet" in t for t in titles)
    assert any(t.startswith("Note 3") for t in titles)
    assert any(t.startswith("Note 4") for t in titles)


def test_versions_are_never_overwritten(workbook: Path, tmp_path: Path) -> None:
    a = render(workbook, tmp_path / "out", mode="draft", engine="native", config=CFG)
    b = render(workbook, tmp_path / "out", mode="draft", engine="native", config=CFG)
    assert a.pdf_path != b.pdf_path
    assert "_v1_" in (a.pdf_path or "")
    assert "_v2_" in (b.pdf_path or "")
    explicit = tmp_path / "fixed.pdf"
    c = render(workbook, explicit, mode="draft", engine="native", config=CFG)
    d = render(workbook, explicit, mode="draft", engine="native", config=CFG)
    assert Path(c.pdf_path or "").name == "fixed.pdf"
    assert Path(d.pdf_path or "").name == "fixed_v2.pdf"


def test_naming_helpers(tmp_path: Path) -> None:
    name = pdf_filename("Group B / UK", "SPFS", "Qtr Mar-26", 3, datetime(2026, 10, 20, 15, 30))
    assert name == "Group-B-UK_SPFS_Qtr-Mar-26_v3_20261020-1530.pdf"
    p = tmp_path / name
    p.write_bytes(b"%PDF")
    assert never_overwrite(p).name == "Group-B-UK_SPFS_Qtr-Mar-26_v4_20261020-1530.pdf"


def test_final_refused_with_high_findings(workbook: Path, tmp_path: Path) -> None:
    res = render(workbook, tmp_path / "f.pdf", mode="final", engine="native", config=CFG, check_result=_check_result(_high()))
    assert not res.ok
    assert "High" in res.message
    assert not (tmp_path / "f.pdf").exists()
    sig = render(workbook, tmp_path / "f.pdf", mode="final", engine="native", config=CFG, check_result=_check_result(_high("TXT-01")))
    assert not sig.ok
    assert "signatory" in sig.message
    none = render(workbook, tmp_path / "f.pdf", mode="final", engine="native", config=CFG, check_result=None)
    assert not none.ok
    assert "check run" in none.message
    assert final_gate(_check_result()) is None


def test_final_needs_excel_or_libreoffice_by_default(workbook: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FSCORE_ALLOW_NATIVE_FINAL", raising=False)
    engine, note = resolve_engine("native", "final", CFG)
    assert engine is None
    assert "Excel or LibreOffice" in note
    monkeypatch.setenv("FSCORE_ALLOW_NATIVE_FINAL", "1")
    res = render(workbook, tmp_path / "final.pdf", mode="final", engine="native", config=CFG, check_result=_check_result())
    assert res.ok, res.message
    doc = read_pdf(Path(res.pdf_path or ""))
    assert all("DRAFT" not in " ".join(w.text for w in p.overlay_words) for p in doc.pages)


def test_final_deleted_when_verification_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A '#REF!' printed on the page fails verification: the final PDF is not kept (control C7)."""
    monkeypatch.setenv("FSCORE_ALLOW_NATIVE_FINAL", "1")
    wb = make_workbook(tmp_path / "err.xlsx", error_cell=True)
    res = render(wb, tmp_path / "final.pdf", mode="final", engine="native", config=CFG, check_result=_check_result())
    assert not res.ok
    assert "rejected by verification" in res.message
    assert not (tmp_path / "final.pdf").exists()
    assert res.verification is not None
    assert res.verification.error_tokens


def test_unknown_mode_and_engine(workbook: Path, tmp_path: Path) -> None:
    assert not render(workbook, tmp_path / "x.pdf", mode="signed", config=CFG).ok
    assert not render(workbook, tmp_path / "x.pdf", engine="cloud", config=CFG).ok


def test_auto_prefers_libreoffice_over_native_when_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render_libreoffice, "available", lambda: True)
    engine, _ = resolve_engine("auto", "draft", CFG)
    assert engine in ("excel", "libreoffice")
    monkeypatch.setattr(render_libreoffice, "available", lambda: False)
    engine, _note = resolve_engine("auto", "draft", CFG)
    assert engine in ("excel", "native")


@pytest.mark.libreoffice
def test_libreoffice_engine_renders_and_verifies(workbook: Path, tmp_path: Path) -> None:
    if not render_libreoffice.available():
        pytest.skip("LibreOffice (soffice) not installed")
    res = render(workbook, tmp_path / "lo", mode="draft", engine="libreoffice", config=CFG, run_id="R-LO")
    assert res.ok, res.message
    assert res.engine == "libreoffice"
    assert "LibreOffice" in res.engine_version
    assert res.verification is not None
    assert res.verification.misses == []
    doc = read_pdf(Path(res.pdf_path or ""))
    text = "\n".join(p.visible_text for p in doc.pages)
    assert "777.77" not in text
    assert "98,765.43" not in text
    assert all("DRAFT" in " ".join(w.text for w in p.overlay_words) for p in doc.pages)
    assert any(t.startswith("Note 3") for _l, t, _p in doc.toc)


def _widen_policies(tmp_path: Path) -> Path:
    """A synthetic workbook whose Policies sheet is one text column wider than A4 portrait, fit to 1 page wide."""
    import re
    import zipfile

    from fscore.synthetic.generator import make_workbook as make_synthetic

    src = Path(make_synthetic(tmp_path / "narrow.xlsx", seed=7).path)
    out = tmp_path / "wide.xlsx"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "xl/worksheets/sheet5.xml":  # Policies
                data = re.sub(rb'(<col min="1" max="1" width=")[\d.]+(")', rb"\g<1>130.7109375\g<2>", data)
            zout.writestr(info, data)
    return out


def test_libreoffice_copy_gets_the_scale_excel_would_use(tmp_path: Path) -> None:
    """LibreOffice treats a single over-wide column as one page wide and would print it off the paper."""
    wide = _widen_policies(tmp_path)
    scales = render_libreoffice.excel_fit_scales(wide, ["BS", "PL", "Policies"])
    assert list(scales) == ["xl/worksheets/sheet5.xml"] and 60 <= scales["xl/worksheets/sheet5.xml"] < 80
    copy = tmp_path / "copy.xlsx"
    render_libreoffice.make_print_copy(wide, copy, ["BS", "Policies"])
    import zipfile

    with zipfile.ZipFile(copy) as z:
        xml = z.read("xl/worksheets/sheet5.xml")
        assert b'fitToPage="0"' in xml and f'scale="{scales["xl/worksheets/sheet5.xml"]}"'.encode() in xml
        assert b'fitToPage="1"' in z.read("xl/worksheets/sheet1.xml")  # other sheets keep their own setup


@pytest.mark.libreoffice
def test_libreoffice_renders_an_over_wide_text_column_on_the_page(tmp_path: Path) -> None:
    if not render_libreoffice.available():
        pytest.skip("LibreOffice (soffice) not installed")
    res = render(_widen_policies(tmp_path), tmp_path / "lo", mode="draft", engine="libreoffice", config=EngagementConfig(), run_id="R-W")
    assert res.ok, res.message


def test_finish_external_render_like_the_windows_worker(workbook: Path, tmp_path: Path) -> None:
    """A PDF produced by the Windows Excel worker gets the same overlay, bookmarks and verification."""
    from fscore.pdf.render import finish_external_render
    from fscore.pdf.render_native import render_native

    produced = tmp_path / "from-windows.pdf"
    render_native(workbook, produced, ["BS", "Note 3-5"], draft=False)
    meta = {"engine": "excel", "engine_version": "Microsoft Excel 16.0", "excel_build": "16.0.19127"}
    res = finish_external_render(workbook, produced, tmp_path / "pub", mode="draft", config=CFG, run_id="R-W", engine_meta=meta)
    assert res.ok, res.message
    assert res.engine == "excel"
    assert "16.0.19127" in res.message
    doc = read_pdf(Path(res.pdf_path or ""))
    assert all("R-W" in " ".join(w.text for w in p.overlay_words) for p in doc.pages)
    assert any(t.startswith("Note 3") for _l, t, _p in doc.toc)
    refused = finish_external_render(workbook, produced, tmp_path / "pub", mode="final", config=CFG, check_result=_check_result(_high()))
    assert not refused.ok


def test_render_synthetic_workbook_when_available(tmp_path: Path) -> None:
    gen = pytest.importorskip("fscore.synthetic.generator")
    make = getattr(gen, "make_workbook", None)
    if make is None:
        pytest.skip("synthetic generator has no make_workbook yet")
    target = tmp_path / "synthetic.xlsx"
    try:
        made = make(target)
    except TypeError:
        pytest.skip("synthetic generator signature differs")
    path = made if isinstance(made, Path) else (Path(made) if isinstance(made, str) else target)
    if not path.exists():
        pytest.skip("synthetic generator did not write a workbook")
    res = render(path, tmp_path / "out", mode="draft", engine="native", config=EngagementConfig(), run_id="R-S")
    assert res.verification is not None
    assert res.verification.misses == [], res.message
