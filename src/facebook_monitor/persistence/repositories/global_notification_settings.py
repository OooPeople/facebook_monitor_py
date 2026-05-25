"""SQLite repository implementation。"""

from __future__ import annotations

import sqlite3

from facebook_monitor.core.notification_channels import transform_notification_endpoints
from facebook_monitor.core.models import GlobalNotificationSettings
from facebook_monitor.persistence.secret_storage import PlaintextSecretCodec
from facebook_monitor.persistence.secret_storage import SecretCodec
from facebook_monitor.persistence.sqlite_codec import decode_datetime
from facebook_monitor.persistence.sqlite_codec import encode_datetime


class GlobalNotificationSettingsRepository:
    """保存舊版全域通知設定，避免既有 DB / secret storage 失去相容性。"""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        secret_codec: SecretCodec | PlaintextSecretCodec,
    ) -> None:
        self.connection = connection
        self.secret_codec = secret_codec

    def get(self) -> GlobalNotificationSettings:
        """讀取舊版全域通知設定；尚未設定時回傳空預設值。"""

        row = self.connection.execute(
            "SELECT * FROM global_notification_settings WHERE id = 1"
        ).fetchone()
        if not row:
            return GlobalNotificationSettings()
        updated_at = decode_datetime(row["updated_at"])
        return self._decrypt_settings(
            GlobalNotificationSettings(
                enable_desktop_notification=bool(row["enable_desktop_notification"]),
                enable_ntfy=bool(row["enable_ntfy"]),
                ntfy_topic=row["ntfy_topic"],
                enable_discord_notification=bool(row["enable_discord_notification"]),
                discord_webhook=row["discord_webhook"],
                updated_at=updated_at or GlobalNotificationSettings().updated_at,
            )
        )

    def save(self, settings: GlobalNotificationSettings) -> None:
        """新增或更新舊版全域通知設定。"""

        encrypted = transform_notification_endpoints(settings, self.secret_codec.encrypt)
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
                int(encrypted.enable_desktop_notification),
                int(encrypted.enable_ntfy),
                encrypted.ntfy_topic,
                int(encrypted.enable_discord_notification),
                encrypted.discord_webhook,
                encode_datetime(encrypted.updated_at),
            ),
        )

    def _decrypt_settings(
        self,
        settings: GlobalNotificationSettings,
    ) -> GlobalNotificationSettings:
        """還原 repository 對外回傳的全域 notification secrets。"""

        return transform_notification_endpoints(settings, self.secret_codec.decrypt)
