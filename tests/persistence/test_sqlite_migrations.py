"""Persistence smoke tests。"""

from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import sqlite3

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.sidebar_models import SidebarGroup
from facebook_monitor.persistence.sqlite import LatestScanItemRepository
from facebook_monitor.persistence.sqlite import MatchHistoryRepository
from facebook_monitor.persistence.sqlite import NotificationEventRepository
from facebook_monitor.persistence.sqlite import ScanRunRepository
from facebook_monitor.persistence.sqlite import SeenItemRepository
from facebook_monitor.persistence.sqlite import SCHEMA_VERSION
from facebook_monitor.persistence.sqlite import SidebarLayoutRepository
from facebook_monitor.persistence.sqlite import SqliteConnection
from facebook_monitor.persistence.sqlite import TargetRepository
from facebook_monitor.persistence.sqlite import TargetRuntimeStateRepository
from facebook_monitor.persistence.sqlite import initialize_schema
from facebook_monitor.persistence.migrations import ensure_legacy_group_configs_table
from facebook_monitor.persistence.repositories.scan_scope_state import ScanScopeStateRepository
from facebook_monitor.persistence.sqlite_codec import encode_datetime
from facebook_monitor.persistence.sqlite_codec import encode_keywords

from tests.persistence.sqlite_test_helpers import target_config_repository
from tests.persistence.sqlite_test_helpers import table_exists
from tests.persistence.sqlite_test_helpers import table_sql
from tests.persistence.sqlite_test_helpers import create_raw_v12_missing_columns_schema
from tests.persistence.sqlite_test_helpers import table_has_column
from tests.persistence.sqlite_test_helpers import create_raw_v10_fixture_schema
from tests.persistence.sqlite_test_helpers import PLAINTEXT_SECRET_CODEC


def test_initialize_schema_migrates_v29_check_constraints(tmp_path: Path) -> None:
    """v29 舊表升級後會重建成帶 CHECK constraints 的 v30 schema。"""

    db_path = tmp_path / "app.db"
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO schema_metadata (key, value) VALUES ('version', '29');
            CREATE TABLE scan_scope_state (
                scope_id TEXT PRIMARY KEY,
                initialized INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO scan_scope_state (scope_id, initialized, updated_at)
            VALUES ('scope-a', 1, '2026-05-01T00:00:00+00:00');
            """
        )

        initialize_schema(connection)

        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()[0]
        row = connection.execute(
            "SELECT initialized FROM scan_scope_state WHERE scope_id = 'scope-a'"
        ).fetchone()
        scan_scope_sql = table_sql(connection, "scan_scope_state")
        indexes = {
            index_row["name"]
            for index_row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        trigger_tables = {
            trigger_row["tbl_name"]
            for trigger_row in connection.execute(
                """
                SELECT tbl_name
                FROM sqlite_master
                WHERE type = 'trigger'
                  AND name LIKE 'trg_dashboard_revision_%'
                """
            ).fetchall()
        }

        try:
            connection.execute(
                """
                INSERT INTO scan_scope_state (scope_id, initialized, updated_at)
                VALUES ('scope-b', 9, '2026-05-01T00:00:00+00:00')
                """
            )
        except sqlite3.IntegrityError as exc:
            assert "CHECK constraint failed" in str(exc)
        else:
            raise AssertionError("v29 -> v30 migration should add initialized CHECK")

    assert version == str(SCHEMA_VERSION)
    assert row["initialized"] == 1
    assert "CHECK (initialized IN (0, 1))" in scan_scope_sql
    assert "idx_runtime_state_status_updated" in indexes
    assert "target_runtime_state" in trigger_tables


def test_initialize_schema_migrates_v31_runtime_notification_constraints(
    tmp_path: Path,
) -> None:
    """v30 升級後 runtime notification 欄位也應帶 CHECK constraints。"""

    db_path = tmp_path / "app.db"
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO schema_metadata (key, value) VALUES ('version', '30');
            CREATE TABLE notification_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id TEXT NOT NULL,
                item_key TEXT NOT NULL,
                channel TEXT NOT NULL CHECK (channel IN ('desktop', 'ntfy', 'discord')),
                status TEXT NOT NULL CHECK (status IN ('sent', 'failed', 'skipped')),
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE notification_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                target_id TEXT NOT NULL,
                item_key TEXT NOT NULL,
                item_kind TEXT NOT NULL CHECK (item_kind IN ('post', 'comment')),
                channel TEXT NOT NULL CHECK (channel IN ('desktop', 'ntfy', 'discord')),
                status TEXT NOT NULL CHECK (
                    status IN (
                        'pending', 'processing_pending', 'sent', 'failed',
                        'processing_failed', 'skipped'
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
            );
            """
        )

        initialize_schema(connection)

        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()[0]
        notification_events_sql = table_sql(connection, "notification_events")
        notification_outbox_sql = table_sql(connection, "notification_outbox")

        try:
            connection.execute(
                """
                INSERT INTO notification_events (
                    target_id, item_key, channel, status, event_kind, message, created_at
                )
                VALUES (
                    'target-a', 'item-a', 'ntfy', 'sent', 'bad',
                    'message', '2026-05-01T00:00:00+00:00'
                )
                """
            )
        except sqlite3.IntegrityError as exc:
            assert "CHECK constraint failed" in str(exc)
        else:
            raise AssertionError("v30 -> v31 migration should constrain event_kind")

        try:
            connection.execute(
                """
                INSERT INTO notification_outbox (
                    idempotency_key, target_id, item_key, item_kind, channel, status,
                    title, message, endpoint, permalink, failure_count,
                    attempts, last_error, created_at, updated_at
                )
                VALUES (
                    'key-a', 'target-a', 'item-a', 'post', 'ntfy', 'pending',
                    'title', 'message', '', '', -1,
                    0, '', '2026-05-01T00:00:00+00:00',
                    '2026-05-01T00:00:00+00:00'
                )
                """
            )
        except sqlite3.IntegrityError as exc:
            assert "CHECK constraint failed" in str(exc)
        else:
            raise AssertionError("v30 -> v31 migration should constrain failure_count")

    assert version == str(SCHEMA_VERSION)
    assert "CHECK (event_kind IN" in notification_events_sql
    assert "CHECK (failure_count >= 0)" in notification_outbox_sql


def test_initialize_schema_migrates_v32_logical_dedupe_tables(
    tmp_path: Path,
) -> None:
    """v31 升級會回填 logical item aliases 與 notification dedupe ledger。"""

    db_path = tmp_path / "app.db"
    now = "2026-05-01T00:00:00+00:00"
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(
            f"""
            CREATE TABLE schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO schema_metadata (key, value) VALUES ('version', '31');
            CREATE TABLE targets (
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
            CREATE TABLE seen_items (
                scope_id TEXT NOT NULL,
                item_key TEXT NOT NULL,
                item_kind TEXT NOT NULL CHECK (item_kind IN ('post', 'comment')),
                parent_post_id TEXT NOT NULL,
                comment_id TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (scope_id, item_key)
            );
            CREATE TABLE notification_outbox (
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
                event_kind TEXT NOT NULL DEFAULT 'match'
                    CHECK (event_kind IN ('match', 'runtime_failure')),
                source_scan_run_id INTEGER,
                failure_reason TEXT NOT NULL DEFAULT '',
                failure_count INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
                attempts INTEGER NOT NULL CHECK (attempts >= 0),
                last_error TEXT NOT NULL,
                notification_event_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO targets (
                id, name, target_kind, group_id, group_name, group_cover_image_url,
                parent_post_id, scope_id, canonical_url, metadata_status, metadata_error,
                enabled, paused, worker_mode, created_at, updated_at
            )
            VALUES
                (
                    'posts-target', 'posts', 'posts', '111', 'group', '',
                    '', '111', 'https://www.facebook.com/groups/111',
                    'resolved', '', 1, 0, 'headless', '{now}', '{now}'
                ),
                (
                    'comments-target', 'comments', 'comments', '111', 'group', '',
                    '999', '111:post:999:comments',
                    'https://www.facebook.com/groups/111/posts/999',
                    'resolved', '', 1, 0, 'headless', '{now}', '{now}'
                );
            INSERT INTO seen_items (
                scope_id, item_key, item_kind, parent_post_id, comment_id,
                first_seen_at, last_seen_at
            )
            VALUES
                ('111', 'post-alias-a', 'post', '', '', '{now}', '{now}'),
                ('111', 'post-alias-b', 'post', '', '', '{now}', '{now}'),
                (
                    '111:post:999:comments', 'comment-alias-a', 'comment',
                    '999', 'comment-1', '{now}', '{now}'
                ),
                (
                    '111:post:999:comments', 'comment-alias-b', 'comment',
                    '999', 'comment-1', '{now}', '{now}'
                );
            INSERT INTO notification_outbox (
                idempotency_key, target_id, item_key, item_kind, channel, status,
                title, message, endpoint, permalink, event_kind, source_scan_run_id,
                failure_reason, failure_count, attempts, last_error,
                notification_event_id, created_at, updated_at
            )
            VALUES
                (
                    'comments-target:comment-alias-a:ntfy',
                    'comments-target', 'comment-alias-a', 'comment', 'ntfy', 'sent',
                    'title', 'message', '', '', 'match', NULL, '', 0, 1, '',
                    10, '{now}', '{now}'
                ),
                (
                    'posts-target:post-alias-a:ntfy',
                    'posts-target', 'post-alias-a', 'post', 'ntfy', 'pending',
                    'title', 'message', '', '', 'match', NULL, '', 0, 0, '',
                    NULL, '{now}', '{now}'
                ),
                (
                    'posts-target:outbox-only:ntfy',
                    'posts-target', 'outbox-only', 'post', 'ntfy', 'sent',
                    'title', 'message', '', '', 'match', NULL, '', 0, 1, '',
                    20, '{now}', '{now}'
                ),
                (
                    'posts-target:outbox-only:discord',
                    'posts-target', 'outbox-only', 'post', 'discord', 'sent',
                    'title', 'message', '', '', 'match', NULL, '', 0, 1, '',
                    21, '{now}', '{now}'
                );
            """
        )

        initialize_schema(connection)

        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()[0]
        post_logical_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM logical_items
            WHERE target_id = 'posts-target'
            """
        ).fetchone()[0]
        comment_logical_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM logical_items
            WHERE target_id = 'comments-target'
            """
        ).fetchone()[0]
        comment_alias_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM logical_item_aliases
            WHERE target_id = 'comments-target'
            """
        ).fetchone()[0]
        sent_dedupe = connection.execute(
            """
            SELECT notification_dedupe.status, notification_dedupe.logical_item_id
            FROM notification_dedupe
            JOIN notification_outbox
              ON notification_outbox.dedupe_id = notification_dedupe.id
            WHERE notification_outbox.idempotency_key = 'comments-target:comment-alias-a:ntfy'
            """
        ).fetchone()
        pending_dedupe = connection.execute(
            """
            SELECT notification_dedupe.status
            FROM notification_dedupe
            JOIN notification_outbox
              ON notification_outbox.dedupe_id = notification_dedupe.id
            WHERE notification_outbox.idempotency_key = 'posts-target:post-alias-a:ntfy'
            """
        ).fetchone()
        outbox_only_dedupe_rows = connection.execute(
            """
            SELECT
                notification_dedupe.status,
                notification_dedupe.logical_item_id,
                notification_dedupe.subject_key,
                notification_dedupe.channel
            FROM notification_dedupe
            JOIN notification_outbox
              ON notification_outbox.dedupe_id = notification_dedupe.id
            WHERE notification_outbox.item_key = 'outbox-only'
            ORDER BY notification_dedupe.channel
            """
        ).fetchall()
        outbox_only_alias_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM logical_item_aliases
            WHERE target_id = 'posts-target'
              AND alias_key = 'outbox-only'
            """
        ).fetchone()[0]

    assert version == str(SCHEMA_VERSION)
    assert post_logical_count == 3
    assert comment_logical_count == 1
    assert comment_alias_count == 2
    assert sent_dedupe is not None
    assert sent_dedupe["status"] == "sent"
    assert sent_dedupe["logical_item_id"] is not None
    assert pending_dedupe is not None
    assert pending_dedupe["status"] == "queued"
    assert len(outbox_only_dedupe_rows) == 2
    assert {row["channel"] for row in outbox_only_dedupe_rows} == {"discord", "ntfy"}
    assert {row["status"] for row in outbox_only_dedupe_rows} == {"sent"}
    assert {
        int(row["logical_item_id"])
        for row in outbox_only_dedupe_rows
        if row["logical_item_id"] is not None
    } == {int(outbox_only_dedupe_rows[0]["logical_item_id"])}
    assert {row["subject_key"] for row in outbox_only_dedupe_rows} == {
        f"logical:{int(outbox_only_dedupe_rows[0]['logical_item_id'])}"
    }
    assert outbox_only_alias_count == 1


def test_initialize_schema_migrates_v33_runtime_scan_skip_columns(
    tmp_path: Path,
) -> None:
    """v32 既有 runtime rows 需保留，並回填 scan skip streak 預設值。"""

    db_path = tmp_path / "app.db"
    now = "2026-05-01T00:00:00+00:00"
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(
            f"""
            CREATE TABLE schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO schema_metadata (key, value) VALUES ('version', '32');
            CREATE TABLE target_runtime_state (
                target_id TEXT PRIMARY KEY,
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
                scan_guard_count INTEGER NOT NULL DEFAULT 0 CHECK (scan_guard_count >= 0),
                display_next_due_at TEXT NOT NULL DEFAULT '',
                consecutive_failure_reason TEXT NOT NULL DEFAULT '',
                consecutive_failure_count INTEGER NOT NULL DEFAULT 0 CHECK (
                    consecutive_failure_count >= 0
                ),
                updated_at TEXT NOT NULL
            );
            INSERT INTO target_runtime_state (
                target_id, desired_state, runtime_status, scan_requested_at,
                last_enqueued_at, last_started_at, last_finished_at,
                last_heartbeat_at, last_error, last_skip_reason, enqueue_reason,
                active_worker_id, active_page_id, last_page_reloaded_at,
                scan_guard_count, display_next_due_at, consecutive_failure_reason,
                consecutive_failure_count, updated_at
            )
            VALUES (
                'target-a', 'active', 'idle', '', '', '', '', '{now}',
                '', 'previous skip', '', '', '', '', 2, '',
                'page_load_timeout', 2, '{now}'
            );
            """
        )

        initialize_schema(connection)

        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()[0]
        runtime_row = connection.execute(
            "SELECT * FROM target_runtime_state WHERE target_id = 'target-a'"
        ).fetchone()
        has_skip_reason = table_has_column(
            connection,
            "target_runtime_state",
            "consecutive_scan_skip_reason",
        )
        has_skip_count = table_has_column(
            connection,
            "target_runtime_state",
            "consecutive_scan_skip_count",
        )

    assert version == str(SCHEMA_VERSION)
    assert has_skip_reason
    assert has_skip_count
    assert runtime_row is not None
    assert runtime_row["last_skip_reason"] == "previous skip"
    assert runtime_row["consecutive_failure_reason"] == "page_load_timeout"
    assert runtime_row["consecutive_failure_count"] == 2
    assert runtime_row["consecutive_scan_skip_reason"] == ""
    assert runtime_row["consecutive_scan_skip_count"] == 0


def test_initialize_schema_v34_drops_stale_group_configs_from_currentish_db(
    tmp_path: Path,
) -> None:
    """v33 之後仍殘留的 legacy group_configs 也必須被 forward migration 移除。"""

    db_path = tmp_path / "app.db"
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO schema_metadata (key, value) VALUES ('version', '33');
            CREATE TABLE group_configs (
                group_id TEXT PRIMARY KEY,
                discord_webhook TEXT NOT NULL
            );
            INSERT INTO group_configs (group_id, discord_webhook)
            VALUES ('legacy', 'https://discord.com/api/webhooks/secret');
            """
        )

        initialize_schema(connection)

        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()[0]
        has_group_configs = table_exists(connection, "group_configs")

    assert version == str(SCHEMA_VERSION)
    assert not has_group_configs


def test_initialize_schema_drops_stale_dashboard_revision_triggers(tmp_path: Path) -> None:
    """schema 初始化會移除舊版 dashboard revision triggers，避免長時間 UI 更新過密。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        connection.execute(
            """
            CREATE TRIGGER trg_dashboard_revision_legacy_insert
            AFTER INSERT ON targets
            BEGIN
                UPDATE dashboard_revision
                SET revision = revision + 1
                WHERE id = 1;
            END
            """
        )
        initialize_schema(connection)
        trigger_names = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'trigger'
                  AND name LIKE 'trg_dashboard_revision_%'
                """
            ).fetchall()
        }

    assert "trg_dashboard_revision_legacy_insert" not in trigger_names


def test_fresh_schema_does_not_create_group_configs_formal_table(tmp_path: Path) -> None:
    """fresh DB 不再建立 group_configs，避免 legacy migration table 回到正式路徑。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        table_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert "group_configs" not in table_names


def test_initialize_schema_repairs_duplicate_target_scopes_before_unique_index(
    tmp_path: Path,
) -> None:
    """v16 以前若出現重複 scope，migration 會先合併再建立 DB unique index。"""

    db_path = tmp_path / "app.db"
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO schema_metadata (key, value) VALUES ('version', '16');
            CREATE TABLE targets (
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
            CREATE TABLE target_configs (
                target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
                include_keywords TEXT NOT NULL,
                exclude_keywords TEXT NOT NULL,
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
                discord_webhook TEXT NOT NULL
            );
            """
        )
        first = TargetDescriptor.for_group_posts(
            group_id="111",
            canonical_url="https://www.facebook.com/groups/111",
        )
        duplicate = replace(
            first,
            id="duplicate-target",
            name="duplicate",
            created_at=first.created_at + timedelta(seconds=1),
            updated_at=first.updated_at + timedelta(seconds=1),
        )
        for target in (first, duplicate):
            connection.execute(
                """
                INSERT INTO targets (
                    id, name, target_kind, group_id, group_name, parent_post_id,
                    scope_id, canonical_url, enabled, paused, worker_mode,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        connection.commit()

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        targets = TargetRepository(connection).list_all()
        indexes = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }

    assert len(targets) == 1
    assert targets[0].id == first.id
    assert "idx_targets_kind_scope_unique" in indexes


def test_initialize_schema_migrates_legacy_paused_runtime_status(tmp_path: Path) -> None:
    """舊 DB 的 runtime_status=paused 會升級成 idle，paused 語義只保留在 target flag。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        target = TargetDescriptor.for_group_posts(
            group_id="111",
            canonical_url="https://www.facebook.com/groups/111",
        )
        TargetRepository(connection).save(target)
        TargetRuntimeStateRepository(connection).save(
            TargetRuntimeState(
                target_id=target.id,
                desired_state=TargetDesiredState.STOPPED,
                runtime_status=TargetRuntimeStatus.IDLE,
            )
        )
        connection.execute("UPDATE schema_metadata SET value = '11' WHERE key = 'version'")
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE target_runtime_state
            SET runtime_status = 'paused'
            WHERE target_id = ?
            """,
            (target.id,),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        loaded = TargetRuntimeStateRepository(connection).get(target.id)
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()["value"]

    assert loaded is not None
    assert loaded.runtime_status == TargetRuntimeStatus.IDLE
    assert loaded.desired_state == TargetDesiredState.STOPPED
    assert version == str(SCHEMA_VERSION)


def test_initialize_schema_migrates_v20_keyword_match_tables(
    tmp_path: Path,
) -> None:
    """v21 會新增多 keyword 命中子表，並回填既有摘要欄位。"""

    db_path = tmp_path / "app.db"
    now_text = encode_datetime(utc_now())
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        target = TargetDescriptor.for_group_posts(
            group_id="222518561920110",
            canonical_url="https://www.facebook.com/groups/222518561920110",
        )
        TargetRepository(connection).save(target)
        connection.execute(
            """
            INSERT INTO match_history (
                target_id, group_id, group_name, item_kind, parent_post_id,
                comment_id, item_key, author, text, permalink, include_rule,
                timestamp_text, notified_at, created_at
            )
            VALUES (?, ?, ?, ?, '', '', ?, ?, ?, ?, ?, '', ?, ?)
            """,
            (
                target.id,
                target.group_id,
                target.group_name,
                ItemKind.POST.value,
                "item-hash",
                "王小明",
                "售6/5,6/6的票各一張",
                "https://www.facebook.com/groups/222518561920110/posts/1",
                "6/5;6/6",
                now_text,
                now_text,
            ),
        )
        connection.execute(
            """
            INSERT INTO latest_scan_items (
                target_id, scan_run_id, item_kind, item_key, item_index,
                author, text, permalink, matched_keyword, debug_metadata, scanned_at
            )
            VALUES (?, 1, ?, ?, 0, ?, ?, ?, ?, '{}', ?)
            """,
            (
                target.id,
                ItemKind.POST.value,
                "item-hash",
                "王小明",
                "售6/5,6/6的票各一張",
                "https://www.facebook.com/groups/222518561920110/posts/1",
                "6/5;6/6",
                now_text,
            ),
        )
        connection.execute("DROP TABLE latest_scan_item_matches")
        connection.execute("DROP TABLE match_history_matches")
        connection.execute(
            "INSERT OR REPLACE INTO schema_metadata (key, value) VALUES ('version', '20')"
        )

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()[0]
        history = MatchHistoryRepository(connection).list_by_target(target.id)
        latest_items = LatestScanItemRepository(connection).list_by_target(target.id)

    assert version == str(SCHEMA_VERSION)
    assert history[0].include_rules == ("6/5", "6/6")
    assert latest_items[0].matched_keywords == ("6/5", "6/6")


def test_initialize_schema_migrates_v28_include_keyword_groups(
    tmp_path: Path,
) -> None:
    """v29 會把既有 flat include keywords 回填到第 1 個 include group。"""

    db_path = tmp_path / "app.db"
    now_text = encode_datetime(utc_now())
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        target = TargetDescriptor.for_group_posts(
            group_id="target-1",
            canonical_url="https://www.facebook.com/groups/target-1",
        )
        group = SidebarGroup.create(name="group-1", sort_order=0)
        TargetRepository(connection).save(target)
        SidebarLayoutRepository(
            connection,
            secret_codec=PLAINTEXT_SECRET_CODEC,
        ).save_group(group)
        connection.execute("UPDATE schema_metadata SET value = '28' WHERE key = 'version'")
        connection.execute("DROP TABLE target_configs")
        connection.execute("DROP TABLE sidebar_group_config_templates")
        connection.executescript(
            """
            CREATE TABLE target_configs (
                target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
                include_keywords TEXT NOT NULL,
                exclude_keywords TEXT NOT NULL,
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
                discord_webhook TEXT NOT NULL
            );

            CREATE TABLE sidebar_group_config_templates (
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
            );
            """
        )
        connection.execute(
            """
            INSERT INTO target_configs (
                target_id, include_keywords, exclude_keywords, exclude_ignore_phrases,
                min_refresh_sec, max_refresh_sec, jitter_enabled, fixed_refresh_sec,
                max_items_per_scan, auto_load_more, auto_adjust_sort,
                enable_desktop_notification, enable_ntfy, ntfy_topic,
                enable_discord_notification, discord_webhook
            )
            VALUES (?, ?, '[]', '[]', 50, 70, 1, NULL, 20, 1, 1, 0, 0, '', 0, '')
            """,
            (target.id, encode_keywords(("票", "交換"))),
        )
        connection.execute(
            """
            INSERT INTO sidebar_group_config_templates (
                sidebar_group_id, include_keywords, exclude_keywords,
                exclude_ignore_phrases, min_refresh_sec, max_refresh_sec,
                jitter_enabled, fixed_refresh_sec, max_items_per_scan,
                auto_load_more, auto_adjust_sort, enable_desktop_notification,
                enable_ntfy, ntfy_topic, enable_discord_notification,
                discord_webhook, updated_at
            )
            VALUES (?, ?, '[]', '[]', 50, 70, 1, NULL, 20, 1, 1, 0, 0, '', 0, '', ?)
            """,
            (group.id, encode_keywords(("模板",)), now_text),
        )

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        config = target_config_repository(connection).get_for_target_id(target.id)
        template = SidebarLayoutRepository(
            connection,
            secret_codec=PLAINTEXT_SECRET_CODEC,
        ).get_template(group.id)

    assert config is not None
    assert [group.keywords for group in config.include_keyword_groups] == [
        ("票", "交換"),
        (),
        (),
    ]
    assert template is not None
    assert [group.keywords for group in template.include_keyword_groups] == [
        ("模板",),
        (),
        (),
    ]


def test_initialize_schema_migrates_v18_sidebar_placements(
    tmp_path: Path,
) -> None:
    """v18 舊 DB 升級後會為既有 targets 建立未分組 sidebar placement。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        first = TargetDescriptor.for_group_posts(
            group_id="111",
            canonical_url="https://www.facebook.com/groups/111",
        )
        second = replace(
            TargetDescriptor.for_group_posts(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            ),
            created_at=first.created_at + timedelta(seconds=10),
        )
        TargetRepository(connection).save(first)
        TargetRepository(connection).save(second)
        connection.execute("DROP TABLE sidebar_group_config_templates")
        connection.execute("DROP TABLE sidebar_target_placements")
        connection.execute("DROP TABLE sidebar_groups")
        connection.execute("UPDATE schema_metadata SET value = '18' WHERE key = 'version'")

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()["value"]
        rows = connection.execute(
            """
            SELECT target_id, sidebar_group_id, sort_order
            FROM sidebar_target_placements
            ORDER BY sort_order
            """
        ).fetchall()

    assert version == str(SCHEMA_VERSION)
    assert [(row["target_id"], row["sidebar_group_id"], row["sort_order"]) for row in rows] == [
        (first.id, None, 0),
        (second.id, None, 1),
    ]


def test_initialize_schema_migrates_v19_group_template_table(
    tmp_path: Path,
) -> None:
    """v19 舊 DB 升級後會建立 group template table，但不建立 target config fallback。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        connection.execute("DROP TABLE sidebar_group_config_templates")
        connection.execute("UPDATE schema_metadata SET value = '19' WHERE key = 'version'")

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()["value"]
        has_template_table = table_exists(connection, "sidebar_group_config_templates")
        has_discord_column = table_has_column(
            connection,
            "sidebar_group_config_templates",
            "discord_webhook",
        )
        table_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert version == str(SCHEMA_VERSION)
    assert has_template_table
    assert has_discord_column
    assert "group_configs" not in table_names


def test_initialize_schema_migrates_v23_scan_scope_state_table(
    tmp_path: Path,
) -> None:
    """v23 舊 DB 升級後會建立 scan_scope_state table。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        connection.execute("DROP TABLE scan_scope_state")
        connection.execute("UPDATE schema_metadata SET value = '23' WHERE key = 'version'")

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()["value"]
        repo = ScanScopeStateRepository(connection)
        inserted_count = repo.clear_scope("scope-from-v23")
        has_scan_scope_state_table = table_exists(connection, "scan_scope_state")
        is_initialized = ScanScopeStateRepository(connection).is_initialized("scope-from-v23")

    assert version == str(SCHEMA_VERSION)
    assert has_scan_scope_state_table
    assert inserted_count == 1
    assert not is_initialized


def test_initialize_schema_migrates_v10_fixture_to_current_schema(tmp_path: Path) -> None:
    """raw v10 DB 會升級 group config、runtime、history 與 latest scan 資料。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        create_raw_v10_fixture_schema(connection)

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()["value"]
        posts_target = TargetRepository(connection).get("posts-target")
        comments_target = TargetRepository(connection).get("comments-target")
        posts_config = target_config_repository(connection).get_for_target_id("posts-target")
        comments_config = target_config_repository(connection).get_for_target_id("comments-target")
        runtime_state = TargetRuntimeStateRepository(connection).get("posts-target")
        latest_scan = ScanRunRepository(connection).latest_by_target("posts-target")
        latest_items = LatestScanItemRepository(connection).list_by_target("posts-target")
        notifications = NotificationEventRepository(connection).list_by_target("posts-target")
        history = MatchHistoryRepository(connection).list_by_target("posts-target")
        has_seen = SeenItemRepository(connection).has_seen("111", "legacy-item")
        targets_sql = table_sql(connection, "targets")

    assert version == str(SCHEMA_VERSION)
    assert posts_target is not None
    assert posts_target.group_id == "111"
    assert comments_target is not None
    assert comments_target.parent_post_id == "999"
    assert posts_config is not None
    assert posts_config.target_id == "posts-target"
    assert posts_config.include_keywords == ("legacy",)
    assert posts_config.fixed_refresh_sec == 90
    assert posts_config.enable_desktop_notification is False
    assert posts_config.enable_ntfy
    assert posts_config.ntfy_topic == "legacy-topic"
    assert posts_config.enable_discord_notification is False
    assert posts_config.discord_webhook == ""
    assert comments_config is not None
    assert comments_config.target_id == "comments-target"
    assert comments_config.include_keywords == ("legacy",)
    assert comments_config.ntfy_topic == "legacy-topic"
    assert runtime_state is not None
    assert runtime_state.runtime_status == TargetRuntimeStatus.IDLE
    assert runtime_state.desired_state == TargetDesiredState.STOPPED
    assert runtime_state.scan_requested_at is None
    assert runtime_state.last_skip_reason == ""
    assert latest_scan is not None
    assert latest_scan.item_count == 1
    assert [item.item_key for item in latest_items] == ["legacy-item"]
    assert latest_items[0].debug_metadata == {}
    assert len(notifications) == 1
    assert notifications[0].message == "legacy sent"
    assert len(history) == 1
    assert history[0].include_rule == "legacy"
    assert has_seen
    assert "CHECK (target_kind IN ('posts', 'comments'))" in targets_sql
    assert "CHECK (metadata_status IN ('resolved', 'pending', 'failed'))" in targets_sql
    assert "CHECK (enabled IN (0, 1))" in targets_sql
    assert "CHECK (paused IN (0, 1))" in targets_sql
    assert "CHECK (worker_mode IN ('headless', 'headed_compat'))" in targets_sql


def test_initialize_schema_migrates_v12_missing_columns_to_current(tmp_path: Path) -> None:
    """v12 歷史缺欄由正式 migration 鏈補齊到 current schema。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        create_raw_v12_missing_columns_schema(connection)

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()["value"]
        has_outbox_endpoint = table_has_column(connection, "notification_outbox", "endpoint")
        has_latest_debug = table_has_column(connection, "latest_scan_items", "debug_metadata")
        has_runtime_request = table_has_column(
            connection,
            "target_runtime_state",
            "scan_requested_at",
        )
        has_runtime_guard_count = table_has_column(
            connection,
            "target_runtime_state",
            "scan_guard_count",
        )
        has_runtime_display_due = table_has_column(
            connection,
            "target_runtime_state",
            "display_next_due_at",
        )
        has_runtime_failure_reason = table_has_column(
            connection,
            "target_runtime_state",
            "consecutive_failure_reason",
        )
        has_runtime_failure_count = table_has_column(
            connection,
            "target_runtime_state",
            "consecutive_failure_count",
        )
        has_runtime_scan_skip_reason = table_has_column(
            connection,
            "target_runtime_state",
            "consecutive_scan_skip_reason",
        )
        has_runtime_scan_skip_count = table_has_column(
            connection,
            "target_runtime_state",
            "consecutive_scan_skip_count",
        )
        has_target_discord = table_has_column(
            connection,
            "target_configs",
            "enable_discord_notification",
        )
        has_target_exclude_ignore = table_has_column(
            connection,
            "target_configs",
            "exclude_ignore_phrases",
        )
        has_target_metadata_status = table_has_column(
            connection,
            "targets",
            "metadata_status",
        )
        has_target_metadata_error = table_has_column(
            connection,
            "targets",
            "metadata_error",
        )
        has_group_configs = table_exists(connection, "group_configs")

    assert version == str(SCHEMA_VERSION)
    assert has_outbox_endpoint
    assert has_latest_debug
    assert has_runtime_request
    assert has_runtime_guard_count
    assert has_runtime_display_due
    assert has_runtime_failure_reason
    assert has_runtime_failure_count
    assert has_runtime_scan_skip_reason
    assert has_runtime_scan_skip_count
    assert has_target_discord
    assert has_target_exclude_ignore
    assert has_target_metadata_status
    assert has_target_metadata_error
    assert not has_group_configs


def test_initialize_schema_migrates_v27_cover_refresh_state_to_current(
    tmp_path: Path,
) -> None:
    """已跑過 v27 的本機 DB 會補上 cover refresh 診斷欄位。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        connection.executescript(
            """
            CREATE TABLE schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO schema_metadata (key, value) VALUES ('version', '27');
            CREATE TABLE target_cover_image_refresh_state (
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
            """
        )

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()["value"]
        has_resolved_url = table_has_column(
            connection,
            "target_cover_image_refresh_state",
            "last_resolved_url",
        )
        has_result = table_has_column(
            connection,
            "target_cover_image_refresh_state",
            "last_result",
        )
        has_changed = table_has_column(
            connection,
            "target_cover_image_refresh_state",
            "changed",
        )

    assert version == str(SCHEMA_VERSION)
    assert has_resolved_url
    assert has_result
    assert has_changed


def test_initialize_schema_v14_copies_group_configs_to_each_target_before_v15(
    tmp_path: Path,
) -> None:
    """v14 migration 會把 v13 group config 複製成每個 target 各自一筆設定。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        posts_target = TargetDescriptor.for_group_posts(
            group_id="111",
            canonical_url="https://www.facebook.com/groups/111",
        )
        comments_target = TargetDescriptor.for_comments(
            group_id="111",
            parent_post_id="999",
            canonical_url="https://www.facebook.com/groups/111/posts/999",
        )
        second_comments_target = TargetDescriptor.for_comments(
            group_id="111",
            parent_post_id="1000",
            canonical_url="https://www.facebook.com/groups/111/posts/1000",
        )
        fallback_target = TargetDescriptor.for_group_posts(
            group_id="222",
            canonical_url="https://www.facebook.com/groups/222",
        )
        defaults_target = TargetDescriptor.for_group_posts(
            group_id="333",
            canonical_url="https://www.facebook.com/groups/333",
        )
        for target in (
            posts_target,
            comments_target,
            second_comments_target,
            fallback_target,
            defaults_target,
        ):
            TargetRepository(connection).save(target)
        target_config_repository(connection).save_for_target(
            posts_target,
            TargetConfig(target_id=posts_target.id, include_keywords=("stale",)),
        )
        target_config_repository(connection).save_for_target(
            fallback_target,
            TargetConfig(target_id=fallback_target.id, include_keywords=("fallback",)),
        )
        ensure_legacy_group_configs_table(connection)
        connection.execute(
            """
            INSERT INTO group_configs (
                group_id, include_keywords, exclude_keywords, min_refresh_sec,
                max_refresh_sec, jitter_enabled, fixed_refresh_sec, max_items_per_scan,
                auto_load_more, auto_adjust_sort, enable_desktop_notification,
                enable_ntfy, ntfy_topic, enable_discord_notification, discord_webhook
            )
            VALUES ('111', '["group"]', '["售完"]', 30, 60, 0, 45, 8, 1, 1, 0, 1,
                    'group-topic', 1, 'https://discord.com/api/webhooks/group')
            """
        )
        connection.execute("UPDATE schema_metadata SET value = '13' WHERE key = 'version'")

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()["value"]
        posts_config = target_config_repository(connection).get_for_target(posts_target)
        comments_config = target_config_repository(connection).get_for_target(comments_target)
        second_comments_config = target_config_repository(connection).get_for_target(
            second_comments_target
        )
        fallback_config = target_config_repository(connection).get_for_target(fallback_target)
        defaults_config = target_config_repository(connection).get_for_target(defaults_target)
        has_group_configs = table_exists(connection, "group_configs")

    assert version == str(SCHEMA_VERSION)
    assert posts_config is not None
    assert posts_config.target_id == posts_target.id
    assert posts_config.include_keywords == ("group",)
    assert posts_config.exclude_keywords == ("售完",)
    assert posts_config.exclude_ignore_phrases == ()
    assert posts_config.fixed_refresh_sec == 45
    assert posts_config.enable_ntfy
    assert posts_config.ntfy_topic == "group-topic"
    assert posts_config.enable_discord_notification
    assert comments_config is not None
    assert comments_config.target_id == comments_target.id
    assert comments_config.include_keywords == ("group",)
    assert comments_config.ntfy_topic == "group-topic"
    assert second_comments_config is not None
    assert second_comments_config.target_id == second_comments_target.id
    assert second_comments_config.include_keywords == ("group",)
    assert second_comments_config.ntfy_topic == "group-topic"
    assert {
        posts_config.target_id,
        comments_config.target_id,
        second_comments_config.target_id,
    } == {
        posts_target.id,
        comments_target.id,
        second_comments_target.id,
    }
    assert fallback_config is not None
    assert fallback_config.include_keywords == ("fallback",)
    assert defaults_config is not None
    assert defaults_config.include_keywords == ()
    assert not has_group_configs


def test_initialize_schema_migrates_v34_display_text_columns(
    tmp_path: Path,
) -> None:
    """v35 會補上 display_text 欄位，並用既有 text 回填可呈現內容。"""

    db_path = tmp_path / "app.db"
    now_text = encode_datetime(utc_now())
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        connection.executescript(
            """
            CREATE TABLE schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO schema_metadata (key, value) VALUES ('version', '34');
            CREATE TABLE match_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id TEXT NOT NULL,
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
            CREATE TABLE latest_scan_items (
                target_id TEXT NOT NULL,
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
            """
        )
        connection.execute(
            """
            INSERT INTO match_history (
                target_id, group_id, group_name, item_kind, parent_post_id,
                comment_id, item_key, author, text, permalink, include_rule,
                timestamp_text, notified_at, created_at
            )
            VALUES (
                'target-1', '111', '測試社團', 'post', '', '', 'item-1',
                '作者', '第一行 第二行', '', '票券', '', ?, ?
            );
            """,
            (now_text, now_text),
        )
        connection.execute(
            """
            INSERT INTO latest_scan_items (
                target_id, scan_run_id, item_kind, item_key, item_index,
                author, text, permalink, matched_keyword, debug_metadata, scanned_at
            )
            VALUES (
                'target-1', 1, 'post', 'item-1', 0, '作者',
                '第一行 第二行', '', '票券', '{}', ?
            );
            """,
            (now_text,),
        )

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()["value"]
        has_history_display_text = table_has_column(
            connection,
            "match_history",
            "display_text",
        )
        has_latest_display_text = table_has_column(
            connection,
            "latest_scan_items",
            "display_text",
        )
        history = MatchHistoryRepository(connection).list_by_target("target-1")
        latest_items = LatestScanItemRepository(connection).list_by_target("target-1")

    assert version == str(SCHEMA_VERSION)
    assert has_history_display_text
    assert has_latest_display_text
    assert history[0].display_text == "第一行 第二行"
    assert latest_items[0].display_text == "第一行 第二行"


def test_initialize_schema_migrates_v35_targets_check_constraints(
    tmp_path: Path,
) -> None:
    """v36 會重建 targets，加入核心 enum / boolean CHECK 並保留既有資料。"""

    db_path = tmp_path / "app.db"
    now_text = "2026-05-01T00:00:00+00:00"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        _create_raw_v35_targets_schema(connection)
        _insert_v35_target(
            connection,
            target_id="posts-target",
            group_id="posts-group",
            target_kind="posts",
            metadata_status="resolved",
            enabled=1,
            paused=0,
            worker_mode="headless",
            now_text=now_text,
        )
        _insert_v35_target(
            connection,
            target_id="comments-target",
            group_id="comments-group",
            target_kind="comments",
            metadata_status="pending",
            enabled=0,
            paused=1,
            worker_mode="headed_compat",
            now_text=now_text,
        )
        _insert_v35_target(
            connection,
            target_id="failed-metadata-target",
            group_id="failed-group",
            target_kind="posts",
            metadata_status="failed",
            enabled=1,
            paused=1,
            worker_mode="headless",
            now_text=now_text,
        )

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()["value"]
        targets_sql = table_sql(connection, "targets")
        rows = connection.execute(
            """
            SELECT id, target_kind, metadata_status, enabled, paused, worker_mode
            FROM targets
            ORDER BY id
            """
        ).fetchall()

        try:
            connection.execute(
                """
                UPDATE targets
                SET worker_mode = 'sync'
                WHERE id = 'posts-target'
                """
            )
        except sqlite3.IntegrityError as exc:
            assert "CHECK constraint failed" in str(exc)
        else:
            raise AssertionError("v35 -> v36 migration should constrain worker_mode")

    assert version == str(SCHEMA_VERSION)
    assert "CHECK (target_kind IN ('posts', 'comments'))" in targets_sql
    assert "CHECK (metadata_status IN ('resolved', 'pending', 'failed'))" in targets_sql
    assert "CHECK (enabled IN (0, 1))" in targets_sql
    assert "CHECK (paused IN (0, 1))" in targets_sql
    assert "CHECK (worker_mode IN ('headless', 'headed_compat'))" in targets_sql
    assert [(row["id"], row["metadata_status"], row["worker_mode"]) for row in rows] == [
        ("comments-target", "pending", "headed_compat"),
        ("failed-metadata-target", "failed", "headless"),
        ("posts-target", "resolved", "headless"),
    ]


def test_initialize_schema_rejects_v35_targets_with_invalid_check_values(
    tmp_path: Path,
) -> None:
    """v36 targets rebuild 遇到既有壞 enum / boolean 應 fail fast，不自動修資料。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        _create_raw_v35_targets_schema(connection)
        _insert_v35_target(
            connection,
            target_id="bad-target",
            target_kind="pages",
            metadata_status="resolved",
            enabled=2,
            paused=0,
            worker_mode="headless",
            now_text="2026-05-01T00:00:00+00:00",
        )

    with SqliteConnection(db_path) as sqlite:
        try:
            initialize_schema(sqlite.require_connection())
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("invalid v35 targets data should stop migration")

    assert "SQLite targets table contains values incompatible with v36 CHECK" in message
    assert "targets.target_kind" in message
    assert "targets.enabled" in message
    assert "bad-target" in message


def test_initialize_schema_migrates_v35_targets_rebuild_preserves_fk_and_indexes(
    tmp_path: Path,
) -> None:
    """targets parent-table rebuild 不應破壞 FK、cascade、metadata index 或 scope unique。"""

    db_path = tmp_path / "app.db"
    now_text = "2026-05-01T00:00:00+00:00"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        _create_raw_v35_targets_schema(connection)
        connection.execute(
            """
            CREATE TABLE target_dedupe_state (
                target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
                dedupe_epoch INTEGER NOT NULL DEFAULT 0 CHECK (dedupe_epoch >= 0),
                updated_at TEXT NOT NULL
            )
            """
        )
        _insert_v35_target(
            connection,
            target_id="posts-target",
            group_id="group-1",
            scope_id="group-1",
            target_kind="posts",
            metadata_status="resolved",
            enabled=1,
            paused=0,
            worker_mode="headless",
            now_text=now_text,
        )
        connection.execute(
            """
            INSERT INTO target_dedupe_state (target_id, dedupe_epoch, updated_at)
            VALUES ('posts-target', 3, ?)
            """,
            (now_text,),
        )

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        indexes = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        fk_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        dedupe_before_delete = connection.execute(
            "SELECT dedupe_epoch FROM target_dedupe_state WHERE target_id = 'posts-target'"
        ).fetchone()
        try:
            _insert_v35_target(
                connection,
                target_id="duplicate-scope-target",
                group_id="group-1",
                scope_id="group-1",
                target_kind="posts",
                metadata_status="resolved",
                enabled=1,
                paused=0,
                worker_mode="headless",
                now_text=now_text,
            )
        except sqlite3.IntegrityError as exc:
            assert "UNIQUE constraint failed" in str(exc)
        else:
            raise AssertionError("target kind/scope unique index should survive rebuild")
        connection.execute("DELETE FROM targets WHERE id = 'posts-target'")
        dedupe_after_delete = connection.execute(
            "SELECT 1 FROM target_dedupe_state WHERE target_id = 'posts-target'"
        ).fetchone()

    assert {"idx_targets_kind_scope_unique", "idx_targets_metadata_status_updated"}.issubset(
        indexes
    )
    assert fk_violations == []
    assert dedupe_before_delete["dedupe_epoch"] == 3
    assert dedupe_after_delete is None


def _create_raw_v35_targets_schema(connection: sqlite3.Connection) -> None:
    """建立 v35 代表性 targets schema；尚未含 v36 CHECK。"""

    connection.executescript(
        """
        CREATE TABLE schema_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT INTO schema_metadata (key, value) VALUES ('version', '35');
        CREATE TABLE targets (
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
        """
    )


def _insert_v35_target(
    connection: sqlite3.Connection,
    *,
    target_id: str,
    group_id: str | None = None,
    scope_id: str | None = None,
    target_kind: str,
    metadata_status: str,
    enabled: int,
    paused: int,
    worker_mode: str,
    now_text: str,
) -> None:
    """直接寫入 raw v35 targets row。"""

    resolved_group_id = group_id or target_id
    resolved_scope_id = (
        scope_id
        or (
            resolved_group_id
            if target_kind == "posts"
            else f"{resolved_group_id}:post:{target_id}:comments"
        )
    )
    connection.execute(
        """
        INSERT INTO targets (
            id, name, target_kind, group_id, group_name, group_cover_image_url,
            parent_post_id, scope_id, canonical_url, metadata_status, metadata_error,
            enabled, paused, worker_mode, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, '', '', ?, ?, ?, '', ?, ?, ?, ?, ?)
        """,
        (
            target_id,
            target_id,
            target_kind,
            resolved_group_id,
            "Group One",
            resolved_scope_id,
            f"https://www.facebook.com/groups/{resolved_group_id}/{target_id}",
            metadata_status,
            enabled,
            paused,
            worker_mode,
            now_text,
            now_text,
        ),
    )
