"""Persistence smoke tests。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.persistence.sqlite_connection import SqliteConnection
from facebook_monitor.persistence.repositories.targets import TargetRepository
from facebook_monitor.persistence.schema import initialize_schema

from tests.persistence.sqlite_test_helpers import save_target_config_for_test
from tests.persistence.sqlite_test_helpers import target_config_repository


def test_target_config_repository_reads_target_scoped_config(tmp_path: Path) -> None:
    """正式 config repository 直接讀寫 target-scoped config。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)

        target = TargetDescriptor.for_group_posts(
            group_id="222518561920110",
            canonical_url="https://www.facebook.com/groups/222518561920110",
        )
        TargetRepository(connection).save(target)
        repo = target_config_repository(connection)
        save_target_config_for_test(
            connection,
            target.id,
            TargetConfig(
                target_id=target.id,
                include_keywords=("legacy",),
                fixed_refresh_sec=90,
            ),
        )

        loaded = repo.get_for_target(target)

    assert loaded is not None
    assert loaded.target_id == target.id
    assert loaded.include_keywords == ("legacy",)
    assert loaded.fixed_refresh_sec == 90


def test_target_config_repository_does_not_expose_group_config_api(tmp_path: Path) -> None:
    """repository 不再提供正式 group-scoped save/get 入口。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        repo = target_config_repository(connection)

        assert not hasattr(repo, "save")
        assert not hasattr(repo, "get")
        assert not hasattr(repo, "save_for_group")
        assert not hasattr(repo, "get_for_group")
        assert hasattr(repo, "save_for_target")
        assert hasattr(repo, "get_for_target")
        assert not hasattr(repo, "save_legacy_target_config_for_migration")
        assert not hasattr(repo, "get_legacy_target_config_for_migration")
