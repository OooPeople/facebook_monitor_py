"""Persistence migration boundary guard tests。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.persistence.migrations import MIGRATIONS
from facebook_monitor.persistence.migrations import V12_TO_13_COLUMNS
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
