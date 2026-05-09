"""SQLite repository implementation。"""

from __future__ import annotations

import sqlite3

from facebook_monitor.core.models import GlobalNotificationSettings
from facebook_monitor.persistence.sqlite_codec import encode_datetime
from facebook_monitor.persistence.sqlite_codec import decode_datetime

class GlobalNotificationSettingsRepository:
    """保存 Web UI 通知預設值。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self) -> GlobalNotificationSettings:
        """讀取通知預設值；尚未設定時回傳預設值。"""

        row = self.connection.execute(
            "SELECT * FROM global_notification_settings WHERE id = 1"
        ).fetchone()
        if not row:
            return GlobalNotificationSettings()
        updated_at = decode_datetime(row["updated_at"])
        return GlobalNotificationSettings(
            enable_desktop_notification=bool(row["enable_desktop_notification"]),
            enable_ntfy=bool(row["enable_ntfy"]),
            ntfy_topic=row["ntfy_topic"],
            enable_discord_notification=bool(row["enable_discord_notification"]),
            discord_webhook=row["discord_webhook"],
            updated_at=updated_at or GlobalNotificationSettings().updated_at,
        )

    def save(self, settings: GlobalNotificationSettings) -> None:
        """新增或更新通知預設值。"""

        self.connection.execute(
            """
            INSERT INTO global_notification_settings (
                id, enable_desktop_notification, enable_ntfy, ntfy_topic,
                enable_discord_notification, discord_webhook, updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                enable_desktop_notification=excluded.enable_desktop_notification,
                enable_ntfy=excluded.enable_ntfy,
                ntfy_topic=excluded.ntfy_topic,
                enable_discord_notification=excluded.enable_discord_notification,
                discord_webhook=excluded.discord_webhook,
                updated_at=excluded.updated_at
            """,
            (
                int(settings.enable_desktop_notification),
                int(settings.enable_ntfy),
                settings.ntfy_topic,
                int(settings.enable_discord_notification),
                settings.discord_webhook,
                encode_datetime(settings.updated_at),
            ),
        )

