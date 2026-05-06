"""Phase A application service tests。"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import CreateCommentsTargetRequest
from facebook_monitor.application.services import CreateGroupPostsTargetRequest
from facebook_monitor.application.services import DEFAULT_WEBUI_FIXED_REFRESH_SECONDS
from facebook_monitor.application.services import RecordScanRequest
from facebook_monitor.application.services import UpdateTargetConfigRequest
from facebook_monitor.application.services import UpdateTargetStatusRequest
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now


def test_create_target_and_record_scan_through_application_context(tmp_path: Path) -> None:
    """透過 application context 建立 target/config 並記錄 scan run。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="test group",
                include_keywords=("票",),
                enable_discord_notification=True,
                discord_webhook="https://discord.com/api/webhooks/example",
                enable_ntfy=True,
                ntfy_topic="phase0test",
            )
        )

        loaded_target = app.repositories.targets.get(target.id)
        loaded_config = app.repositories.configs.get_for_target(target)
        loaded_runtime_state = app.repositories.runtime_states.get(target.id)

        assert loaded_target == target
        assert loaded_config is not None
        assert loaded_config.include_keywords == ("票",)
        assert loaded_config.enable_discord_notification
        assert loaded_config.discord_webhook == "https://discord.com/api/webhooks/example"
        assert loaded_config.ntfy_topic == "phase0test"
        assert loaded_runtime_state is not None
        assert loaded_runtime_state.desired_state == TargetDesiredState.ACTIVE

        scan_id = app.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
                item_count=11,
                matched_count=1,
                metadata={"scroll_rounds": 5},
            )
        )
        assert scan_id > 0


def test_target_create_helpers_are_internal_only(tmp_path: Path) -> None:
    """正式 target 建立 API 只暴露 upsert，避免 create/upsert 雙軌。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        assert not hasattr(app.services.targets, "create_group_posts_target")
        assert not hasattr(app.services.targets, "create_comments_target")
        assert hasattr(app.services.targets, "_create_group_posts_target")
        assert hasattr(app.services.targets, "_create_comments_target")


def test_upsert_group_posts_target_reuses_existing_target(tmp_path: Path) -> None:
    """重複 capture 同一 group feed 時沿用既有 target id。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="old name",
                include_keywords=("票",),
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="new name",
                enable_discord_notification=True,
                discord_webhook="https://discord.com/api/webhooks/example",
                enable_ntfy=True,
                ntfy_topic="phase0test",
            )
        )

        config = app.repositories.configs.get_for_target(first)

        assert second.id == first.id
        assert second.group_name == "new name"
        assert config is not None
        assert config.include_keywords == ("票",)
        assert config.enable_discord_notification
        assert config.discord_webhook == "https://discord.com/api/webhooks/example"
        assert config.enable_ntfy
        assert config.ntfy_topic == "phase0test"


def test_upsert_group_posts_target_can_clear_existing_config(tmp_path: Path) -> None:
    """upsert 明確收到 false/空值時會覆寫既有 group config。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                include_keywords=("票",),
                exclude_keywords=("售完",),
                fixed_refresh_sec=90,
                max_items_per_scan=10,
                auto_load_more=True,
                auto_adjust_sort=True,
                enable_desktop_notification=True,
                enable_ntfy=True,
                ntfy_topic="phase0test",
                enable_discord_notification=True,
                discord_webhook="https://discord.com/api/webhooks/example",
            )
        )

        app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                include_keywords=(),
                exclude_keywords=(),
                fixed_refresh_sec=None,
                max_items_per_scan=3,
                auto_load_more=False,
                auto_adjust_sort=False,
                enable_desktop_notification=False,
                enable_ntfy=False,
                ntfy_topic="",
                enable_discord_notification=False,
                discord_webhook="",
            )
        )
        config = app.repositories.configs.get_for_target(target)

    assert config is not None
    assert config.include_keywords == ()
    assert config.exclude_keywords == ()
    assert config.fixed_refresh_sec is None
    assert config.max_items_per_scan == 3
    assert not config.auto_load_more
    assert not config.auto_adjust_sort
    assert not config.enable_desktop_notification
    assert not config.enable_ntfy
    assert config.ntfy_topic == ""
    assert not config.enable_discord_notification
    assert config.discord_webhook == ""


def test_upsert_comments_target_can_clear_existing_group_config(tmp_path: Path) -> None:
    """comments upsert 也必須支援明確關閉通知與清空 keyword。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            CreateCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
                include_keywords=("留言",),
                exclude_keywords=("售完",),
                enable_ntfy=True,
                ntfy_topic="phase0test",
                enable_desktop_notification=True,
                enable_discord_notification=True,
                discord_webhook="https://discord.com/api/webhooks/example",
            )
        )

        app.services.targets.upsert_comments_target(
            CreateCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
                include_keywords=(),
                exclude_keywords=(),
                enable_ntfy=False,
                ntfy_topic="",
                enable_desktop_notification=False,
                enable_discord_notification=False,
                discord_webhook="",
            )
        )
        config = app.repositories.configs.get_for_target(target)

    assert config is not None
    assert config.include_keywords == ()
    assert config.exclude_keywords == ()
    assert not config.enable_ntfy
    assert config.ntfy_topic == ""
    assert not config.enable_desktop_notification
    assert not config.enable_discord_notification
    assert config.discord_webhook == ""


def test_upsert_comments_target_sets_parent_post_and_scope(tmp_path: Path) -> None:
    """comments target 會保存 group_id / parent_post_id / target-scoped scope_id。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            CreateCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
                name="留言監視",
                include_keywords=("票",),
            )
        )
        loaded = app.repositories.targets.find_by_kind_scope(
            TargetKind.COMMENTS,
            "222518561920110:post:2187454285426518:comments",
        )
        config = app.repositories.configs.get_for_target(target)
        state = app.repositories.runtime_states.get(target.id)

    assert loaded is not None
    assert loaded.id == target.id
    assert loaded.target_kind == TargetKind.COMMENTS
    assert loaded.group_id == "222518561920110"
    assert loaded.parent_post_id == "2187454285426518"
    assert loaded.scope_id == "222518561920110:post:2187454285426518:comments"
    assert loaded.paused
    assert config is not None
    assert config.include_keywords == ("票",)
    assert state is not None


def test_posts_and_comments_targets_share_group_scoped_config(tmp_path: Path) -> None:
    """同一社團的 posts/comments target 共用 group-scoped config。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        posts_target = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                include_keywords=("票",),
                enable_ntfy=True,
                ntfy_topic="phase0test",
            )
        )
        comments_target = app.services.targets.upsert_comments_target(
            CreateCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
            )
        )

        posts_config = app.repositories.configs.get_for_target(posts_target)
        comments_config = app.repositories.configs.get_for_target(comments_target)

        assert posts_config is not None
        assert comments_config is not None
        assert posts_config == comments_config
        assert posts_config.target_id == "222518561920110"
        assert comments_config.include_keywords == ("票",)
        assert comments_config.enable_ntfy

        updated = app.services.targets.update_target_config(
            UpdateTargetConfigRequest(
                target_id=comments_target.id,
                include_keywords=("留言",),
                exclude_keywords=("售完",),
                fixed_refresh_sec=45,
                max_items_per_scan=7,
                auto_load_more=True,
                auto_adjust_sort=True,
                enable_ntfy=True,
                ntfy_topic="phase0test",
            )
        )
        loaded_from_posts = app.repositories.configs.get_for_target(posts_target)

    assert loaded_from_posts == updated
    assert loaded_from_posts is not None
    assert loaded_from_posts.include_keywords == ("留言",)
    assert loaded_from_posts.exclude_keywords == ("售完",)
    assert loaded_from_posts.fixed_refresh_sec == 45
    assert loaded_from_posts.max_items_per_scan == 7
    assert loaded_from_posts.auto_adjust_sort


def test_upsert_group_posts_target_replaces_generated_name_when_group_name_resolved(
    tmp_path: Path,
) -> None:
    """既有 target 若只有系統預設名稱，補到社團名稱時會同步更新顯示名稱。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="測試社團",
            )
        )

        assert second.id == first.id
        assert second.name == "測試社團"
        assert second.group_name == "測試社團"


def test_group_posts_target_names_are_cleaned_before_persistence(
    tmp_path: Path,
) -> None:
    """社團名稱在保存 target 階段清理，避免通知數前綴流到通知或歷史。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="(2) (3) 自訂社團 | Facebook",
                group_name="(3) 測試社團 | Facebook",
            )
        )
        loaded = app.repositories.targets.get(target.id)

        assert loaded is not None
        assert loaded.name == "自訂社團"
        assert loaded.group_name == "測試社團"


def test_restart_target_monitoring_cleans_existing_dirty_target_name(
    tmp_path: Path,
) -> None:
    """既有髒資料在開始監視時會寫回乾淨名稱，讓後續 worker 共用。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="測試社團",
            )
        )
        app.repositories.targets.save(
            replace(
                target,
                name="(2) 測試社團 | Facebook",
                group_name="(3) 測試社團 | Facebook",
            )
        )

        app.services.targets.restart_target_monitoring(target.id)
        loaded = app.repositories.targets.get(target.id)

        assert loaded is not None
        assert loaded.name == "測試社團"
        assert loaded.group_name == "測試社團"


def test_update_target_config(tmp_path: Path) -> None:
    """application service 可更新 target config，供設定入口共用。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

        config = app.services.targets.update_target_config(
            UpdateTargetConfigRequest(
                target_id=target.id,
                include_keywords=("票", "交換"),
                exclude_keywords=("售完",),
                fixed_refresh_sec=90,
                max_items_per_scan=30,
                auto_load_more=False,
                auto_adjust_sort=True,
                enable_desktop_notification=True,
                enable_ntfy=True,
                ntfy_topic="phase0test",
                enable_discord_notification=True,
                discord_webhook="https://discord.com/api/webhooks/example",
            )
        )
        loaded_config = app.repositories.configs.get_for_target(target)

        assert loaded_config == config
        assert config.include_keywords == ("票", "交換")
        assert config.exclude_keywords == ("售完",)
        assert config.fixed_refresh_sec == 90
        assert config.max_items_per_scan == 10
        assert not config.auto_load_more
        assert config.auto_adjust_sort
        assert config.enable_desktop_notification
        assert config.enable_ntfy
        assert config.ntfy_topic == "phase0test"
        assert config.enable_discord_notification
        assert config.discord_webhook == "https://discord.com/api/webhooks/example"


def test_update_target_config_preserves_reserved_notification_channels(
    tmp_path: Path,
) -> None:
    """未顯示在目前 UI 的通知欄位，更新一般設定時會保留。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                enable_desktop_notification=True,
                enable_discord_notification=True,
                discord_webhook="https://discord.com/api/webhooks/example",
            )
        )

        config = app.services.targets.update_target_config(
            UpdateTargetConfigRequest(
                target_id=target.id,
                include_keywords=("票",),
                exclude_keywords=(),
                fixed_refresh_sec=60,
                max_items_per_scan=5,
                auto_load_more=True,
                auto_adjust_sort=False,
                enable_ntfy=True,
                ntfy_topic="phase0test",
            )
        )

        assert config.enable_desktop_notification
        assert config.enable_discord_notification
        assert config.discord_webhook == "https://discord.com/api/webhooks/example"


def test_start_and_stop_target_do_not_touch_other_targets(tmp_path: Path) -> None:
    """單一 target 開始/停止不會影響其他 target。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )

        stopped_first = app.services.targets.pause_target_monitoring(first.id)
        loaded_second = app.repositories.targets.get(second.id)
        first_runtime_state = app.repositories.runtime_states.get(first.id)
        second_runtime_state = app.repositories.runtime_states.get(second.id)

        assert stopped_first.enabled
        assert stopped_first.paused
        assert first_runtime_state is not None
        assert first_runtime_state.desired_state == TargetDesiredState.STOPPED
        assert loaded_second is not None
        assert loaded_second.enabled
        assert not loaded_second.paused
        assert second_runtime_state is not None
        assert second_runtime_state.desired_state == TargetDesiredState.ACTIVE

        started_first = app.services.targets.restart_target_monitoring(first.id)
        first_runtime_state = app.repositories.runtime_states.get(first.id)

        assert started_first.enabled
        assert not started_first.paused
        assert first_runtime_state is not None
        assert first_runtime_state.desired_state == TargetDesiredState.ACTIVE
        assert first_runtime_state.scan_requested_at is not None


def test_restart_monitoring_clears_only_target_seen_scope(tmp_path: Path) -> None:
    """開始監視會清該 target seen scope，對齊 userscript restart 語義。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app.repositories.seen_items.mark_seen(
            SeenItem(scope_id=first.scope_id, item_key="first-item", item_kind=ItemKind.POST)
        )
        app.repositories.seen_items.mark_seen(
            SeenItem(scope_id=second.scope_id, item_key="second-item", item_kind=ItemKind.POST)
        )

        app.services.targets.restart_target_monitoring(first.id)

        assert not app.repositories.seen_items.has_seen(first.scope_id, "first-item")
        assert app.repositories.seen_items.has_seen(second.scope_id, "second-item")


def test_restart_comments_monitoring_clears_comments_seen_scope(tmp_path: Path) -> None:
    """comments target 開始監視時也會清自己的 seen scope 並要求立即掃描。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            CreateCommentsTargetRequest(
                group_id="111",
                parent_post_id="999",
                canonical_url="https://www.facebook.com/groups/111/posts/999",
            )
        )
        other = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="comment-before-start",
                item_kind=ItemKind.COMMENT,
            )
        )
        app.repositories.seen_items.mark_seen(
            SeenItem(scope_id=other.scope_id, item_key="post-before-start", item_kind=ItemKind.POST)
        )

        started = app.services.targets.restart_target_monitoring(target.id)
        state = app.repositories.runtime_states.get(target.id)

        assert started.enabled
        assert not started.paused
        assert state is not None
        assert state.desired_state == TargetDesiredState.ACTIVE
        assert state.scan_requested_at is not None
        assert not app.repositories.seen_items.has_seen(target.scope_id, "comment-before-start")
        assert app.repositories.seen_items.has_seen(other.scope_id, "post-before-start")


def test_pause_monitoring_preserves_seen_scope(tmp_path: Path) -> None:
    """停止監視只停排程，不清 seen scope。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.repositories.seen_items.mark_seen(
            SeenItem(scope_id=target.scope_id, item_key="item-1", item_kind=ItemKind.POST)
        )

        app.services.targets.pause_target_monitoring(target.id)

        assert app.repositories.seen_items.has_seen(target.scope_id, "item-1")


def test_webui_startup_pause_all_targets_and_normalizes_fixed_interval(
    tmp_path: Path,
) -> None:
    """Web UI 啟動整理會停止所有 target 並補固定掃描間隔。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                fixed_refresh_sec=None,
            )
        )

        app.services.targets.pause_all_targets_for_webui_startup()

        loaded = app.repositories.targets.get(target.id)
        state = app.repositories.runtime_states.get(target.id)
        config = app.repositories.configs.get_for_target(target)

    assert loaded is not None
    assert loaded.paused
    assert state is not None
    assert state.desired_state == TargetDesiredState.STOPPED
    assert config is not None
    assert config.fixed_refresh_sec == DEFAULT_WEBUI_FIXED_REFRESH_SECONDS


def test_update_target_status_request(tmp_path: Path) -> None:
    """可透過明確 request 更新 target enabled/paused 狀態。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )

        updated = app.services.targets.update_target_status(
            UpdateTargetStatusRequest(
                target_id=target.id,
                enabled=False,
                paused=True,
            )
        )

        assert not updated.enabled
        assert updated.paused


def test_recover_stale_running_targets_marks_old_heartbeat_as_error(tmp_path: Path) -> None:
    """application service 會修復過舊 running state，避免 target 永久卡住。"""

    db_path = tmp_path / "app.db"
    now = utc_now()
    with SqliteApplicationContext(db_path) as app:
        stale_target = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        fresh_target = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        stale_state = app.services.targets.mark_target_running(stale_target.id, "old-worker")
        fresh_state = app.services.targets.mark_target_running(fresh_target.id, "new-worker")
        app.repositories.runtime_states.save(
            replace(
                stale_state,
                last_heartbeat_at=now - timedelta(seconds=240),
                updated_at=now - timedelta(seconds=240),
            )
        )
        app.repositories.runtime_states.save(
            replace(
                fresh_state,
                last_heartbeat_at=now - timedelta(seconds=30),
                updated_at=now - timedelta(seconds=30),
            )
        )

        recovered = app.services.targets.recover_stale_running_targets(
            stale_after_seconds=180,
            now=now,
        )
        loaded_stale = app.repositories.runtime_states.get(stale_target.id)
        loaded_fresh = app.repositories.runtime_states.get(fresh_target.id)

    assert len(recovered) == 1
    assert loaded_stale is not None
    assert loaded_fresh is not None
    assert loaded_stale.runtime_status == TargetRuntimeStatus.ERROR
    assert loaded_stale.active_worker_id == ""
    assert "stale_running" in loaded_stale.last_error
    assert loaded_fresh.runtime_status == TargetRuntimeStatus.RUNNING


def test_delete_target_does_not_touch_other_targets(tmp_path: Path) -> None:
    """刪除單一 target 不會影響其他 target 與其狀態。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app.services.targets.stop_target(second.id)

        app.services.targets.delete_target(first.id)

        assert app.repositories.targets.get(first.id) is None
        loaded_second = app.repositories.targets.get(second.id)
        assert loaded_second is not None
        assert loaded_second.enabled
        assert loaded_second.paused

