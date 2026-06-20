"""SQLite invariant checker tests。"""

from __future__ import annotations

from pathlib import Path
import sqlite3

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.persistence.invariants import validate_database_invariants
from facebook_monitor.persistence.schema_contract import BOOLEAN_CONTRACTS
from facebook_monitor.persistence.schema_contract import DATETIME_CONTRACTS
from facebook_monitor.persistence.schema_contract import ENUM_CONTRACTS
from facebook_monitor.persistence.schema_contract import RANGE_CONTRACTS
from facebook_monitor.persistence.sqlite_connection import SqliteConnection
from tests.persistence.sqlite_test_helpers import create_min_supported_v35_fixture_schema


EXPECTED_ENUM_CONTRACT_KEYS = {
    ("targets", "target_kind"),
    ("targets", "metadata_status"),
    ("targets", "worker_mode"),
    ("seen_items", "item_kind"),
    ("match_history", "item_kind"),
    ("latest_scan_items", "item_kind"),
    ("logical_items", "item_kind"),
    ("scan_runs", "status"),
    ("scan_runs", "worker_mode"),
    ("notification_events", "channel"),
    ("notification_events", "status"),
    ("notification_events", "event_kind"),
    ("notification_outbox", "item_kind"),
    ("notification_outbox", "channel"),
    ("notification_outbox", "status"),
    ("notification_outbox", "event_kind"),
    ("notification_dedupe", "event_kind"),
    ("notification_dedupe", "channel"),
    ("notification_dedupe", "item_kind"),
    ("notification_dedupe", "status"),
    ("target_runtime_state", "desired_state"),
    ("target_runtime_state", "runtime_status"),
    ("target_cover_image_refresh_state", "status"),
    ("target_cover_image_refresh_state", "last_result"),
}

EXPECTED_BOOLEAN_CONTRACT_KEYS = {
    ("targets", "enabled"),
    ("targets", "paused"),
    ("target_configs", "jitter_enabled"),
    ("target_configs", "auto_load_more"),
    ("target_configs", "auto_adjust_sort"),
    ("target_configs", "enable_desktop_notification"),
    ("target_configs", "enable_ntfy"),
    ("target_configs", "enable_discord_notification"),
    ("scan_scope_state", "initialized"),
    ("sidebar_groups", "collapsed"),
    ("sidebar_group_config_templates", "jitter_enabled"),
    ("sidebar_group_config_templates", "auto_load_more"),
    ("sidebar_group_config_templates", "auto_adjust_sort"),
    ("sidebar_group_config_templates", "enable_desktop_notification"),
    ("sidebar_group_config_templates", "enable_ntfy"),
    ("sidebar_group_config_templates", "enable_discord_notification"),
    ("target_cover_image_refresh_state", "changed"),
}

EXPECTED_RANGE_CONTRACT_KEYS = {
    ("target_configs", "refresh_range"),
    ("sidebar_group_config_templates", "refresh_range"),
    ("target_configs", "max_items_per_scan"),
    ("sidebar_group_config_templates", "max_items_per_scan"),
    ("scan_runs", "item_count"),
    ("notification_outbox", "attempts"),
    ("notification_outbox", "failure_count"),
    ("notification_events", "failure_count"),
    ("target_dedupe_state", "dedupe_epoch"),
    ("logical_items", "dedupe_epoch"),
    ("logical_item_aliases", "dedupe_epoch"),
    ("notification_dedupe", "dedupe_epoch"),
    ("notification_dedupe", "failure_count"),
    ("target_runtime_state", "scan_guard_count"),
}

EXPECTED_DATETIME_CONTRACT_KEYS = {
    ("targets", "created_at"),
    ("targets", "updated_at"),
    ("match_history", "recorded_at"),
    ("match_history", "created_at"),
    ("latest_scan_items", "scanned_at"),
    ("scan_runs", "started_at"),
    ("scan_runs", "finished_at"),
    ("notification_events", "created_at"),
    ("notification_outbox", "created_at"),
    ("notification_outbox", "updated_at"),
    ("target_runtime_state", "scan_requested_at"),
    ("target_runtime_state", "last_enqueued_at"),
    ("target_runtime_state", "last_started_at"),
    ("target_runtime_state", "last_finished_at"),
    ("target_runtime_state", "last_heartbeat_at"),
    ("target_runtime_state", "last_page_reloaded_at"),
    ("target_runtime_state", "display_next_due_at"),
    ("target_runtime_state", "updated_at"),
    ("target_cover_image_refresh_state", "requested_at"),
    ("target_cover_image_refresh_state", "last_attempted_at"),
    ("target_cover_image_refresh_state", "last_succeeded_at"),
    ("target_cover_image_refresh_state", "last_failed_at"),
    ("target_cover_image_refresh_state", "updated_at"),
    ("sidebar_groups", "created_at"),
    ("sidebar_groups", "updated_at"),
    ("sidebar_target_placements", "updated_at"),
    ("sidebar_group_config_templates", "updated_at"),
}


def test_database_invariants_pass_for_fresh_application_rows(tmp_path: Path) -> None:
    """正常 application service 寫入的資料應通過 invariant checker。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        violations = validate_database_invariants(app.repositories.targets.connection)

    assert violations == ()


def test_database_invariants_report_enum_boolean_range_and_runtime_errors(
    tmp_path: Path,
) -> None:
    """checker 需抓到 enum、boolean、range 與 runtime ownership 異常。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        connection = app.repositories.targets.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE targets
            SET target_kind = ?,
                metadata_status = ?,
                worker_mode = ?,
                enabled = ?,
                paused = ?
            WHERE id = ?
            """,
            ("pages", "unknown", "sync", 2, -1, target.id),
        )
        connection.execute(
            """
            UPDATE target_configs
            SET min_refresh_sec = 1,
                max_refresh_sec = 0
            WHERE target_id = ?
            """,
            (target.id,),
        )
        connection.execute(
            """
            INSERT INTO target_dedupe_state (target_id, dedupe_epoch, updated_at)
            VALUES (?, -1, '2026-05-01T00:00:00+00:00')
            """,
            (target.id,),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")
        connection.execute(
            """
            UPDATE target_runtime_state
            SET active_worker_id = ?,
                active_page_id = ?
            WHERE target_id = ?
            """,
            ("worker-a", "page-a", target.id),
        )
        violations = validate_database_invariants(connection)

    formatted = "\n".join(violation.format() for violation in violations)
    assert "targets" in formatted
    assert "target_kind" in formatted
    assert "metadata_status" in formatted
    assert "worker_mode" in formatted
    assert "enabled" in formatted
    assert "paused" in formatted
    assert "target_configs" in formatted
    assert "refresh_range" in formatted
    assert "target_dedupe_state" in formatted
    assert "dedupe_epoch" in formatted
    assert "target_runtime_state" in formatted
    assert "non-running state must not keep active worker/page ownership" in formatted


def test_database_invariants_report_required_runtime_updated_at(
    tmp_path: Path,
) -> None:
    """runtime updated_at 為 mapper 必要欄位，空值需被 invariant checker 抓到。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="runtime-updated-at",
                canonical_url="https://www.facebook.com/groups/runtime-updated-at",
            )
        )
        connection = app.repositories.targets.connection
        connection.execute(
            "UPDATE target_runtime_state SET updated_at = '' WHERE target_id = ?",
            (target.id,),
        )

        violations = validate_database_invariants(connection)

    formatted = "\n".join(violation.format() for violation in violations)
    assert "target_runtime_state" in formatted
    assert "updated_at" in formatted
    assert "datetime value is required" in formatted


def test_database_invariants_report_duplicate_target_scopes(tmp_path: Path) -> None:
    """duplicate target scope 應被 read-only invariant checker 回報而非修復。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="duplicate-invariant",
                canonical_url="https://www.facebook.com/groups/duplicate-invariant",
            )
        )
        connection = app.repositories.targets.connection
        connection.execute("DROP INDEX idx_targets_kind_scope_unique")
        connection.execute(
            """
            INSERT INTO targets (
                id, name, target_kind, group_id, group_name, group_cover_image_url,
                parent_post_id, scope_id, canonical_url, metadata_status, metadata_error,
                enabled, paused, worker_mode, created_at, updated_at
            )
            SELECT
                ?, name, target_kind, group_id, group_name, group_cover_image_url,
                parent_post_id, scope_id, canonical_url, metadata_status, metadata_error,
                enabled, paused, worker_mode, created_at, updated_at
            FROM targets
            WHERE id = ?
            """,
            ("duplicate-invariant-copy", target.id),
        )

        violations = validate_database_invariants(connection)
        remaining_count = connection.execute(
            "SELECT COUNT(1) AS count FROM targets WHERE scope_id = ?",
            (target.scope_id,),
        ).fetchone()["count"]

    formatted = "\n".join(violation.format() for violation in violations)
    assert "duplicate target scope" in formatted
    assert "duplicate-invariant-copy" in formatted
    assert remaining_count == 2


def test_schema_contract_fields_exist_in_current_schema(tmp_path: Path) -> None:
    """schema contract 引用的欄位必須存在於 current schema。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        connection = app.repositories.targets.connection
        _assert_schema_contract_queries_are_valid(connection)


def test_schema_contract_has_independent_expected_key_set() -> None:
    """schema contract 內容需由獨立 expected set 鎖住，避免欄位漂移。"""

    enum_keys = {(contract.table, contract.field) for contract in ENUM_CONTRACTS}
    boolean_keys = {
        (contract.table, field)
        for contract in BOOLEAN_CONTRACTS
        for field in contract.fields
    }
    range_keys = {(contract.table, contract.field) for contract in RANGE_CONTRACTS}
    datetime_keys = {
        (contract.table, field)
        for contract in DATETIME_CONTRACTS
        for field in contract.fields
    }

    assert enum_keys == EXPECTED_ENUM_CONTRACT_KEYS
    assert boolean_keys == EXPECTED_BOOLEAN_CONTRACT_KEYS
    assert range_keys == EXPECTED_RANGE_CONTRACT_KEYS
    assert datetime_keys == EXPECTED_DATETIME_CONTRACT_KEYS


def test_schema_contract_fields_exist_after_min_supported_migration(
    tmp_path: Path,
) -> None:
    """最低支援 v35 DB migration 到 current 後仍需符合 schema contract 查詢形狀。"""

    db_path = tmp_path / "min-supported.db"
    with SqliteConnection(db_path) as sqlite:
        create_min_supported_v35_fixture_schema(sqlite.require_connection())

    with SqliteApplicationContext(db_path) as app:
        _assert_schema_contract_queries_are_valid(app.repositories.targets.connection)


def _assert_schema_contract_queries_are_valid(connection: sqlite3.Connection) -> None:
    """使用 invariant checker 同形狀 SELECT 驗證 contract 欄位與 row id 表達式。"""

    for enum_contract in ENUM_CONTRACTS:
        allowed = tuple(sorted(enum_contract.allowed_values))
        placeholders = ",".join("?" for _ in allowed)
        connection.execute(
            f"""
            SELECT {enum_contract.row_id_expr} AS row_id, {enum_contract.field}
            FROM {enum_contract.table}
            WHERE {enum_contract.field} NOT IN ({placeholders})
            LIMIT 1
            """,
            allowed,
        ).fetchall()
    for boolean_contract in BOOLEAN_CONTRACTS:
        for field in boolean_contract.fields:
            connection.execute(
                f"""
                SELECT {boolean_contract.row_id_column} AS row_id, {field}
                FROM {boolean_contract.table}
                WHERE {field} NOT IN (0, 1)
                LIMIT 1
                """
            ).fetchall()
    for range_contract in RANGE_CONTRACTS:
        connection.execute(
            (
                f"SELECT {range_contract.row_id_column} AS row_id "
                f"FROM {range_contract.table} "
                f"WHERE {range_contract.where_clause} "
                "LIMIT 1"
            ),
            range_contract.params,
        ).fetchall()
    for datetime_contract in DATETIME_CONTRACTS:
        fields = ", ".join(datetime_contract.fields)
        connection.execute(
            f"""
            SELECT {datetime_contract.row_id_column} AS row_id, {fields}
            FROM {datetime_contract.table}
            LIMIT 1
            """
        ).fetchall()
