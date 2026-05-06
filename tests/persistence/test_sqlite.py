"""Phase A persistence smoke tests。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import GlobalNotificationSettings
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
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.sqlite import MatchHistoryRepository
from facebook_monitor.persistence.sqlite import GlobalNotificationSettingsRepository
from facebook_monitor.persistence.sqlite import LatestScanItemRepository
from facebook_monitor.persistence.sqlite import NotificationEventRepository
from facebook_monitor.persistence.sqlite import ScanRunRepository
from facebook_monitor.persistence.sqlite import SeenItemRepository
from facebook_monitor.persistence.sqlite import SqliteConnection
from facebook_monitor.persistence.sqlite import TargetConfigRepository
from facebook_monitor.persistence.sqlite import TargetRepository
from facebook_monitor.persistence.sqlite import TargetRuntimeStateRepository
from facebook_monitor.persistence.sqlite import initialize_schema
from facebook_monitor.persistence.maintenance import RuntimeDataMaintenanceRepository


def table_count(connection: object, table_name: str) -> int:
    """回傳指定測試資料表目前筆數。"""

    row = connection.execute(f"SELECT COUNT(1) FROM {table_name}").fetchone()
    return int(row[0])


def test_target_config_seen_scan_and_notification_roundtrip(tmp_path: Path) -> None:
    """儲存 target/config/seen/scan/notification 後可查詢或取得 id。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)

        target = TargetDescriptor.for_group_posts(
            group_id="222518561920110",
            canonical_url="https://www.facebook.com/groups/222518561920110",
            group_name="test group",
        )
        TargetRepository(connection).save(target)
        loaded_target = TargetRepository(connection).get(target.id)

        assert loaded_target is not None
        assert loaded_target.scope_id == target.group_id
        assert TargetRepository(connection).list_enabled() == [loaded_target]
        assert TargetRepository(connection).list_all() == [loaded_target]

        comments_target = TargetDescriptor.for_comments(
            group_id=target.group_id,
            parent_post_id="2187454285426518",
            canonical_url=(
                "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"
            ),
            name="留言 target",
        )
        TargetRepository(connection).save(comments_target)
        loaded_comments = TargetRepository(connection).find_by_kind_scope(
            TargetKind.COMMENTS,
            "222518561920110:post:2187454285426518:comments",
        )

        assert loaded_comments is not None
        assert loaded_comments.target_kind == TargetKind.COMMENTS
        assert loaded_comments.parent_post_id == "2187454285426518"
        assert loaded_comments.scope_id == "222518561920110:post:2187454285426518:comments"
        assert loaded_comments.paused

        config = TargetConfig(
            target_id=target.id,
            include_keywords=("票", "交換"),
            enable_desktop_notification=True,
            enable_ntfy=True,
            ntfy_topic="phase0test",
            enable_discord_notification=True,
            discord_webhook="https://discord.com/api/webhooks/example",
        )
        TargetConfigRepository(connection).save_legacy_target_config_for_migration(config)
        loaded_config = TargetConfigRepository(
            connection
        ).get_legacy_target_config_for_migration(target.id)

        assert loaded_config is not None
        assert loaded_config.include_keywords == ("票", "交換")
        assert loaded_config.enable_desktop_notification
        assert loaded_config.enable_ntfy
        assert loaded_config.ntfy_topic == "phase0test"
        assert loaded_config.enable_discord_notification
        assert loaded_config.discord_webhook == "https://discord.com/api/webhooks/example"

        global_settings = GlobalNotificationSettings(
            enable_desktop_notification=True,
            enable_ntfy=True,
            ntfy_topic="global-topic",
            enable_discord_notification=True,
            discord_webhook="https://discord.com/api/webhooks/global",
        )
        settings_repo = GlobalNotificationSettingsRepository(connection)
        settings_repo.save(global_settings)
        loaded_settings = settings_repo.get()

        assert loaded_settings.enable_desktop_notification
        assert loaded_settings.enable_ntfy
        assert loaded_settings.ntfy_topic == "global-topic"
        assert loaded_settings.enable_discord_notification
        assert loaded_settings.discord_webhook == "https://discord.com/api/webhooks/global"

        runtime_state = TargetRuntimeState(
            target_id=target.id,
            desired_state=TargetDesiredState.ACTIVE,
            runtime_status=TargetRuntimeStatus.IDLE,
        )
        TargetRuntimeStateRepository(connection).save(runtime_state)
        loaded_runtime_state = TargetRuntimeStateRepository(connection).get(target.id)

        assert loaded_runtime_state is not None
        assert loaded_runtime_state.target_id == target.id
        assert loaded_runtime_state.desired_state == TargetDesiredState.ACTIVE
        assert loaded_runtime_state.runtime_status == TargetRuntimeStatus.IDLE

        seen_repo = SeenItemRepository(connection)
        seen_item = SeenItem(
            scope_id=target.scope_id,
            item_key="item-hash",
            item_kind=ItemKind.POST,
        )
        assert seen_repo.mark_seen(seen_item)
        assert not seen_repo.mark_seen(seen_item)
        assert seen_repo.has_seen(target.scope_id, "item-hash")

        alias_item = SeenItem(
            scope_id=target.scope_id,
            item_key="primary-alias",
            item_kind=ItemKind.POST,
        )
        assert seen_repo.mark_seen_aliases(alias_item, ("primary-alias", "secondary-alias"))
        assert not seen_repo.mark_seen_aliases(alias_item, ("new-primary", "secondary-alias"))
        assert seen_repo.has_seen(target.scope_id, "new-primary")
        assert seen_repo.has_seen_any(target.scope_id, ("missing", "secondary-alias"))

        scan_id = ScanRunRepository(connection).add(
            ScanRun(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
                started_at=utc_now(),
                finished_at=utc_now(),
                item_count=9,
                matched_count=1,
                metadata={"scroll_rounds": 5},
            )
        )
        assert scan_id > 0
        latest_scan = ScanRunRepository(connection).latest_by_target(target.id)
        assert latest_scan is not None
        assert latest_scan.item_count == 9
        assert latest_scan.metadata == {"scroll_rounds": 5}

        history_repo = MatchHistoryRepository(connection)
        history_id = history_repo.add(
            MatchHistoryEntry(
                target_id=target.id,
                group_id=target.group_id,
                group_name=target.group_name,
                item_kind=ItemKind.POST,
                item_key="item-hash",
                text="測試文字",
                permalink="https://www.facebook.com/groups/example/posts/1",
                include_rule="票",
            )
        )
        assert history_id > 0
        history = history_repo.list_by_target(target.id)
        assert len(history) == 1
        assert history[0].include_rule == "票"

        latest_repo = LatestScanItemRepository(connection)
        latest_repo.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=scan_id,
                    item_kind=ItemKind.POST,
                    item_key="item-hash",
                    item_index=0,
                    author="王小明",
                    text="測試文字",
                    permalink="https://www.facebook.com/groups/example/posts/1",
                    matched_keyword="票",
                    debug_metadata={"textSource": "primary", "expandCount": 1},
                )
            ],
        )
        latest_items = latest_repo.list_by_target(target.id)
        assert len(latest_items) == 1
        assert latest_items[0].author == "王小明"
        assert latest_items[0].matched_keyword == "票"
        assert latest_items[0].debug_metadata == {"textSource": "primary", "expandCount": 1}

        event_id = NotificationEventRepository(connection).add(
            NotificationEvent(
                target_id=target.id,
                item_key="item-hash",
                channel=NotificationChannel.NTFY,
                status=NotificationStatus.SENT,
                message="sent",
            )
        )
        assert event_id > 0
        events = NotificationEventRepository(connection).list_by_target(target.id)
        assert len(events) == 1
        assert events[0].status == NotificationStatus.SENT
        assert events[0].channel == NotificationChannel.NTFY


def test_group_config_migrates_from_legacy_target_config(tmp_path: Path) -> None:
    """正式 group config 缺資料時，會從舊 target config fallback 並保存。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)

        target = TargetDescriptor.for_group_posts(
            group_id="222518561920110",
            canonical_url="https://www.facebook.com/groups/222518561920110",
        )
        TargetRepository(connection).save(target)
        repo = TargetConfigRepository(connection)
        repo.save_legacy_target_config_for_migration(
            TargetConfig(
                target_id=target.id,
                include_keywords=("legacy",),
                fixed_refresh_sec=90,
            )
        )

        migrated = repo.get_for_target(target)
        loaded_group_config = repo.get_for_group(target.group_id)

    assert migrated is not None
    assert migrated.target_id == target.group_id
    assert migrated.include_keywords == ("legacy",)
    assert migrated.fixed_refresh_sec == 90
    assert loaded_group_config == migrated


def test_target_config_repository_legacy_methods_are_migration_only(tmp_path: Path) -> None:
    """repository 不再提供正式 target-scoped save/get 入口。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        repo = TargetConfigRepository(connection)

        assert not hasattr(repo, "save")
        assert not hasattr(repo, "get")
        assert hasattr(repo, "save_legacy_target_config_for_migration")
        assert hasattr(repo, "get_legacy_target_config_for_migration")


def test_runtime_data_maintenance_clears_debug_tables_but_keeps_settings(
    tmp_path: Path,
) -> None:
    """runtime data 清理會刪除可重建資料，但保留 target/config/global settings。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)

        target = TargetDescriptor.for_group_posts(
            group_id="222518561920110",
            canonical_url="https://www.facebook.com/groups/222518561920110",
            group_name="test group",
        )
        TargetRepository(connection).save(target)
        TargetConfigRepository(connection).save_legacy_target_config_for_migration(
            TargetConfig(target_id=target.id)
        )
        TargetRuntimeStateRepository(connection).save(
            TargetRuntimeState(
                target_id=target.id,
                desired_state=TargetDesiredState.ACTIVE,
                runtime_status=TargetRuntimeStatus.IDLE,
            )
        )
        GlobalNotificationSettingsRepository(connection).save(
            GlobalNotificationSettings(enable_ntfy=True, ntfy_topic="phase0test")
        )
        SeenItemRepository(connection).mark_seen(
            SeenItem(scope_id=target.scope_id, item_key="seen-key", item_kind=ItemKind.POST)
        )
        scan_id = ScanRunRepository(connection).add(
            ScanRun(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
                started_at=utc_now(),
                finished_at=utc_now(),
            )
        )
        MatchHistoryRepository(connection).add(
            MatchHistoryEntry(
                target_id=target.id,
                group_id=target.group_id,
                item_kind=ItemKind.POST,
                item_key="seen-key",
            )
        )
        LatestScanItemRepository(connection).replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=scan_id,
                    item_kind=ItemKind.POST,
                    item_key="seen-key",
                    item_index=0,
                )
            ],
        )
        NotificationEventRepository(connection).add(
            NotificationEvent(
                target_id=target.id,
                item_key="seen-key",
                channel=NotificationChannel.NTFY,
                status=NotificationStatus.SENT,
            )
        )

        result = RuntimeDataMaintenanceRepository(connection).clear_runtime_data()

        assert result.scan_runs == 1
        assert result.latest_scan_items == 1
        assert result.match_history == 1
        assert result.notification_events == 1
        assert result.seen_items == 1
        assert result.total_deleted == 5
        assert table_count(connection, "scan_runs") == 0
        assert table_count(connection, "latest_scan_items") == 0
        assert table_count(connection, "match_history") == 0
        assert table_count(connection, "notification_events") == 0
        assert table_count(connection, "seen_items") == 0
        assert TargetRepository(connection).get(target.id) is not None
        assert (
            TargetConfigRepository(connection).get_legacy_target_config_for_migration(target.id)
            is not None
        )
        assert TargetRuntimeStateRepository(connection).get(target.id) is not None
        assert GlobalNotificationSettingsRepository(connection).get().ntfy_topic == "phase0test"
