"""Windows desktop notification sender。

職責：參考同層 `hotel_price_watch` 專案的 PowerShell balloon tip 作法，
提供 Python 版本機桌面通知通道。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import subprocess
import sys

from facebook_monitor.notifications.safe_messages import safe_exception_message


CommandRunner = Callable[[list[str]], None]


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
) -> DesktopNotificationResult:
    """送出一則 Windows 桌面通知。"""

    if not sys.platform.startswith("win"):
        return DesktopNotificationResult(
            ok=False,
            status_code=None,
            message="desktop_failed: unsupported platform",
        )
    runner = command_runner or _run_command
    try:
        runner(build_desktop_notification_command(title=title, message=message))
    except Exception as exc:
        return DesktopNotificationResult(
            ok=False,
            status_code=None,
            message=safe_exception_message("desktop_failed", exc),
        )
    return DesktopNotificationResult(ok=True, status_code=None, message="desktop_sent")


def build_desktop_notification_command(*, title: str, message: str) -> list[str]:
    """建立 PowerShell balloon tip 命令列。"""

    escaped_title = _escape_powershell_single_quoted_text(title)
    escaped_body = _escape_powershell_single_quoted_text(message)
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$notify = New-Object System.Windows.Forms.NotifyIcon; "
        "$notify.Icon = [System.Drawing.SystemIcons]::Information; "
        f"$notify.BalloonTipTitle = '{escaped_title}'; "
        f"$notify.BalloonTipText = '{escaped_body}'; "
        "$notify.Visible = $true; "
        "$notify.ShowBalloonTip(5000); "
        "Start-Sleep -Milliseconds 1000; "
        "$notify.Dispose();"
    )
    return [
        "powershell",
        "-NoProfile",
        "-Command",
        script,
    ]


def _escape_powershell_single_quoted_text(value: str) -> str:
    """轉義 PowerShell single-quoted string 內容。"""

    return str(value or "").replace("'", "''")


def _run_command(command: list[str]) -> None:
    """執行桌面通知命令。"""

    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        creationflags=creationflags,
    )
