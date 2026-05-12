"""Notification sender helpers for tests。"""

from __future__ import annotations

from facebook_monitor.notifications.desktop import DesktopNotificationResult
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.discord import DiscordResult
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult


class NotificationRecorder:
    """記錄測試通知 payload，避免 route tests 重複定義 fake sender。"""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def desktop_sender(self, title: str, message: str) -> DesktopNotificationResult:
        """記錄 desktop 通知 payload。"""

        self.sent.append(f"desktop:{title}:{message}")
        return DesktopNotificationResult(ok=True, status_code=None, message="desktop_sent")

    def ntfy_sender(self, config: NtfyConfig, title: str, message: str) -> NtfyResult:
        """記錄 ntfy 通知 payload。"""

        self.sent.append(f"ntfy:{config.topic}:{title}:{message}")
        return NtfyResult(ok=True, status_code=200, message="sent")

    def discord_sender(
        self,
        config: DiscordConfig,
        title: str,
        message: str,
    ) -> DiscordResult:
        """記錄 Discord 通知 payload。"""

        self.sent.append(f"discord:{config.webhook_url}:{title}:{message}")
        return DiscordResult(ok=True, status_code=204, message="discord_sent")
