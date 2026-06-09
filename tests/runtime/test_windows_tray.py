"""Windows tray integration characterization tests。"""

from __future__ import annotations

import importlib
from pathlib import Path
import sys
from ctypes import wintypes
from typing import Any
from typing import cast

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
    assert user32.UnregisterClassW.restype is wintypes.BOOL
    assert kernel32.GetModuleHandleW.restype is wintypes.HINSTANCE


def test_windows_tray_builds_notification_data_with_custom_icon() -> None:
    """notification payload 要使用自訂 icon handle，而不是系統 information icon。"""

    windows_tray = importlib.import_module("facebook_monitor.runtime.windows_tray")

    notify_data = windows_tray.build_windows_tray_notification_data(
        hwnd=99,
        icon_handle=123,
        title="T" * 80,
        message="M" * 300,
        timeout_ms=5000,
    )

    assert notify_data.hWnd == 99
    assert notify_data.uID == windows_tray.TRAY_ICON_ID
    assert notify_data.uFlags == windows_tray.NIF_INFO
    assert notify_data.dwInfoFlags & windows_tray.NIIF_USER
    assert notify_data.dwInfoFlags & windows_tray.NIIF_LARGE_ICON
    assert notify_data.hBalloonIcon == 123
    assert len(notify_data.szInfoTitle) == 63
    assert notify_data.szInfoTitle.endswith("...")
    assert len(notify_data.szInfo) == 255
    assert notify_data.szInfo.endswith("...")


def test_windows_tray_show_notification_modifies_existing_shell_icon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """發通知時應用既有 tray hWnd/uID 做 NIM_MODIFY 並帶高解析 hBalloonIcon。"""

    windows_tray = importlib.import_module("facebook_monitor.runtime.windows_tray")
    shell_calls: list[tuple[int, Any]] = []

    class FakeShell32:
        def Shell_NotifyIconW(self, action: int, data: object) -> bool:
            shell_calls.append((action, getattr(data, "_obj")))
            return True

    monkeypatch.setattr(windows_tray, "_load_shell32", lambda: FakeShell32())
    tray = windows_tray.WindowsTrayIcon(
        url="http://127.0.0.1:4818",
        icon_path=None,
        on_exit=lambda: None,
    )
    tray._hwnd = 99
    tray._icon_handle = 123
    tray._notification_icon_handle = 456
    tray._notify_data = windows_tray.NOTIFYICONDATAW()
    tray._ready.set()

    tray.show_notification(title="Facebook Monitor", message="社團: 測試")

    action, data = shell_calls[0]
    assert action == windows_tray.NIM_MODIFY
    assert data.hWnd == 99
    assert data.hBalloonIcon == 456
    assert data.szInfoTitle == "Facebook Monitor"
    assert data.szInfo == "社團: 測試"


def test_windows_tray_loads_64px_notification_icon(tmp_path: Path) -> None:
    """notification icon 要從 ICO 載入 64px handle，避免使用 tray 預設小圖。"""

    windows_tray = importlib.import_module("facebook_monitor.runtime.windows_tray")
    icon_path = tmp_path / "facebook-monitor-tray.ico"
    icon_path.write_bytes(b"ico")
    load_calls: list[tuple[int, int, int]] = []

    class FakeUser32:
        def LoadImageW(
            self,
            hinstance: object,
            path: str,
            image_type: int,
            width: int,
            height: int,
            flags: int,
        ) -> int:
            assert path == str(icon_path)
            assert image_type == windows_tray.IMAGE_ICON
            load_calls.append((width, height, flags))
            return len(load_calls)

    tray = windows_tray.WindowsTrayIcon(
        url="http://127.0.0.1:4818",
        icon_path=icon_path,
        on_exit=lambda: None,
    )

    tray_icon = tray._load_icon_handle(cast(Any, FakeUser32()), size=0)
    notification_icon = tray._load_icon_handle(
        cast(Any, FakeUser32()),
        size=windows_tray.NOTIFICATION_ICON_SIZE,
    )

    assert tray_icon == 1
    assert notification_icon == 2
    assert load_calls == [
        (0, 0, windows_tray.LR_LOADFROMFILE | windows_tray.LR_DEFAULTSIZE),
        (
            windows_tray.NOTIFICATION_ICON_SIZE,
            windows_tray.NOTIFICATION_ICON_SIZE,
            windows_tray.LR_LOADFROMFILE,
        ),
    ]


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


def test_windows_tray_active_registry_only_returns_ready_icons() -> None:
    """active tray registry 只回傳仍可發通知的正式 tray icon。"""

    windows_tray = importlib.import_module("facebook_monitor.runtime.windows_tray")
    tray = windows_tray.WindowsTrayIcon(
        url="http://127.0.0.1:4818",
        icon_path=None,
        on_exit=lambda: None,
    )
    tray._hwnd = 99
    tray._notify_data = windows_tray.NOTIFYICONDATAW()
    tray._ready.set()

    windows_tray._set_active_tray_icon(tray)

    assert windows_tray._get_active_tray_icon() is tray

    tray._stopping = True

    assert windows_tray._get_active_tray_icon() is None

    windows_tray._clear_active_tray_icon(tray)


def test_windows_tray_transient_notification_does_not_register_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一次性 notification owner 不應覆蓋正式 launcher tray icon。"""

    windows_tray = importlib.import_module("facebook_monitor.runtime.windows_tray")
    created_register_active: list[bool] = []
    shown_notifications: list[tuple[str, str]] = []
    stopped = False

    class FakeTrayIcon:
        def __init__(
            self,
            *,
            url: str,
            icon_path: object,
            on_exit: object,
            register_active: bool = True,
        ) -> None:
            assert url == ""
            created_register_active.append(register_active)

        def start(self) -> None:
            return None

        def show_notification(self, *, title: str, message: str) -> None:
            shown_notifications.append((title, message))

        def stop(self) -> None:
            nonlocal stopped
            stopped = True

    monkeypatch.setattr(windows_tray, "_get_active_tray_icon", lambda: None)
    monkeypatch.setattr(windows_tray, "WindowsTrayIcon", FakeTrayIcon)
    monkeypatch.setattr(windows_tray.time, "sleep", lambda _seconds: None)

    windows_tray.show_windows_tray_notification(
        title="Facebook Monitor",
        message="社團: 測試",
        cleanup_sleep_ms=0,
    )

    assert created_register_active == [False]
    assert shown_notifications == [("Facebook Monitor", "社團: 測試")]
    assert stopped is True


def test_windows_tray_delete_releases_shell_icon_and_loaded_icon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """移除 tray icon 時釋放 shell icon 與 icon handle，class 交給 loop finalizer。"""

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
    tray._notification_icon_handle = 456
    tray._class_name = "FacebookMonitorTrayWindowTest"
    tray._hinstance = 456

    tray._delete_tray_icon()

    assert shell_calls == [windows_tray.NIM_DELETE]
    assert [getattr(icon, "value") for icon in destroyed_icons] == [123, 456]
    assert tray._notify_data is None
    assert tray._icon_handle is None
    assert tray._notification_icon_handle is None
    assert tray._class_name == "FacebookMonitorTrayWindowTest"
    assert tray._hinstance == 456


def test_windows_tray_unregisters_window_class_after_message_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """message loop 結束後才 unregister window class，避免視窗尚未釋放。"""

    windows_tray = importlib.import_module("facebook_monitor.runtime.windows_tray")
    unregistered_classes: list[tuple[str, object]] = []

    class FakeUser32:
        def UnregisterClassW(self, class_name: str, hinstance: object) -> bool:
            unregistered_classes.append((class_name, hinstance))
            return True

    monkeypatch.setattr(windows_tray, "_load_user32", lambda: FakeUser32())
    tray = windows_tray.WindowsTrayIcon(
        url="http://127.0.0.1:4818",
        icon_path=None,
        on_exit=lambda: None,
    )
    tray._class_name = "FacebookMonitorTrayWindowTest"
    tray._hinstance = 456
    tray._class_registered = True

    tray._unregister_window_class()

    assert len(unregistered_classes) == 1
    assert unregistered_classes[0][0] == "FacebookMonitorTrayWindowTest"
    assert getattr(unregistered_classes[0][1], "value") == 456
    assert tray._class_name is None
    assert tray._hinstance is None
    assert tray._class_registered is False
