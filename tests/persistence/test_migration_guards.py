"""Persistence migration boundary guard tests。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.persistence.migrations import MIGRATIONS
from facebook_monitor.persistence.migrations import TARGETS_V35_TO_V36_COLUMNS
from facebook_monitor.persistence.schema import MIN_SUPPORTED_SCHEMA_VERSION
from facebook_monitor.persistence.schema import SCHEMA_VERSION


def test_schema_version_has_explicit_supported_migration_chain() -> None:
    """SCHEMA_VERSION 增加時必須補目前支援範圍內的版本 migration。"""

    assert MIN_SUPPORTED_SCHEMA_VERSION == 35
    assert set(MIGRATIONS) == set(range(MIN_SUPPORTED_SCHEMA_VERSION, SCHEMA_VERSION))


def test_schema_repairs_module_does_not_exist() -> None:
    """不得恢復 current-schema repair 平行路徑。"""

    assert not Path("src/facebook_monitor/persistence/schema_repairs.py").exists()


def test_targets_check_rebuild_is_owned_by_v35_to_v36() -> None:
    """targets 是 FK parent table，核心 CHECK 只能由 v35 -> v36 migration 導入。"""

    assert TARGETS_V35_TO_V36_COLUMNS
    assert MIGRATIONS[35].__name__ == "migrate_35_to_36"
