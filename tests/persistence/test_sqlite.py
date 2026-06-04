"""Persistence smoke tests。"""

from __future__ import annotations

from pathlib import Path
import sqlite3

from facebook_monitor.persistence.sqlite import SqliteConnection
from facebook_monitor.persistence.sqlite import initialize_schema

from tests.persistence.sqlite_test_helpers import table_sql


def test_sqlite_connection_uses_wal_busy_timeout_and_dashboard_indexes(tmp_path: Path) -> None:
    """SQLite 連線與 schema 具備 Web UI/background worker 並行所需設定。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)

        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
        indexes = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        revision_trigger_tables = {
            row["tbl_name"]
            for row in connection.execute(
                """
                SELECT tbl_name
                FROM sqlite_master
                WHERE type = 'trigger'
                  AND name LIKE 'trg_dashboard_revision_%'
                """
            ).fetchall()
        }

    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) == 5000
    assert int(synchronous) == 1
    assert {
        "idx_targets_kind_scope_unique",
        "idx_targets_metadata_status_updated",
        "idx_scan_runs_target_created",
        "idx_notification_events_target_created",
        "idx_latest_scan_items_target_index",
        "idx_runtime_state_status_updated",
        "idx_runtime_state_desired_updated",
        "idx_notification_outbox_dedupe",
    }.issubset(indexes)
    assert "idx_targets_kind_scope" not in indexes
    assert "latest_scan_item_matches" not in revision_trigger_tables
    assert "match_history_matches" not in revision_trigger_tables


def test_current_schema_enforces_selected_check_constraints(tmp_path: Path) -> None:
    """fresh DB 對已導入的 enum / boolean CHECK constraints 會直接拒絕壞值。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)

        assert "CHECK (runtime_status IN" in table_sql(connection, "target_runtime_state")
        assert "CHECK (status IN" in table_sql(connection, "notification_outbox")
        assert "CREATE TABLE logical_items" in table_sql(connection, "logical_items")
        assert "CREATE TABLE notification_dedupe" in table_sql(
            connection,
            "notification_dedupe",
        )
        assert "CHECK (min_refresh_sec >= 5)" in table_sql(connection, "target_configs")

        try:
            connection.execute(
                """
                INSERT INTO scan_scope_state (scope_id, initialized, updated_at)
                VALUES ('scope-a', 2, '2026-05-01T00:00:00+00:00')
                """
            )
        except sqlite3.IntegrityError as exc:
            assert "CHECK constraint failed" in str(exc)
        else:
            raise AssertionError("scan_scope_state.initialized should be constrained")

        try:
            connection.execute(
                """
                INSERT INTO seen_items (
                    scope_id, item_key, item_kind, parent_post_id, comment_id,
                    first_seen_at, last_seen_at
                )
                VALUES (
                    'scope-a',
                    'item-a',
                    'unexpected_kind',
                    '',
                    '',
                    '2026-05-01T00:00:00+00:00',
                    '2026-05-01T00:00:00+00:00'
                )
                """
            )
        except sqlite3.IntegrityError as exc:
            assert "CHECK constraint failed" in str(exc)
        else:
            raise AssertionError("seen_items.item_kind should be constrained")

        try:
            connection.execute(
                """
                INSERT INTO notification_dedupe (
                    target_id, dedupe_epoch, event_kind, channel, subject_key,
                    item_key, item_kind, status, first_queued_at, last_deduped_at,
                    created_at, updated_at
                )
                VALUES (
                    'target-a', 0, 'match', 'ntfy', 'subject-a',
                    'item-a', 'post', 'unexpected_status',
                    '2026-05-01T00:00:00+00:00',
                    '2026-05-01T00:00:00+00:00',
                    '2026-05-01T00:00:00+00:00',
                    '2026-05-01T00:00:00+00:00'
                )
                """
            )
        except sqlite3.IntegrityError as exc:
            assert "CHECK constraint failed" in str(exc)
        else:
            raise AssertionError("notification_dedupe.status should be constrained")
