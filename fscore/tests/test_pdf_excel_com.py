"""Excel COM engine with a fake COM object (runs on any OS)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from fscore.pdf import render_excel_com as xl


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
        doc = pymupdf.open()
        for name in [c[1] for c in self.app.calls if c[0] == "Select"]:
            doc.new_page().insert_text((72, 72), name)
        doc.save(kw["Filename"])
        doc.close()


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
