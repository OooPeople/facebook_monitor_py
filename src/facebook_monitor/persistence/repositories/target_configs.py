"""SQLite repository implementation。"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from facebook_monitor.core.models import LegacyTargetConfig
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.persistence.row_mappers import legacy_target_config_from_row
from facebook_monitor.persistence.row_mappers import target_config_from_row
from facebook_monitor.persistence.sqlite_codec import encode_keywords

class TargetConfigRepository:
    """保存與查詢監視設定。

    正式路徑只能使用 group-scoped `group_configs`，對齊 userscript 的
    group config 語義；舊版 `target_configs` 只允許 migration fallback 使用。
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save_legacy_target_config_for_migration(
        self,
        target_id: str,
        config: TargetConfig,
    ) -> None:
        """新增或更新舊版 target-scoped config，僅供 migration 測試或舊資料準備。"""

        normalized_target_id = target_id.strip()
        if not normalized_target_id:
            raise ValueError("target_id is required for legacy target config")
        self.connection.execute(
            """
            INSERT INTO target_configs (
                target_id, include_keywords, exclude_keywords, min_refresh_sec,
                max_refresh_sec, jitter_enabled, fixed_refresh_sec, max_items_per_scan,
                auto_load_more, auto_adjust_sort, enable_desktop_notification,
                enable_ntfy, ntfy_topic, enable_discord_notification, discord_webhook
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET
                include_keywords=excluded.include_keywords,
                exclude_keywords=excluded.exclude_keywords,
                min_refresh_sec=excluded.min_refresh_sec,
                max_refresh_sec=excluded.max_refresh_sec,
                jitter_enabled=excluded.jitter_enabled,
                fixed_refresh_sec=excluded.fixed_refresh_sec,
                max_items_per_scan=excluded.max_items_per_scan,
                auto_load_more=excluded.auto_load_more,
                auto_adjust_sort=excluded.auto_adjust_sort,
                enable_desktop_notification=excluded.enable_desktop_notification,
                enable_ntfy=excluded.enable_ntfy,
                ntfy_topic=excluded.ntfy_topic,
                enable_discord_notification=excluded.enable_discord_notification,
                discord_webhook=excluded.discord_webhook
            """,
            (
                normalized_target_id,
                encode_keywords(config.include_keywords),
                encode_keywords(config.exclude_keywords),
                config.min_refresh_sec,
                config.max_refresh_sec,
                int(config.jitter_enabled),
                config.fixed_refresh_sec,
                config.max_items_per_scan,
                int(config.auto_load_more),
                int(config.auto_adjust_sort),
                int(config.enable_desktop_notification),
                int(config.enable_ntfy),
                config.ntfy_topic,
                int(config.enable_discord_notification),
                config.discord_webhook,
            ),
        )

    def get_legacy_target_config_for_migration(self, target_id: str) -> LegacyTargetConfig | None:
        """依舊版 target id 查詢 target-scoped migration DTO。"""

        row = self.connection.execute(
            "SELECT * FROM target_configs WHERE target_id = ?", (target_id,)
        ).fetchone()
        if not row:
            return None
        return legacy_target_config_from_row(row)

    def save_for_group(self, group_id: str, config: TargetConfig) -> TargetConfig:
        """新增或更新 group-scoped config，回傳保存後的 config。"""

        normalized_group_id = group_id.strip()
        if not normalized_group_id:
            raise ValueError("group_id is required for group-scoped config")
        group_config = replace(config, group_id=normalized_group_id)
        self.connection.execute(
            """
            INSERT INTO group_configs (
                group_id, include_keywords, exclude_keywords, min_refresh_sec,
                max_refresh_sec, jitter_enabled, fixed_refresh_sec, max_items_per_scan,
                auto_load_more, auto_adjust_sort, enable_desktop_notification,
                enable_ntfy, ntfy_topic, enable_discord_notification, discord_webhook
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                include_keywords=excluded.include_keywords,
                exclude_keywords=excluded.exclude_keywords,
                min_refresh_sec=excluded.min_refresh_sec,
                max_refresh_sec=excluded.max_refresh_sec,
                jitter_enabled=excluded.jitter_enabled,
                fixed_refresh_sec=excluded.fixed_refresh_sec,
                max_items_per_scan=excluded.max_items_per_scan,
                auto_load_more=excluded.auto_load_more,
                auto_adjust_sort=excluded.auto_adjust_sort,
                enable_desktop_notification=excluded.enable_desktop_notification,
                enable_ntfy=excluded.enable_ntfy,
                ntfy_topic=excluded.ntfy_topic,
                enable_discord_notification=excluded.enable_discord_notification,
                discord_webhook=excluded.discord_webhook
            """,
            (
                group_config.group_id,
                encode_keywords(group_config.include_keywords),
                encode_keywords(group_config.exclude_keywords),
                group_config.min_refresh_sec,
                group_config.max_refresh_sec,
                int(group_config.jitter_enabled),
                group_config.fixed_refresh_sec,
                group_config.max_items_per_scan,
                int(group_config.auto_load_more),
                int(group_config.auto_adjust_sort),
                int(group_config.enable_desktop_notification),
                int(group_config.enable_ntfy),
                group_config.ntfy_topic,
                int(group_config.enable_discord_notification),
                group_config.discord_webhook,
            ),
        )
        return group_config

    def save_for_target(self, target: TargetDescriptor, config: TargetConfig) -> TargetConfig:
        """依 target 所屬 group 保存 group-scoped config。"""

        return self.save_for_group(target.group_id, config)

    def get_for_group(self, group_id: str) -> TargetConfig | None:
        """依 group id 查詢正式 group-scoped config。"""

        row = self.connection.execute(
            "SELECT * FROM group_configs WHERE group_id = ?",
            (group_id,),
        ).fetchone()
        if not row:
            return None
        return target_config_from_row(row, id_column="group_id")

    def list_for_groups(self, group_ids: list[str]) -> dict[str, TargetConfig]:
        """一次查詢多個 group-scoped config，供 dashboard read model 使用。"""

        unique_group_ids = list(dict.fromkeys(group_id for group_id in group_ids if group_id))
        if not unique_group_ids:
            return {}
        placeholders = ",".join("?" for _ in unique_group_ids)
        rows = self.connection.execute(
            f"""
            SELECT * FROM group_configs
            WHERE group_id IN ({placeholders})
            """,
            tuple(unique_group_ids),
        ).fetchall()
        configs: dict[str, TargetConfig] = {}
        for row in rows:
            config = target_config_from_row(row, id_column="group_id")
            configs[config.group_id] = config
        return configs

    def get_for_target(self, target: TargetDescriptor) -> TargetConfig | None:
        """依 target 查詢 group-scoped config，必要時從舊 target config 遷移。"""

        group_config = self.get_for_group(target.group_id)
        if group_config:
            return group_config

        legacy_config = self.get_legacy_target_config_for_migration(target.id)
        if legacy_config is None:
            return None
        return self.save_for_target(
            target,
            legacy_config.to_target_config(group_id=target.group_id),
        )

