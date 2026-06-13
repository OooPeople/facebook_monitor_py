"""Application service tests。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_registry_service import InvalidTargetMetadataError
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.application.scan_recording_service import RecordScanRequest
from facebook_monitor.application.target_requests import UpdateTargetConfigRequest
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import TargetCoverImageRefreshStatus
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import ItemKind


def test_create_target_and_record_scan_through_application_context(tmp_path: Path) -> None:
    """透過 application context 建立 target/config 並記錄 scan run。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="test group",
                config=TargetConfigPatch(
                    include_keywords=("票",),
                    enable_discord_notification=True,
                    discord_webhook="https://discord.com/api/webhooks/example",
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )

        loaded_target = app.repositories.targets.get(target.id)
        loaded_config = app.repositories.configs.get_for_target(target)
        loaded_runtime_state = app.repositories.runtime_states.get(target.id)

        assert loaded_target == target
        assert loaded_config is not None
        assert loaded_config.include_keywords == ("票",)
        assert loaded_config.auto_adjust_sort
        assert loaded_config.enable_discord_notification
        assert loaded_config.discord_webhook == "https://discord.com/api/webhooks/example"
        assert loaded_config.ntfy_topic == "phase0test"
        assert loaded_runtime_state is not None
        assert loaded_target.paused
        assert loaded_runtime_state.desired_state == TargetDesiredState.STOPPED

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


def test_target_registry_exposes_upsert_without_create_api(tmp_path: Path) -> None:
    """正式 target 建立 API 只暴露 upsert，不鎖定 private helper。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        assert not hasattr(app.services.targets, "create_group_posts_target")
        assert not hasattr(app.services.targets, "create_comments_target")
        assert hasattr(app.services.targets, "upsert_group_posts_target")
        assert hasattr(app.services.targets, "upsert_comments_target")


def test_upsert_group_posts_target_reuses_existing_target(tmp_path: Path) -> None:
    """重複 capture 同一 group feed 時沿用既有 target id。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="old name",
                config=TargetConfigPatch(include_keywords=("票",)),
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="new name",
                config=TargetConfigPatch(
                    enable_discord_notification=True,
                    discord_webhook="https://discord.com/api/webhooks/example",
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
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


def test_upsert_group_posts_target_stores_group_cover_image_url(tmp_path: Path) -> None:
    """group metadata 最小實作會保存社團封面圖 URL，後續空值 upsert 不清掉。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="測試社團",
                group_cover_image_url="https://scontent.xx.fbcdn.net/group-cover.jpg",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="測試社團",
            )
        )
        loaded = app.repositories.targets.get(first.id)

    assert second.group_cover_image_url == "https://scontent.xx.fbcdn.net/group-cover.jpg"
    assert loaded is not None
    assert loaded.group_cover_image_url == "https://scontent.xx.fbcdn.net/group-cover.jpg"


def test_refresh_target_group_cover_image_does_not_overwrite_custom_name(
    tmp_path: Path,
) -> None:
    """image-only cover refresh 只更新封面 URL，不覆蓋使用者自訂名稱。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="我的自訂名稱",
                group_name="舊社團名稱",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        updated = app.services.targets.refresh_target_group_cover_image(
            target.id,
            "https://scontent.xx.fbcdn.net/new.jpg",
        )

    assert updated.name == "我的自訂名稱"
    assert updated.group_name == "舊社團名稱"
    assert updated.group_cover_image_url == "https://scontent.xx.fbcdn.net/new.jpg"


def test_refresh_target_group_metadata_rejects_error_page_metadata(
    tmp_path: Path,
) -> None:
    """metadata refresh 不可把 Facebook 錯誤頁 title 與 logo 寫入 target。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="原社團",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        with pytest.raises(InvalidTargetMetadataError):
            app.services.targets.refresh_target_group_metadata(
                target.id,
                group_name="Facebook | Error",
                group_cover_image_url=(
                    "https://static.facebook.com/images/logos/facebook_2x.png"
                ),
                overwrite_name=True,
            )
        loaded = app.repositories.targets.get(target.id)

    assert loaded is not None
    assert loaded.name == "原社團"
    assert loaded.group_name == "原社團"
    assert loaded.group_cover_image_url == "https://scontent.xx.fbcdn.net/old.jpg"


def test_refresh_target_group_cover_image_rejects_generic_facebook_logo(
    tmp_path: Path,
) -> None:
    """image-only refresh 不可把 Facebook 通用 logo 當作社團封面。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        with pytest.raises(InvalidTargetMetadataError):
            app.services.targets.refresh_target_group_cover_image(
                target.id,
                "https://static.facebook.com/images/logos/facebook_2x.png",
            )
        loaded = app.repositories.targets.get(target.id)

    assert loaded is not None
    assert loaded.group_cover_image_url == "https://scontent.xx.fbcdn.net/old.jpg"


def test_upsert_group_posts_target_ignores_error_page_metadata(
    tmp_path: Path,
) -> None:
    """capture/upsert 入口收到錯誤頁 metadata 時只丟棄，不保存污染值。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="Facebook | Error",
                group_cover_image_url=(
                    "https://static.facebook.com/images/logos/facebook_2x.png"
                ),
            )
        )

    assert target.name == "group:222518561920110:posts"
    assert target.group_name == ""
    assert target.group_cover_image_url == ""


def test_upsert_group_posts_target_recovers_existing_error_page_metadata(
    tmp_path: Path,
) -> None:
    """既有 target 已被錯誤頁污染時，下一次有效 metadata upsert 會修回。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app.repositories.targets.save(
            replace(
                target,
                name="Facebook | Error",
                group_name="Facebook | Error",
                group_cover_image_url=(
                    "https://static.facebook.com/images/logos/facebook_2x.png"
                ),
            )
        )

        updated = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="測試社團",
                group_cover_image_url="https://scontent.xx.fbcdn.net/group-cover.jpg",
            )
        )

    assert updated.name == "測試社團"
    assert updated.group_name == "測試社團"
    assert updated.group_cover_image_url == "https://scontent.xx.fbcdn.net/group-cover.jpg"


def test_upsert_group_posts_target_clears_existing_generic_logo(
    tmp_path: Path,
) -> None:
    """既有 target 的 Facebook 通用 logo 不應被後續 upsert 保留下來。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app.repositories.targets.save(
            replace(
                target,
                group_cover_image_url=(
                    "https://static.facebook.com/images/logos/facebook_2x.png"
                ),
            )
        )

        updated = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="測試社團",
            )
        )

    assert updated.name == "測試社團"
    assert updated.group_cover_image_url == ""


def test_refresh_target_group_metadata_clears_existing_generic_logo_without_new_cover(
    tmp_path: Path,
) -> None:
    """full metadata refresh 若沒有新封面，也不可保留既有 Facebook 通用 logo。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app.repositories.targets.save(
            replace(
                target,
                group_cover_image_url=(
                    "https://static.facebook.com/images/logos/facebook_2x.png"
                ),
            )
        )

        updated = app.services.targets.refresh_target_group_metadata(
            target.id,
            group_name="測試社團",
            group_cover_image_url="",
        )

    assert updated.name == "測試社團"
    assert updated.group_name == "測試社團"
    assert updated.group_cover_image_url == ""


def test_refresh_target_group_metadata_drops_existing_polluted_group_name_without_new_name(
    tmp_path: Path,
) -> None:
    """full metadata refresh 只有新封面時，也不可保留既有污染 group name。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app.repositories.targets.save(
            replace(
                target,
                group_name="Facebook | Error",
            )
        )

        updated = app.services.targets.refresh_target_group_metadata(
            target.id,
            group_name="",
            group_cover_image_url="https://scontent.xx.fbcdn.net/group-cover.jpg",
        )

    assert updated.group_name == ""
    assert updated.group_cover_image_url == "https://scontent.xx.fbcdn.net/group-cover.jpg"


def test_cover_image_load_failure_request_uses_url_scoped_throttle(
    tmp_path: Path,
) -> None:
    """同一 URL 壞圖上報會節流；目前 DB URL 已變更時忽略舊 DOM 上報。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        first = app.services.targets.request_target_cover_image_refresh(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/old.jpg",
            min_interval_seconds=21600,
        )
        second = app.services.targets.request_target_cover_image_refresh(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/old.jpg",
            min_interval_seconds=21600,
        )
        state = app.repositories.cover_image_refreshes.get(target.id)
        app.services.targets.refresh_target_group_cover_image(
            target.id,
            "https://scontent.xx.fbcdn.net/new.jpg",
        )
        stale = app.services.targets.request_target_cover_image_refresh(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/old.jpg",
            min_interval_seconds=21600,
        )

    assert first.status == "queued"
    assert first.queued
    assert second.status == "pending"
    assert not second.queued
    assert state is not None
    assert state.status == TargetCoverImageRefreshStatus.PENDING
    assert state.last_reported_url == "https://scontent.xx.fbcdn.net/old.jpg"
    assert state.last_resolved_url == ""
    assert state.last_result == "queued"
    assert state.changed is False
    assert stale.status == "ignored_stale_url"


def test_restart_target_monitoring_keeps_polluted_metadata_for_maintenance(
    tmp_path: Path,
) -> None:
    """開始監看不修 metadata；污染資料由 resident maintenance 低頻修復。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app.repositories.targets.save(
            replace(
                target,
                name="Facebook | Error",
                group_name="Facebook | Error",
                group_cover_image_url=(
                    "https://static.facebook.com/images/logos/facebook_2x.png"
                ),
            )
        )
        app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:item-1:ntfy",
                target_id=target.id,
                item_key="item-1",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )

        updated = app.services.targets.restart_target_monitoring(target.id)
        outbox_count = app.repositories.notification_outbox.connection.execute(
            "SELECT COUNT(*) FROM notification_outbox WHERE target_id = ?",
            (target.id,),
        ).fetchone()[0]

    assert updated.name == "Facebook | Error"
    assert updated.group_name == "Facebook | Error"
    assert (
        updated.group_cover_image_url
        == "https://static.facebook.com/images/logos/facebook_2x.png"
    )
    assert updated.metadata_status == TargetMetadataStatus.RESOLVED
    assert updated.metadata_error == ""
    assert updated.enabled is True
    assert updated.paused is False
    assert outbox_count == 1


def test_upsert_group_posts_target_can_clear_existing_config(tmp_path: Path) -> None:
    """upsert 明確收到 false/空值時會覆寫既有 target config。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
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
                ),
            )
        )

        app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
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
                ),
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
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"
                ),
                config=TargetConfigPatch(
                    include_keywords=("留言",),
                    exclude_keywords=("售完",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                    enable_desktop_notification=True,
                    enable_discord_notification=True,
                    discord_webhook="https://discord.com/api/webhooks/example",
                ),
            )
        )

        app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"
                ),
                config=TargetConfigPatch(
                    include_keywords=(),
                    exclude_keywords=(),
                    enable_ntfy=False,
                    ntfy_topic="",
                    enable_desktop_notification=False,
                    enable_discord_notification=False,
                    discord_webhook="",
                ),
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
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"
                ),
                name="留言監視",
                config=TargetConfigPatch(include_keywords=("票",)),
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


def test_posts_and_comments_targets_keep_independent_config(tmp_path: Path) -> None:
    """同一社團的 posts/comments target 不共用 target-scoped config。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        posts_target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    include_keywords=("票",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        comments_target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"
                ),
            )
        )

        posts_config = app.repositories.configs.get_for_target(posts_target)
        comments_config = app.repositories.configs.get_for_target(comments_target)

        assert posts_config is not None
        assert comments_config is not None
        assert posts_config.target_id == posts_target.id
        assert comments_config.target_id == comments_target.id
        assert posts_config.include_keywords == ("票",)
        assert posts_config.enable_ntfy
        assert comments_config.include_keywords == ()
        assert not comments_config.enable_ntfy

        updated = app.services.targets.update_target_config(
            UpdateTargetConfigRequest(
                target_id=comments_target.id,
                config=TargetConfigPatch(
                    include_keywords=("留言",),
                    exclude_keywords=("售完",),
                    fixed_refresh_sec=45,
                    max_items_per_scan=7,
                    auto_load_more=True,
                    auto_adjust_sort=True,
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        loaded_from_posts = app.repositories.configs.get_for_target(posts_target)
        loaded_from_comments = app.repositories.configs.get_for_target(comments_target)

    assert loaded_from_posts is not None
    assert loaded_from_posts.include_keywords == ("票",)
    assert loaded_from_posts.exclude_keywords == PYTHON_TARGET_CONFIG_DEFAULTS.exclude_keywords
    assert loaded_from_posts.ntfy_topic == "phase0test"
    assert loaded_from_comments == updated
    assert loaded_from_comments is not None
    assert loaded_from_comments.include_keywords == ("留言",)
    assert loaded_from_comments.exclude_keywords == ("售完",)
    assert loaded_from_comments.fixed_refresh_sec == 45
    assert loaded_from_comments.max_items_per_scan == 7
    assert loaded_from_comments.auto_adjust_sort


def test_same_group_comments_targets_keep_independent_config(tmp_path: Path) -> None:
    """同一社團不同貼文的 comments target 不共用設定。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="111",
                canonical_url="https://www.facebook.com/groups/222518561920110/posts/111",
                config=TargetConfigPatch(include_keywords=("第一篇",)),
            )
        )
        second = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="222",
                canonical_url="https://www.facebook.com/groups/222518561920110/posts/222",
                config=TargetConfigPatch(include_keywords=("第二篇",)),
            )
        )

        app.services.targets.update_target_config(
            UpdateTargetConfigRequest(
                target_id=second.id,
                config=TargetConfigPatch(include_keywords=("只改第二篇",)),
            )
        )
        first_config = app.repositories.configs.get_for_target(first)
        second_config = app.repositories.configs.get_for_target(second)

    assert first_config is not None
    assert second_config is not None
    assert first_config.target_id == first.id
    assert second_config.target_id == second.id
    assert first_config.include_keywords == ("第一篇",)
    assert second_config.include_keywords == ("只改第二篇",)


def test_upsert_comments_target_refreshes_generated_display_name(
    tmp_path: Path,
) -> None:
    """comments target 的自動顯示名會跟著新的 group metadata 更新。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="111",
                canonical_url="https://www.facebook.com/groups/222518561920110/posts/111",
                group_name="舊社團",
            )
        )
        second = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="111",
                canonical_url="https://www.facebook.com/groups/222518561920110/posts/111",
                group_name="新社團",
            )
        )
        loaded = app.repositories.targets.get(first.id)

    assert second.name == "新社團 / post:111"
    assert second.group_name == "新社團"
    assert loaded is not None
    assert loaded.name == "新社團 / post:111"


def test_upsert_group_posts_target_replaces_generated_name_when_group_name_resolved(
    tmp_path: Path,
) -> None:
    """既有 target 若只有系統預設名稱，補到社團名稱時會同步更新顯示名稱。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
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
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="(20+) (3) 自訂社團 | Facebook",
                group_name="（20+） 測試社團 | 臉書",
            )
        )
        loaded = app.repositories.targets.get(target.id)

        assert loaded is not None
        assert loaded.name == "自訂社團"
        assert loaded.group_name == "測試社團"


def test_update_target_name_preserves_group_metadata(tmp_path: Path) -> None:
    """更改卡片名稱只更新使用者顯示名稱，不覆蓋 Facebook group metadata。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="原本名稱",
                group_name="(20+) 測試社團 | Facebook",
            )
        )

        updated = app.services.targets.update_target_name(
            target.id,
            "(20+) 新卡片名稱 | Facebook",
        )
        loaded = app.repositories.targets.get(target.id)

        assert updated.name == "新卡片名稱"
        assert updated.group_name == "測試社團"
        assert loaded is not None
        assert loaded.name == "新卡片名稱"
        assert loaded.group_name == "測試社團"


def test_restart_target_monitoring_cleans_existing_dirty_target_name(
    tmp_path: Path,
) -> None:
    """既有髒資料在開始監視時會寫回乾淨名稱，讓後續 worker 共用。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="測試社團",
            )
        )
        app.repositories.targets.save(
            replace(
                target,
                name="(20+) 測試社團 | Facebook",
                group_name="（20+） 測試社團 | 臉書",
            )
        )

        app.services.targets.restart_target_monitoring(target.id)
        loaded = app.repositories.targets.get(target.id)

        assert loaded is not None
        assert loaded.name == "測試社團"
        assert loaded.group_name == "測試社團"
