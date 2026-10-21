"""Tray icon for the desktop runtime (Windows, pywin32): Open FS-Platform · Back up now · Open data folder · Quit.

The message loop runs on the main thread; the server runs in background threads (:mod:`fsoffline.desktop`). The icon
comes back when Explorer restarts, and signing out of Windows stops the server cleanly.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import threading
from typing import Any

import win32api  # type: ignore[import-not-found]
import win32con  # type: ignore[import-not-found]
import win32gui  # type: ignore[import-not-found]

from fsoffline.install import load_install_config
from fsoffline.settings import OfflineSettings

log = logging.getLogger("fsoffline.tray")

ID_OPEN, ID_BACKUP, ID_FOLDER, ID_QUIT = 1001, 1002, 1003, 1004
WM_TRAY = win32con.WM_USER + 20
WM_NOTIFY_PENDING = win32con.WM_USER + 21
CLASS_NAME = "FSPlatformTray"


class Tray:  # pragma: no cover - Windows desktop only
    def __init__(self, settings: OfflineSettings) -> None:
        self.settings = settings
        self.hwnd: int | None = None
        self.runtime: Any = None
        self._pending: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
        self._taskbar_created = win32gui.RegisterWindowMessage("TaskbarCreated")
        icon = load_install_config().icon
        self.icon_path = str(icon) if icon is not None and icon.is_file() else None

    # ---- lifecycle
    def run(self, runtime: Any) -> None:
        self.runtime = runtime
        hinst = win32api.GetModuleHandle(None)
        wc = win32gui.WNDCLASS()
        wc.hInstance = hinst
        wc.lpszClassName = CLASS_NAME
        wc.lpfnWndProc = {
            self._taskbar_created: self._on_taskbar_created,
            WM_TRAY: self._on_tray,
            WM_NOTIFY_PENDING: self._on_notify_pending,
            win32con.WM_COMMAND: self._on_command,
            win32con.WM_CLOSE: self._on_close,
            win32con.WM_DESTROY: self._on_destroy,
            win32con.WM_QUERYENDSESSION: lambda *_: True,
            win32con.WM_ENDSESSION: self._on_end_session,
        }
        atom = win32gui.RegisterClass(wc)
        self.hwnd = win32gui.CreateWindow(atom, "FS-Platform", win32con.WS_OVERLAPPED | win32con.WS_SYSMENU, 0, 0,
                                          win32con.CW_USEDEFAULT, win32con.CW_USEDEFAULT, 0, 0, hinst, None)
        win32gui.UpdateWindow(self.hwnd)
        self._add_icon()
        win32gui.PostMessage(self.hwnd, WM_NOTIFY_PENDING, 0, 0)  # messages queued while the server was starting
        win32gui.PumpMessages()

    def close(self) -> None:
        """Remove the icon and end the message loop (callable from any thread)."""
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)

    def notify(self, title: str, text: str) -> None:
        """A balloon notification (callable from any thread, also before the icon exists)."""
        log.info("%s: %s", title, text)
        self._pending.put((title, text))
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, WM_NOTIFY_PENDING, 0, 0)

    # ---- icon and menu
    def _icon(self) -> Any:
        if self.icon_path:
            try:
                return win32gui.LoadImage(0, self.icon_path, win32con.IMAGE_ICON, 0, 0, win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
            except win32gui.error:
                log.warning("could not load the tray icon from %s", self.icon_path)
        return win32gui.LoadIcon(0, win32con.IDI_APPLICATION)

    def _add_icon(self) -> None:
        nid = (self.hwnd, 0, win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP, WM_TRAY, self._icon(), "FS-Platform")
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)
        except win32gui.error:
            log.warning("the notification area is not available yet; the icon appears when Explorer starts")

    def _menu(self) -> None:
        menu = win32gui.CreatePopupMenu()
        win32gui.AppendMenu(menu, win32con.MF_STRING, ID_OPEN, "Open FS-Platform")
        win32gui.AppendMenu(menu, win32con.MF_STRING, ID_BACKUP, "Back up now")
        win32gui.AppendMenu(menu, win32con.MF_STRING, ID_FOLDER, "Open data folder")
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(menu, win32con.MF_STRING, ID_QUIT, "Quit FS-Platform")
        win32gui.SetMenuDefaultItem(menu, ID_OPEN, False)
        x, y = win32gui.GetCursorPos()
        win32gui.SetForegroundWindow(self.hwnd)
        win32gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON, x, y, 0, self.hwnd, None)
        win32gui.PostMessage(self.hwnd, win32con.WM_NULL, 0, 0)

    # ---- window procedure handlers
    def _on_taskbar_created(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        self._add_icon()  # Explorer restarted
        return 0

    def _on_tray(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if lparam == win32con.WM_LBUTTONDBLCLK:
            self.runtime.open_browser_now()
        elif lparam in (win32con.WM_RBUTTONUP, win32con.WM_LBUTTONUP):
            self._menu()
        return 1

    def _on_command(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        cmd = win32api.LOWORD(wparam)
        if cmd == ID_OPEN:
            self.runtime.open_browser_now()
        elif cmd == ID_BACKUP:
            self.notify("FS-Platform backup", "Backing up…")
            threading.Thread(target=self.runtime.backup_now, kwargs={"reason": "manual"}, name="fsoffline-backup-now", daemon=True).start()
        elif cmd == ID_FOLDER:
            os.startfile(self.settings.data_dir)  # type: ignore[attr-defined]  # noqa: S606 - opens the folder in Explorer
        elif cmd == ID_QUIT:
            threading.Thread(target=self.runtime.shutdown, name="fsoffline-quit", daemon=True).start()  # then close() via on_shutdown
        return 0

    def _on_notify_pending(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        while not self._pending.empty():
            title, text = self._pending.get()
            nid = (self.hwnd, 0, win32gui.NIF_INFO, WM_TRAY, self._icon(), "FS-Platform", text[:255], 10000, title[:63], win32gui.NIIF_INFO)
            with contextlib.suppress(win32gui.error):  # already in the log
                win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, nid)
        return 0

    def _on_end_session(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if wparam:  # Windows is signing out or shutting down: stop the server cleanly now
            self.runtime.shutdown()
        return 0

    def _on_close(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        win32gui.DestroyWindow(hwnd)
        return 0

    def _on_destroy(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        with contextlib.suppress(win32gui.error):
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (hwnd, 0))
        win32gui.PostQuitMessage(0)
        return 0
