"""SQLite migration chain。

職責：保存明確版本鏈的 migration entrypoint。既有 DB 欄位補齊必須進
本模組的版本鏈，不得另建 current-schema repair 平行路徑。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.keyword_groups import legacy_include_keyword_groups
from facebook_monitor.core.keyword_rules import split_keyword_rule_text
from facebook_monitor.persistence.sqlite_codec import decode_keywords
from facebook_monitor.persistence.sqlite_codec import encode_include_keyword_groups
from facebook_monitor.persistence.sqlite_codec import encode_keywords


Migration = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class MigrationColumn:
    """描述 migration 需要補上的欄位。"""

    table_name: str
    column_name: str
    definition: str


@dataclass(frozen=True)
class CheckedTableRebuild:
    """描述需要重建以導入 SQLite CHECK constraints 的資料表。"""

    table_name: str
    create_sql_template: str
    columns: tuple[str, ...]


V10_TARGET_CONFIG_NOTIFICATION_COLUMNS = (
    MigrationColumn(
        "target_configs",
        "enable_desktop_notification",
        "INTEGER NOT NULL DEFAULT 0",
    ),
    MigrationColumn(
        "target_configs",
        "enable_discord_notification",
        "INTEGER NOT NULL DEFAULT 0",
    ),
    MigrationColumn(
        "target_configs",
        "discord_webhook",
        "TEXT NOT NULL DEFAULT ''",
    ),
)


V12_TO_13_COLUMNS = (
    MigrationColumn(
        "notification_outbox",
        "endpoint",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "latest_scan_items",
        "debug_metadata",
        "TEXT NOT NULL DEFAULT '{}'",
    ),
    MigrationColumn(
        "target_runtime_state",
        "scan_requested_at",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "target_runtime_state",
        "last_skip_reason",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "target_runtime_state",
        "last_enqueued_at",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "target_runtime_state",
        "last_started_at",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "target_runtime_state",
        "last_finished_at",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "target_runtime_state",
        "enqueue_reason",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "target_runtime_state",
        "active_page_id",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "target_runtime_state",
        "last_page_reloaded_at",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "target_runtime_state",
        "scan_guard_count",
        "INTEGER NOT NULL DEFAULT 0",
    ),
    *V10_TARGET_CONFIG_NOTIFICATION_COLUMNS,
    MigrationColumn(
        "group_configs",
        "enable_desktop_notification",
        "INTEGER NOT NULL DEFAULT 0",
    ),
    MigrationColumn(
        "group_configs",
        "enable_discord_notification",
        "INTEGER NOT NULL DEFAULT 0",
    ),
    MigrationColumn(
        "group_configs",
        "discord_webhook",
        "TEXT NOT NULL DEFAULT ''",
    ),
)


V32_TO_33_COLUMNS = (
    MigrationColumn(
        "target_runtime_state",
        "consecutive_scan_skip_reason",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "target_runtime_state",
        "consecutive_scan_skip_count",
        "INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_scan_skip_count >= 0)",
    ),
)


def migrate_10_to_11(connection: sqlite3.Connection) -> None:
    """將舊 target-scoped config 搬到 group-scoped config。"""

    ensure_legacy_group_configs_table(connection)
    for column in V10_TARGET_CONFIG_NOTIFICATION_COLUMNS:
        add_column_if_missing(connection, column)
    add_column_if_missing(
        connection,
        MigrationColumn(
            "target_configs",
            "exclude_ignore_phrases",
            "TEXT NOT NULL DEFAULT '[]'",
        ),
    )
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


def migrate_12_to_13(connection: sqlite3.Connection) -> None:
    """將歷史 current-schema 補欄位收斂進正式 migration 鏈。"""

    for column in V12_TO_13_COLUMNS:
        add_column_if_missing(connection, column)


def migrate_13_to_14(connection: sqlite3.Connection) -> None:
    """將正式 config owner 從 group_configs 改回 target_configs[target_id]。"""

    ensure_legacy_group_configs_table(connection)
    for column in V10_TARGET_CONFIG_NOTIFICATION_COLUMNS:
        add_column_if_missing(connection, column)
    add_column_if_missing(
        connection,
        MigrationColumn(
            "target_configs",
            "exclude_ignore_phrases",
            "TEXT NOT NULL DEFAULT '[]'",
        ),
    )
    for column in (
        MigrationColumn(
            "group_configs",
            "enable_desktop_notification",
            "INTEGER NOT NULL DEFAULT 0",
        ),
        MigrationColumn(
            "group_configs",
            "enable_discord_notification",
            "INTEGER NOT NULL DEFAULT 0",
        ),
        MigrationColumn(
            "group_configs",
            "discord_webhook",
            "TEXT NOT NULL DEFAULT ''",
        ),
    ):
        add_column_if_missing(connection, column)
    connection.execute(
        """
        INSERT INTO target_configs (
            target_id, include_keywords, exclude_keywords, min_refresh_sec,
            max_refresh_sec, jitter_enabled, fixed_refresh_sec, max_items_per_scan,
            auto_load_more, auto_adjust_sort, enable_desktop_notification,
            enable_ntfy, ntfy_topic, enable_discord_notification, discord_webhook
        )
        SELECT
            targets.id,
            group_configs.include_keywords,
            group_configs.exclude_keywords,
            group_configs.min_refresh_sec,
            group_configs.max_refresh_sec,
            group_configs.jitter_enabled,
            group_configs.fixed_refresh_sec,
            group_configs.max_items_per_scan,
            group_configs.auto_load_more,
            group_configs.auto_adjust_sort,
            group_configs.enable_desktop_notification,
            group_configs.enable_ntfy,
            group_configs.ntfy_topic,
            group_configs.enable_discord_notification,
            group_configs.discord_webhook
        FROM targets
        JOIN group_configs ON group_configs.group_id = targets.group_id
        WHERE targets.id <> ''
        ON CONFLICT(target_id) DO UPDATE SET
            include_keywords=excluded.include_keywords,
            exclude_keywords=excluded.exclude_keywords,
            exclude_ignore_phrases='[]',
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
        """
    )
    defaults = PYTHON_TARGET_CONFIG_DEFAULTS
    connection.execute(
        """
        INSERT OR IGNORE INTO target_configs (
            target_id, include_keywords, exclude_keywords, min_refresh_sec,
            max_refresh_sec, jitter_enabled, fixed_refresh_sec, max_items_per_scan,
            auto_load_more, auto_adjust_sort, enable_desktop_notification,
            enable_ntfy, ntfy_topic, enable_discord_notification, discord_webhook
        )
        SELECT id, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        FROM targets
        WHERE id <> ''
        """,
        (
            encode_keywords(()),
            encode_keywords(()),
            defaults.min_refresh_sec,
            defaults.max_refresh_sec,
            int(defaults.jitter_enabled),
            defaults.fixed_refresh_sec,
            defaults.max_items_per_scan,
            int(defaults.auto_load_more),
            int(defaults.auto_adjust_sort),
            int(defaults.enable_desktop_notification),
            int(defaults.enable_ntfy),
            defaults.ntfy_topic,
            int(defaults.enable_discord_notification),
            defaults.discord_webhook,
        ),
    )
    connection.execute("DROP TABLE IF EXISTS group_configs")


def migrate_14_to_15(connection: sqlite3.Connection) -> None:
    """新增排除字忽略片語欄位，既有 target 預設空值。"""

    add_column_if_missing(
        connection,
        MigrationColumn(
            "target_configs",
            "exclude_ignore_phrases",
            "TEXT NOT NULL DEFAULT '[]'",
        ),
    )


def migrate_15_to_16(connection: sqlite3.Connection) -> None:
    """新增 app-level settings table，保存 theme 等 UI preference。"""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def migrate_16_to_17(connection: sqlite3.Connection) -> None:
    """標記 target scope uniqueness migration；實際 index 建立由 schema 統一收尾。"""

    return None


def migrate_17_to_18(connection: sqlite3.Connection) -> None:
    """新增 target metadata refresh 狀態欄位。"""

    add_column_if_missing(
        connection,
        MigrationColumn(
            "targets",
            "metadata_status",
            "TEXT NOT NULL DEFAULT 'resolved'",
        ),
    )
    add_column_if_missing(
        connection,
        MigrationColumn(
            "targets",
            "metadata_error",
            "TEXT NOT NULL DEFAULT ''",
        ),
    )


def migrate_18_to_19(connection: sqlite3.Connection) -> None:
    """新增 sidebar layout tables，並為既有 targets 建立未分組 placement。"""

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS sidebar_groups (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            collapsed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sidebar_target_placements (
            target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
            sidebar_group_id TEXT REFERENCES sidebar_groups(id) ON DELETE SET NULL,
            sort_order INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_sidebar_groups_order
            ON sidebar_groups(sort_order);
        CREATE INDEX IF NOT EXISTS idx_sidebar_target_placements_group_order
            ON sidebar_target_placements(sidebar_group_id, sort_order);
        """
    )
    target_rows = connection.execute(
        """
        SELECT id
        FROM targets
        ORDER BY created_at, id
        """
    ).fetchall()
    for index, row in enumerate(target_rows):
        connection.execute(
            """
            INSERT OR IGNORE INTO sidebar_target_placements (
                target_id, sidebar_group_id, sort_order, updated_at
            )
            VALUES (?, NULL, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            """,
            (row["id"], index),
        )


def migrate_19_to_20(connection: sqlite3.Connection) -> None:
    """新增 sidebar group config template table。"""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sidebar_group_config_templates (
            sidebar_group_id TEXT PRIMARY KEY REFERENCES sidebar_groups(id) ON DELETE CASCADE,
            include_keywords TEXT NOT NULL DEFAULT '[]',
            exclude_keywords TEXT NOT NULL DEFAULT '[]',
            exclude_ignore_phrases TEXT NOT NULL DEFAULT '[]',
            min_refresh_sec INTEGER NOT NULL,
            max_refresh_sec INTEGER NOT NULL,
            jitter_enabled INTEGER NOT NULL,
            fixed_refresh_sec INTEGER,
            max_items_per_scan INTEGER NOT NULL,
            auto_load_more INTEGER NOT NULL,
            auto_adjust_sort INTEGER NOT NULL,
            enable_desktop_notification INTEGER NOT NULL,
            enable_ntfy INTEGER NOT NULL,
            ntfy_topic TEXT NOT NULL,
            enable_discord_notification INTEGER NOT NULL,
            discord_webhook TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def migrate_20_to_21(connection: sqlite3.Connection) -> None:
    """新增多 keyword 命中正規化表，並回填既有摘要欄位。"""

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS match_history_matches (
            history_id INTEGER NOT NULL REFERENCES match_history(id) ON DELETE CASCADE,
            match_order INTEGER NOT NULL,
            rule TEXT NOT NULL,
            PRIMARY KEY (history_id, match_order)
        );

        CREATE TABLE IF NOT EXISTS latest_scan_item_matches (
            target_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            match_order INTEGER NOT NULL,
            rule TEXT NOT NULL,
            PRIMARY KEY (target_id, item_key, match_order),
            FOREIGN KEY (target_id, item_key)
                REFERENCES latest_scan_items(target_id, item_key)
                ON UPDATE CASCADE
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_match_history_matches_history
            ON match_history_matches(history_id, match_order);
        CREATE INDEX IF NOT EXISTS idx_latest_scan_item_matches_target_item
            ON latest_scan_item_matches(target_id, item_key, match_order);
        """
    )
    for row in connection.execute(
        """
        SELECT id, include_rule
        FROM match_history
        WHERE include_rule <> ''
        """
    ).fetchall():
        for index, rule in enumerate(split_keyword_rule_text(row["include_rule"])):
            connection.execute(
                """
                INSERT OR IGNORE INTO match_history_matches (history_id, match_order, rule)
                VALUES (?, ?, ?)
                """,
                (row["id"], index, rule),
            )
    for row in connection.execute(
        """
        SELECT target_id, item_key, matched_keyword
        FROM latest_scan_items
        WHERE matched_keyword <> ''
        """
    ).fetchall():
        for index, rule in enumerate(split_keyword_rule_text(row["matched_keyword"])):
            connection.execute(
                """
                INSERT OR IGNORE INTO latest_scan_item_matches (
                    target_id, item_key, match_order, rule
                )
                VALUES (?, ?, ?, ?)
                """,
                (row["target_id"], row["item_key"], index, rule),
            )


def migrate_21_to_22(connection: sqlite3.Connection) -> None:
    """新增 dashboard 熱查詢使用的 id 排序索引。"""

    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_scan_runs_target_id_desc
            ON scan_runs(target_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_scan_runs_target_status_id_desc
            ON scan_runs(target_id, status, id DESC);
        CREATE INDEX IF NOT EXISTS idx_notification_events_target_id_desc
            ON notification_events(target_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_notification_events_target_channel_id_desc
            ON notification_events(target_id, channel, id DESC);
        """
    )


def migrate_22_to_23(connection: sqlite3.Connection) -> None:
    """新增 target 社團封面圖 URL metadata 欄位。"""

    add_column_if_missing(
        connection,
        MigrationColumn(
            "targets",
            "group_cover_image_url",
            "TEXT NOT NULL DEFAULT ''",
        ),
    )


def migrate_23_to_24(connection: sqlite3.Connection) -> None:
    """新增 scan scope baseline state，供非使用者 start 的安全清理使用。"""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_scope_state (
            scope_id TEXT PRIMARY KEY,
            initialized INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def migrate_24_to_25(connection: sqlite3.Connection) -> None:
    """新增 UI 顯示用 next due 欄位；scheduler 不以此欄位作排程判斷。"""

    add_column_if_missing(
        connection,
        MigrationColumn(
            "target_runtime_state",
            "display_next_due_at",
            "TEXT NOT NULL DEFAULT ''",
        ),
    )


def migrate_25_to_26(connection: sqlite3.Connection) -> None:
    """新增 scan failure 連續失敗計數欄位。"""

    for column in (
        MigrationColumn(
            "target_runtime_state",
            "consecutive_failure_reason",
            "TEXT NOT NULL DEFAULT ''",
        ),
        MigrationColumn(
            "target_runtime_state",
            "consecutive_failure_count",
            "INTEGER NOT NULL DEFAULT 0",
        ),
    ):
        add_column_if_missing(connection, column)


def migrate_26_to_27(connection: sqlite3.Connection) -> None:
    """新增 target cover image URL 背景刷新狀態表。"""

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS target_cover_image_refresh_state (
            target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            requested_at TEXT NOT NULL DEFAULT '',
            last_attempted_at TEXT NOT NULL DEFAULT '',
            last_succeeded_at TEXT NOT NULL DEFAULT '',
            last_failed_at TEXT NOT NULL DEFAULT '',
            last_reported_url TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cover_image_refresh_status_requested
            ON target_cover_image_refresh_state(status, requested_at);
        """
    )


def migrate_27_to_28(connection: sqlite3.Connection) -> None:
    """補上 cover image refresh 的診斷欄位。"""

    for column in (
        MigrationColumn(
            "target_cover_image_refresh_state",
            "last_resolved_url",
            "TEXT NOT NULL DEFAULT ''",
        ),
        MigrationColumn(
            "target_cover_image_refresh_state",
            "last_result",
            "TEXT NOT NULL DEFAULT ''",
        ),
        MigrationColumn(
            "target_cover_image_refresh_state",
            "changed",
            "INTEGER NOT NULL DEFAULT 0",
        ),
    ):
        add_column_if_missing(connection, column)


def migrate_28_to_29(connection: sqlite3.Connection) -> None:
    """新增 include keyword groups 與命中 group 快照欄位。"""

    for column in (
        MigrationColumn(
            "target_configs",
            "include_keyword_groups",
            "TEXT NOT NULL DEFAULT '[]'",
        ),
        MigrationColumn(
            "sidebar_group_config_templates",
            "include_keyword_groups",
            "TEXT NOT NULL DEFAULT '[]'",
        ),
        MigrationColumn(
            "match_history_matches",
            "keyword_group_id",
            "TEXT NOT NULL DEFAULT ''",
        ),
        MigrationColumn(
            "match_history_matches",
            "keyword_group_label",
            "TEXT NOT NULL DEFAULT ''",
        ),
        MigrationColumn(
            "latest_scan_item_matches",
            "keyword_group_id",
            "TEXT NOT NULL DEFAULT ''",
        ),
        MigrationColumn(
            "latest_scan_item_matches",
            "keyword_group_label",
            "TEXT NOT NULL DEFAULT ''",
        ),
    ):
        add_column_if_missing(connection, column)
    _backfill_include_keyword_groups(connection, "target_configs")
    _backfill_include_keyword_groups(connection, "sidebar_group_config_templates")


def migrate_29_to_30(connection: sqlite3.Connection) -> None:
    """分批導入低風險 child tables 的 SQLite CHECK constraints。"""

    for spec in V29_TO_V30_CHECKED_TABLES:
        rebuild_table_with_check_constraints(connection, spec)
    ensure_v30_rebuilt_table_indexes(connection)


def migrate_30_to_31(connection: sqlite3.Connection) -> None:
    """為 notification outbox/events 補 runtime failure 事件語義欄位。"""

    for column in (
        MigrationColumn(
            "notification_events",
            "event_kind",
            "TEXT NOT NULL DEFAULT 'match' CHECK (event_kind IN ('match', 'runtime_failure'))",
        ),
        MigrationColumn(
            "notification_events",
            "source_scan_run_id",
            "INTEGER",
        ),
        MigrationColumn(
            "notification_events",
            "failure_reason",
            "TEXT NOT NULL DEFAULT ''",
        ),
        MigrationColumn(
            "notification_events",
            "failure_count",
            "INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0)",
        ),
        MigrationColumn(
            "notification_outbox",
            "event_kind",
            "TEXT NOT NULL DEFAULT 'match' CHECK (event_kind IN ('match', 'runtime_failure'))",
        ),
        MigrationColumn(
            "notification_outbox",
            "source_scan_run_id",
            "INTEGER",
        ),
        MigrationColumn(
            "notification_outbox",
            "failure_reason",
            "TEXT NOT NULL DEFAULT ''",
        ),
        MigrationColumn(
            "notification_outbox",
            "failure_count",
            "INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0)",
        ),
    ):
        add_column_if_missing(connection, column)


def migrate_31_to_32(connection: sqlite3.Connection) -> None:
    """新增 logical item / notification dedupe shadow tables 並回填既有狀態。"""

    ensure_v32_logical_dedupe_schema(connection)
    add_column_if_missing(
        connection,
        MigrationColumn(
            "notification_outbox",
            "dedupe_id",
            "INTEGER REFERENCES notification_dedupe(id) ON DELETE SET NULL",
        ),
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_notification_outbox_dedupe
            ON notification_outbox(dedupe_id)
        """
    )
    _backfill_target_dedupe_state(connection)
    _backfill_logical_items_from_seen_items(connection)
    _backfill_notification_dedupe_from_outbox(connection)


def migrate_32_to_33(connection: sqlite3.Connection) -> None:
    """新增 skipped scan 連續計數欄位，支援排序未確認升級重試。"""

    for column in V32_TO_33_COLUMNS:
        add_column_if_missing(connection, column)


def migrate_33_to_34(connection: sqlite3.Connection) -> None:
    """移除 legacy group_configs，避免舊 webhook secrets 留在正式 DB。"""

    connection.execute("DROP TABLE IF EXISTS group_configs")


def migrate_34_to_35(connection: sqlite3.Connection) -> None:
    """新增保留換行的顯示文字欄位，補齊掃描結果持久化語義。"""

    for column in (
        MigrationColumn(
            "match_history",
            "display_text",
            "TEXT NOT NULL DEFAULT ''",
        ),
        MigrationColumn(
            "latest_scan_items",
            "display_text",
            "TEXT NOT NULL DEFAULT ''",
        ),
    ):
        add_column_if_missing(connection, column)
    _backfill_display_text_from_text(connection, "match_history")
    _backfill_display_text_from_text(connection, "latest_scan_items")


def ensure_v32_logical_dedupe_schema(connection: sqlite3.Connection) -> None:
    """建立 v32 logical item 與 notification dedupe tables/indexes。"""

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS target_dedupe_state (
            target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
            dedupe_epoch INTEGER NOT NULL DEFAULT 0 CHECK (dedupe_epoch >= 0),
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS logical_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            scope_id TEXT NOT NULL,
            dedupe_epoch INTEGER NOT NULL DEFAULT 0 CHECK (dedupe_epoch >= 0),
            item_kind TEXT NOT NULL CHECK (item_kind IN ('post', 'comment')),
            canonical_item_key TEXT NOT NULL,
            parent_post_id TEXT NOT NULL DEFAULT '',
            comment_id TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS logical_item_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            logical_item_id INTEGER NOT NULL
                REFERENCES logical_items(id) ON DELETE CASCADE,
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            scope_id TEXT NOT NULL,
            dedupe_epoch INTEGER NOT NULL DEFAULT 0 CHECK (dedupe_epoch >= 0),
            alias_key TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (target_id, dedupe_epoch, alias_key)
        );

        CREATE TABLE IF NOT EXISTS notification_dedupe (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            dedupe_epoch INTEGER NOT NULL DEFAULT 0 CHECK (dedupe_epoch >= 0),
            event_kind TEXT NOT NULL CHECK (event_kind IN ('match', 'runtime_failure')),
            channel TEXT NOT NULL CHECK (channel IN ('desktop', 'ntfy', 'discord')),
            subject_key TEXT NOT NULL,
            logical_item_id INTEGER REFERENCES logical_items(id) ON DELETE SET NULL,
            item_key TEXT NOT NULL DEFAULT '',
            item_kind TEXT NOT NULL CHECK (item_kind IN ('post', 'comment')),
            status TEXT NOT NULL CHECK (status IN ('queued', 'sent', 'failed', 'skipped')),
            notification_event_id INTEGER,
            failure_reason TEXT NOT NULL DEFAULT '',
            failure_count INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
            first_queued_at TEXT NOT NULL,
            last_deduped_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (target_id, dedupe_epoch, event_kind, channel, subject_key)
        );

        CREATE INDEX IF NOT EXISTS idx_target_dedupe_state_epoch
            ON target_dedupe_state(target_id, dedupe_epoch);
        CREATE INDEX IF NOT EXISTS idx_logical_items_target_scope_seen
            ON logical_items(target_id, scope_id, dedupe_epoch, last_seen_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_logical_items_comment_identity
            ON logical_items(target_id, dedupe_epoch, item_kind, parent_post_id, comment_id)
            WHERE item_kind = 'comment' AND comment_id <> '';
        CREATE INDEX IF NOT EXISTS idx_logical_item_aliases_logical
            ON logical_item_aliases(logical_item_id);
        CREATE INDEX IF NOT EXISTS idx_logical_item_aliases_scope_seen
            ON logical_item_aliases(target_id, scope_id, dedupe_epoch, last_seen_at);
        CREATE INDEX IF NOT EXISTS idx_notification_dedupe_target_updated
            ON notification_dedupe(target_id, dedupe_epoch, last_deduped_at);
        CREATE INDEX IF NOT EXISTS idx_notification_dedupe_logical
            ON notification_dedupe(logical_item_id);
        """
    )


def _backfill_target_dedupe_state(connection: sqlite3.Connection) -> None:
    """為既有 targets 建立 dedupe epoch 0。"""

    connection.execute(
        """
        INSERT OR IGNORE INTO target_dedupe_state (target_id, dedupe_epoch, updated_at)
        SELECT id, 0, updated_at
        FROM targets
        WHERE TRIM(id) <> ''
        """
    )


def _backfill_logical_items_from_seen_items(connection: sqlite3.Connection) -> None:
    """將 flat seen alias rows 保守回填成 logical item / alias rows。"""

    if not table_exists(connection, "seen_items"):
        return
    rows = connection.execute(
        """
        SELECT
            targets.id AS target_id,
            seen_items.scope_id AS scope_id,
            seen_items.item_key AS item_key,
            seen_items.item_kind AS item_kind,
            seen_items.parent_post_id AS parent_post_id,
            seen_items.comment_id AS comment_id,
            seen_items.first_seen_at AS first_seen_at,
            seen_items.last_seen_at AS last_seen_at,
            COALESCE(target_dedupe_state.dedupe_epoch, 0) AS dedupe_epoch
        FROM seen_items
        JOIN targets ON targets.scope_id = seen_items.scope_id
        LEFT JOIN target_dedupe_state ON target_dedupe_state.target_id = targets.id
        ORDER BY targets.id, seen_items.scope_id, seen_items.item_kind,
                 seen_items.parent_post_id, seen_items.comment_id, seen_items.item_key
        """
    ).fetchall()
    groups: dict[tuple[str, int, str, str, str, str, str], list[sqlite3.Row]] = {}
    for row in rows:
        item_kind = str(row["item_kind"])
        comment_id = str(row["comment_id"] or "")
        if item_kind == "comment" and comment_id:
            group_key = (
                str(row["target_id"]),
                int(row["dedupe_epoch"]),
                str(row["scope_id"]),
                item_kind,
                str(row["parent_post_id"] or ""),
                comment_id,
                "__comment_identity__",
            )
        else:
            group_key = (
                str(row["target_id"]),
                int(row["dedupe_epoch"]),
                str(row["scope_id"]),
                item_kind,
                str(row["parent_post_id"] or ""),
                comment_id,
                str(row["item_key"]),
            )
        groups.setdefault(group_key, []).append(row)

    for group_rows in groups.values():
        first = group_rows[0]
        first_seen_at = min(str(row["first_seen_at"]) for row in group_rows)
        last_seen_at = max(str(row["last_seen_at"]) for row in group_rows)
        canonical_item_key = str(first["item_key"])
        connection.execute(
            """
            INSERT INTO logical_items (
                target_id, scope_id, dedupe_epoch, item_kind, canonical_item_key,
                parent_post_id, comment_id, first_seen_at, last_seen_at,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                first["target_id"],
                first["scope_id"],
                int(first["dedupe_epoch"]),
                first["item_kind"],
                canonical_item_key,
                first["parent_post_id"] or "",
                first["comment_id"] or "",
                first_seen_at,
                last_seen_at,
                first_seen_at,
                last_seen_at,
            ),
        )
        logical_item_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        for row in group_rows:
            connection.execute(
                """
                INSERT OR IGNORE INTO logical_item_aliases (
                    logical_item_id, target_id, scope_id, dedupe_epoch, alias_key,
                    first_seen_at, last_seen_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    logical_item_id,
                    row["target_id"],
                    row["scope_id"],
                    int(row["dedupe_epoch"]),
                    row["item_key"],
                    row["first_seen_at"],
                    row["last_seen_at"],
                    row["first_seen_at"],
                    row["last_seen_at"],
                ),
            )


def _backfill_notification_dedupe_from_outbox(connection: sqlite3.Connection) -> None:
    """將既有 outbox idempotency rows 回填為 notification dedupe ledger。"""

    if not table_exists(connection, "notification_outbox"):
        return
    rows = connection.execute(
        """
        SELECT
            notification_outbox.id AS outbox_id,
            notification_outbox.target_id AS target_id,
            notification_outbox.item_key AS item_key,
            notification_outbox.item_kind AS item_kind,
            notification_outbox.channel AS channel,
            notification_outbox.status AS outbox_status,
            notification_outbox.event_kind AS event_kind,
            notification_outbox.source_scan_run_id AS source_scan_run_id,
            notification_outbox.failure_reason AS failure_reason,
            notification_outbox.failure_count AS failure_count,
            notification_outbox.notification_event_id AS notification_event_id,
            notification_outbox.created_at AS created_at,
            notification_outbox.updated_at AS updated_at,
            targets.scope_id AS target_scope_id,
            COALESCE(target_dedupe_state.dedupe_epoch, 0) AS dedupe_epoch,
            logical_item_aliases.logical_item_id AS logical_item_id
        FROM notification_outbox
        LEFT JOIN targets
            ON targets.id = notification_outbox.target_id
        LEFT JOIN target_dedupe_state
            ON target_dedupe_state.target_id = notification_outbox.target_id
        LEFT JOIN logical_item_aliases
            ON logical_item_aliases.target_id = notification_outbox.target_id
           AND logical_item_aliases.dedupe_epoch = COALESCE(
               target_dedupe_state.dedupe_epoch,
               0
           )
           AND logical_item_aliases.alias_key = notification_outbox.item_key
        ORDER BY notification_outbox.id
        """
    ).fetchall()
    for row in rows:
        logical_item_id = (
            int(row["logical_item_id"]) if row["logical_item_id"] is not None else None
        )
        if str(row["event_kind"] or "match") == "match" and logical_item_id is None:
            logical_item_id = _create_logical_item_for_outbox_row(connection, row)
        subject_key = _notification_dedupe_subject_key(
            event_kind=str(row["event_kind"] or "match"),
            item_key=str(row["item_key"]),
            logical_item_id=logical_item_id,
            source_scan_run_id=row["source_scan_run_id"],
        )
        status = _notification_dedupe_status_from_outbox(str(row["outbox_status"]))
        connection.execute(
            """
            INSERT OR IGNORE INTO notification_dedupe (
                target_id, dedupe_epoch, event_kind, channel, subject_key,
                logical_item_id, item_key, item_kind, status, notification_event_id,
                failure_reason, failure_count, first_queued_at, last_deduped_at,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["target_id"],
                int(row["dedupe_epoch"]),
                row["event_kind"] or "match",
                row["channel"],
                subject_key,
                logical_item_id,
                row["item_key"],
                row["item_kind"],
                status,
                row["notification_event_id"],
                row["failure_reason"] or "",
                int(row["failure_count"] or 0),
                row["created_at"],
                row["updated_at"],
                row["created_at"],
                row["updated_at"],
            ),
        )
        dedupe_row = connection.execute(
            """
            SELECT id
            FROM notification_dedupe
            WHERE target_id = ?
              AND dedupe_epoch = ?
              AND event_kind = ?
              AND channel = ?
              AND subject_key = ?
            """,
            (
                row["target_id"],
                int(row["dedupe_epoch"]),
                row["event_kind"] or "match",
                row["channel"],
                subject_key,
            ),
        ).fetchone()
        if dedupe_row is not None:
            connection.execute(
                "UPDATE notification_outbox SET dedupe_id = ? WHERE id = ?",
                (int(dedupe_row["id"]), int(row["outbox_id"])),
            )


def _create_logical_item_for_outbox_row(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> int | None:
    """為缺少 seen alias 的既有 match outbox 建立保守 logical item。"""

    item_key = str(row["item_key"] or "").strip()
    target_id = str(row["target_id"] or "").strip()
    scope_id = str(row["target_scope_id"] or "").strip()
    if not item_key or not target_id or not scope_id:
        return None
    existing_alias = connection.execute(
        """
        SELECT logical_item_id
        FROM logical_item_aliases
        WHERE target_id = ?
          AND dedupe_epoch = ?
          AND alias_key = ?
        ORDER BY id
        LIMIT 1
        """,
        (target_id, int(row["dedupe_epoch"]), item_key),
    ).fetchone()
    if existing_alias is not None:
        return int(existing_alias["logical_item_id"])
    connection.execute(
        """
        INSERT INTO logical_items (
            target_id, scope_id, dedupe_epoch, item_kind, canonical_item_key,
            parent_post_id, comment_id, first_seen_at, last_seen_at,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, '', '', ?, ?, ?, ?)
        """,
        (
            target_id,
            scope_id,
            int(row["dedupe_epoch"]),
            row["item_kind"],
            item_key,
            row["created_at"],
            row["updated_at"],
            row["created_at"],
            row["updated_at"],
        ),
    )
    logical_item_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    connection.execute(
        """
        INSERT OR IGNORE INTO logical_item_aliases (
            logical_item_id, target_id, scope_id, dedupe_epoch, alias_key,
            first_seen_at, last_seen_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            logical_item_id,
            target_id,
            scope_id,
            int(row["dedupe_epoch"]),
            item_key,
            row["created_at"],
            row["updated_at"],
            row["created_at"],
            row["updated_at"],
        ),
    )
    return logical_item_id


def _notification_dedupe_subject_key(
    *,
    event_kind: str,
    item_key: str,
    logical_item_id: int | None,
    source_scan_run_id: object,
) -> str:
    """建立 migration 使用的 notification dedupe subject key。"""

    if event_kind == "match" and logical_item_id is not None:
        return f"logical:{logical_item_id}"
    if event_kind == "runtime_failure":
        scan_run_id = str(source_scan_run_id or "").strip()
        return f"runtime-failure:{scan_run_id or item_key}"
    return f"legacy:{item_key}"


def _notification_dedupe_status_from_outbox(status: str) -> str:
    """將 outbox status 映射到 dedupe ledger status。"""

    if status == "sent":
        return "sent"
    if status == "skipped":
        return "skipped"
    if status in {"failed", "processing_failed"}:
        return "failed"
    return "queued"


V29_TO_V30_CHECKED_TABLES: tuple[CheckedTableRebuild, ...] = (
    CheckedTableRebuild(
        "target_configs",
        """
        CREATE TABLE {table_name} (
            target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
            include_keywords TEXT NOT NULL,
            include_keyword_groups TEXT NOT NULL DEFAULT '[]',
            exclude_keywords TEXT NOT NULL,
            exclude_ignore_phrases TEXT NOT NULL DEFAULT '[]',
            min_refresh_sec INTEGER NOT NULL CHECK (min_refresh_sec >= 5),
            max_refresh_sec INTEGER NOT NULL CHECK (
                max_refresh_sec >= 5 AND max_refresh_sec >= min_refresh_sec
            ),
            jitter_enabled INTEGER NOT NULL CHECK (jitter_enabled IN (0, 1)),
            fixed_refresh_sec INTEGER,
            max_items_per_scan INTEGER NOT NULL CHECK (max_items_per_scan > 0),
            auto_load_more INTEGER NOT NULL CHECK (auto_load_more IN (0, 1)),
            auto_adjust_sort INTEGER NOT NULL CHECK (auto_adjust_sort IN (0, 1)),
            enable_desktop_notification INTEGER NOT NULL CHECK (
                enable_desktop_notification IN (0, 1)
            ),
            enable_ntfy INTEGER NOT NULL CHECK (enable_ntfy IN (0, 1)),
            ntfy_topic TEXT NOT NULL,
            enable_discord_notification INTEGER NOT NULL CHECK (
                enable_discord_notification IN (0, 1)
            ),
            discord_webhook TEXT NOT NULL
        )
        """,
        (
            "target_id",
            "include_keywords",
            "include_keyword_groups",
            "exclude_keywords",
            "exclude_ignore_phrases",
            "min_refresh_sec",
            "max_refresh_sec",
            "jitter_enabled",
            "fixed_refresh_sec",
            "max_items_per_scan",
            "auto_load_more",
            "auto_adjust_sort",
            "enable_desktop_notification",
            "enable_ntfy",
            "ntfy_topic",
            "enable_discord_notification",
            "discord_webhook",
        ),
    ),
    CheckedTableRebuild(
        "seen_items",
        """
        CREATE TABLE {table_name} (
            scope_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            item_kind TEXT NOT NULL CHECK (item_kind IN ('post', 'comment')),
            parent_post_id TEXT NOT NULL,
            comment_id TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (scope_id, item_key)
        )
        """,
        (
            "scope_id",
            "item_key",
            "item_kind",
            "parent_post_id",
            "comment_id",
            "first_seen_at",
            "last_seen_at",
        ),
    ),
    CheckedTableRebuild(
        "scan_scope_state",
        """
        CREATE TABLE {table_name} (
            scope_id TEXT PRIMARY KEY,
            initialized INTEGER NOT NULL CHECK (initialized IN (0, 1)),
            updated_at TEXT NOT NULL
        )
        """,
        ("scope_id", "initialized", "updated_at"),
    ),
    CheckedTableRebuild(
        "scan_runs",
        """
        CREATE TABLE {table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
            item_count INTEGER NOT NULL CHECK (item_count >= 0),
            matched_count INTEGER NOT NULL CHECK (matched_count >= 0),
            error_message TEXT NOT NULL,
            worker_mode TEXT NOT NULL,
            metadata TEXT NOT NULL
        )
        """,
        (
            "id",
            "target_id",
            "started_at",
            "finished_at",
            "status",
            "item_count",
            "matched_count",
            "error_message",
            "worker_mode",
            "metadata",
        ),
    ),
    CheckedTableRebuild(
        "notification_events",
        """
        CREATE TABLE {table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            item_key TEXT NOT NULL,
            channel TEXT NOT NULL CHECK (channel IN ('desktop', 'ntfy', 'discord')),
            status TEXT NOT NULL CHECK (status IN ('sent', 'failed', 'skipped')),
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        ("id", "target_id", "item_key", "channel", "status", "message", "created_at"),
    ),
    CheckedTableRebuild(
        "notification_outbox",
        """
        CREATE TABLE {table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idempotency_key TEXT NOT NULL UNIQUE,
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            item_key TEXT NOT NULL,
            item_kind TEXT NOT NULL CHECK (item_kind IN ('post', 'comment')),
            channel TEXT NOT NULL CHECK (channel IN ('desktop', 'ntfy', 'discord')),
            status TEXT NOT NULL CHECK (
                status IN (
                    'pending',
                    'processing_pending',
                    'sent',
                    'failed',
                    'processing_failed',
                    'skipped'
                )
            ),
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            endpoint TEXT NOT NULL DEFAULT '',
            permalink TEXT NOT NULL,
            attempts INTEGER NOT NULL CHECK (attempts >= 0),
            last_error TEXT NOT NULL,
            notification_event_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        (
            "id",
            "idempotency_key",
            "target_id",
            "item_key",
            "item_kind",
            "channel",
            "status",
            "title",
            "message",
            "endpoint",
            "permalink",
            "attempts",
            "last_error",
            "notification_event_id",
            "created_at",
            "updated_at",
        ),
    ),
    CheckedTableRebuild(
        "target_runtime_state",
        """
        CREATE TABLE {table_name} (
            target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
            desired_state TEXT NOT NULL CHECK (desired_state IN ('active', 'stopped')),
            runtime_status TEXT NOT NULL CHECK (
                runtime_status IN ('idle', 'queued', 'running', 'error')
            ),
            scan_requested_at TEXT NOT NULL DEFAULT '',
            last_enqueued_at TEXT NOT NULL DEFAULT '',
            last_started_at TEXT NOT NULL DEFAULT '',
            last_finished_at TEXT NOT NULL DEFAULT '',
            last_heartbeat_at TEXT NOT NULL,
            last_error TEXT NOT NULL,
            last_skip_reason TEXT NOT NULL DEFAULT '',
            enqueue_reason TEXT NOT NULL DEFAULT '',
            active_worker_id TEXT NOT NULL,
            active_page_id TEXT NOT NULL DEFAULT '',
            last_page_reloaded_at TEXT NOT NULL DEFAULT '',
            scan_guard_count INTEGER NOT NULL DEFAULT 0 CHECK (scan_guard_count >= 0),
            display_next_due_at TEXT NOT NULL DEFAULT '',
            consecutive_failure_reason TEXT NOT NULL DEFAULT '',
            consecutive_failure_count INTEGER NOT NULL DEFAULT 0 CHECK (
                consecutive_failure_count >= 0
            ),
            updated_at TEXT NOT NULL
        )
        """,
        (
            "target_id",
            "desired_state",
            "runtime_status",
            "scan_requested_at",
            "last_enqueued_at",
            "last_started_at",
            "last_finished_at",
            "last_heartbeat_at",
            "last_error",
            "last_skip_reason",
            "enqueue_reason",
            "active_worker_id",
            "active_page_id",
            "last_page_reloaded_at",
            "scan_guard_count",
            "display_next_due_at",
            "consecutive_failure_reason",
            "consecutive_failure_count",
            "updated_at",
        ),
    ),
    CheckedTableRebuild(
        "target_cover_image_refresh_state",
        """
        CREATE TABLE {table_name} (
            target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (status IN ('idle', 'pending', 'failed')),
            requested_at TEXT NOT NULL DEFAULT '',
            last_attempted_at TEXT NOT NULL DEFAULT '',
            last_succeeded_at TEXT NOT NULL DEFAULT '',
            last_failed_at TEXT NOT NULL DEFAULT '',
            last_reported_url TEXT NOT NULL DEFAULT '',
            last_resolved_url TEXT NOT NULL DEFAULT '',
            last_result TEXT NOT NULL DEFAULT '' CHECK (
                last_result IN (
                    '',
                    'queued',
                    'attempted',
                    'succeeded_changed',
                    'succeeded_unchanged',
                    'stale_skipped',
                    'failed'
                )
            ),
            changed INTEGER NOT NULL DEFAULT 0 CHECK (changed IN (0, 1)),
            error TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """,
        (
            "target_id",
            "status",
            "requested_at",
            "last_attempted_at",
            "last_succeeded_at",
            "last_failed_at",
            "last_reported_url",
            "last_resolved_url",
            "last_result",
            "changed",
            "error",
            "updated_at",
        ),
    ),
    CheckedTableRebuild(
        "global_notification_settings",
        """
        CREATE TABLE {table_name} (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enable_desktop_notification INTEGER NOT NULL CHECK (
                enable_desktop_notification IN (0, 1)
            ),
            enable_ntfy INTEGER NOT NULL CHECK (enable_ntfy IN (0, 1)),
            ntfy_topic TEXT NOT NULL,
            enable_discord_notification INTEGER NOT NULL CHECK (
                enable_discord_notification IN (0, 1)
            ),
            discord_webhook TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        (
            "id",
            "enable_desktop_notification",
            "enable_ntfy",
            "ntfy_topic",
            "enable_discord_notification",
            "discord_webhook",
            "updated_at",
        ),
    ),
    CheckedTableRebuild(
        "sidebar_group_config_templates",
        """
        CREATE TABLE {table_name} (
            sidebar_group_id TEXT PRIMARY KEY REFERENCES sidebar_groups(id) ON DELETE CASCADE,
            include_keywords TEXT NOT NULL DEFAULT '[]',
            include_keyword_groups TEXT NOT NULL DEFAULT '[]',
            exclude_keywords TEXT NOT NULL DEFAULT '[]',
            exclude_ignore_phrases TEXT NOT NULL DEFAULT '[]',
            min_refresh_sec INTEGER NOT NULL CHECK (min_refresh_sec >= 5),
            max_refresh_sec INTEGER NOT NULL CHECK (
                max_refresh_sec >= 5 AND max_refresh_sec >= min_refresh_sec
            ),
            jitter_enabled INTEGER NOT NULL CHECK (jitter_enabled IN (0, 1)),
            fixed_refresh_sec INTEGER,
            max_items_per_scan INTEGER NOT NULL CHECK (max_items_per_scan > 0),
            auto_load_more INTEGER NOT NULL CHECK (auto_load_more IN (0, 1)),
            auto_adjust_sort INTEGER NOT NULL CHECK (auto_adjust_sort IN (0, 1)),
            enable_desktop_notification INTEGER NOT NULL CHECK (
                enable_desktop_notification IN (0, 1)
            ),
            enable_ntfy INTEGER NOT NULL CHECK (enable_ntfy IN (0, 1)),
            ntfy_topic TEXT NOT NULL,
            enable_discord_notification INTEGER NOT NULL CHECK (
                enable_discord_notification IN (0, 1)
            ),
            discord_webhook TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        (
            "sidebar_group_id",
            "include_keywords",
            "include_keyword_groups",
            "exclude_keywords",
            "exclude_ignore_phrases",
            "min_refresh_sec",
            "max_refresh_sec",
            "jitter_enabled",
            "fixed_refresh_sec",
            "max_items_per_scan",
            "auto_load_more",
            "auto_adjust_sort",
            "enable_desktop_notification",
            "enable_ntfy",
            "ntfy_topic",
            "enable_discord_notification",
            "discord_webhook",
            "updated_at",
        ),
    ),
)


MIGRATIONS: dict[int, Migration] = {
    10: migrate_10_to_11,
    11: migrate_11_to_12,
    12: migrate_12_to_13,
    13: migrate_13_to_14,
    14: migrate_14_to_15,
    15: migrate_15_to_16,
    16: migrate_16_to_17,
    17: migrate_17_to_18,
    18: migrate_18_to_19,
    19: migrate_19_to_20,
    20: migrate_20_to_21,
    21: migrate_21_to_22,
    22: migrate_22_to_23,
    23: migrate_23_to_24,
    24: migrate_24_to_25,
    25: migrate_25_to_26,
    26: migrate_26_to_27,
    27: migrate_27_to_28,
    28: migrate_28_to_29,
    29: migrate_29_to_30,
    30: migrate_30_to_31,
    31: migrate_31_to_32,
    32: migrate_32_to_33,
    33: migrate_33_to_34,
    34: migrate_34_to_35,
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


def rebuild_table_with_check_constraints(
    connection: sqlite3.Connection,
    spec: CheckedTableRebuild,
) -> None:
    """以 create-copy-drop-rename 將 CHECK constraints 套到既有 child table。"""

    if not table_exists(connection, spec.table_name):
        return
    temp_table = f"__{spec.table_name}_v30_checked"
    connection.execute(f"DROP TABLE IF EXISTS {temp_table}")
    connection.execute(spec.create_sql_template.format(table_name=temp_table))
    columns_sql = ", ".join(spec.columns)
    connection.execute(
        f"""
        INSERT INTO {temp_table} ({columns_sql})
        SELECT {columns_sql}
        FROM {spec.table_name}
        """
    )
    connection.execute(f"DROP TABLE {spec.table_name}")
    connection.execute(f"ALTER TABLE {temp_table} RENAME TO {spec.table_name}")


def ensure_v30_rebuilt_table_indexes(connection: sqlite3.Connection) -> None:
    """重建 v30 table rebuild 會移除的查詢索引。"""

    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_scan_runs_target_created
            ON scan_runs(target_id, started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_scan_runs_target_id_desc
            ON scan_runs(target_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_scan_runs_target_status_id_desc
            ON scan_runs(target_id, status, id DESC);
        CREATE INDEX IF NOT EXISTS idx_notification_events_target_created
            ON notification_events(target_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_notification_events_target_id_desc
            ON notification_events(target_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_notification_events_target_channel_id_desc
            ON notification_events(target_id, channel, id DESC);
        CREATE INDEX IF NOT EXISTS idx_runtime_state_status_updated
            ON target_runtime_state(runtime_status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_runtime_state_desired_updated
            ON target_runtime_state(desired_state, updated_at);
        CREATE INDEX IF NOT EXISTS idx_notification_outbox_status_updated
            ON notification_outbox(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_cover_image_refresh_status_requested
            ON target_cover_image_refresh_state(status, requested_at);
        """
    )


def add_column_if_missing(
    connection: sqlite3.Connection,
    column: MigrationColumn,
) -> None:
    """在 migration 內補齊舊表缺欄；table 不存在時交給 current schema bootstrap 建立。"""

    if not table_exists(connection, column.table_name):
        return
    rows = connection.execute(f"PRAGMA table_info({column.table_name})").fetchall()
    if any(row[1] == column.column_name for row in rows):
        return
    connection.execute(
        f"ALTER TABLE {column.table_name} ADD COLUMN {column.column_name} {column.definition}"
    )


def _backfill_include_keyword_groups(connection: sqlite3.Connection, table_name: str) -> None:
    """將既有 flat include keywords 回填到 include group 第 1 組。"""

    if not table_exists(connection, table_name):
        return
    rows = connection.execute(
        f"""
        SELECT rowid, include_keywords, include_keyword_groups
        FROM {table_name}
        """
    ).fetchall()
    for row in rows:
        if row["include_keyword_groups"] and row["include_keyword_groups"] != "[]":
            continue
        keywords = decode_keywords(row["include_keywords"])
        groups = legacy_include_keyword_groups(keywords, fill_empty_slots=True)
        connection.execute(
            f"""
            UPDATE {table_name}
            SET include_keyword_groups = ?
            WHERE rowid = ?
            """,
            (encode_include_keyword_groups(groups), row["rowid"]),
        )


def _backfill_display_text_from_text(connection: sqlite3.Connection, table_name: str) -> None:
    """舊資料沒有 display_text 時，以既有 text 回填可呈現內容。"""

    if not table_exists(connection, table_name):
        return
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    column_names = {str(row[1]) for row in rows}
    if "text" not in column_names or "display_text" not in column_names:
        return
    connection.execute(
        f"""
        UPDATE {table_name}
        SET display_text = text
        WHERE display_text = ''
          AND text <> ''
        """
    )


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    """回傳 SQLite table 是否存在。"""

    row = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def ensure_legacy_group_configs_table(connection: sqlite3.Connection) -> None:
    """建立舊版 group_configs migration 暫存表；fresh schema 不再使用此表。"""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS group_configs (
            group_id TEXT PRIMARY KEY,
            include_keywords TEXT NOT NULL,
            exclude_keywords TEXT NOT NULL,
            min_refresh_sec INTEGER NOT NULL,
            max_refresh_sec INTEGER NOT NULL,
            jitter_enabled INTEGER NOT NULL,
            fixed_refresh_sec INTEGER,
            max_items_per_scan INTEGER NOT NULL,
            auto_load_more INTEGER NOT NULL,
            auto_adjust_sort INTEGER NOT NULL,
            enable_desktop_notification INTEGER NOT NULL DEFAULT 0,
            enable_ntfy INTEGER NOT NULL,
            ntfy_topic TEXT NOT NULL,
            enable_discord_notification INTEGER NOT NULL DEFAULT 0,
            discord_webhook TEXT NOT NULL DEFAULT ''
        )
        """
    )


__all__ = [
    "CheckedTableRebuild",
    "MIGRATIONS",
    "Migration",
    "MigrationColumn",
    "V12_TO_13_COLUMNS",
    "V29_TO_V30_CHECKED_TABLES",
    "add_column_if_missing",
    "ensure_v30_rebuilt_table_indexes",
    "migrate_10_to_11",
    "migrate_11_to_12",
    "migrate_12_to_13",
    "migrate_13_to_14",
    "migrate_14_to_15",
    "migrate_15_to_16",
    "migrate_16_to_17",
    "migrate_17_to_18",
    "migrate_18_to_19",
    "migrate_19_to_20",
    "migrate_20_to_21",
    "migrate_21_to_22",
    "migrate_22_to_23",
    "migrate_23_to_24",
    "migrate_24_to_25",
    "migrate_25_to_26",
    "migrate_26_to_27",
    "migrate_27_to_28",
    "migrate_28_to_29",
    "migrate_29_to_30",
    "migrate_30_to_31",
    "migrate_31_to_32",
    "migrate_32_to_33",
    "migrate_33_to_34",
    "migrate_34_to_35",
    "rebuild_table_with_check_constraints",
    "run_known_migrations",
]
