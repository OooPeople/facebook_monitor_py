"""Windows tray integration characterization tests。"""

from __future__ import annotations

import importlib
import sys
from ctypes import wintypes
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows tray uses Win32 APIs")


def test_windows_tray_declares_pointer_sized_win32_prototypes() -> None:
    """Win32 handle-returning APIs must not fall back to ctypes c_int defaults。"""

    windows_tray = importlib.import_module("facebook_monitor.runtime.windows_tray")

    user32 = windows_tray._load_user32()
    kernel32 = windows_tray._load_kernel32()

    assert user32.CreateWindowExW.restype is wintypes.HWND
    assert user32.RegisterClassW.restype is wintypes.ATOM
    assert user32.GetMessageW.restype is wintypes.BOOL
    assert user32.DispatchMessageW.restype is wintypes.LPARAM
    assert user32.DestroyIcon.restype is wintypes.BOOL
    assert kernel32.GetModuleHandleW.restype is wintypes.HINSTANCE


def test_windows_tray_menu_dispatches_open_and_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """右鍵選單要保留文字，並依選擇 dispatch open / exit。"""

    windows_tray = importlib.import_module("facebook_monitor.runtime.windows_tray")
    opened_urls: list[str] = []
    exit_calls: list[str] = []

    class FakeUser32:
        def __init__(self, command: int) -> None:
            self.command = command
            self.menu_labels: list[str | None] = []
            self.destroyed_windows: list[int] = []

        def GetCursorPos(self, point: object) -> bool:
            return True

        def CreatePopupMenu(self) -> int:
            return 101

        def AppendMenuW(
            self,
            menu: int,
            flags: int,
            item_id: int,
            label: str | None,
        ) -> bool:
            self.menu_labels.append(label)
            return True

        def SetForegroundWindow(self, hwnd: int) -> bool:
            return True

        def TrackPopupMenu(self, *args: object) -> int:
            return self.command

        def DestroyMenu(self, menu: int) -> bool:
            return True

        def DestroyWindow(self, hwnd: int) -> bool:
            self.destroyed_windows.append(hwnd)
            return True

    monkeypatch.setattr(windows_tray.webbrowser, "open", opened_urls.append)
    open_user32 = FakeUser32(windows_tray.MENU_OPEN_ID)
    tray = windows_tray.WindowsTrayIcon(
        url="http://127.0.0.1:4818",
        icon_path=None,
        on_exit=lambda: exit_calls.append("exit"),
    )

    tray._show_context_menu(open_user32, hwnd=99)

    assert open_user32.menu_labels == ["Open Facebook Monitor", None, "Exit"]
    assert opened_urls == ["http://127.0.0.1:4818"]
    assert exit_calls == []

    exit_user32 = FakeUser32(windows_tray.MENU_EXIT_ID)
    tray._show_context_menu(exit_user32, hwnd=99)

    assert exit_calls == ["exit"]
    assert exit_user32.destroyed_windows == [99]


def test_windows_tray_delete_releases_shell_icon_and_loaded_icon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """移除 tray icon 時，也要釋放 LoadImageW 載入的 icon handle。"""

    windows_tray = importlib.import_module("facebook_monitor.runtime.windows_tray")
    shell_calls: list[int] = []
    destroyed_icons: list[Any] = []

    class FakeShell32:
        def Shell_NotifyIconW(self, action: int, data: object) -> bool:
            shell_calls.append(action)
            return True

    class FakeUser32:
        def DestroyIcon(self, icon: object) -> bool:
            destroyed_icons.append(icon)
            return True

    monkeypatch.setattr(windows_tray, "_load_shell32", lambda: FakeShell32())
    monkeypatch.setattr(windows_tray, "_load_user32", lambda: FakeUser32())
    tray = windows_tray.WindowsTrayIcon(
        url="http://127.0.0.1:4818",
        icon_path=None,
        on_exit=lambda: None,
    )
    tray._notify_data = windows_tray.NOTIFYICONDATAW()
    tray._icon_handle = 123

    tray._delete_tray_icon()

    assert shell_calls == [windows_tray.NIM_DELETE]
    assert destroyed_icons
    assert tray._notify_data is None
    assert tray._icon_handle is None
