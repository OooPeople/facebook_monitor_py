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
from typing import Any
import webbrowser


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
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080


def _load_user32() -> Any:
    """載入 user32 並宣告本模組會用到的 Win32 API prototype。"""

    user32 = ctypes.WinDLL("user32", use_last_error=True)
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

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.Shell_NotifyIconW.argtypes = [
        wintypes.DWORD,
        ctypes.POINTER(NOTIFYICONDATAW),
    ]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    return shell32


def _load_kernel32() -> Any:
    """載入 kernel32 並宣告 module handle API prototype。"""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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


WNDPROC = ctypes.WINFUNCTYPE(
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
    ) -> None:
        self._url = url
        self._icon_path = icon_path
        self._on_exit = on_exit
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._hwnd: int | None = None
        self._icon_handle: int | None = None
        self._notify_data: NOTIFYICONDATAW | None = None
        self._wndproc: Any | None = None
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

    def stop(self) -> None:
        """移除 tray icon 並停止 hidden window message loop。"""

        if self._hwnd:
            user32 = _load_user32()
            user32.PostMessageW(wintypes.HWND(self._hwnd), WM_CLOSE, 0, 0)
        self._thread.join(timeout=5)

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
            self._stopped.set()

    def _create_window_and_icon(self) -> None:
        user32 = _load_user32()
        shell32 = _load_shell32()
        kernel32 = _load_kernel32()

        hinstance = kernel32.GetModuleHandleW(None)
        class_name = "FacebookMonitorTrayWindow"
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
        user32.RegisterClassW(ctypes.byref(window_class))
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
            raise ctypes.WinError(ctypes.get_last_error())
        self._hwnd = int(hwnd)
        icon_handle = self._load_icon_handle(user32)
        self._icon_handle = icon_handle or None
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
            raise ctypes.WinError(ctypes.get_last_error())
        self._ready.set()

    def _load_icon_handle(self, user32: ctypes.CDLL) -> int:
        if self._icon_path is None:
            return 0
        icon_path = str(self._icon_path)
        return int(
            user32.LoadImageW(
                None,
                icon_path,
                IMAGE_ICON,
                0,
                0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE,
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

    def _delete_tray_icon(self) -> None:
        if self._notify_data is None:
            return
        shell32 = _load_shell32()
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._notify_data))
        self._notify_data = None
        if self._icon_handle:
            user32 = _load_user32()
            user32.DestroyIcon(wintypes.HICON(self._icon_handle))
            self._icon_handle = None


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
