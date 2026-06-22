"""Persistence smoke tests。"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from facebook_monitor.core.keyword_groups import keyword_group_slots
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import KeywordGroupMatch
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.repositories.app_settings import AppSettingsRepository
from facebook_monitor.persistence.repositories.latest_scan_items import LatestScanItemRepository
from facebook_monitor.persistence.repositories.match_history import MatchHistoryRepository
from facebook_monitor.persistence.repositories.notification_events import NotificationEventRepository
from facebook_monitor.persistence.repositories.scan_runs import ScanRunRepository
from facebook_monitor.persistence.repositories.seen_items import SeenItemRepository
from facebook_monitor.persistence.sqlite_connection import SqliteConnection
from facebook_monitor.persistence.repositories.targets import TargetRepository
from facebook_monitor.persistence.repositories.target_runtime_state import TargetRuntimeStateRepository
from facebook_monitor.persistence.schema import initialize_schema

from tests.persistence.sqlite_test_helpers import save_target_config_for_test
from tests.persistence.sqlite_test_helpers import get_target_config_for_test
from tests.persistence.sqlite_test_helpers import notification_outbox_repository


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
        target = replace(
            target,
            metadata_status=TargetMetadataStatus.PENDING,
            metadata_error="",
        )
        TargetRepository(connection).save(target)
        loaded_target = TargetRepository(connection).get(target.id)

        assert loaded_target is not None
        assert loaded_target.scope_id == target.group_id
        assert loaded_target.metadata_status == TargetMetadataStatus.PENDING
        assert loaded_target.metadata_error == ""
        assert loaded_target.paused
        assert TargetRepository(connection).list_enabled() == []
        assert TargetRepository(connection).list_by_metadata_status(
            TargetMetadataStatus.PENDING,
            limit=10,
        ) == [loaded_target]
        active_target = replace(loaded_target, paused=False)
        TargetRepository(connection).save(active_target)
        assert TargetRepository(connection).list_enabled() == [active_target]
        loaded_target = active_target
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
            include_keyword_groups=keyword_group_slots((("票",), ("交換",))),
            exclude_ignore_phrases=("全收;回收",),
            enable_desktop_notification=True,
            enable_ntfy=True,
            ntfy_topic="phase0test",
            enable_discord_notification=True,
            discord_webhook="https://discord.com/api/webhooks/example",
        )
        save_target_config_for_test(connection, target.id, config)
        loaded_config = get_target_config_for_test(connection, target.id)

        assert loaded_config is not None
        assert loaded_config.target_id == target.id
        assert not hasattr(loaded_config, "group_id")
        assert loaded_config.include_keywords == ("票", "交換")
        assert [group.keywords for group in loaded_config.include_keyword_groups] == [
            ("票",),
            ("交換",),
            (),
        ]
        assert loaded_config.exclude_ignore_phrases == ("全收;回收",)
        assert loaded_config.enable_desktop_notification
        assert loaded_config.enable_ntfy
        assert loaded_config.ntfy_topic == "phase0test"
        assert loaded_config.enable_discord_notification
        assert loaded_config.discord_webhook == "https://discord.com/api/webhooks/example"

        app_settings = AppSettingsRepository(connection)
        assert app_settings.get_theme() == "dark"
        assert app_settings.save_theme("dark") == "dark"
        assert app_settings.get_theme() == "dark"

        runtime_state = TargetRuntimeState(
            target_id=target.id,
            desired_state=TargetDesiredState.ACTIVE,
            runtime_status=TargetRuntimeStatus.IDLE,
            display_next_due_at=utc_now() + timedelta(seconds=60),
        )
        TargetRuntimeStateRepository(connection).save(runtime_state)
        loaded_runtime_state = TargetRuntimeStateRepository(connection).get(target.id)

        assert loaded_runtime_state is not None
        assert loaded_runtime_state.target_id == target.id
        assert loaded_runtime_state.desired_state == TargetDesiredState.ACTIVE
        assert loaded_runtime_state.runtime_status == TargetRuntimeStatus.IDLE
        assert loaded_runtime_state.display_next_due_at == runtime_state.display_next_due_at

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
                display_text="測試文字\n第二行",
                permalink="https://www.facebook.com/groups/example/posts/1",
                include_rule="票;讓票",
                include_rules=("票", "讓票"),
                include_group_matches=(
                    KeywordGroupMatch("1", "關鍵字 1", "票"),
                    KeywordGroupMatch("2", "關鍵字 2", "讓票"),
                ),
            )
        )
        assert history_id > 0
        history = history_repo.list_by_target(target.id)
        assert len(history) == 1
        assert history[0].display_text == "測試文字\n第二行"
        assert history[0].include_rule == "票;讓票"
        assert history[0].include_rules == ("票", "讓票")
        assert [
            (match.group_id, match.group_label, match.rule)
            for match in history[0].include_group_matches
        ] == [("1", "關鍵字 1", "票"), ("2", "關鍵字 2", "讓票")]
        history_match_rows = connection.execute(
            """
            SELECT rule, keyword_group_id, keyword_group_label
            FROM match_history_matches
            WHERE history_id = ?
            ORDER BY match_order
            """,
            (history_id,),
        ).fetchall()
        assert [row["rule"] for row in history_match_rows] == ["票", "讓票"]
        assert [
            (row["keyword_group_id"], row["keyword_group_label"]) for row in history_match_rows
        ] == [("1", "關鍵字 1"), ("2", "關鍵字 2")]

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
                    display_text="測試文字\n第二行",
                    permalink="https://www.facebook.com/groups/example/posts/1",
                    matched_keyword="票;讓票",
                    matched_keywords=("票", "讓票"),
                    matched_keyword_groups=(
                        KeywordGroupMatch("1", "關鍵字 1", "票"),
                        KeywordGroupMatch("2", "關鍵字 2", "讓票"),
                    ),
                    debug_metadata={"textSource": "primary", "expandCount": 1},
                )
            ],
        )
        latest_items = latest_repo.list_by_target(target.id)
        assert len(latest_items) == 1
        assert latest_items[0].author == "王小明"
        assert latest_items[0].display_text == "測試文字\n第二行"
        assert latest_items[0].matched_keyword == "票;讓票"
        assert latest_items[0].matched_keywords == ("票", "讓票")
        assert [
            (match.group_id, match.group_label, match.rule)
            for match in latest_items[0].matched_keyword_groups
        ] == [("1", "關鍵字 1", "票"), ("2", "關鍵字 2", "讓票")]
        assert latest_items[0].debug_metadata == {"textSource": "primary", "expandCount": 1}
        latest_match_rows = connection.execute(
            """
            SELECT rule, keyword_group_id, keyword_group_label
            FROM latest_scan_item_matches
            WHERE target_id = ? AND item_key = ?
            ORDER BY match_order
            """,
            (target.id, "item-hash"),
        ).fetchall()
        assert [row["rule"] for row in latest_match_rows] == ["票", "讓票"]
        assert [
            (row["keyword_group_id"], row["keyword_group_label"]) for row in latest_match_rows
        ] == [("1", "關鍵字 1"), ("2", "關鍵字 2")]

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

        outbox_repo = notification_outbox_repository(connection)
        outbox_entry = outbox_repo.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:item-hash:ntfy",
                target_id=target.id,
                item_key="item-hash",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
                endpoint="phase0test",
                permalink="https://www.facebook.com/groups/example/posts/1",
            )
        ).entry
        assert outbox_entry.id is not None
        assert outbox_entry.status == NotificationOutboxStatus.PENDING
        claimed = outbox_repo.claim_pending()[0]
        assert outbox_repo.mark_result(
            entry_id=outbox_entry.id,
            status=NotificationOutboxStatus.SENT,
            attempts=1,
            processing_status=claimed.entry.status,
            claim_token=claimed.claim_token,
            notification_event_id=event_id,
        )
        loaded_outbox = outbox_repo.get_by_idempotency_key(f"{target.id}:item-hash:ntfy")
        assert loaded_outbox is not None
        assert loaded_outbox.status == NotificationOutboxStatus.SENT
        assert loaded_outbox.notification_event_id == event_id


def test_target_runtime_state_full_row_bindings_preserve_contract(
    tmp_path: Path,
) -> None:
    """runtime state full-row save/update 應保留欄位與較新的 scan request。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        target = TargetDescriptor.for_group_posts(
            group_id="runtime-bindings",
            canonical_url="https://www.facebook.com/groups/runtime-bindings",
        )
        TargetRepository(connection).save(target)
        repo = TargetRuntimeStateRepository(connection)

        started_at = utc_now()
        running_state = TargetRuntimeState(
            target_id=target.id,
            desired_state=TargetDesiredState.ACTIVE,
            runtime_status=TargetRuntimeStatus.RUNNING,
            scan_requested_at=started_at - timedelta(seconds=30),
            last_enqueued_at=started_at - timedelta(seconds=20),
            last_started_at=started_at,
            last_finished_at=started_at - timedelta(seconds=10),
            last_heartbeat_at=started_at,
            last_error="previous error",
            last_skip_reason="previous skip",
            enqueue_reason="manual_request",
            active_worker_id="worker-a",
            active_page_id="page-a",
            last_page_reloaded_at=started_at,
            scan_guard_count=2,
            display_next_due_at=started_at + timedelta(seconds=60),
            consecutive_failure_reason="page_load_timeout",
            consecutive_failure_count=1,
            consecutive_scan_skip_reason="sort_adjust_unconfirmed",
            consecutive_scan_skip_count=2,
            updated_at=started_at,
        )
        repo.save(running_state)
        loaded_running = repo.get(target.id)

        newer_request_at = started_at + timedelta(seconds=5)
        repo.set_scan_requested_at(
            target.id,
            requested_at=newer_request_at,
            updated_at=newer_request_at,
        )
        finished_state = replace(
            running_state,
            runtime_status=TargetRuntimeStatus.IDLE,
            scan_requested_at=None,
            last_finished_at=started_at + timedelta(seconds=30),
            last_error="",
            last_skip_reason="",
            enqueue_reason="",
            active_worker_id="",
            active_page_id="",
            consecutive_failure_reason="",
            consecutive_failure_count=0,
            consecutive_scan_skip_reason="",
            consecutive_scan_skip_count=0,
            updated_at=started_at + timedelta(seconds=30),
        )
        committed = repo.save_if_running_owner(
            finished_state,
            worker_id="worker-a",
            started_at=started_at,
            page_id="page-a",
        )

    assert loaded_running == running_state
    assert committed is not None
    assert committed == replace(finished_state, scan_requested_at=newer_request_at)
