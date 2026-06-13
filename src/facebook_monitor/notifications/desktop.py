"""跨平台 desktop notification sender。

職責：Windows 由本 process 內的 Win32 tray owner 發送；macOS frozen `.app`
透過母程序或同 bundle launcher 的 UserNotifications 身分送出。source mode 則
保留 osascript fallback。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
from uuid import uuid4

from facebook_monitor.core.defaults import PYTHON_NOTIFICATION_RUNTIME_DEFAULTS
from facebook_monitor.notifications.safe_messages import safe_exception_message
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER_ENV
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_NOTIFICATION_SEND_FLAG
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_NOTIFICATION_SOCKET_ENV
from facebook_monitor.updates.platforms import MACOS_APP_ENTRY


CommandRunner = Callable[[list[str]], None]
InputCommandRunner = Callable[[list[str], str], str]
MacOSSocketPayloadSender = Callable[[str, str], str]
WindowsNativeNotificationSender = Callable[[str, str], None]

_LOGGER = logging.getLogger(__name__)

_MACOS_NOTIFICATION_SETTING_DISABLED = 1
_MACOS_SUPPRESSED_ALERT_MESSAGE = "desktop_failed:macos_alert_disabled"


class DesktopNotificationCommandFailed(Exception):
    """表示桌面通知命令執行失敗且沒有可用結構化結果。"""


@dataclass(frozen=True)
class DesktopNotificationResult:
    """保存桌面通知發送結果。"""

    ok: bool
    status_code: int | None
    message: str


def send_desktop_notification(
    title: str,
    message: str,
    *,
    command_runner: CommandRunner | None = None,
    input_command_runner: InputCommandRunner | None = None,
    macos_socket_payload_sender: MacOSSocketPayloadSender | None = None,
    windows_native_sender: WindowsNativeNotificationSender | None = None,
) -> DesktopNotificationResult:
    """依目前平台送出一則桌面通知。"""

    if sys.platform.startswith("win"):
        return _send_windows_desktop_notification(
            title=title,
            message=message,
            native_sender=windows_native_sender,
        )
    if sys.platform == "darwin":
        return _send_macos_desktop_notification(
            title=title,
            message=message,
            command_runner=command_runner,
            input_command_runner=input_command_runner,
            socket_payload_sender=macos_socket_payload_sender,
        )
    return DesktopNotificationResult(
        ok=False,
        status_code=None,
        message="desktop_failed: unsupported platform",
    )


def _send_windows_desktop_notification(
    *,
    title: str,
    message: str,
    native_sender: WindowsNativeNotificationSender | None = None,
) -> DesktopNotificationResult:
    """送出 Windows process-local native tray notification。"""

    sender = native_sender or _run_windows_native_notification
    try:
        sender(title, message)
    except Exception as exc:
        _LOGGER.warning(
            "windows_desktop_notification_failed exception_class=%s",
            exc.__class__.__name__,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return DesktopNotificationResult(
            ok=False,
            status_code=None,
            message="desktop_failed:windows_native_failed",
        )
    return DesktopNotificationResult(ok=True, status_code=None, message="desktop_sent")


def _run_windows_native_notification(title: str, message: str) -> None:
    """用 Windows onedir / source icon 由目前 process 發出 notification。"""

    from facebook_monitor.runtime.windows_integration import find_windows_notification_icon
    from facebook_monitor.runtime.windows_tray import show_windows_tray_notification

    show_windows_tray_notification(
        title=title,
        message=message,
        icon_path=find_windows_notification_icon(),
    )


def _send_macos_desktop_notification(
    *,
    title: str,
    message: str,
    command_runner: CommandRunner | None = None,
    input_command_runner: InputCommandRunner | None = None,
    socket_payload_sender: MacOSSocketPayloadSender | None = None,
) -> DesktopNotificationResult:
    """送出 macOS native notification；source mode fallback 到 osascript。"""

    payload = build_macos_native_notification_payload(title=title, message=message)
    parent_socket = resolve_macos_parent_notification_socket()
    if parent_socket is not None:
        sender = socket_payload_sender or send_macos_native_notification_payload_to_socket
        try:
            output = sender(parent_socket, payload)
        except Exception:
            native_launcher = resolve_macos_native_notification_launcher()
            if native_launcher is None:
                return DesktopNotificationResult(
                    ok=False,
                    status_code=None,
                    message="desktop_failed:macos_native_failed",
                )
        else:
            result = parse_macos_native_notification_result(output)
            return result

    native_launcher = resolve_macos_native_notification_launcher()
    if native_launcher is not None:
        input_runner = input_command_runner or _run_command_with_input
        command = build_macos_native_notification_command(native_launcher)
        try:
            output = input_runner(command, payload)
        except Exception as exc:
            return DesktopNotificationResult(
                ok=False,
                status_code=None,
                message=safe_exception_message("desktop_failed", exc),
            )
        result = parse_macos_native_notification_result(output)
        return result

    command_runner_fn = command_runner or _run_command
    try:
        command_runner_fn(
            build_macos_osascript_notification_command(title=title, message=message)
        )
    except Exception as exc:
        return DesktopNotificationResult(
            ok=False,
            status_code=None,
            message=safe_exception_message("desktop_failed", exc),
        )
    return DesktopNotificationResult(ok=True, status_code=None, message="desktop_sent")


def resolve_macos_native_notification_launcher() -> Path | None:
    """回傳 frozen macOS `.app` launcher path；source mode 回 None。"""

    if not _is_macos_frozen_app_child():
        return None
    executable = Path(sys.executable).resolve()
    launcher = executable.parent / MACOS_APP_BUNDLE_LAUNCHER
    return launcher if launcher.is_file() else None


def resolve_macos_parent_notification_socket() -> str | None:
    """回傳 `.app` 母程序提供的 macOS notification socket path。"""

    if not _is_macos_frozen_app_child():
        return None
    if os.environ.get(MACOS_APP_BUNDLE_LAUNCHER_ENV) != MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE:
        return None
    socket_path = os.environ.get(MACOS_APP_BUNDLE_NOTIFICATION_SOCKET_ENV, "").strip()
    if not socket_path:
        return None
    path = Path(socket_path)
    if os.name != "nt" and not path.is_socket():
        return None
    return str(path) if path.exists() else None


def _is_macos_frozen_app_child() -> bool:
    """判斷目前 process 是否為 `.app` launcher 啟動的 frozen root binary。"""

    if sys.platform != "darwin":
        return False
    if not getattr(sys, "frozen", False):
        return False
    return Path(sys.executable).resolve().name == MACOS_APP_ENTRY


def build_macos_native_notification_command(launcher: Path) -> list[str]:
    """建立 macOS native launcher notify mode 命令列；payload 由 stdin 傳入。"""

    return [str(launcher), MACOS_APP_BUNDLE_NOTIFICATION_SEND_FLAG]


def build_macos_native_notification_payload(*, title: str, message: str) -> str:
    """建立 native notification stdin JSON，避免通知文字出現在 process list。"""

    payload = {
        "schema_version": 1,
        "title": str(title or ""),
        "body": str(message or ""),
        "identifier": f"facebook-monitor-{uuid4()}",
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def send_macos_native_notification_payload_to_socket(
    socket_path: str,
    payload: str,
) -> str:
    """透過 `.app` 母程序 AF_UNIX socket 傳送 native notification payload。"""

    chunks: list[bytes] = []
    timeout = PYTHON_NOTIFICATION_RUNTIME_DEFAULTS.desktop_command_timeout_seconds
    af_unix = getattr(socket, "AF_UNIX", None)
    if af_unix is None:
        raise DesktopNotificationCommandFailed
    with socket.socket(af_unix, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(socket_path)
        sock.sendall(payload.encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


def parse_macos_native_notification_result(output: str) -> DesktopNotificationResult:
    """解析 native notification stdout JSON 並轉成 sender result。"""

    try:
        payload = json.loads(str(output or "").strip() or "{}")
    except json.JSONDecodeError:
        return DesktopNotificationResult(
            ok=False,
            status_code=None,
            message="desktop_failed:macos_result_invalid",
        )
    ok = bool(payload.get("ok"))
    message = str(payload.get("message") or "")
    _log_macos_native_notification_result(payload=payload, ok=ok, message=message)
    if ok:
        suppressed_message = _macos_suppressed_notification_message(payload)
        if suppressed_message is not None:
            return DesktopNotificationResult(
                ok=False,
                status_code=None,
                message=suppressed_message,
            )
        return DesktopNotificationResult(ok=True, status_code=None, message="desktop_sent")
    if not message.startswith("desktop_failed:"):
        message = "desktop_failed:macos_native_failed"
    return DesktopNotificationResult(ok=False, status_code=None, message=message)


def _macos_suppressed_notification_message(payload: object) -> str | None:
    """判斷 macOS 是否會收下通知但不顯示右上角 banner。"""

    if not isinstance(payload, dict):
        return None
    details = _macos_notification_result_details(payload)
    alert_setting = details.get("alert_setting")
    notification_center_setting = details.get("notification_center_setting")
    if alert_setting == _MACOS_NOTIFICATION_SETTING_DISABLED:
        return _MACOS_SUPPRESSED_ALERT_MESSAGE
    if notification_center_setting == _MACOS_NOTIFICATION_SETTING_DISABLED:
        return _MACOS_SUPPRESSED_ALERT_MESSAGE
    return None


def _macos_notification_result_details(payload: dict[object, object]) -> dict[object, object]:
    """取出 native result 中不含通知內容的設定診斷欄位。"""

    details = payload.get("details")
    if isinstance(details, dict):
        return details
    detail_keys = {
        "alert_setting",
        "authorization_status",
        "error_code",
        "error_domain",
        "notification_center_setting",
        "sound_setting",
    }
    return {key: payload[key] for key in detail_keys if key in payload}


def _log_macos_native_notification_result(
    *,
    payload: object,
    ok: bool,
    message: str,
) -> None:
    """記錄不含通知內容的 macOS native 結果，方便排查權限問題。"""

    if not isinstance(payload, dict):
        return
    details = _macos_notification_result_details(payload)
    if not details and ok:
        return
    _LOGGER.info(
        "macos_desktop_notification_result ok=%s message=%s backend=%s details=%s",
        ok,
        message or "",
        payload.get("backend") or "",
        details,
    )


def build_macos_osascript_notification_command(*, title: str, message: str) -> list[str]:
    """建立 macOS source-mode osascript fallback 命令列。"""

    return [
        "/usr/bin/osascript",
        "-e",
        "on run argv",
        "-e",
        (
            "display notification (item 2 of argv) with title (item 1 of argv)"
        ),
        "-e",
        "end run",
        str(title or ""),
        str(message or ""),
    ]


def _run_command(command: list[str]) -> None:
    """執行桌面通知命令。"""

    creationflags = (
        int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if sys.platform.startswith("win")
        else 0
    )
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=PYTHON_NOTIFICATION_RUNTIME_DEFAULTS.desktop_command_timeout_seconds,
        creationflags=creationflags,
    )


def _run_command_with_input(command: list[str], stdin_text: str) -> str:
    """執行需要 stdin JSON 的 native desktop notification command。"""

    completed = subprocess.run(
        command,
        input=stdin_text,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=PYTHON_NOTIFICATION_RUNTIME_DEFAULTS.desktop_command_timeout_seconds,
    )
    if completed.returncode != 0 and not completed.stdout.strip():
        raise DesktopNotificationCommandFailed
    return completed.stdout
