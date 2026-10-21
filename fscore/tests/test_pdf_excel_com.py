"""Excel COM engine with a fake COM object (runs on any OS)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from helpers_pdf import make_workbook
from reportlab.pdfgen import canvas

from fscore.config import EngagementConfig
from fscore.pdf import render_excel_com as xl
from fscore.pdf import render_libreoffice
from fscore.pdf.render import fallback_engines, render


class FakeSheet:
    def __init__(self, app: FakeApp, name: str, visible: int = -1) -> None:
        self.app, self.Name, self.Visible = app, name, visible

    def Select(self, replace: bool = True) -> None:
        self.app.calls.append(("Select", self.Name, replace))
        self.app.active = self

    def ExportAsFixedFormat(self, **kw: Any) -> None:
        self.app.calls.append(("ExportAsFixedFormat", kw))
        if self.app.export_delay:
            time.sleep(self.app.export_delay)
        pdf = canvas.Canvas(kw["Filename"])
        for name in [c[1] for c in self.app.calls if c[0] == "Select"]:
            pdf.drawString(72, 720, name)
            pdf.showPage()
        pdf.save()


class FakeSheets:
    def __init__(self, app: FakeApp, names: list[str]) -> None:
        self.items = [FakeSheet(app, n, -1 if n != "TB" else 0) for n in names]

    def __call__(self, name: str) -> FakeSheet:
        return next(s for s in self.items if s.Name == name)

    def __iter__(self):
        return iter(self.items)


class FakeWorkbook:
    def __init__(self, app: FakeApp) -> None:
        self.app = app
        self.Worksheets = FakeSheets(app, ["BS", "PL", "TB"])

    def Close(self, SaveChanges: bool) -> None:
        self.app.calls.append(("Close", SaveChanges))


class FakeWorkbooks:
    def __init__(self, app: FakeApp) -> None:
        self.app = app

    def Open(self, *args: Any) -> FakeWorkbook:
        self.app.calls.append(("Open", args))
        return FakeWorkbook(self.app)


class FakeApp:
    Version = "16.0"
    Build = 19127
    Hwnd = 1

    def __init__(self, export_delay: float = 0.0) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.export_delay = export_delay
        self.Workbooks = FakeWorkbooks(self)
        self.active: FakeSheet | None = None
        self.settings: dict[str, Any] = {}

    def __setattr__(self, key: str, value: Any) -> None:
        if key in ("Visible", "DisplayAlerts", "ScreenUpdating", "EnableEvents", "AskToUpdateLinks", "AutomationSecurity"):
            self.settings[key] = value
            self.calls.append(("set", key, value))
            return
        object.__setattr__(self, key, value)

    @property
    def ActiveSheet(self) -> FakeSheet:
        assert self.active is not None
        return self.active

    def CalculateFull(self) -> None:
        self.calls.append(("CalculateFull",))

    def Quit(self) -> None:
        self.calls.append(("Quit",))


def test_export_call_order_and_settings(tmp_path: Path) -> None:
    app = FakeApp()
    killed: list[int] = []
    out = xl.export_pdf(
        tmp_path / "in.xlsx", tmp_path / "out.pdf", ["BS", "PL"], 30,
        _dispatch=lambda: app, _pid_of=lambda _a: 4242, _kill=killed.append, _alive=lambda _p: False,
    )
    assert out == {"engine": "excel", "engine_version": "Microsoft Excel 16.0", "excel_build": "16.0.19127", "pages": 2}
    assert app.settings["DisplayAlerts"] is False
    assert app.settings["EnableEvents"] is False
    assert app.settings["AutomationSecurity"] == xl.MSO_AUTOMATION_SECURITY_FORCE_DISABLE
    names = [c[0] for c in app.calls if c[0] != "set"]
    assert names == ["Open", "CalculateFull", "Select", "Select", "ExportAsFixedFormat", "Close", "Quit"]
    open_args = next(c[1] for c in app.calls if c[0] == "Open")
    assert open_args[1] == 0
    assert open_args[2] is True
    assert ("Select", "BS", True) in app.calls
    assert ("Select", "PL", False) in app.calls
    export_kw = next(c[1] for c in app.calls if c[0] == "ExportAsFixedFormat")
    assert export_kw["Type"] == 0
    assert export_kw["IgnorePrintAreas"] is False
    assert export_kw["OpenAfterPublish"] is False
    assert ("Close", False) in app.calls  # never saved
    assert killed == []


def test_default_sheets_are_the_visible_ones(tmp_path: Path) -> None:
    app = FakeApp()
    xl.export_pdf(tmp_path / "in.xlsx", tmp_path / "o.pdf", None, 30, _dispatch=lambda: app, _pid_of=lambda _a: 1, _kill=lambda _p: None, _alive=lambda _p: False)
    selected = [c[1] for c in app.calls if c[0] == "Select"]
    assert selected == ["BS", "PL"]  # TB is hidden (Visible = 0)


def test_watchdog_kills_only_its_own_excel(tmp_path: Path) -> None:
    app = FakeApp(export_delay=0.6)
    killed: list[int] = []
    with pytest.raises(xl.ExcelComError, match="timed out"):
        xl.export_pdf(
            tmp_path / "in.xlsx", tmp_path / "slow.pdf", ["BS"], 0.1,  # type: ignore[arg-type]
            _dispatch=lambda: app, _pid_of=lambda _a: 4242, _kill=killed.append, _alive=lambda _p: True,
        )
    assert killed
    assert set(killed) == {4242}
    assert ("Close", False) in app.calls
    assert ("Quit",) in app.calls


def test_not_available_off_windows(tmp_path: Path) -> None:
    if xl.available():
        pytest.skip("running on Windows with pywin32")
    with pytest.raises(xl.ExcelComError, match="Windows"):
        xl.export_pdf(tmp_path / "a.xlsx", tmp_path / "a.pdf", None)


class FakeComError(Exception):
    """Stands in for pywintypes.com_error (not importable off Windows)."""


@pytest.fixture
def excel_probe(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pretend to be on Windows; the registry and pywin32 probes are patched per test."""
    xl._excel_registered.cache_clear()
    monkeypatch.setattr(xl, "_on_windows", lambda: True)
    monkeypatch.setattr(xl, "_pywin32_loads", lambda: True)
    yield
    xl._excel_registered.cache_clear()


@pytest.mark.usefixtures("excel_probe")
def test_not_available_when_excel_is_not_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xl, "_registered_clsid", lambda _prog_id: None)
    assert not xl.available()


@pytest.mark.usefixtures("excel_probe")
def test_available_when_excel_is_registered_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    probes: list[str] = []

    def clsid(prog_id: str) -> str:
        probes.append(prog_id)
        return "{00024500-0000-0000-C000-000000000046}"

    monkeypatch.setattr(xl, "_registered_clsid", clsid)
    assert xl.available()
    assert xl.available()
    assert probes == ["Excel.Application"]  # looked up once per process


@pytest.mark.usefixtures("excel_probe")
def test_not_available_when_pywin32_is_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xl, "_registered_clsid", lambda _prog_id: "{00024500-0000-0000-C000-000000000046}")
    monkeypatch.setattr(xl, "_pywin32_loads", lambda: False)
    assert not xl.available()


@pytest.mark.parametrize("error", [FakeComError(-2147221005, "Invalid class string", None, None), OSError("access denied")])
def test_dispatch_failure_is_an_excel_error(tmp_path: Path, error: Exception) -> None:
    def dispatch() -> Any:
        raise error

    with pytest.raises(xl.ExcelComError, match="could not be started"):
        xl.export_pdf(tmp_path / "in.xlsx", tmp_path / "o.pdf", ["BS"], 30, _dispatch=dispatch,
                      _pid_of=lambda _a: 1, _kill=lambda _p: None, _alive=lambda _p: False)


def test_com_error_while_driving_excel_is_an_excel_error(tmp_path: Path) -> None:
    app = FakeApp()

    def broken_open(*_args: Any) -> Any:
        raise FakeComError(-2147352567, "Exception occurred.", None, None)

    app.Workbooks.Open = broken_open  # type: ignore[method-assign]
    with pytest.raises(xl.ExcelComError, match="Excel export failed"):
        xl.export_pdf(tmp_path / "in.xlsx", tmp_path / "o.pdf", ["BS"], 30, _dispatch=lambda: app,
                      _pid_of=lambda _a: 7, _kill=lambda _p: None, _alive=lambda _p: False)
    assert ("Quit",) in app.calls


def _excel_that_cannot_start(monkeypatch: pytest.MonkeyPatch) -> None:
    def dispatch() -> Any:
        raise FakeComError(-2147221005, "Invalid class string", None, None)

    monkeypatch.setattr(xl, "available", lambda: True)
    monkeypatch.setattr(xl, "_default_dispatch", dispatch)


def test_auto_falls_back_to_the_builtin_renderer_when_excel_cannot_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _excel_that_cannot_start(monkeypatch)
    monkeypatch.setattr(render_libreoffice, "available", lambda: False)
    cfg = EngagementConfig(client="Corvus")
    assert fallback_engines("excel", "auto", "draft", cfg) == ["native"]
    assert fallback_engines("excel", "excel", "draft", cfg) == []  # an explicit engine never falls back
    res = render(make_workbook(tmp_path / "wb.xlsx"), tmp_path / "out", mode="draft", engine="auto", config=cfg, run_id="R-F")
    assert res.ok, res.message
    assert res.engine == "native"
    assert "Excel failed" in res.message and "built-in renderer" in res.message
    explicit = render(make_workbook(tmp_path / "wb2.xlsx"), tmp_path / "out2", mode="draft", engine="excel", config=cfg)
    assert not explicit.ok
    assert "could not be started" in explicit.message


def test_final_auto_does_not_fall_back_to_native_without_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FSCORE_ALLOW_NATIVE_FINAL", raising=False)
    _excel_that_cannot_start(monkeypatch)
    monkeypatch.setattr(render_libreoffice, "available", lambda: False)
    assert fallback_engines("excel", "auto", "final", EngagementConfig()) == []


@pytest.mark.libreoffice
def test_auto_falls_back_to_libreoffice_when_excel_cannot_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not render_libreoffice.available():
        pytest.skip("LibreOffice (soffice) not installed")
    _excel_that_cannot_start(monkeypatch)
    res = render(make_workbook(tmp_path / "wb.xlsx"), tmp_path / "out", mode="draft", engine="auto",
                 config=EngagementConfig(client="Corvus"), run_id="R-LF")
    assert res.ok, res.message
    assert res.engine == "libreoffice"
    assert "Excel failed" in res.message
