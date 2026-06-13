"""SQLite repository implementation。"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from facebook_monitor.core.keyword_groups import flatten_include_keyword_groups
from facebook_monitor.core.keyword_groups import legacy_include_keyword_groups
from facebook_monitor.core.keyword_groups import normalize_include_keyword_groups
from facebook_monitor.core.notification_channels import transform_notification_endpoints
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.persistence.row_mappers import target_config_from_row
from facebook_monitor.persistence.secret_storage import PlaintextSecretCodec
from facebook_monitor.persistence.secret_storage import SecretCodec
from facebook_monitor.persistence.sqlite_codec import encode_include_keyword_groups
from facebook_monitor.persistence.sqlite_codec import encode_keywords


class TargetConfigRepository:
    """保存與查詢 target-scoped 監視設定。"""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        secret_codec: SecretCodec | PlaintextSecretCodec,
    ) -> None:
        self.connection = connection
        self.secret_codec = secret_codec

    def save_for_target_id(self, target_id: str, config: TargetConfig) -> TargetConfig:
        """新增或更新單一 target config，回傳保存後的 config。"""

        normalized_target_id = target_id.strip()
        if not normalized_target_id:
            raise ValueError("target_id is required for target-scoped config")
        normalized_groups = normalize_include_keyword_groups(
            config.include_keyword_groups,
            fill_empty_slots=True,
        )
        include_keywords = (
            flatten_include_keyword_groups(normalized_groups)
            if any(group.keywords for group in normalized_groups)
            else config.include_keywords
        )
        if not any(group.keywords for group in normalized_groups) and include_keywords:
            normalized_groups = legacy_include_keyword_groups(
                include_keywords,
                fill_empty_slots=True,
            )
        target_config = transform_notification_endpoints(
            replace(
                config,
                target_id=normalized_target_id,
                include_keywords=include_keywords,
                include_keyword_groups=normalized_groups,
            ),
            self.secret_codec.encrypt,
        )
        self.connection.execute(
            """
            INSERT INTO target_configs (
                target_id, include_keywords, include_keyword_groups,
                exclude_keywords, exclude_ignore_phrases, min_refresh_sec,
                max_refresh_sec, jitter_enabled, fixed_refresh_sec, max_items_per_scan,
                auto_load_more, auto_adjust_sort, enable_desktop_notification,
                enable_ntfy, ntfy_topic, enable_discord_notification, discord_webhook
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET
                include_keywords=excluded.include_keywords,
                include_keyword_groups=excluded.include_keyword_groups,
                exclude_keywords=excluded.exclude_keywords,
                exclude_ignore_phrases=excluded.exclude_ignore_phrases,
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
                target_config.target_id,
                encode_keywords(target_config.include_keywords),
                encode_include_keyword_groups(target_config.include_keyword_groups),
                encode_keywords(target_config.exclude_keywords),
                encode_keywords(target_config.exclude_ignore_phrases),
                target_config.min_refresh_sec,
                target_config.max_refresh_sec,
                int(target_config.jitter_enabled),
                target_config.fixed_refresh_sec,
                target_config.max_items_per_scan,
                int(target_config.auto_load_more),
                int(target_config.auto_adjust_sort),
                int(target_config.enable_desktop_notification),
                int(target_config.enable_ntfy),
                target_config.ntfy_topic,
                int(target_config.enable_discord_notification),
                target_config.discord_webhook,
            ),
        )
        return replace(
            config,
            target_id=normalized_target_id,
            include_keywords=include_keywords,
            include_keyword_groups=normalized_groups,
        )

    def save_for_target(self, target: TargetDescriptor, config: TargetConfig) -> TargetConfig:
        """依 target id 保存 target-scoped config。"""

        return self.save_for_target_id(target.id, config)

    def get_for_target_id(self, target_id: str) -> TargetConfig | None:
        """依 target id 查詢 target-scoped config。"""

        row = self.connection.execute(
            "SELECT * FROM target_configs WHERE target_id = ?",
            (target_id,),
        ).fetchone()
        if not row:
            return None
        return self._decrypt_target_config(target_config_from_row(row, id_column="target_id"))

    def get_for_target(self, target: TargetDescriptor) -> TargetConfig | None:
        """依 target 查詢 target-scoped config。"""

        return self.get_for_target_id(target.id)

    def list_for_targets(self, target_ids: list[str]) -> dict[str, TargetConfig]:
        """一次查詢多個 target-scoped config，供 dashboard read model 使用。"""

        unique_target_ids = list(dict.fromkeys(target_id for target_id in target_ids if target_id))
        if not unique_target_ids:
            return {}
        placeholders = ",".join("?" for _ in unique_target_ids)
        rows = self.connection.execute(
            f"""
            SELECT * FROM target_configs
            WHERE target_id IN ({placeholders})
            """,
            tuple(unique_target_ids),
        ).fetchall()
        configs: dict[str, TargetConfig] = {}
        for row in rows:
            config = self._decrypt_target_config(target_config_from_row(row, id_column="target_id"))
            configs[config.target_id] = config
        return configs

    def _decrypt_target_config(self, config: TargetConfig) -> TargetConfig:
        """還原 repository 對外回傳的 target notification secrets。"""

        return transform_notification_endpoints(config, self.secret_codec.decrypt)
