"""SQLite migration chain。

職責：保存明確版本鏈的 migration entrypoint。現階段歷史 migration 仍由
`initialize_schema()` 的 idempotent schema repair 執行；本模組固定公開
版本鏈，避免後續新欄位繼續散落在 repository 內。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable


Migration = Callable[[sqlite3.Connection], None]


def migrate_10_to_11(connection: sqlite3.Connection) -> None:
    """將舊 target-scoped config 搬到 group-scoped config。"""

    connection.execute(
        """
        INSERT OR IGNORE INTO group_configs (
            group_id, include_keywords, exclude_keywords, min_refresh_sec,
            max_refresh_sec, jitter_enabled, fixed_refresh_sec, max_items_per_scan,
            auto_load_more, auto_adjust_sort, enable_desktop_notification,
            enable_ntfy, ntfy_topic, enable_discord_notification, discord_webhook
        )
        SELECT
            targets.group_id,
            target_configs.include_keywords,
            target_configs.exclude_keywords,
            target_configs.min_refresh_sec,
            target_configs.max_refresh_sec,
            target_configs.jitter_enabled,
            target_configs.fixed_refresh_sec,
            target_configs.max_items_per_scan,
            target_configs.auto_load_more,
            target_configs.auto_adjust_sort,
            target_configs.enable_desktop_notification,
            target_configs.enable_ntfy,
            target_configs.ntfy_topic,
            target_configs.enable_discord_notification,
            target_configs.discord_webhook
        FROM target_configs
        JOIN targets ON targets.id = target_configs.target_id
        WHERE targets.group_id <> ''
        """
    )


def migrate_11_to_12(connection: sqlite3.Connection) -> None:
    """將舊 runtime_status=paused 正規化為 executor 狀態 idle。"""

    connection.execute(
        """
        UPDATE target_runtime_state
        SET runtime_status = 'idle'
        WHERE runtime_status = 'paused'
        """
    )


MIGRATIONS: dict[int, Migration] = {
    10: migrate_10_to_11,
    11: migrate_11_to_12,
}


def run_known_migrations(connection: sqlite3.Connection, *, from_version: int, to_version: int) -> None:
    """依版本鏈執行已知 migrations，成功後才更新 schema_metadata。"""

    current_version = from_version
    while current_version < to_version:
        migration = MIGRATIONS.get(current_version)
        if migration is None:
            raise RuntimeError(f"Missing SQLite migration {current_version} -> {current_version + 1}")
        migration(connection)
        current_version += 1
        connection.execute(
            """
            INSERT OR REPLACE INTO schema_metadata (key, value)
            VALUES ('version', ?)
            """,
            (str(current_version),),
        )


__all__ = [
    "MIGRATIONS",
    "Migration",
    "migrate_10_to_11",
    "migrate_11_to_12",
    "run_known_migrations",
]
