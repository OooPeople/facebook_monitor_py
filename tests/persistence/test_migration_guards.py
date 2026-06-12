"""Persistence migration boundary guard tests。"""

from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path

from facebook_monitor.persistence.migrations import MIGRATIONS
from facebook_monitor.persistence.migrations import TARGETS_V35_TO_V36_COLUMNS
from facebook_monitor.persistence.migrations import V12_TO_13_COLUMNS
from facebook_monitor.persistence.migrations import V29_TO_V30_CHECKED_TABLES
from facebook_monitor.persistence.migrations import migrate_26_to_27
from facebook_monitor.persistence.migrations import migrate_27_to_28
from facebook_monitor.persistence.schema import SCHEMA_VERSION


def test_schema_version_has_explicit_migration_chain() -> None:
    """SCHEMA_VERSION 增加時必須補版本 migration。"""

    assert set(MIGRATIONS) == set(range(10, SCHEMA_VERSION))


def test_schema_repairs_module_does_not_exist() -> None:
    """不得恢復 current-schema repair 平行路徑。"""

    assert not Path("src/facebook_monitor/persistence/schema_repairs.py").exists()


def test_v12_to_v13_migration_owns_historical_repair_columns() -> None:
    """歷史缺欄補齊現在由正式 12 -> 13 migration 擁有。"""

    assert V12_TO_13_COLUMNS
    assert {column.table_name for column in V12_TO_13_COLUMNS} == {
        "group_configs",
        "latest_scan_items",
        "notification_outbox",
        "target_configs",
        "target_runtime_state",
    }


def test_targets_check_rebuild_is_owned_by_v35_to_v36() -> None:
    """targets 是 FK parent table，不得塞回 v29 child-table rebuild。"""

    assert TARGETS_V35_TO_V36_COLUMNS
    assert "targets" not in {spec.table_name for spec in V29_TO_V30_CHECKED_TABLES}


def test_cover_refresh_diagnostics_are_owned_by_v27_to_v28() -> None:
    """v27 保持已發布表格形狀，診斷欄位只由 v28 migration 補上。"""

    with closing(sqlite3.connect(":memory:")) as connection:
        migrate_26_to_27(connection)
        v27_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(target_cover_image_refresh_state)"
            ).fetchall()
        }
        migrate_27_to_28(connection)
        v28_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(target_cover_image_refresh_state)"
            ).fetchall()
        }
    assert "last_reported_url" in v27_columns
    assert "last_resolved_url" not in v27_columns
    assert "last_result" not in v27_columns
    assert "changed" not in v27_columns

    assert "last_resolved_url" in v28_columns
    assert "last_result" in v28_columns
    assert "changed" in v28_columns
