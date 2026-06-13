"""Shared scan finalize tests。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.keyword_groups import keyword_group_slots
from facebook_monitor.facebook.extracted_item import ExtractedItem
from facebook_monitor.notifications.desktop import DesktopNotificationResult
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.discord import DiscordResult
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult
from facebook_monitor.worker import scan_failure_finalize as scan_failure_finalize_module
from facebook_monitor.worker.scan_finalize import NormalizedScanItem
from facebook_monitor.worker.scan_finalize import normalize_extracted_scan_items
from facebook_monitor.worker.scan_failure_finalize import record_scan_failure
from facebook_monitor.worker.scan_failure_finalize import record_guarded_scan_failure
from facebook_monitor.worker.errors import WorkerFailure

from tests.worker.scan_finalize_test_helpers import finalize_scan_items
from tests.worker.scan_finalize_test_helpers import record_skipped_scan
from tests.worker.scan_finalize_test_helpers import _activate_target
from tests.worker.scan_finalize_test_helpers import _create_running_target_with_guard


def test_normalize_extracted_scan_items_preserves_display_text() -> None:
    """extractor 顯示文字需傳到 shared finalize 中間模型。"""

    target = TargetDescriptor.for_group_posts(
        group_id="123",
        canonical_url="https://www.facebook.com/groups/123",
    )
    items = normalize_extracted_scan_items(
        items=[
            ExtractedItem(
                text="第一行票券 第二行座位",
                text_length=11,
                permalink="https://www.facebook.com/groups/123/posts/1",
                link_count=1,
                display_text="第一行票券\n第二行座位",
            )
        ],
        item_kind=ItemKind.POST,
        target=target,
    )

    assert len(items) == 1
    assert items[0].text == "第一行票券 第二行座位"
    assert items[0].display_text == "第一行票券\n第二行座位"


def test_finalize_scan_items_records_shared_postprocess_state(tmp_path: Path) -> None:
    """shared finalize 會集中寫入 seen/history/latest scan 與通知事件。"""

    sent_ntfy: list[tuple[NtfyConfig, str, str]] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        """記錄 ntfy payload，避免測試送出真實通知。"""

        sent_ntfy.append((config, title, message))
        return NtfyResult(ok=True, status_code=200, message="ntfy_sent")

    def fake_desktop_sender(title: str, message: str) -> DesktopNotificationResult:
        """記錄桌面通知成功結果。"""

        return DesktopNotificationResult(ok=True, status_code=None, message="desktop_sent")

    def fake_discord_sender(
        config: DiscordConfig,
        title: str,
        message: str,
    ) -> DiscordResult:
        """記錄 Discord 通知成功結果。"""

        return DiscordResult(ok=True, status_code=204, message="discord_sent")

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="測試社團",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                    enable_desktop_notification=True,
                    enable_discord_notification=True,
                    discord_webhook="https://discord.example/webhook",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)

        result = finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:1",
                    alias_keys=("post:1", "legacy:1"),
                    group_id="123",
                    author="作者",
                    text="這是一篇票券貼文",
                    permalink="https://www.facebook.com/groups/123/posts/1",
                    raw_target_kind="posts",
                    metadata={"source": "test"},
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
            desktop_notification_sender=fake_desktop_sender,
            discord_notification_sender=fake_discord_sender,
        )

        assert result.new_count == 1
        assert result.matched_count == 1
        assert result.scan_run_id > 0
        assert result.latest_items[0].matched_keyword == "票券"
        assert result.latest_items[0].debug_metadata["source"] == "test"
        assert result.latest_items[0].debug_metadata["classification"] == {
            "is_new": True,
            "is_matched": True,
            "include_rule": "票券",
            "include_rules": ["票券"],
            "include_group_results": [
                {
                    "group_id": "1",
                    "group_label": "關鍵字 1",
                    "matched": True,
                    "rules": ["票券"],
                }
            ],
            "exclude_rule": "",
            "eligible_for_notify": True,
            "baseline_mode": False,
        }
        history = app.repositories.match_history.list_by_target(target.id)
        assert len(history) == 1
        assert history[0].include_rule == "票券"
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)
        assert len(latest_items) == 1
        outbox_entries = app.repositories.notification_outbox.list_pending()
        assert {entry.channel for entry in outbox_entries} == {
            NotificationChannel.DESKTOP,
            NotificationChannel.NTFY,
            NotificationChannel.DISCORD,
        }
        assert app.repositories.notification_events.list_by_target(target.id) == []

        second_result = finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:1",
                    alias_keys=("post:1", "legacy:1"),
                    group_id="123",
                    author="作者",
                    text="這是一篇票券貼文",
                    permalink="https://www.facebook.com/groups/123/posts/1",
                    raw_target_kind="posts",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
            desktop_notification_sender=fake_desktop_sender,
            discord_notification_sender=fake_discord_sender,
        )

        assert second_result.new_count == 0
        assert second_result.matched_count == 1
        assert len(app.repositories.match_history.list_by_target(target.id)) == 1

    assert sent_ntfy
    with SqliteApplicationContext(db_path) as app:
        events = app.repositories.notification_events.list_by_target(target.id)
        assert {event.channel for event in events} == {
            NotificationChannel.DESKTOP,
            NotificationChannel.NTFY,
            NotificationChannel.DISCORD,
        }
        assert all(event.status == NotificationStatus.SENT for event in events)
        assert app.repositories.notification_outbox.list_pending() == []


def test_finalize_scan_items_sanitizes_polluted_match_history_group_name(
    tmp_path: Path,
) -> None:
    """match history 不應保存 Facebook 錯誤頁名稱。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="Facebook | Error",
                config=TargetConfigPatch(include_keywords=("票券",)),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)

        finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:polluted-name",
                    alias_keys=("post:polluted-name",),
                    group_id="123",
                    text="這是一篇票券貼文",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
        )
        history = app.repositories.match_history.list_by_target(target.id)

    assert len(history) == 1
    assert history[0].group_name == "group:123:posts"


def test_finalize_scan_items_persists_display_text_for_visible_results(
    tmp_path: Path,
) -> None:
    """通知與可見掃描結果使用 display text，keyword 比對仍維持 text 語義。"""

    sent_ntfy: list[tuple[NtfyConfig, str, str]] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        """記錄 ntfy payload，避免測試送出真實通知。"""

        sent_ntfy.append((config, title, message))
        return NtfyResult(ok=True, status_code=200, message="ntfy_sent")

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="測試社團",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)

        result = finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:display-text",
                    alias_keys=("post:display-text",),
                    group_id="123",
                    author="作者",
                    text="第一行票券 第二行座位",
                    display_text="第一行票券\n第二行座位",
                    permalink="https://www.facebook.com/groups/123/posts/1",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
        )

        history = app.repositories.match_history.list_by_target(target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)

    assert result.notification_payloads[0].text == "第一行票券\n第二行座位"
    assert result.history_entries[0].text == "第一行票券 第二行座位"
    assert result.history_entries[0].display_text == "第一行票券\n第二行座位"
    assert result.latest_items[0].display_text == "第一行票券\n第二行座位"
    assert history[0].text == "第一行票券 第二行座位"
    assert history[0].display_text == "第一行票券\n第二行座位"
    assert latest_items[0].text == "第一行票券 第二行座位"
    assert latest_items[0].display_text == "第一行票券\n第二行座位"
    assert sent_ntfy
    assert (
        "命中：票券\n"
        "---------------------------------------------\n"
        "第一行票券\n第二行座位"
    ) in sent_ntfy[0][2]


def test_finalize_scan_items_keyword_ignores_display_only_text(tmp_path: Path) -> None:
    """display text 不可擴大 keyword 比對範圍，避免通知語義漂移。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="測試社團",
                config=TargetConfigPatch(include_keywords=("只在顯示文字",)),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)

        result = finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:display-only",
                    alias_keys=("post:display-only",),
                    group_id="123",
                    text="沒有命中",
                    display_text="只在顯示文字\n沒有命中",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
        )

    assert result.matched_count == 0
    assert result.notification_payloads == ()


def test_finalize_scan_items_uses_renamed_target_display_name_for_notifications(
    tmp_path: Path,
) -> None:
    """手動改名後的新通知使用使用者顯示名稱，不回退到舊 group metadata。"""

    sent_ntfy: list[tuple[NtfyConfig, str, str]] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        """記錄 ntfy payload，避免測試送出真實通知。"""

        sent_ntfy.append((config, title, message))
        return NtfyResult(ok=True, status_code=200, message="ntfy_sent")

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="測試社團",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        target = _activate_target(app, target)
        app.repositories.targets.save(
            replace(
                target,
                group_name="(20+) 測試社團 | Facebook",
            )
        )
        app.services.targets.update_target_name(
            target.id,
            "(20+) 我的票券社團 | Facebook",
        )
        reloaded_target = app.repositories.targets.get(target.id)
        assert reloaded_target is not None
        config = app.services.targets.get_config_for_target(reloaded_target)

        finalize_scan_items(
            app=app,
            target=reloaded_target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:1",
                    alias_keys=("post:1",),
                    group_id="123",
                    author="作者",
                    text="這是一篇票券貼文",
                    permalink="https://www.facebook.com/groups/123/posts/1",
                    raw_target_kind="posts",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
        )
        history = app.repositories.match_history.list_by_target(target.id)
        assert history[0].group_name == "測試社團"

    assert sent_ntfy
    assert "社團：我的票券社團" in sent_ntfy[0][2]
    assert "社團：測試社團" not in sent_ntfy[0][2]
    assert "(20+)" not in sent_ntfy[0][2]


def test_finalize_scan_items_requires_all_include_keyword_groups(tmp_path: Path) -> None:
    """shared finalize 使用 include groups 判斷命中並保存 group 診斷。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                config=TargetConfigPatch(
                    include_keyword_groups=keyword_group_slots((("5/1;5/2",), ("108;109",))),
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)

        result = finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:1",
                    alias_keys=("post:1",),
                    group_id="123",
                    text="售 5/2 109 區票券",
                    raw_target_kind="posts",
                ),
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:2",
                    alias_keys=("post:2",),
                    group_id="123",
                    text="售 5/2 票券",
                    raw_target_kind="posts",
                ),
            ],
            item_count=2,
            metadata={"worker": "test_worker"},
        )

        assert result.matched_count == 1
        assert result.latest_items[0].matched_keyword == "5/2;109"
        assert [
            (match.group_id, match.group_label, match.rule)
            for match in result.latest_items[0].matched_keyword_groups
        ] == [("1", "關鍵字 1", "5/2"), ("2", "關鍵字 2", "109")]
        assert result.latest_items[1].matched_keyword == ""
        assert result.latest_items[1].debug_metadata["classification"]["include_group_results"] == [
            {
                "group_id": "1",
                "group_label": "關鍵字 1",
                "matched": True,
                "rules": ["5/2"],
            },
            {
                "group_id": "2",
                "group_label": "關鍵字 2",
                "matched": False,
                "rules": [],
            },
        ]


def test_finalize_scan_items_refuses_stopped_target_commit(tmp_path: Path) -> None:
    """target 停止後才完成的掃描不得寫入 seen/history/latest/notification。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="測試社團",
                config=TargetConfigPatch(include_keywords=("票券",)),
            )
        )
        config = app.services.targets.get_config_for_target(target)
        app.repositories.scan_scope_state.mark_initialized(target.scope_id)
        target = app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.pause_target_monitoring(target.id)

        with pytest.raises(WorkerFailure) as excinfo:
            finalize_scan_items(
                app=app,
                target=target,
                config=config,
                items=[
                    NormalizedScanItem(
                        item_kind=ItemKind.POST,
                        item_key="post:1",
                        alias_keys=("post:1",),
                        group_id="123",
                        author="作者",
                        text="票券",
                        permalink="https://www.facebook.com/groups/123/posts/1",
                    )
                ],
                item_count=1,
                metadata={"worker": "test_worker"},
            )

        assert excinfo.value.reason == "target_stopped"
        assert app.repositories.match_history.list_by_target(target.id) == []
        assert app.repositories.latest_scan_items.list_by_target(target.id) == []
        assert app.repositories.notification_outbox.list_pending(limit=10) == []


def test_finalize_scan_items_refuses_paused_descriptor_commit(tmp_path: Path) -> None:
    """即使呼叫端傳入的是 paused descriptor，finalize 仍不得寫入結果。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="測試社團",
                config=TargetConfigPatch(include_keywords=("票券",)),
            )
        )
        config = app.services.targets.get_config_for_target(target)
        app.repositories.scan_scope_state.mark_initialized(target.scope_id)

        with pytest.raises(WorkerFailure) as excinfo:
            finalize_scan_items(
                app=app,
                target=target,
                config=config,
                items=[
                    NormalizedScanItem(
                        item_kind=ItemKind.POST,
                        item_key="post:1",
                        alias_keys=("post:1",),
                        group_id="123",
                        author="作者",
                        text="票券",
                        permalink="https://www.facebook.com/groups/123/posts/1234567890",
                    )
                ],
                item_count=1,
                metadata={"worker": "test_worker"},
            )

        assert excinfo.value.reason == "target_stopped"
        assert app.repositories.match_history.list_by_target(target.id) == []
        assert app.repositories.latest_scan_items.list_by_target(target.id) == []
        assert app.repositories.notification_outbox.list_pending(limit=10) == []


def test_finalize_scan_items_refuses_restarted_attempt_commit(tmp_path: Path) -> None:
    """target stop/start 後，舊掃描 attempt 不得再寫入結果。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(
            app,
            include_keywords=("票券",),
        )
        app.services.targets.pause_target_monitoring(fixture.target.id)
        app.services.targets.restart_target_monitoring(fixture.target.id)

        with pytest.raises(WorkerFailure) as excinfo:
            finalize_scan_items(
                app=app,
                target=fixture.target,
                config=fixture.config,
                items=[
                    NormalizedScanItem(
                        item_kind=ItemKind.POST,
                        item_key="post:restart-race",
                        alias_keys=("post:restart-race",),
                        group_id="123",
                        author="作者",
                        text="票券",
                        permalink="https://www.facebook.com/groups/123/posts/1234567890",
                    )
                ],
                item_count=1,
                metadata={"worker": "test_worker"},
                commit_guard=fixture.commit_guard,
            )

        assert excinfo.value.reason == "target_stopped"
        assert app.repositories.match_history.list_by_target(fixture.target.id) == []
        assert app.repositories.latest_scan_items.list_by_target(fixture.target.id) == []
        assert app.repositories.notification_outbox.list_pending(limit=10) == []


def test_record_skipped_scan_refuses_restarted_attempt_commit(tmp_path: Path) -> None:
    """sort-adjust skip 也不得在 stop/start 後清掉新一輪 latest snapshot。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        app.repositories.latest_scan_items.replace_for_target(
            fixture.target.id,
            [
                LatestScanItem(
                    target_id=fixture.target.id,
                    scan_run_id=99,
                    item_kind=ItemKind.POST,
                    item_key="previous",
                    item_index=0,
                    text="新一輪已存在的 snapshot",
                )
            ],
        )
        app.services.targets.pause_target_monitoring(fixture.target.id)
        app.services.targets.restart_target_monitoring(fixture.target.id)

        with pytest.raises(WorkerFailure) as excinfo:
            record_skipped_scan(
                app=app,
                target=fixture.target,
                metadata={"worker": "test_worker"},
                commit_guard=fixture.commit_guard,
            )

        latest_items = app.repositories.latest_scan_items.list_by_target(fixture.target.id)
        assert excinfo.value.reason == "target_stopped"
        assert len(latest_items) == 1
        assert latest_items[0].item_key == "previous"


def test_finalize_scan_items_refuses_reused_worker_with_different_page(
    tmp_path: Path,
) -> None:
    """同 worker / started_at 但 page identity 已換掉時，舊頁面不得寫回。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(
            app,
            include_keywords=("票券",),
        )
        runtime_state = app.services.targets.ensure_runtime_state(fixture.target.id)
        app.repositories.runtime_states.save(replace(runtime_state, active_page_id="page-b"))

        with pytest.raises(WorkerFailure) as excinfo:
            finalize_scan_items(
                app=app,
                target=fixture.target,
                config=fixture.config,
                items=[
                    NormalizedScanItem(
                        item_kind=ItemKind.POST,
                        item_key="post:page-drift",
                        alias_keys=("post:page-drift",),
                        group_id="123",
                        author="作者",
                        text="票券",
                        permalink="https://www.facebook.com/groups/123/posts/1234567890",
                    )
                ],
                item_count=1,
                metadata={"worker": "test_worker"},
                commit_guard=fixture.commit_guard,
            )

        assert excinfo.value.reason == "target_stopped"
        assert app.repositories.match_history.list_by_target(fixture.target.id) == []
        assert app.repositories.latest_scan_items.list_by_target(fixture.target.id) == []


def test_finalize_scan_items_starts_write_transaction_before_first_scan_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """guard check 與 scan 結果寫入必須包在同一個 SQLite write transaction。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)

    saw_write_transaction: list[bool] = []
    with SqliteApplicationContext(db_path) as app:
        original_mark_profile_ok = app.repositories.app_settings.mark_profile_ok

        def mark_profile_ok_with_assertion(*, source: str) -> None:
            """記錄第一個 scan finalize 寫入前是否已持有 transaction。"""

            saw_write_transaction.append(app.repositories.runtime_states.connection.in_transaction)
            original_mark_profile_ok(source=source)

        monkeypatch.setattr(
            app.repositories.app_settings,
            "mark_profile_ok",
            mark_profile_ok_with_assertion,
        )
        finalize_scan_items(
            app=app,
            target=fixture.target,
            config=fixture.config,
            items=[],
            item_count=0,
            metadata={"worker": "test_worker"},
            commit_guard=fixture.commit_guard,
        )

    assert saw_write_transaction == [True]


def test_record_guarded_scan_failure_starts_write_transaction_before_failure_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """failure finalize 的 guard check 與 failure scan run 寫入也要同 transaction。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)

    saw_write_transaction: list[bool] = []
    with SqliteApplicationContext(db_path) as app:

        def record_scan_failure_with_assertion(**kwargs: object) -> int:
            """記錄 failure scan run 寫入前是否已持有 transaction。"""

            saw_write_transaction.append(app.repositories.runtime_states.connection.in_transaction)
            return record_scan_failure(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(
            scan_failure_finalize_module,
            "record_scan_failure",
            record_scan_failure_with_assertion,
        )
        decision = record_guarded_scan_failure(
            app=app,
            target_id=fixture.target.id,
            reason="unknown",
            message="boom",
            source="worker_failure",
            worker_path="resident_main",
            commit_guard=fixture.commit_guard,
        )

    assert decision is not None
    assert saw_write_transaction == [True]
