"""SQLite migration smoke tests。"""

from __future__ import annotations

from pathlib import Path
import sqlite3

from facebook_monitor.persistence.schema import MIN_SUPPORTED_SCHEMA_VERSION
from facebook_monitor.persistence.schema import SCHEMA_VERSION
from facebook_monitor.persistence.schema import initialize_schema
from facebook_monitor.persistence.sqlite_connection import SqliteConnection

from tests.persistence.sqlite_test_helpers import table_exists
from tests.persistence.sqlite_test_helpers import table_has_column
from tests.persistence.sqlite_test_helpers import table_sql


def test_initialize_schema_migrates_v35_fixture_to_current(tmp_path: Path) -> None:
    """v0.5.3 的 v35 DB 可升到目前 schema，並套用後續所有 migration。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        _create_v35_fixture_schema(connection)

        initialize_schema(connection)

        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()["value"]
        match_time = connection.execute(
            "SELECT recorded_at FROM match_history WHERE item_key = 'item-a'"
        ).fetchone()["recorded_at"]
        targets_sql = table_sql(connection, "targets")
        has_global_notification_settings = table_exists(
            connection,
            "global_notification_settings",
        )
        has_recorded_at = table_has_column(connection, "match_history", "recorded_at")
        has_notified_at = table_has_column(connection, "match_history", "notified_at")

    assert version == str(SCHEMA_VERSION)
    assert targets_sql.count("CHECK") >= 5
    assert not has_global_notification_settings
    assert has_recorded_at
    assert not has_notified_at
    assert match_time == "2026-05-01T00:00:00+00:00"


def test_initialize_schema_rejects_v35_targets_with_invalid_check_values(
    tmp_path: Path,
) -> None:
    """v35 -> v36 會先用可讀錯誤拒絕不符合 current enum / boolean 的 target。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        _create_v35_fixture_schema(
            connection,
            target_kind="page",
            enabled=9,
        )

        try:
            initialize_schema(connection)
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("invalid v35 target values should fail migration")

    assert "incompatible with v36 CHECK constraints" in message
    assert "targets.target_kind invalid row id(s): target-a" in message
    assert "targets.enabled invalid row id(s): target-a" in message


def test_initialize_schema_migrates_v35_targets_check_constraints(
    tmp_path: Path,
) -> None:
    """v35 -> v36 會重建 targets，加入核心 CHECK 並保留既有資料。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        _create_v35_fixture_schema(connection)
        _insert_v35_target(
            connection,
            target_id="comments-target",
            group_id="comments-group",
            scope_id="comments-group:post:post-1:comments",
            target_kind="comments",
            metadata_status="pending",
            enabled=0,
            paused=1,
            worker_mode="headed_compat",
        )
        _insert_v35_target(
            connection,
            target_id="failed-metadata-target",
            group_id="failed-group",
            scope_id="group:failed-group",
            target_kind="posts",
            metadata_status="failed",
            enabled=1,
            paused=1,
            worker_mode="headless",
        )

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
                WHERE id = 'target-a'
                """
            )
        except sqlite3.IntegrityError as exc:
            constraint_error = str(exc)
        else:
            raise AssertionError("v35 -> v36 migration should constrain worker_mode")

    assert version == str(SCHEMA_VERSION)
    assert "CHECK (target_kind IN ('posts', 'comments'))" in targets_sql
    assert "CHECK (metadata_status IN ('resolved', 'pending', 'failed'))" in targets_sql
    assert "CHECK (enabled IN (0, 1))" in targets_sql
    assert "CHECK (paused IN (0, 1))" in targets_sql
    assert "CHECK (worker_mode IN ('headless', 'headed_compat'))" in targets_sql
    assert "CHECK constraint failed" in constraint_error
    assert [(row["id"], row["metadata_status"], row["worker_mode"]) for row in rows] == [
        ("comments-target", "pending", "headed_compat"),
        ("failed-metadata-target", "failed", "headless"),
        ("target-a", "resolved", "headless"),
    ]


def test_initialize_schema_migrates_v35_targets_preserves_fk_and_indexes(
    tmp_path: Path,
) -> None:
    """targets parent-table rebuild 不得破壞 FK、cascade 或 post-migration indexes。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        _create_v35_fixture_schema(connection)
        connection.execute(
            """
            CREATE TABLE target_dedupe_state (
                target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
                dedupe_epoch INTEGER NOT NULL DEFAULT 0 CHECK (dedupe_epoch >= 0),
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO target_dedupe_state (target_id, dedupe_epoch, updated_at)
            VALUES ('target-a', 3, '2026-05-01T00:00:00+00:00')
            """
        )

        initialize_schema(connection)

        indexes = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        fk_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        dedupe_before_delete = connection.execute(
            "SELECT dedupe_epoch FROM target_dedupe_state WHERE target_id = 'target-a'"
        ).fetchone()
        try:
            _insert_v35_target(
                connection,
                target_id="duplicate-scope-target",
                group_id="group-a",
                scope_id="group:group-a",
                target_kind="posts",
                metadata_status="resolved",
                enabled=1,
                paused=0,
                worker_mode="headless",
            )
        except sqlite3.IntegrityError as exc:
            duplicate_error = str(exc)
        else:
            raise AssertionError("target kind/scope unique index should survive rebuild")
        connection.execute("DELETE FROM targets WHERE id = 'target-a'")
        dedupe_after_delete = connection.execute(
            "SELECT 1 FROM target_dedupe_state WHERE target_id = 'target-a'"
        ).fetchone()

    assert {"idx_targets_kind_scope_unique", "idx_targets_metadata_status_updated"}.issubset(
        indexes
    )
    assert fk_violations == []
    assert dedupe_before_delete["dedupe_epoch"] == 3
    assert "UNIQUE constraint failed" in duplicate_error
    assert dedupe_after_delete is None


def test_initialize_schema_migrates_v36_drops_global_notification_settings(
    tmp_path: Path,
) -> None:
    """v37 會移除不再是正式設定來源的全域通知設定表。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        connection.executescript(
            """
            CREATE TABLE schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO schema_metadata (key, value) VALUES ('version', '36');
            CREATE TABLE global_notification_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enable_desktop_notification INTEGER NOT NULL,
                enable_ntfy INTEGER NOT NULL,
                ntfy_topic TEXT NOT NULL,
                enable_discord_notification INTEGER NOT NULL,
                discord_webhook TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO global_notification_settings (
                id, enable_desktop_notification, enable_ntfy, ntfy_topic,
                enable_discord_notification, discord_webhook, updated_at
            )
            VALUES (1, 1, 1, 'topic-a', 1, 'https://discord.example', '2026-05-01');
            """
        )

        initialize_schema(connection)

        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()["value"]
        has_global_notification_settings = table_exists(
            connection,
            "global_notification_settings",
        )

    assert version == str(SCHEMA_VERSION)
    assert not has_global_notification_settings


def test_initialize_schema_migrates_v37_match_history_recorded_at(
    tmp_path: Path,
) -> None:
    """v38 會把舊 notified_at 欄位改成 recorded_at，並保留既有值。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        _create_v37_match_history_schema(connection)

        initialize_schema(connection)

        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()["value"]
        row = connection.execute(
            "SELECT recorded_at FROM match_history WHERE item_key = 'item-a'"
        ).fetchone()
        has_recorded_at = table_has_column(connection, "match_history", "recorded_at")
        has_notified_at = table_has_column(connection, "match_history", "notified_at")

    assert version == str(SCHEMA_VERSION)
    assert has_recorded_at
    assert not has_notified_at
    assert row["recorded_at"] == "2026-05-01T00:00:00+00:00"


def test_initialize_schema_migrates_v38_notification_outbox_processing_token(
    tmp_path: Path,
) -> None:
    """v39 會替 notification_outbox 補 processing claim token 欄位。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        connection.executescript(
            """
            CREATE TABLE schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO schema_metadata (key, value) VALUES ('version', '38');
            CREATE TABLE notification_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                dedupe_id INTEGER,
                target_id TEXT NOT NULL,
                item_key TEXT NOT NULL,
                item_kind TEXT NOT NULL,
                channel TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                endpoint TEXT NOT NULL DEFAULT '',
                permalink TEXT NOT NULL,
                event_kind TEXT NOT NULL DEFAULT 'match',
                source_scan_run_id INTEGER,
                failure_reason TEXT NOT NULL DEFAULT '',
                failure_count INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL,
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
        ).fetchone()["value"]
        has_processing_token = table_has_column(
            connection,
            "notification_outbox",
            "processing_token",
        )

    assert version == str(SCHEMA_VERSION)
    assert has_processing_token


def test_fresh_schema_does_not_create_group_configs_formal_table(
    tmp_path: Path,
) -> None:
    """fresh current schema 不再建立舊 group_configs formal table。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        initialize_schema(sqlite.require_connection())

    with SqliteConnection(db_path) as sqlite:
        assert not table_exists(sqlite.require_connection(), "group_configs")


def _create_v35_fixture_schema(
    connection: sqlite3.Connection,
    *,
    target_kind: str = "posts",
    enabled: int = 1,
) -> None:
    """建立 v0.5.3 可代表的 v35 fixture。"""

    assert MIN_SUPPORTED_SCHEMA_VERSION == 35
    connection.executescript(
        f"""
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
        INSERT INTO targets (
            id, name, target_kind, group_id, group_name, group_cover_image_url,
            parent_post_id, scope_id, canonical_url, metadata_status, metadata_error,
            enabled, paused, worker_mode, created_at, updated_at
        )
        VALUES (
            'target-a', '社團 A', '{target_kind}', 'group-a', '社團 A', '',
            '', 'group:group-a', 'https://www.facebook.com/groups/group-a',
            'resolved', '', {enabled}, 0, 'headless',
            '2026-05-01T00:00:00+00:00', '2026-05-01T00:00:00+00:00'
        );

        CREATE TABLE global_notification_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enable_desktop_notification INTEGER NOT NULL,
            enable_ntfy INTEGER NOT NULL,
            ntfy_topic TEXT NOT NULL,
            enable_discord_notification INTEGER NOT NULL,
            discord_webhook TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO global_notification_settings (
            id, enable_desktop_notification, enable_ntfy, ntfy_topic,
            enable_discord_notification, discord_webhook, updated_at
        )
        VALUES (1, 1, 0, '', 0, '', '2026-05-01T00:00:00+00:00');
        """
    )
    _create_v37_match_history_schema(
        connection,
        include_schema_metadata=False,
    )


def _create_v37_match_history_schema(
    connection: sqlite3.Connection,
    *,
    include_schema_metadata: bool = True,
) -> None:
    """建立仍使用 notified_at 欄位的舊 match_history table。"""

    metadata_sql = (
        """
        CREATE TABLE schema_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT INTO schema_metadata (key, value) VALUES ('version', '37');
        """
        if include_schema_metadata
        else ""
    )
    connection.executescript(
        metadata_sql
        + """
        CREATE TABLE match_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            group_name TEXT NOT NULL,
            item_kind TEXT NOT NULL,
            parent_post_id TEXT NOT NULL DEFAULT '',
            comment_id TEXT NOT NULL DEFAULT '',
            item_key TEXT NOT NULL,
            author TEXT NOT NULL,
            text TEXT NOT NULL,
            display_text TEXT NOT NULL DEFAULT '',
            permalink TEXT NOT NULL,
            include_rule TEXT NOT NULL,
            timestamp_text TEXT NOT NULL,
            notified_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        INSERT INTO match_history (
            target_id, group_id, group_name, item_kind, parent_post_id, comment_id,
            item_key, author, text, display_text, permalink, include_rule,
            timestamp_text, notified_at, created_at
        )
        VALUES (
            'target-a', 'group-a', '社團 A', 'post', '', '',
            'item-a', '作者', '內容', '內容', 'https://example.test/post',
            '票', '', '2026-05-01T00:00:00+00:00',
            '2026-05-01T00:00:00+00:00'
        );
        """
    )


def _insert_v35_target(
    connection: sqlite3.Connection,
    *,
    target_id: str,
    group_id: str,
    scope_id: str,
    target_kind: str,
    metadata_status: str,
    enabled: int,
    paused: int,
    worker_mode: str,
) -> None:
    """直接寫入 raw v35 targets row。"""

    now_text = "2026-05-01T00:00:00+00:00"
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
            group_id,
            "Group One",
            scope_id,
            f"https://www.facebook.com/groups/{group_id}/{target_id}",
            metadata_status,
            enabled,
            paused,
            worker_mode,
            now_text,
            now_text,
        ),
    )
