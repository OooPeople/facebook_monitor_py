"""目前版本 SQLite schema bootstrap。

職責：集中全新 DB 初始化需要的 table、index 與 dashboard revision triggers。
歷史 migration 與啟動後 repair guard 留在 `schema.py` 流程層處理。
"""

from __future__ import annotations

import sqlite3


CURRENT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS targets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    group_id TEXT NOT NULL,
    group_name TEXT NOT NULL,
    group_cover_image_url TEXT NOT NULL DEFAULT '',
    parent_post_id TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    metadata_status TEXT NOT NULL DEFAULT 'resolved',
    metadata_error TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL,
    paused INTEGER NOT NULL,
    worker_mode TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS target_configs (
    target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
    include_keywords TEXT NOT NULL,
    include_keyword_groups TEXT NOT NULL DEFAULT '[]',
    exclude_keywords TEXT NOT NULL,
    exclude_ignore_phrases TEXT NOT NULL DEFAULT '[]',
    min_refresh_sec INTEGER NOT NULL CHECK (min_refresh_sec >= 5),
    max_refresh_sec INTEGER NOT NULL CHECK (max_refresh_sec >= 5 AND max_refresh_sec >= min_refresh_sec),
    jitter_enabled INTEGER NOT NULL CHECK (jitter_enabled IN (0, 1)),
    fixed_refresh_sec INTEGER,
    max_items_per_scan INTEGER NOT NULL CHECK (max_items_per_scan > 0),
    auto_load_more INTEGER NOT NULL CHECK (auto_load_more IN (0, 1)),
    auto_adjust_sort INTEGER NOT NULL CHECK (auto_adjust_sort IN (0, 1)),
    enable_desktop_notification INTEGER NOT NULL CHECK (enable_desktop_notification IN (0, 1)),
    enable_ntfy INTEGER NOT NULL CHECK (enable_ntfy IN (0, 1)),
    ntfy_topic TEXT NOT NULL,
    enable_discord_notification INTEGER NOT NULL CHECK (enable_discord_notification IN (0, 1)),
    discord_webhook TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_items (
    scope_id TEXT NOT NULL,
    item_key TEXT NOT NULL,
    item_kind TEXT NOT NULL CHECK (item_kind IN ('post', 'comment')),
    parent_post_id TEXT NOT NULL,
    comment_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (scope_id, item_key)
);

CREATE TABLE IF NOT EXISTS scan_scope_state (
    scope_id TEXT PRIMARY KEY,
    initialized INTEGER NOT NULL CHECK (initialized IN (0, 1)),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS match_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    group_id TEXT NOT NULL,
    group_name TEXT NOT NULL,
    item_kind TEXT NOT NULL,
    parent_post_id TEXT NOT NULL,
    comment_id TEXT NOT NULL,
    item_key TEXT NOT NULL,
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    permalink TEXT NOT NULL,
    include_rule TEXT NOT NULL,
    timestamp_text TEXT NOT NULL,
    notified_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS match_history_matches (
    history_id INTEGER NOT NULL REFERENCES match_history(id) ON DELETE CASCADE,
    match_order INTEGER NOT NULL,
    rule TEXT NOT NULL,
    keyword_group_id TEXT NOT NULL DEFAULT '',
    keyword_group_label TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (history_id, match_order)
);

CREATE TABLE IF NOT EXISTS latest_scan_items (
    target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    scan_run_id INTEGER NOT NULL,
    item_kind TEXT NOT NULL,
    item_key TEXT NOT NULL,
    item_index INTEGER NOT NULL,
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    permalink TEXT NOT NULL,
    matched_keyword TEXT NOT NULL,
    debug_metadata TEXT NOT NULL DEFAULT '{}',
    scanned_at TEXT NOT NULL,
    PRIMARY KEY (target_id, item_key)
);

CREATE TABLE IF NOT EXISTS latest_scan_item_matches (
    target_id TEXT NOT NULL,
    item_key TEXT NOT NULL,
    match_order INTEGER NOT NULL,
    rule TEXT NOT NULL,
    keyword_group_id TEXT NOT NULL DEFAULT '',
    keyword_group_label TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (target_id, item_key, match_order),
    FOREIGN KEY (target_id, item_key)
        REFERENCES latest_scan_items(target_id, item_key)
        ON UPDATE CASCADE
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scan_runs (
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
);

CREATE TABLE IF NOT EXISTS notification_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    item_key TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('desktop', 'ntfy', 'discord')),
    status TEXT NOT NULL CHECK (status IN ('sent', 'failed', 'skipped')),
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    item_key TEXT NOT NULL,
    item_kind TEXT NOT NULL CHECK (item_kind IN ('post', 'comment')),
    channel TEXT NOT NULL CHECK (channel IN ('desktop', 'ntfy', 'discord')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'processing_pending', 'sent', 'failed', 'processing_failed', 'skipped')),
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    endpoint TEXT NOT NULL DEFAULT '',
    permalink TEXT NOT NULL,
    attempts INTEGER NOT NULL CHECK (attempts >= 0),
    last_error TEXT NOT NULL,
    notification_event_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS target_runtime_state (
    target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
    desired_state TEXT NOT NULL CHECK (desired_state IN ('active', 'stopped')),
    runtime_status TEXT NOT NULL CHECK (runtime_status IN ('idle', 'queued', 'running', 'error')),
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
    consecutive_failure_count INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failure_count >= 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS target_cover_image_refresh_state (
    target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('idle', 'pending', 'failed')),
    requested_at TEXT NOT NULL DEFAULT '',
    last_attempted_at TEXT NOT NULL DEFAULT '',
    last_succeeded_at TEXT NOT NULL DEFAULT '',
    last_failed_at TEXT NOT NULL DEFAULT '',
    last_reported_url TEXT NOT NULL DEFAULT '',
    last_resolved_url TEXT NOT NULL DEFAULT '',
    last_result TEXT NOT NULL DEFAULT '' CHECK (last_result IN ('', 'queued', 'attempted', 'succeeded_changed', 'succeeded_unchanged', 'stale_skipped', 'failed')),
    changed INTEGER NOT NULL DEFAULT 0 CHECK (changed IN (0, 1)),
    error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS global_notification_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enable_desktop_notification INTEGER NOT NULL CHECK (enable_desktop_notification IN (0, 1)),
    enable_ntfy INTEGER NOT NULL CHECK (enable_ntfy IN (0, 1)),
    ntfy_topic TEXT NOT NULL,
    enable_discord_notification INTEGER NOT NULL CHECK (enable_discord_notification IN (0, 1)),
    discord_webhook TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

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

CREATE TABLE IF NOT EXISTS sidebar_group_config_templates (
    sidebar_group_id TEXT PRIMARY KEY REFERENCES sidebar_groups(id) ON DELETE CASCADE,
    include_keywords TEXT NOT NULL DEFAULT '[]',
    include_keyword_groups TEXT NOT NULL DEFAULT '[]',
    exclude_keywords TEXT NOT NULL DEFAULT '[]',
    exclude_ignore_phrases TEXT NOT NULL DEFAULT '[]',
    min_refresh_sec INTEGER NOT NULL CHECK (min_refresh_sec >= 5),
    max_refresh_sec INTEGER NOT NULL CHECK (max_refresh_sec >= 5 AND max_refresh_sec >= min_refresh_sec),
    jitter_enabled INTEGER NOT NULL CHECK (jitter_enabled IN (0, 1)),
    fixed_refresh_sec INTEGER,
    max_items_per_scan INTEGER NOT NULL CHECK (max_items_per_scan > 0),
    auto_load_more INTEGER NOT NULL CHECK (auto_load_more IN (0, 1)),
    auto_adjust_sort INTEGER NOT NULL CHECK (auto_adjust_sort IN (0, 1)),
    enable_desktop_notification INTEGER NOT NULL CHECK (enable_desktop_notification IN (0, 1)),
    enable_ntfy INTEGER NOT NULL CHECK (enable_ntfy IN (0, 1)),
    ntfy_topic TEXT NOT NULL,
    enable_discord_notification INTEGER NOT NULL CHECK (enable_discord_notification IN (0, 1)),
    discord_webhook TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dashboard_revision (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO dashboard_revision (id, revision, updated_at)
VALUES (1, 0, '');

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
CREATE INDEX IF NOT EXISTS idx_latest_scan_items_target_index
    ON latest_scan_items(target_id, item_index);
CREATE INDEX IF NOT EXISTS idx_latest_scan_item_matches_target_item
    ON latest_scan_item_matches(target_id, item_key, match_order);
CREATE INDEX IF NOT EXISTS idx_match_history_matches_history
    ON match_history_matches(history_id, match_order);
CREATE INDEX IF NOT EXISTS idx_runtime_state_status_updated
    ON target_runtime_state(runtime_status, updated_at);
CREATE INDEX IF NOT EXISTS idx_runtime_state_desired_updated
    ON target_runtime_state(desired_state, updated_at);
CREATE INDEX IF NOT EXISTS idx_notification_outbox_status_updated
    ON notification_outbox(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_cover_image_refresh_status_requested
    ON target_cover_image_refresh_state(status, requested_at);
CREATE INDEX IF NOT EXISTS idx_sidebar_groups_order
    ON sidebar_groups(sort_order);
CREATE INDEX IF NOT EXISTS idx_sidebar_target_placements_group_order
    ON sidebar_target_placements(sidebar_group_id, sort_order);
"""


DASHBOARD_REVISION_TABLES = (
    "targets",
    "target_configs",
    "target_runtime_state",
    "scan_runs",
    "notification_events",
    "notification_outbox",
    "latest_scan_items",
    "match_history",
    "app_settings",
    "sidebar_groups",
    "sidebar_target_placements",
    "sidebar_group_config_templates",
)


def create_current_schema(connection: sqlite3.Connection) -> None:
    """建立目前版本新 DB 所需 schema 與 dashboard revision triggers。"""

    connection.executescript(CURRENT_SCHEMA_SQL)
    ensure_dashboard_revision_triggers(connection)


def ensure_dashboard_revision_triggers(connection: sqlite3.Connection) -> None:
    """建立 dashboard revision bump triggers，讓 polling query 固定成本。"""

    stale_triggers = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'trigger'
          AND name LIKE 'trg_dashboard_revision_%'
        """
    ).fetchall()
    for row in stale_triggers:
        trigger_name = str(row[0]).replace('"', '""')
        connection.execute(f'DROP TRIGGER IF EXISTS "{trigger_name}"')

    for table_name in DASHBOARD_REVISION_TABLES:
        for operation in ("INSERT", "UPDATE", "DELETE"):
            trigger_name = f"trg_dashboard_revision_{table_name}_{operation.lower()}"
            connection.execute(
                f"""
                CREATE TRIGGER {trigger_name}
                AFTER {operation} ON {table_name}
                BEGIN
                    UPDATE dashboard_revision
                    SET revision = revision + 1,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = 1;
                END
                """
            )
