"""Windows system tray integration for the frozen launcher.

職責：在不新增第三方依賴的前提下，讓 Windows GUI EXE 可以常駐系統托盤，
並提供開啟 Web UI 與優雅結束 server 的最小操作。
"""

from __future__ import annotations

from collections.abc import Callable
import ctypes
from ctypes import wintypes
import logging
from pathlib import Path
import sys
import threading
import time
from typing import Any
from uuid import uuid4
import webbrowser

from facebook_monitor.core.defaults import PYTHON_NOTIFICATION_RUNTIME_DEFAULTS


logger = logging.getLogger(__name__)

TRAY_CALLBACK_MESSAGE = 0x8000 + 41
TRAY_ICON_ID = 1
MENU_OPEN_ID = 1001
MENU_EXIT_ID = 1002

WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_CONTEXTMENU = 0x007B

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NIIF_USER = 0x00000004
NIIF_LARGE_ICON = 0x00000020

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
NOTIFICATION_ICON_SIZE = 64

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080

_WinDLL = getattr(ctypes, "WinDLL", ctypes.CDLL)
_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_ACTIVE_TRAY_LOCK = threading.Lock()
_ACTIVE_TRAY_ICON: WindowsTrayIcon | None = None


class WindowsTrayNotificationError(Exception):
    """表示 Windows tray notification Win32 呼叫失敗。"""


def _raise_last_win_error() -> None:
    """拋出 Win32 last-error；非 Windows 型別檢查環境使用 OSError fallback。"""

    get_last_error = getattr(ctypes, "get_last_error", lambda: 0)
    win_error = getattr(ctypes, "WinError", OSError)
    raise win_error(get_last_error())


def _load_user32() -> Any:
    """載入 user32 並宣告本模組會用到的 Win32 API prototype。"""

    user32 = _WinDLL("user32", use_last_error=True)
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = wintypes.LPARAM
    user32.AppendMenuW.argtypes = [
        wintypes.HMENU,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.LPCWSTR,
    ]
    user32.AppendMenuW.restype = wintypes.BOOL
    user32.CreatePopupMenu.argtypes = []
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.DestroyMenu.restype = wintypes.BOOL
    user32.TrackPopupMenu.argtypes = [
        wintypes.HMENU,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        ctypes.c_void_p,
    ]
    user32.TrackPopupMenu.restype = wintypes.UINT
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.UnregisterClassW.restype = wintypes.BOOL
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.DefWindowProcW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.DefWindowProcW.restype = wintypes.LPARAM
    user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE,
        wintypes.LPCWSTR,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.DestroyIcon.restype = wintypes.BOOL
    return user32


def _load_shell32() -> Any:
    """載入 shell32 並宣告 Shell_NotifyIconW prototype。"""

    shell32 = _WinDLL("shell32", use_last_error=True)
    shell32.Shell_NotifyIconW.argtypes = [
        wintypes.DWORD,
        ctypes.POINTER(NOTIFYICONDATAW),
    ]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    return shell32


def _load_kernel32() -> Any:
    """載入 kernel32 並宣告 module handle API prototype。"""

    kernel32 = _WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
    return kernel32


class NOTIFYICONDATAW(ctypes.Structure):
    """Shell_NotifyIconW 使用的 icon data 結構。"""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HANDLE),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HANDLE),
    ]


WNDPROC = _WINFUNCTYPE(
    wintypes.LPARAM,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WNDCLASSW(ctypes.Structure):
    """RegisterClassW 使用的 hidden window class。"""

    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class WindowsTrayIcon:
    """管理 Windows tray icon message loop。"""

    def __init__(
        self,
        *,
        url: str,
        icon_path: Path | None,
        on_exit: Callable[[], None],
        register_active: bool = True,
    ) -> None:
        self._url = url
        self._icon_path = icon_path
        self._on_exit = on_exit
        self._register_active = register_active
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._hwnd: int | None = None
        self._icon_handle: int | None = None
        self._notification_icon_handle: int | None = None
        self._notify_data: NOTIFYICONDATAW | None = None
        self._wndproc: Any | None = None
        self._hinstance: int | None = None
        self._class_name: str | None = None
        self._class_registered = False
        self._stopping = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run_message_loop,
            name="facebook-monitor-windows-tray",
            daemon=True,
        )

    def start(self) -> None:
        """啟動 tray message loop；失敗時只記 log，不阻止 Web UI 啟動。"""

        if not is_windows_tray_supported():
            logger.warning("Windows tray requested on unsupported platform: %s", sys.platform)
            return
        self._thread.start()
        if not self._ready.wait(timeout=5):
            logger.warning("Windows tray icon did not become ready before timeout")
            return
        if self._register_active and self.is_ready:
            _set_active_tray_icon(self)

    def stop(self) -> None:
        """移除 tray icon 並停止 hidden window message loop。"""

        _clear_active_tray_icon(self)
        with self._lock:
            self._stopping = True
            hwnd = self._hwnd
        if hwnd:
            user32 = _load_user32()
            user32.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0)
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    @property
    def is_ready(self) -> bool:
        """回傳 tray icon 是否已成功加入 notification area。"""

        return bool(
            self._ready.is_set()
            and not self._stopping
            and self._hwnd
            and self._notify_data is not None
        )

    def show_notification(
        self,
        *,
        title: str,
        message: str,
        timeout_ms: int | None = None,
    ) -> None:
        """用目前 tray icon owner 發出 Windows shell notification。"""

        if not is_windows_tray_supported():
            raise WindowsTrayNotificationError("windows tray notification unsupported")
        with self._lock:
            if not self.is_ready:
                raise WindowsTrayNotificationError("windows tray notification icon is not ready")
            notify_data = build_windows_tray_notification_data(
                hwnd=int(self._hwnd or 0),
                icon_handle=int(self._notification_icon_handle or self._icon_handle or 0),
                title=title,
                message=message,
                timeout_ms=(
                    PYTHON_NOTIFICATION_RUNTIME_DEFAULTS.desktop_balloon_tip_milliseconds
                    if timeout_ms is None
                    else timeout_ms
                ),
            )
            shell32 = _load_shell32()
            if not shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(notify_data)):
                _raise_last_win_error()

    def _run_message_loop(self) -> None:
        try:
            self._create_window_and_icon()
            msg = wintypes.MSG()
            user32 = _load_user32()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            logger.exception("Windows tray icon failed")
            self._ready.set()
        finally:
            self._destroy_hidden_window()
            self._delete_tray_icon()
            self._unregister_window_class()
            self._stopped.set()

    def _create_window_and_icon(self) -> None:
        user32 = _load_user32()
        shell32 = _load_shell32()
        kernel32 = _load_kernel32()

        hinstance = kernel32.GetModuleHandleW(None)
        class_name = f"FacebookMonitorTrayWindow{uuid4().hex}"
        self._hinstance = int(hinstance or 0)
        self._class_name = class_name
        self._wndproc = WNDPROC(self._window_proc)
        window_class = WNDCLASSW(
            0,
            self._wndproc,
            0,
            0,
            hinstance,
            None,
            None,
            None,
            None,
            class_name,
        )
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            _raise_last_win_error()
        self._class_registered = True
        hwnd = user32.CreateWindowExW(
            0,
            class_name,
            "Facebook Monitor",
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            hinstance,
            None,
        )
        if not hwnd:
            _raise_last_win_error()
        self._hwnd = int(hwnd)
        icon_handle = self._load_icon_handle(user32, size=0)
        notification_icon_handle = self._load_icon_handle(
            user32,
            size=NOTIFICATION_ICON_SIZE,
        )
        self._icon_handle = icon_handle or None
        self._notification_icon_handle = notification_icon_handle or icon_handle or None
        notify_data = NOTIFYICONDATAW()
        notify_data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        notify_data.hWnd = hwnd
        notify_data.uID = TRAY_ICON_ID
        notify_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        notify_data.uCallbackMessage = TRAY_CALLBACK_MESSAGE
        notify_data.hIcon = icon_handle
        notify_data.szTip = "Facebook Monitor"
        self._notify_data = notify_data
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(notify_data)):
            _raise_last_win_error()
        self._ready.set()

    def _load_icon_handle(self, user32: ctypes.CDLL, *, size: int) -> int:
        """載入 icon handle；size=0 使用系統預設，非 0 用於高解析 notification。"""

        if self._icon_path is None:
            return 0
        icon_path = str(self._icon_path)
        requested_size = max(0, int(size))
        flags = LR_LOADFROMFILE
        if requested_size == 0:
            flags |= LR_DEFAULTSIZE
        return int(
            user32.LoadImageW(
                None,
                icon_path,
                IMAGE_ICON,
                requested_size,
                requested_size,
                flags,
            )
            or 0
        )

    def _window_proc(
        self,
        hwnd: int,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        user32 = _load_user32()
        if message == TRAY_CALLBACK_MESSAGE:
            if lparam in {WM_LBUTTONUP, WM_LBUTTONDBLCLK}:
                self._open_url()
                return 0
            if lparam in {WM_RBUTTONUP, WM_CONTEXTMENU}:
                self._show_context_menu(user32, hwnd)
                return 0
        if message == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            self._delete_tray_icon()
            user32.PostQuitMessage(0)
            return 0
        return int(user32.DefWindowProcW(hwnd, message, wparam, lparam))

    def _show_context_menu(self, user32: ctypes.CDLL, hwnd: int) -> None:
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        menu = user32.CreatePopupMenu()
        try:
            user32.AppendMenuW(
                menu,
                MF_STRING,
                MENU_OPEN_ID,
                "Open Facebook Monitor",
            )
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, MENU_EXIT_ID, "Exit")
            user32.SetForegroundWindow(hwnd)
            command = user32.TrackPopupMenu(
                menu,
                TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                point.x,
                point.y,
                0,
                hwnd,
                None,
            )
            if command == MENU_OPEN_ID:
                self._open_url()
            elif command == MENU_EXIT_ID:
                self._request_exit(user32, hwnd)
        finally:
            user32.DestroyMenu(menu)

    def _open_url(self) -> None:
        if not self._url:
            return
        try:
            webbrowser.open(self._url)
        except Exception:
            logger.exception("Failed to open Web UI from tray icon")

    def _request_exit(self, user32: ctypes.CDLL, hwnd: int) -> None:
        try:
            self._on_exit()
        except Exception:
            logger.exception("Tray exit callback failed")
        user32.DestroyWindow(hwnd)

    def _destroy_hidden_window(self) -> None:
        """確保建立途中失敗時，hidden window 也會被釋放。"""

        with self._lock:
            hwnd = self._hwnd
        if hwnd:
            user32 = _load_user32()
            if not user32.DestroyWindow(wintypes.HWND(hwnd)):
                logger.warning("Failed to destroy Windows tray hidden window")

    def _delete_tray_icon(self) -> None:
        _clear_active_tray_icon(self)
        with self._lock:
            self._stopping = True
            notify_data = self._notify_data
            icon_handle = self._icon_handle
            notification_icon_handle = self._notification_icon_handle
            self._notify_data = None
            self._icon_handle = None
            self._notification_icon_handle = None
            self._hwnd = None
        user32 = _load_user32()
        if notify_data is not None:
            shell32 = _load_shell32()
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(notify_data))
        if icon_handle:
            user32.DestroyIcon(wintypes.HICON(icon_handle))
        if notification_icon_handle and notification_icon_handle != icon_handle:
            user32.DestroyIcon(wintypes.HICON(notification_icon_handle))

    def _unregister_window_class(self) -> None:
        """message loop 結束後才解除 window class 註冊。"""

        with self._lock:
            class_name = self._class_name
            hinstance = self._hinstance
            class_registered = self._class_registered
            self._class_name = None
            self._hinstance = None
            self._class_registered = False
        if not (class_registered and class_name and hinstance):
            return
        user32 = _load_user32()
        if not user32.UnregisterClassW(class_name, wintypes.HINSTANCE(hinstance)):
            get_last_error = getattr(ctypes, "get_last_error", lambda: 0)
            logger.warning(
                "Failed to unregister Windows tray window class: %s",
                get_last_error(),
            )


def build_windows_tray_notification_data(
    *,
    hwnd: int,
    icon_handle: int,
    title: str,
    message: str,
    timeout_ms: int,
) -> NOTIFYICONDATAW:
    """建立 `Shell_NotifyIconW(NIM_MODIFY)` 使用的 notification payload。"""

    notify_data = NOTIFYICONDATAW()
    notify_data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    notify_data.hWnd = wintypes.HWND(hwnd)
    notify_data.uID = TRAY_ICON_ID
    notify_data.uFlags = NIF_INFO
    notify_data.szInfoTitle = _windows_notify_text(title, max_chars=63)
    notify_data.szInfo = _windows_notify_text(message, max_chars=255)
    notify_data.uTimeoutOrVersion = max(0, int(timeout_ms))
    if icon_handle:
        notify_data.dwInfoFlags = NIIF_USER | NIIF_LARGE_ICON
        notify_data.hBalloonIcon = wintypes.HANDLE(icon_handle)
    return notify_data


def show_windows_tray_notification(
    *,
    title: str,
    message: str,
    icon_path: Path | None = None,
    cleanup_sleep_ms: int | None = None,
) -> None:
    """由目前 `facebook-monitor.exe` process 送出 Windows tray notification。"""

    active_tray_icon = _get_active_tray_icon()
    if active_tray_icon is not None:
        active_tray_icon.show_notification(title=title, message=message)
        return

    transient_icon = WindowsTrayIcon(
        url="",
        icon_path=icon_path,
        on_exit=lambda: None,
        register_active=False,
    )
    transient_icon.start()
    try:
        transient_icon.show_notification(title=title, message=message)
        time.sleep(
            max(
                0,
                PYTHON_NOTIFICATION_RUNTIME_DEFAULTS.desktop_cleanup_sleep_milliseconds
                if cleanup_sleep_ms is None
                else cleanup_sleep_ms,
            )
            / 1000
        )
    finally:
        transient_icon.stop()


def is_windows_tray_supported() -> bool:
    """回傳目前平台是否支援 Windows tray。"""

    return sys.platform == "win32"


def start_windows_tray_icon(
    *,
    url: str,
    icon_path: Path | None,
    on_exit: Callable[[], None],
) -> WindowsTrayIcon:
    """建立並啟動 Windows tray icon。"""

    tray_icon = WindowsTrayIcon(url=url, icon_path=icon_path, on_exit=on_exit)
    tray_icon.start()
    return tray_icon


def _windows_notify_text(value: str, *, max_chars: int) -> str:
    """限制 Shell notification 文字長度，避免 ctypes 寫入固定 buffer 失敗。"""

    text = str(value or "")
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3] + "..."


def _get_active_tray_icon() -> WindowsTrayIcon | None:
    """回傳目前 launcher 持有的 tray icon；若已停止則回 None。"""

    with _ACTIVE_TRAY_LOCK:
        if _ACTIVE_TRAY_ICON is not None and _ACTIVE_TRAY_ICON.is_ready:
            return _ACTIVE_TRAY_ICON
    return None


def _set_active_tray_icon(tray_icon: WindowsTrayIcon) -> None:
    """登記目前 process 的正式 tray icon，供 desktop sender 重用。"""

    global _ACTIVE_TRAY_ICON
    with _ACTIVE_TRAY_LOCK:
        _ACTIVE_TRAY_ICON = tray_icon


def _clear_active_tray_icon(tray_icon: WindowsTrayIcon) -> None:
    """清除已停止的正式 tray icon 登記。"""

    global _ACTIVE_TRAY_ICON
    with _ACTIVE_TRAY_LOCK:
        if _ACTIVE_TRAY_ICON is tray_icon:
            _ACTIVE_TRAY_ICON = None
