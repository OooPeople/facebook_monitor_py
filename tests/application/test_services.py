"""Application service tests。"""

from __future__ import annotations

from dataclasses import fields
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_config_merge import TARGET_CONFIG_PATCH_FIELDS
from facebook_monitor.application.target_config_merge import build_target_config_from_patch
from facebook_monitor.application.target_config_merge import merge_target_config_patch
from facebook_monitor.application.services import TargetConfigPatch
from facebook_monitor.application.services import UpsertCommentsTargetRequest
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.application.services import RecordScanRequest
from facebook_monitor.application.services import UpdateTargetConfigRequest
from facebook_monitor.application.services import UpdateTargetStatusRequest
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import GlobalNotificationSettings
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import TargetCoverImageRefreshStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now


def test_target_runtime_state_default_is_stopped() -> None:
    """直接建立 runtime state 時，預設應符合新 target 停止語義。"""

    state = TargetRuntimeState(target_id="target-1")

    assert state.desired_state == TargetDesiredState.STOPPED


def test_page_load_timeout_failure_streak_marks_error_on_third_failure(
    tmp_path: Path,
) -> None:
    """page_load_timeout 連續失敗由 runtime state 累計，第三次才停止 target。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

        first = app.services.targets.decide_scan_failure(
            target.id,
            "page_load_timeout",
            source="playwright",
        )
        app.services.targets.apply_scan_failure_decision(target.id, first, "timeout")
        first_state = app.repositories.runtime_states.get(target.id)

        second = app.services.targets.decide_scan_failure(
            target.id,
            "page_load_timeout",
            source="playwright",
        )
        app.services.targets.apply_scan_failure_decision(target.id, second, "timeout")
        second_state = app.repositories.runtime_states.get(target.id)

        third = app.services.targets.decide_scan_failure(
            target.id,
            "page_load_timeout",
            source="playwright",
        )
        app.services.targets.apply_scan_failure_decision(target.id, third, "timeout")
        third_state = app.repositories.runtime_states.get(target.id)

    assert first_state is not None
    assert first_state.runtime_status == TargetRuntimeStatus.IDLE
    assert first_state.last_error == ""
    assert first_state.consecutive_failure_reason == "page_load_timeout"
    assert first_state.consecutive_failure_count == 1
    assert second_state is not None
    assert second_state.runtime_status == TargetRuntimeStatus.IDLE
    assert second_state.consecutive_failure_count == 2
    assert third_state is not None
    assert third_state.runtime_status == TargetRuntimeStatus.ERROR
    assert third_state.consecutive_failure_reason == "page_load_timeout"
    assert third_state.consecutive_failure_count == 3
    assert "已連續 3 次失敗" in third_state.last_error


def test_success_idle_resets_failure_streak(tmp_path: Path) -> None:
    """成功回 idle 時需清除先前可重試失敗 streak。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        decision = app.services.targets.decide_scan_failure(
            target.id,
            "page_load_timeout",
            source="playwright",
        )
        app.services.targets.apply_scan_failure_decision(target.id, decision, "timeout")
        app.services.targets.mark_target_idle(target.id)
        state = app.repositories.runtime_states.get(target.id)

    assert state is not None
    assert state.consecutive_failure_reason == ""
    assert state.consecutive_failure_count == 0


def test_target_status_update_resets_runtime_state(tmp_path: Path) -> None:
    """target 停止時 runtime reset 需清除錯誤與 retry streak。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        decision = app.services.targets.decide_scan_failure(
            target.id,
            "page_load_timeout",
            source="playwright",
        )
        app.services.targets.apply_scan_failure_decision(target.id, decision, "timeout")
        app.services.targets.pause_target_monitoring(target.id)
        state = app.repositories.runtime_states.get(target.id)

    assert state is not None
    assert state.desired_state == TargetDesiredState.STOPPED
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.scan_requested_at is None
    assert state.last_error == ""
    assert state.consecutive_failure_reason == ""
    assert state.consecutive_failure_count == 0


def test_restart_target_monitoring_resets_runtime_and_requests_scan(
    tmp_path: Path,
) -> None:
    """target 開始時需清 runtime failure 並要求下一輪立即掃描。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.services.targets.mark_target_error(
            target.id,
            "timeout",
            failure_reason="page_load_timeout",
            failure_count=3,
        )
        app.services.targets.restart_target_monitoring(target.id)
        state = app.repositories.runtime_states.get(target.id)

    assert state is not None
    assert state.desired_state == TargetDesiredState.ACTIVE
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.scan_requested_at is not None
    assert state.last_error == ""
    assert state.consecutive_failure_reason == ""
    assert state.consecutive_failure_count == 0


def test_scan_request_during_running_survives_current_scan_finish(
    tmp_path: Path,
) -> None:
    """target running 時再按 scan-once，完成目前掃描後仍保留下一輪要求。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.clear_target_scan_request(target.id)
        app.services.targets.mark_target_running(target.id, "worker-1")
        requested_state = app.services.targets.request_target_scan(target.id)
        finished_state = app.services.targets.mark_target_idle(target.id)

    assert requested_state.scan_requested_at is not None
    assert finished_state.runtime_status == TargetRuntimeStatus.IDLE
    assert finished_state.scan_requested_at == requested_state.scan_requested_at


def test_scan_request_during_queued_survives_current_scan_finish(
    tmp_path: Path,
) -> None:
    """target queued 時再按 scan-once，也應在本輪完成後保留下一輪要求。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.clear_target_scan_request(target.id)
        app.services.targets.mark_target_queued(target.id, "due")
        requested_state = app.services.targets.request_target_scan(target.id)
        app.services.targets.mark_target_running(target.id, "worker-1")
        finished_state = app.services.targets.mark_target_idle(target.id)

    assert requested_state.scan_requested_at is not None
    assert finished_state.runtime_status == TargetRuntimeStatus.IDLE
    assert finished_state.scan_requested_at == requested_state.scan_requested_at


def test_clear_consumed_scan_request_preserves_newer_request(
    tmp_path: Path,
) -> None:
    """已入隊 request 的清除動作不得刪掉稍後送出的 scan-once。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        consumed_state = app.repositories.runtime_states.get(target.id)
        assert consumed_state is not None
        assert consumed_state.scan_requested_at is not None
        newer_state = app.services.targets.request_target_scan(target.id)

        cleared_state = app.services.targets.clear_target_scan_request_if_not_newer(
            target.id,
            consumed_state.scan_requested_at,
        )

    assert newer_state.scan_requested_at is not None
    assert cleared_state.scan_requested_at == newer_state.scan_requested_at


def test_target_config_patch_fields_track_request_model() -> None:
    """config merge 欄位清單需能安全套用到 TargetConfig。"""

    assert TARGET_CONFIG_PATCH_FIELDS == tuple(field.name for field in fields(TargetConfigPatch))
    target_config_fields = {field.name for field in fields(TargetConfig)}
    assert set(TARGET_CONFIG_PATCH_FIELDS) <= target_config_fields


def test_build_target_config_from_patch_preserves_explicit_values_and_defaults() -> None:
    """建立新 target config 時，未提供欄位走正式預設，明確 false/空值要保留。"""

    config = build_target_config_from_patch(
        "target-1",
        TargetConfigPatch(
            include_keywords=(),
            exclude_keywords=(),
            enable_ntfy=False,
            ntfy_topic="",
            max_items_per_scan=30,
            auto_load_more=False,
        ),
    )

    assert config.target_id == "target-1"
    assert config.include_keywords == ()
    assert config.exclude_keywords == ()
    assert config.exclude_ignore_phrases == (
        PYTHON_TARGET_CONFIG_DEFAULTS.exclude_ignore_phrases
    )
    assert config.enable_ntfy is False
    assert config.ntfy_topic == ""
    assert config.max_items_per_scan == 10
    assert config.auto_load_more is False


def test_merge_target_config_patch_preserves_omitted_values_and_clamps() -> None:
    """更新既有 target config 時，只改 patch 欄位，並保留 max items clamp。"""

    existing = TargetConfig(
        target_id="target-1",
        include_keywords=("old",),
        exclude_keywords=("old-exclude",),
        enable_ntfy=True,
        ntfy_topic="old-topic",
        max_items_per_scan=4,
        auto_load_more=True,
    )

    merged = merge_target_config_patch(
        existing,
        TargetConfigPatch(
            include_keywords=(),
            max_items_per_scan=30,
            auto_load_more=False,
        ),
    )

    assert merged.target_id == "target-1"
    assert merged.include_keywords == ()
    assert merged.exclude_keywords == ("old-exclude",)
    assert merged.enable_ntfy is True
    assert merged.ntfy_topic == "old-topic"
    assert merged.max_items_per_scan == 10
    assert merged.auto_load_more is False


def test_target_facade_exposes_display_next_due_update(tmp_path: Path) -> None:
    """resident main 走 targets facade 寫入 display-only next due。"""

    db_path = tmp_path / "app.db"
    due_at = utc_now() + timedelta(seconds=60)
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

        updated_state = app.services.targets.set_target_display_next_due_at(
            target.id,
            due_at,
        )
        loaded_state = app.repositories.runtime_states.get(target.id)

        assert updated_state is not None
        assert loaded_state is not None
        assert loaded_state.display_next_due_at == due_at


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
                group_cover_image_url="https://scontent.example.test/group-cover.jpg",
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

    assert second.group_cover_image_url == "https://scontent.example.test/group-cover.jpg"
    assert loaded is not None
    assert loaded.group_cover_image_url == "https://scontent.example.test/group-cover.jpg"


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
                group_cover_image_url="https://scontent.example.test/old.jpg",
            )
        )
        updated = app.services.targets.refresh_target_group_cover_image(
            target.id,
            "https://scontent.example.test/new.jpg",
        )

    assert updated.name == "我的自訂名稱"
    assert updated.group_name == "舊社團名稱"
    assert updated.group_cover_image_url == "https://scontent.example.test/new.jpg"


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
                group_cover_image_url="https://scontent.example.test/old.jpg",
            )
        )
        first = app.services.targets.request_target_cover_image_refresh(
            target.id,
            reported_url="https://scontent.example.test/old.jpg",
            min_interval_seconds=21600,
        )
        second = app.services.targets.request_target_cover_image_refresh(
            target.id,
            reported_url="https://scontent.example.test/old.jpg",
            min_interval_seconds=21600,
        )
        state = app.repositories.cover_image_refreshes.get(target.id)
        app.services.targets.refresh_target_group_cover_image(
            target.id,
            "https://scontent.example.test/new.jpg",
        )
        stale = app.services.targets.request_target_cover_image_refresh(
            target.id,
            reported_url="https://scontent.example.test/old.jpg",
            min_interval_seconds=21600,
        )

    assert first.status == "queued"
    assert first.queued
    assert second.status == "pending"
    assert not second.queued
    assert state is not None
    assert state.status == TargetCoverImageRefreshStatus.PENDING
    assert state.last_reported_url == "https://scontent.example.test/old.jpg"
    assert state.last_resolved_url == ""
    assert state.last_result == "queued"
    assert state.changed is False
    assert stale.status == "ignored_stale_url"


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
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
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
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
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
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
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
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
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
                name="(2) (3) 自訂社團 | Facebook",
                group_name="(3) 測試社團 | Facebook",
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
                group_name="測試社團",
            )
        )

        updated = app.services.targets.update_target_name(
            target.id,
            "(1) 新卡片名稱 | Facebook",
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
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        initial_config = app.repositories.configs.get_for_target(target)

        assert initial_config is not None
        assert (
            initial_config.exclude_ignore_phrases
            == PYTHON_TARGET_CONFIG_DEFAULTS.exclude_ignore_phrases
        )

        config = app.services.targets.update_target_config(
            UpdateTargetConfigRequest(
                target_id=target.id,
                config=TargetConfigPatch(
                    include_keywords=("票", "交換"),
                    exclude_keywords=("售完",),
                    exclude_ignore_phrases=("全收;回收",),
                    fixed_refresh_sec=90,
                    max_items_per_scan=30,
                    auto_load_more=False,
                    auto_adjust_sort=True,
                    enable_desktop_notification=True,
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                    enable_discord_notification=True,
                    discord_webhook="https://discord.com/api/webhooks/example",
                ),
            )
        )
        loaded_config = app.repositories.configs.get_for_target(target)

        assert loaded_config == config
        assert config.include_keywords == ("票", "交換")
        assert config.exclude_keywords == ("售完",)
        assert config.exclude_ignore_phrases == ("全收;回收",)
        assert config.fixed_refresh_sec == 90
        assert config.max_items_per_scan == 10
        assert not config.auto_load_more
        assert config.auto_adjust_sort
        assert config.enable_desktop_notification
        assert config.enable_ntfy
        assert config.ntfy_topic == "phase0test"
        assert config.enable_discord_notification
        assert config.discord_webhook == "https://discord.com/api/webhooks/example"


def test_update_target_config_preserves_omitted_notification_channels(
    tmp_path: Path,
) -> None:
    """patch 未提供的通知欄位，更新一般設定時會保留原值。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    enable_desktop_notification=True,
                    enable_discord_notification=True,
                    discord_webhook="https://discord.com/api/webhooks/example",
                ),
            )
        )

        config = app.services.targets.update_target_config(
            UpdateTargetConfigRequest(
                target_id=target.id,
                config=TargetConfigPatch(
                    include_keywords=("票",),
                    exclude_keywords=(),
                    fixed_refresh_sec=60,
                    max_items_per_scan=5,
                    auto_load_more=True,
                    auto_adjust_sort=False,
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )

        assert config.enable_desktop_notification
        assert config.enable_discord_notification
        assert config.discord_webhook == "https://discord.com/api/webhooks/example"


def test_apply_global_notification_settings_updates_each_target_config(tmp_path: Path) -> None:
    """同 group 多個 target 套用通知預設值時不得被 group_id 去重跳過。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        posts_target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        first_comments_target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="111",
                canonical_url="https://www.facebook.com/groups/222518561920110/posts/111",
            )
        )
        second_comments_target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="222",
                canonical_url="https://www.facebook.com/groups/222518561920110/posts/222",
            )
        )

        count = app.services.targets.apply_global_notification_settings(
            GlobalNotificationSettings(
                enable_desktop_notification=True,
                enable_ntfy=True,
                ntfy_topic="global-topic",
                enable_discord_notification=True,
                discord_webhook="https://discord.com/api/webhooks/global",
            )
        )
        configs = [
            app.repositories.configs.get_for_target(target)
            for target in (posts_target, first_comments_target, second_comments_target)
        ]

    assert count == 3
    for config in configs:
        assert config is not None
        assert config.enable_desktop_notification
        assert config.enable_ntfy
        assert config.ntfy_topic == "global-topic"
        assert config.enable_discord_notification
        assert config.discord_webhook == "https://discord.com/api/webhooks/global"


def test_start_and_stop_target_do_not_touch_other_targets(tmp_path: Path) -> None:
    """單一 target 開始/停止不會影響其他 target。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app.services.targets.restart_target_monitoring(second.id)

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


def test_restart_monitoring_clears_only_target_runtime_dedupe_state(tmp_path: Path) -> None:
    """開始監視會清該 target seen/outbox 去重狀態，不影響其他 target。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
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
        app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{first.id}:first-item:ntfy",
                target_id=first.id,
                item_key="first-item",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )
        app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{second.id}:second-item:ntfy",
                target_id=second.id,
                item_key="second-item",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )

        app.services.targets.restart_target_monitoring(first.id)

        assert not app.repositories.seen_items.has_seen(first.scope_id, "first-item")
        assert app.repositories.seen_items.has_seen(second.scope_id, "second-item")
        assert (
            app.repositories.notification_outbox.get_by_idempotency_key(
                f"{first.id}:first-item:ntfy"
            )
            is None
        )
        assert (
            app.repositories.notification_outbox.get_by_idempotency_key(
                f"{second.id}:second-item:ntfy"
            )
            is not None
        )


def test_restart_comments_monitoring_clears_comments_seen_scope(tmp_path: Path) -> None:
    """comments target 開始監視時也會清自己的 seen scope 並要求立即掃描。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="111",
                parent_post_id="999",
                canonical_url="https://www.facebook.com/groups/111/posts/999",
            )
        )
        other = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
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
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.repositories.seen_items.mark_seen(
            SeenItem(scope_id=target.scope_id, item_key="item-1", item_kind=ItemKind.POST)
        )

        app.services.targets.pause_target_monitoring(target.id)

        assert app.repositories.seen_items.has_seen(target.scope_id, "item-1")


def test_webui_startup_pause_all_targets_without_overwriting_floating_interval(
    tmp_path: Path,
) -> None:
    """Web UI 啟動整理會停止所有 target，但不把浮動刷新改回固定。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                config=TargetConfigPatch(
                    fixed_refresh_sec=None,
                    min_refresh_sec=25,
                    max_refresh_sec=35,
                    jitter_enabled=True,
                ),
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
    assert config.fixed_refresh_sec is None
    assert config.jitter_enabled
    assert config.min_refresh_sec == 25
    assert config.max_refresh_sec == 35


def test_update_target_status_request(tmp_path: Path) -> None:
    """可透過明確 request 更新 target enabled/paused 狀態。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
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
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        fresh_target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
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
    assert "掃描狀態逾時" in loaded_stale.last_error
    assert loaded_fresh.runtime_status == TargetRuntimeStatus.RUNNING


def test_recover_stale_queued_targets_returns_to_idle_for_retry(tmp_path: Path) -> None:
    """排隊過久的 target 會回到 idle 並保留立即掃描請求。"""

    db_path = tmp_path / "app.db"
    now = utc_now()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.request_target_scan(target.id)
        queued_state = app.services.targets.mark_target_queued(target.id, "manual_request")
        app.repositories.runtime_states.save(
            replace(
                queued_state,
                last_enqueued_at=now - timedelta(seconds=240),
                updated_at=now - timedelta(seconds=240),
            )
        )

        recovered = app.services.targets.recover_stale_queued_targets(
            stale_after_seconds=180,
            now=now,
        )
        loaded = app.repositories.runtime_states.get(target.id)

    assert len(recovered) == 1
    assert loaded is not None
    assert loaded.runtime_status == TargetRuntimeStatus.IDLE
    assert loaded.scan_requested_at is not None
    assert "監視項目排隊等待過久" in loaded.last_skip_reason


def test_delete_target_does_not_touch_other_targets(tmp_path: Path) -> None:
    """刪除單一 target 不會影響其他 target 與其狀態。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app.services.targets.pause_target_monitoring(second.id)

        app.services.targets.delete_target(first.id)

        assert app.repositories.targets.get(first.id) is None
        loaded_second = app.repositories.targets.get(second.id)
        assert loaded_second is not None
        assert loaded_second.enabled
        assert loaded_second.paused

