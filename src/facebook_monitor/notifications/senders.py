"""通知 sender protocol definitions。"""

from __future__ import annotations

from typing import Protocol

from facebook_monitor.notifications.desktop import DesktopNotificationResult
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.discord import DiscordResult
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult

__all__ = ["DesktopSender", "DiscordSender", "NtfySender"]


class NtfySender(Protocol):
    """定義可注入的 ntfy sender 介面。"""

    def __call__(self, config: NtfyConfig, title: str, message: str, /) -> NtfyResult:
        """送出 ntfy 通知並回傳結果。"""


class DesktopSender(Protocol):
    """定義可注入的桌面通知 sender 介面。"""

    def __call__(self, title: str, message: str, /) -> DesktopNotificationResult:
        """送出桌面通知並回傳結果。"""


class DiscordSender(Protocol):
    """定義可注入的 Discord sender 介面。"""

    def __call__(self, config: DiscordConfig, title: str, message: str, /) -> DiscordResult:
        """送出 Discord webhook 通知並回傳結果。"""
