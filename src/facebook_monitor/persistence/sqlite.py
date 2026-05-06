"""Phase A SQLite schema 與 repository。

職責：以 stdlib sqlite3 提供最小 target/config/seen/scan/notification 持久化。
此層不處理 Playwright 流程，也不承擔 worker orchestration。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from facebook_monitor.core.models import GlobalNotificationSettings
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import WorkerMode


SCHEMA_VERSION = 10


def encode_datetime(value: datetime | None) -> str:
    """將 datetime 轉成 SQLite 可保存的 ISO 字串。"""

    return value.isoformat() if value else ""


def decode_datetime(value: str) -> datetime | None:
    """將 SQLite ISO 字串還原為 datetime。"""

    return datetime.fromisoformat(value) if value else None


def encode_keywords(values: Iterable[str]) -> str:
    """將 keyword tuple/list 編碼為 JSON。"""

    return json.dumps(list(values), ensure_ascii=False)


def decode_keywords(value: str) -> tuple[str, ...]:
    """將 keyword JSON 還原為 tuple。"""

    if not value:
        return ()
    return tuple(str(item) for item in json.loads(value))


class SqliteConnection(AbstractContextManager["SqliteConnection"]):
    """管理 SQLite 連線與 schema 初始化。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> SqliteConnection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        self.connection = connection
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if not self.connection:
            return
        if exc_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
        self.connection.close()
        self.connection = None

    def require_connection(self) -> sqlite3.Connection:
        """取得已開啟連線，未開啟時回報明確錯誤。"""

        if self.connection is None:
            raise RuntimeError("SQLite connection is not open")
        return self.connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    """建立 Phase A 最小 schema。"""

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        INSERT OR REPLACE INTO schema_metadata (key, value)
        VALUES ('version', '10');

        CREATE TABLE IF NOT EXISTS targets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            target_kind TEXT NOT NULL,
            group_id TEXT NOT NULL,
            group_name TEXT NOT NULL,
            parent_post_id TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            paused INTEGER NOT NULL,
            worker_mode TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS target_configs (
            target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
            include_keywords TEXT NOT NULL,
            exclude_keywords TEXT NOT NULL,
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
            discord_webhook TEXT NOT NULL
        );

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
            enable_desktop_notification INTEGER NOT NULL,
            enable_ntfy INTEGER NOT NULL,
            ntfy_topic TEXT NOT NULL,
            enable_discord_notification INTEGER NOT NULL,
            discord_webhook TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS seen_items (
            scope_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            item_kind TEXT NOT NULL,
            parent_post_id TEXT NOT NULL,
            comment_id TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (scope_id, item_key)
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

        CREATE TABLE IF NOT EXISTS scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            status TEXT NOT NULL,
            item_count INTEGER NOT NULL,
            matched_count INTEGER NOT NULL,
            error_message TEXT NOT NULL,
            worker_mode TEXT NOT NULL,
            metadata TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notification_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            item_key TEXT NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS target_runtime_state (
            target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
            desired_state TEXT NOT NULL,
            runtime_status TEXT NOT NULL,
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
            scan_guard_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS global_notification_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enable_desktop_notification INTEGER NOT NULL,
            enable_ntfy INTEGER NOT NULL,
            ntfy_topic TEXT NOT NULL,
            enable_discord_notification INTEGER NOT NULL,
            discord_webhook TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    ensure_column(
        connection,
        table_name="latest_scan_items",
        column_name="debug_metadata",
        definition="TEXT NOT NULL DEFAULT '{}'",
    )
    ensure_column(
        connection,
        table_name="target_runtime_state",
        column_name="scan_requested_at",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="target_runtime_state",
        column_name="last_skip_reason",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="target_runtime_state",
        column_name="last_enqueued_at",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="target_runtime_state",
        column_name="last_started_at",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="target_runtime_state",
        column_name="last_finished_at",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="target_runtime_state",
        column_name="enqueue_reason",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="target_runtime_state",
        column_name="active_page_id",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="target_runtime_state",
        column_name="last_page_reloaded_at",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="target_runtime_state",
        column_name="scan_guard_count",
        definition="INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(
        connection,
        table_name="target_configs",
        column_name="enable_desktop_notification",
        definition="INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(
        connection,
        table_name="target_configs",
        column_name="enable_discord_notification",
        definition="INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(
        connection,
        table_name="target_configs",
        column_name="discord_webhook",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="group_configs",
        column_name="enable_desktop_notification",
        definition="INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(
        connection,
        table_name="group_configs",
        column_name="enable_discord_notification",
        definition="INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(
        connection,
        table_name="group_configs",
        column_name="discord_webhook",
        definition="TEXT NOT NULL DEFAULT ''",
    )


def ensure_column(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    """確保既有 SQLite table 有指定欄位，供小型 schema migration 使用。"""

    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    if any(row["name"] == column_name for row in rows):
        return
    connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


class TargetRepository:
    """保存與查詢 target descriptor。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, target: TargetDescriptor) -> None:
        """新增或更新 target。"""

        self.connection.execute(
            """
            INSERT INTO targets (
                id, name, target_kind, group_id, group_name, parent_post_id,
                scope_id, canonical_url, enabled, paused, worker_mode, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                target_kind=excluded.target_kind,
                group_id=excluded.group_id,
                group_name=excluded.group_name,
                parent_post_id=excluded.parent_post_id,
                scope_id=excluded.scope_id,
                canonical_url=excluded.canonical_url,
                enabled=excluded.enabled,
                paused=excluded.paused,
                worker_mode=excluded.worker_mode,
                updated_at=excluded.updated_at
            """,
            (
                target.id,
                target.name,
                target.target_kind.value,
                target.group_id,
                target.group_name,
                target.parent_post_id,
                target.scope_id,
                target.canonical_url,
                int(target.enabled),
                int(target.paused),
                target.worker_mode.value,
                encode_datetime(target.created_at),
                encode_datetime(target.updated_at),
            ),
        )

    def get(self, target_id: str) -> TargetDescriptor | None:
        """依 id 查詢 target。"""

        row = self.connection.execute("SELECT * FROM targets WHERE id = ?", (target_id,)).fetchone()
        return _target_from_row(row) if row else None

    def delete(self, target_id: str) -> bool:
        """刪除單一 target，回傳是否真的刪到資料。"""

        cursor = self.connection.execute("DELETE FROM targets WHERE id = ?", (target_id,))
        return cursor.rowcount > 0

    def list_enabled(self) -> list[TargetDescriptor]:
        """列出啟用且未暫停的 target。"""

        rows = self.connection.execute(
            "SELECT * FROM targets WHERE enabled = 1 AND paused = 0 ORDER BY created_at"
        ).fetchall()
        return [_target_from_row(row) for row in rows]

    def list_all(self) -> list[TargetDescriptor]:
        """列出所有 target，供設定管理入口使用。"""

        rows = self.connection.execute("SELECT * FROM targets ORDER BY created_at").fetchall()
        return [_target_from_row(row) for row in rows]

    def find_by_kind_scope(
        self,
        target_kind: TargetKind,
        scope_id: str,
    ) -> TargetDescriptor | None:
        """依 target 類型與 scope 查詢既有 target。"""

        row = self.connection.execute(
            """
            SELECT * FROM targets
            WHERE target_kind = ? AND scope_id = ?
            ORDER BY created_at
            LIMIT 1
            """,
            (target_kind.value, scope_id),
        ).fetchone()
        return _target_from_row(row) if row else None


class TargetConfigRepository:
    """保存與查詢監視設定。

    正式路徑只能使用 group-scoped `group_configs`，對齊 userscript 的
    group config 語義；舊版 `target_configs` 只允許 migration fallback 使用。
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save_legacy_target_config_for_migration(self, config: TargetConfig) -> None:
        """新增或更新舊版 target-scoped config，僅供 migration 測試或舊資料準備。"""

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
                config.target_id,
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

    def get_legacy_target_config_for_migration(self, target_id: str) -> TargetConfig | None:
        """依舊版 target id 查詢設定，正式功能不得直接使用。"""

        row = self.connection.execute(
            "SELECT * FROM target_configs WHERE target_id = ?", (target_id,)
        ).fetchone()
        if not row:
            return None
        return _target_config_from_row(row, id_column="target_id")

    def save_for_group(self, group_id: str, config: TargetConfig) -> TargetConfig:
        """新增或更新 group-scoped config，回傳保存後的 config。"""

        normalized_group_id = group_id.strip()
        if not normalized_group_id:
            raise ValueError("group_id is required for group-scoped config")
        group_config = replace(config, target_id=normalized_group_id)
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
                group_config.target_id,
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
        return _target_config_from_row(row, id_column="group_id")

    def get_for_target(self, target: TargetDescriptor) -> TargetConfig | None:
        """依 target 查詢 group-scoped config，必要時從舊 target config 遷移。"""

        group_config = self.get_for_group(target.group_id)
        if group_config:
            return group_config

        legacy_config = self.get_legacy_target_config_for_migration(target.id)
        if legacy_config is None:
            return None
        return self.save_for_target(target, legacy_config)


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


class TargetRuntimeStateRepository:
    """保存與查詢 target scheduler runtime state。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, state: TargetRuntimeState) -> None:
        """新增或更新單一 target runtime state。"""

        self.connection.execute(
            """
            INSERT INTO target_runtime_state (
                target_id, desired_state, runtime_status, scan_requested_at, last_enqueued_at,
                last_started_at, last_finished_at, last_heartbeat_at, last_error,
                last_skip_reason, enqueue_reason, active_worker_id, active_page_id,
                last_page_reloaded_at, scan_guard_count, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET
                desired_state=excluded.desired_state,
                runtime_status=excluded.runtime_status,
                scan_requested_at=excluded.scan_requested_at,
                last_enqueued_at=excluded.last_enqueued_at,
                last_started_at=excluded.last_started_at,
                last_finished_at=excluded.last_finished_at,
                last_heartbeat_at=excluded.last_heartbeat_at,
                last_error=excluded.last_error,
                last_skip_reason=excluded.last_skip_reason,
                enqueue_reason=excluded.enqueue_reason,
                active_worker_id=excluded.active_worker_id,
                active_page_id=excluded.active_page_id,
                last_page_reloaded_at=excluded.last_page_reloaded_at,
                scan_guard_count=excluded.scan_guard_count,
                updated_at=excluded.updated_at
            """,
            (
                state.target_id,
                state.desired_state.value,
                state.runtime_status.value,
                encode_datetime(state.scan_requested_at),
                encode_datetime(state.last_enqueued_at),
                encode_datetime(state.last_started_at),
                encode_datetime(state.last_finished_at),
                encode_datetime(state.last_heartbeat_at),
                state.last_error,
                state.last_skip_reason,
                state.enqueue_reason,
                state.active_worker_id,
                state.active_page_id,
                encode_datetime(state.last_page_reloaded_at),
                state.scan_guard_count,
                encode_datetime(state.updated_at),
            ),
        )

    def get(self, target_id: str) -> TargetRuntimeState | None:
        """依 target id 查詢 runtime state。"""

        row = self.connection.execute(
            "SELECT * FROM target_runtime_state WHERE target_id = ?",
            (target_id,),
        ).fetchone()
        return _runtime_state_from_row(row) if row else None

    def list_desired_active(self) -> list[TargetRuntimeState]:
        """列出期望由 scheduler 掃描的 target runtime state。"""

        rows = self.connection.execute(
            """
            SELECT * FROM target_runtime_state
            WHERE desired_state = ?
            ORDER BY updated_at
            """,
            (TargetDesiredState.ACTIVE.value,),
        ).fetchall()
        return [_runtime_state_from_row(row) for row in rows]

    def list_all(self) -> list[TargetRuntimeState]:
        """列出所有 target runtime state，供 stale recovery 使用。"""

        rows = self.connection.execute(
            "SELECT * FROM target_runtime_state ORDER BY updated_at"
        ).fetchall()
        return [_runtime_state_from_row(row) for row in rows]


class SeenItemRepository:
    """保存 seen item 去重狀態。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def mark_seen(self, item: SeenItem) -> bool:
        """標記 item 已看過；回傳是否為第一次看見。"""

        return self.mark_seen_aliases(item, (item.item_key,))

    def mark_seen_aliases(self, item: SeenItem, item_keys: Iterable[str]) -> bool:
        """標記 item 與所有等價 aliases 已看過；回傳 aliases 是否全新。"""

        keys = tuple(dict.fromkeys(key.strip() for key in item_keys if key.strip()))
        if not keys:
            return False

        is_new = not self.has_seen_any(item.scope_id, keys)
        for item_key in keys:
            self._upsert_seen_item(
                SeenItem(
                    scope_id=item.scope_id,
                    item_key=item_key,
                    item_kind=item.item_kind,
                    parent_post_id=item.parent_post_id,
                    comment_id=item.comment_id,
                    first_seen_at=item.first_seen_at,
                    last_seen_at=item.last_seen_at,
                )
            )
        return is_new

    def _upsert_seen_item(self, item: SeenItem) -> None:
        """新增或更新單一 seen item key。"""

        existing = self.connection.execute(
            "SELECT first_seen_at FROM seen_items WHERE scope_id = ? AND item_key = ?",
            (item.scope_id, item.item_key),
        ).fetchone()
        if existing:
            self.connection.execute(
                """
                UPDATE seen_items
                SET last_seen_at = ?, item_kind = ?, parent_post_id = ?, comment_id = ?
                WHERE scope_id = ? AND item_key = ?
                """,
                (
                    encode_datetime(item.last_seen_at),
                    item.item_kind.value,
                    item.parent_post_id,
                    item.comment_id,
                    item.scope_id,
                    item.item_key,
                ),
            )
            return

        self.connection.execute(
            """
            INSERT INTO seen_items (
                scope_id, item_key, item_kind, parent_post_id, comment_id,
                first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.scope_id,
                item.item_key,
                item.item_kind.value,
                item.parent_post_id,
                item.comment_id,
                encode_datetime(item.first_seen_at),
                encode_datetime(item.last_seen_at),
            ),
        )

    def has_seen(self, scope_id: str, item_key: str) -> bool:
        """檢查 item 是否已看過。"""

        return self.has_seen_any(scope_id, (item_key,))

    def has_seen_any(self, scope_id: str, item_keys: Iterable[str]) -> bool:
        """檢查任一 item key alias 是否已看過。"""

        keys = tuple(dict.fromkeys(key.strip() for key in item_keys if key.strip()))
        if not keys:
            return False
        placeholders = ", ".join("?" for _ in keys)
        row = self.connection.execute(
            f"""
            SELECT 1 FROM seen_items
            WHERE scope_id = ? AND item_key IN ({placeholders})
            LIMIT 1
            """,
            (scope_id, *keys),
        ).fetchone()
        return row is not None

    def clear_scope(self, scope_id: str) -> int:
        """清空指定 scan scope 的 seen item，對齊 userscript 開始監控語義。"""

        normalized_scope_id = scope_id.strip()
        if not normalized_scope_id:
            return 0
        cursor = self.connection.execute(
            "DELETE FROM seen_items WHERE scope_id = ?",
            (normalized_scope_id,),
        )
        return cursor.rowcount


class ScanRunRepository:
    """保存 scan run 結果。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, run: ScanRun) -> int:
        """新增 scan run 並回傳 row id。"""

        cursor = self.connection.execute(
            """
            INSERT INTO scan_runs (
                target_id, started_at, finished_at, status, item_count,
                matched_count, error_message, worker_mode, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.target_id,
                encode_datetime(run.started_at),
                encode_datetime(run.finished_at),
                run.status.value,
                run.item_count,
                run.matched_count,
                run.error_message,
                run.worker_mode.value,
                json.dumps(run.metadata, ensure_ascii=False),
            ),
        )
        return int(cursor.lastrowid)

    def latest_by_target(
        self,
        target_id: str,
        status: ScanStatus | None = None,
    ) -> ScanRun | None:
        """查詢單一 target 最近一筆 scan run，可依狀態過濾。"""

        if status is None:
            row = self.connection.execute(
                """
                SELECT * FROM scan_runs
                WHERE target_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (target_id,),
            ).fetchone()
        else:
            row = self.connection.execute(
                """
                SELECT * FROM scan_runs
                WHERE target_id = ? AND status = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (target_id, status.value),
            ).fetchone()
        return _scan_run_from_row(row) if row else None


class MatchHistoryRepository:
    """保存 keyword match 歷史。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, entry: MatchHistoryEntry) -> int:
        """新增 match history 並回傳 row id。"""

        cursor = self.connection.execute(
            """
            INSERT INTO match_history (
                target_id, group_id, group_name, item_kind, parent_post_id,
                comment_id, item_key, author, text, permalink, include_rule,
                timestamp_text, notified_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.target_id,
                entry.group_id,
                entry.group_name,
                entry.item_kind.value,
                entry.parent_post_id,
                entry.comment_id,
                entry.item_key,
                entry.author,
                entry.text,
                entry.permalink,
                entry.include_rule,
                entry.timestamp_text,
                encode_datetime(entry.notified_at),
                encode_datetime(entry.created_at),
            ),
        )
        return int(cursor.lastrowid)

    def list_by_target(self, target_id: str, limit: int = 50) -> list[MatchHistoryEntry]:
        """依 target id 查詢最近 match history。"""

        rows = self.connection.execute(
            """
            SELECT * FROM match_history
            WHERE target_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (target_id, limit),
        ).fetchall()
        return [_match_history_from_row(row) for row in rows]


class LatestScanItemRepository:
    """保存每個 target 最近一輪掃描到的貼文候選。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def replace_for_target(self, target_id: str, items: Iterable[LatestScanItem]) -> None:
        """覆蓋單一 target 的最近掃描貼文清單。"""

        self.connection.execute("DELETE FROM latest_scan_items WHERE target_id = ?", (target_id,))
        self.connection.executemany(
            """
            INSERT INTO latest_scan_items (
                target_id, scan_run_id, item_kind, item_key, item_index,
                author, text, permalink, matched_keyword, debug_metadata, scanned_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.target_id,
                    item.scan_run_id,
                    item.item_kind.value,
                    item.item_key,
                    item.item_index,
                    item.author,
                    item.text,
                    item.permalink,
                    item.matched_keyword,
                    json.dumps(item.debug_metadata, ensure_ascii=False),
                    encode_datetime(item.scanned_at),
                )
                for item in items
            ],
        )

    def list_by_target(self, target_id: str, limit: int = 50) -> list[LatestScanItem]:
        """依 target id 查詢最近一輪掃描到的貼文候選。"""

        rows = self.connection.execute(
            """
            SELECT * FROM latest_scan_items
            WHERE target_id = ?
            ORDER BY item_index
            LIMIT ?
            """,
            (target_id, limit),
        ).fetchall()
        return [_latest_scan_item_from_row(row) for row in rows]


class NotificationEventRepository:
    """保存通知事件。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, event: NotificationEvent) -> int:
        """新增通知事件並回傳 row id。"""

        cursor = self.connection.execute(
            """
            INSERT INTO notification_events (
                target_id, item_key, channel, status, message, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.target_id,
                event.item_key,
                event.channel.value,
                event.status.value,
                event.message,
                encode_datetime(event.created_at),
            ),
        )
        return int(cursor.lastrowid)

    def list_by_target(self, target_id: str, limit: int = 50) -> list[NotificationEvent]:
        """依 target id 查詢最近 notification events。"""

        rows = self.connection.execute(
            """
            SELECT * FROM notification_events
            WHERE target_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (target_id, limit),
        ).fetchall()
        return [_notification_event_from_row(row) for row in rows]

    def latest_by_target(self, target_id: str) -> NotificationEvent | None:
        """查詢單一 target 最近一筆通知事件。"""

        row = self.connection.execute(
            """
            SELECT * FROM notification_events
            WHERE target_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (target_id,),
        ).fetchone()
        return _notification_event_from_row(row) if row else None


def _target_from_row(row: sqlite3.Row) -> TargetDescriptor:
    """將 SQLite row 轉為 TargetDescriptor。"""

    created_at = decode_datetime(row["created_at"])
    updated_at = decode_datetime(row["updated_at"])
    if created_at is None or updated_at is None:
        raise ValueError("target row has invalid datetime fields")
    return TargetDescriptor(
        id=row["id"],
        name=row["name"],
        target_kind=TargetKind(row["target_kind"]),
        group_id=row["group_id"],
        group_name=row["group_name"],
        parent_post_id=row["parent_post_id"],
        scope_id=row["scope_id"],
        canonical_url=row["canonical_url"],
        enabled=bool(row["enabled"]),
        paused=bool(row["paused"]),
        worker_mode=WorkerMode(row["worker_mode"]),
        created_at=created_at,
        updated_at=updated_at,
    )


def _target_config_from_row(row: sqlite3.Row, *, id_column: str) -> TargetConfig:
    """將 target/group config row 轉為 TargetConfig。"""

    return TargetConfig(
        target_id=row[id_column],
        include_keywords=decode_keywords(row["include_keywords"]),
        exclude_keywords=decode_keywords(row["exclude_keywords"]),
        min_refresh_sec=row["min_refresh_sec"],
        max_refresh_sec=row["max_refresh_sec"],
        jitter_enabled=bool(row["jitter_enabled"]),
        fixed_refresh_sec=row["fixed_refresh_sec"],
        max_items_per_scan=row["max_items_per_scan"],
        auto_load_more=bool(row["auto_load_more"]),
        auto_adjust_sort=bool(row["auto_adjust_sort"]),
        enable_desktop_notification=bool(row["enable_desktop_notification"]),
        enable_ntfy=bool(row["enable_ntfy"]),
        ntfy_topic=row["ntfy_topic"],
        enable_discord_notification=bool(row["enable_discord_notification"]),
        discord_webhook=row["discord_webhook"],
    )


def _match_history_from_row(row: sqlite3.Row) -> MatchHistoryEntry:
    """將 SQLite row 轉為 MatchHistoryEntry。"""

    created_at = decode_datetime(row["created_at"])
    if created_at is None:
        raise ValueError("match history row has invalid created_at")
    return MatchHistoryEntry(
        target_id=row["target_id"],
        group_id=row["group_id"],
        group_name=row["group_name"],
        item_kind=ItemKind(row["item_kind"]),
        parent_post_id=row["parent_post_id"],
        comment_id=row["comment_id"],
        item_key=row["item_key"],
        author=row["author"],
        text=row["text"],
        permalink=row["permalink"],
        include_rule=row["include_rule"],
        timestamp_text=row["timestamp_text"],
        notified_at=decode_datetime(row["notified_at"]),
        created_at=created_at,
    )


def _scan_run_from_row(row: sqlite3.Row) -> ScanRun:
    """將 SQLite row 轉為 ScanRun。"""

    started_at = decode_datetime(row["started_at"])
    finished_at = decode_datetime(row["finished_at"])
    if started_at is None or finished_at is None:
        raise ValueError("scan run row has invalid datetime fields")
    return ScanRun(
        target_id=row["target_id"],
        status=ScanStatus(row["status"]),
        started_at=started_at,
        finished_at=finished_at,
        item_count=row["item_count"],
        matched_count=row["matched_count"],
        error_message=row["error_message"],
        worker_mode=WorkerMode(row["worker_mode"]),
        metadata=json.loads(row["metadata"] or "{}"),
    )


def _latest_scan_item_from_row(row: sqlite3.Row) -> LatestScanItem:
    """將 SQLite row 轉為 LatestScanItem。"""

    scanned_at = decode_datetime(row["scanned_at"])
    if scanned_at is None:
        raise ValueError("latest scan item row has invalid scanned_at")
    return LatestScanItem(
        target_id=row["target_id"],
        scan_run_id=row["scan_run_id"],
        item_kind=ItemKind(row["item_kind"]),
        item_key=row["item_key"],
        item_index=row["item_index"],
        author=row["author"],
        text=row["text"],
        permalink=row["permalink"],
        matched_keyword=row["matched_keyword"],
        debug_metadata=json.loads(row["debug_metadata"] or "{}"),
        scanned_at=scanned_at,
    )


def _runtime_state_from_row(row: sqlite3.Row) -> TargetRuntimeState:
    """將 SQLite row 轉為 TargetRuntimeState。"""

    updated_at = decode_datetime(row["updated_at"])
    if updated_at is None:
        raise ValueError("target runtime state row has invalid updated_at")
    return TargetRuntimeState(
        target_id=row["target_id"],
        desired_state=TargetDesiredState(row["desired_state"]),
        runtime_status=TargetRuntimeStatus(row["runtime_status"]),
        scan_requested_at=decode_datetime(row["scan_requested_at"]),
        last_enqueued_at=decode_datetime(row["last_enqueued_at"]),
        last_started_at=decode_datetime(row["last_started_at"]),
        last_finished_at=decode_datetime(row["last_finished_at"]),
        last_heartbeat_at=decode_datetime(row["last_heartbeat_at"]),
        last_error=row["last_error"],
        last_skip_reason=row["last_skip_reason"],
        enqueue_reason=row["enqueue_reason"],
        active_worker_id=row["active_worker_id"],
        active_page_id=row["active_page_id"],
        last_page_reloaded_at=decode_datetime(row["last_page_reloaded_at"]),
        scan_guard_count=row["scan_guard_count"],
        updated_at=updated_at,
    )


def _notification_event_from_row(row: sqlite3.Row) -> NotificationEvent:
    """將 SQLite row 轉為 NotificationEvent。"""

    created_at = decode_datetime(row["created_at"])
    if created_at is None:
        raise ValueError("notification event row has invalid created_at")
    return NotificationEvent(
        target_id=row["target_id"],
        item_key=row["item_key"],
        channel=NotificationChannel(row["channel"]),
        status=NotificationStatus(row["status"]),
        message=row["message"],
        created_at=created_at,
    )
