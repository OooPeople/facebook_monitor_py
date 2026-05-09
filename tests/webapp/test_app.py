"""FastAPI Web UI tests。"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from facebook_monitor.application import context as application_context
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import UpsertCommentsTargetRequest
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.application.services import RecordScanRequest
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import TargetKind
from facebook_monitor.notifications.desktop import DesktopNotificationResult
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.discord import DiscordResult
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult
from facebook_monitor.webapp.app import create_app
from facebook_monitor.webapp.app import parse_keywords_text
from facebook_monitor.webapp import query_service
from facebook_monitor.webapp.query_service import get_dashboard_view
from facebook_monitor.webapp.profile_session import ProfileSessionOptions
from facebook_monitor.webapp.routes import dashboard as dashboard_routes
from facebook_monitor.webapp.routes.dashboard import _format_dashboard_revision_event
from facebook_monitor.webapp.scheduler_session import SchedulerSessionState


class FakeProfileManager:
    """測試用 profile manager，避免 Web UI route 測試真的開 Playwright。"""

    def __init__(self) -> None:
        self.active = False
        self.options: ProfileSessionOptions | None = None

    def is_active(self) -> bool:
        """回傳 fake profile 視窗是否開啟。"""

        return self.active

    def open(self, options: ProfileSessionOptions) -> None:
        """保存設定並標記 fake profile 視窗已開啟。"""

        self.options = options
        self.active = True

    def close(self) -> None:
        """關閉 fake profile 視窗。"""

        self.active = False


class FakeSchedulerManager:
    """測試用 scheduler manager，避免 Web UI route 測試真的跑背景掃描。"""

    def __init__(self) -> None:
        self.running = False
        self.started_count = 0
        self.stopped_count = 0
        self.woken_count = 0
        self.options: object | None = None
        self.queued_target_ids: tuple[str, ...] = ()

    def state(self) -> SchedulerSessionState:
        """回傳 fake scheduler 狀態。"""

        return SchedulerSessionState(
            running=self.running,
            interval_seconds=60,
            last_cycle_at="",
            last_error="",
            max_concurrent_scans=2,
            current_running_count=1 if self.running else 0,
            current_queued_count=len(self.queued_target_ids),
            queue_length=len(self.queued_target_ids),
            queued_target_ids=self.queued_target_ids,
            worker_ids=("resident-slot-1", "resident-slot-2") if self.running else (),
            page_pool_size=1 if self.running else 0,
            last_opened_page_count=1 if self.running else 0,
            last_reused_page_count=2 if self.running else 0,
            last_closed_page_count=0,
            resident_browser_alive=self.running,
        )

    def is_running(self) -> bool:
        """回傳 fake scheduler 是否執行中。"""

        return self.running

    def start(self, options: object) -> None:
        """標記 fake scheduler 已啟動。"""

        self.started_count += 1
        self.options = options
        self.running = True

    def stop(self) -> None:
        """標記 fake scheduler 已停止。"""

        self.stopped_count += 1
        self.running = False

    def wake(self) -> None:
        """記錄 manual-start 喚醒要求。"""

        self.woken_count += 1


def test_parse_keywords_text_dedupes_and_trims() -> None:
    """Web UI keyword parser 會去除空白與重複值。"""

    assert parse_keywords_text("票, 交換,票,,讓票") == ("票", "交換", "讓票")


def test_index_renders_target_rows(tmp_path: Path) -> None:
    """首頁會顯示已保存 target，並清理 Facebook title 前置通知數。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="(3) 測試社團",
            )
        )
        target = app_context.repositories.targets.find_by_kind_scope(
            TargetKind.POSTS,
            "222518561920110",
        )
        assert target is not None
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.FAILED,
                error_message="page_load_timeout: timeout",
            )
        )
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
                item_count=2,
                matched_count=1,
                metadata={
                    "worker": "posts_scan",
                    "collection_strategy": "feed_visible_window",
                    "new_count": 1,
                    "matched_count": 1,
                    "target_count": 5,
                    "scanned_count": 2,
                    "candidate_count": 2,
                    "round_count": 1,
                    "scroll_rounds": 0,
                    "scroll_wait_ms": 0,
                    "stop_reason": "scroll_rounds_completed",
                    "rounds": [
                        {
                            "round_index": 0,
                            "raw_item_count": 2,
                            "unique_item_count": 2,
                            "scroll_y": 0,
                            "scroll_height": 1200,
                        }
                    ],
                },
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="item-1",
                    item_index=0,
                    author="王小明",
                    text="這是一篇有票券關鍵字的貼文",
                    permalink="https://www.facebook.com/groups/222518561920110/posts/1",
                    matched_keyword="票券",
                    debug_metadata={
                        "textSource": "primary",
                        "permalinkSource": "container:groups_post_anchor",
                        "expandCount": 1,
                        "linkDiagnostics": {
                            "total": 2,
                            "kindCounts": {"profile": 1, "hashtag": 1},
                            "samples": [
                                {
                                    "kind": "profile",
                                    "href": "https://www.facebook.com/groups/1/user/2",
                                    "text": "王小明",
                                }
                            ],
                        },
                    },
                ),
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="item-2",
                    item_index=1,
                    author="陳小華",
                    text="這是一篇普通貼文",
                    permalink="",
                    matched_keyword="",
                ),
            ],
        )
        app_context.repositories.notification_events.add(
            NotificationEvent(
                target_id=target.id,
                item_key="item-1",
                channel=NotificationChannel.NTFY,
                status=NotificationStatus.SENT,
                message="sent",
            )
        )

    scheduler_manager = FakeSchedulerManager()
    scheduler_manager.running = True
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.get("/")

    assert response.status_code == 200
    assert "測試社團" in response.text
    assert "(3) 測試社團" not in response.text
    assert "222518561920110" in response.text
    assert "group=222518561920110" not in response.text
    assert "scope=222518561920110" not in response.text
    assert "閒置" in response.text
    assert "最近掃描" in response.text
    assert "命中紀錄 0" in response.text
    assert f'data-hit-records-modal="{target.id}"' in response.text
    assert f'data-clear-hit-records data-target-id="{target.id}"' in response.text
    assert "/static/dashboard/main.js" in response.text
    assert "查看紀錄" in response.text
    assert "設定" in response.text
    assert "掃描診斷" in response.text
    assert "rounds=1 · candidates=2 · stop=完成捲動輪數" in response.text
    assert "collection_strategy=feed_visible_window" in response.text
    assert "round=0 raw=2 unique=2" in response.text
    assert "複製掃描診斷" in response.text
    assert "最近有錯誤" in response.text
    assert "最近掃描貼文" not in response.text
    assert "最近通知" in response.text
    assert "ntfy: sent" not in response.text
    assert "背景掃描服務" not in response.text
    assert "running=1 · queued=0 · slots=2" not in response.text
    assert "監看清單快速跳轉" in response.text
    assert f'data-sidebar-target="target-{target.id}"' in response.text
    assert f'data-action-anchor="target-{target.id}"' in response.text
    assert f'id="target-{target.id}"' in response.text
    assert f'value="#target-{target.id}"' not in response.text
    assert "啟動自動掃描" not in response.text
    assert "停止自動掃描" not in response.text
    assert "王小明" in response.text
    assert "命中: 票券" in response.text
    assert "陳小華" in response.text
    assert "未命中" in response.text
    assert "未取得連結" in response.text
    assert "這是一篇有票券關鍵字的貼文" in response.text
    assert "開啟連結" in response.text
    assert "除錯" not in response.text
    assert "複製除錯資訊" not in response.text
    assert "latest_scan_items:" in response.text
    assert "textSource=primary" in response.text
    assert "expandCount=1" in response.text
    assert "linkDiagnostics=" in response.text
    assert "debug_json=" not in response.text
    assert "https://www.facebook.com/groups/1/user/2" in response.text
    assert "監視中" not in response.text
    assert "掃描一次" not in response.text
    assert 'class="collapse-toggle"' in response.text
    assert 'aria-label="收合 target"' in response.text
    assert 'class="collapse-toggle-icon"' in response.text
    assert (
        '<dl class="target-collapsed-summary field-grid field-grid--summary" '
        "data-collapsed-summary hidden>"
    ) in response.text
    assert "target-collapsed-summary-field" in response.text
    assert "包含關鍵字" in response.text
    assert "排除關鍵字" in response.text
    assert "設定摘要" in response.text
    assert '</div>\n\n  <div class="target-footer-controls">' in response.text
    assert ">收合</button>" not in response.text


def test_index_renders_runtime_state_and_error(tmp_path: Path) -> None:
    """首頁會顯示 scheduler runtime state 與 last error。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        running_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="執行中社團",
            )
        )
        error_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
                group_name="錯誤社團",
            )
        )
        stopped_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="333",
                canonical_url="https://www.facebook.com/groups/333",
                group_name="停止社團",
            )
        )
        app_context.services.targets.mark_target_running(running_target.id, "worker-1")
        app_context.services.targets.mark_target_error(error_target.id, "login_required: 需要登入")
        app_context.services.targets.pause_target_monitoring(stopped_target.id)

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "執行中" in response.text
    assert "錯誤" in response.text
    assert "login_required: 需要登入" in response.text
    assert "已停止" in response.text


def test_index_does_not_render_queue_position_runtime_note(tmp_path: Path) -> None:
    """排隊資訊不應以會推動 card 高度的 queue_position raw note 顯示。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="排隊測試社團",
            )
        )
        app_context.services.targets.mark_target_queued(target.id, "due")

    scheduler_manager = FakeSchedulerManager()
    scheduler_manager.running = True
    scheduler_manager.queued_target_ids = (target.id,)
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.get("/")

    assert response.status_code == 200
    assert "排隊測試社團" in response.text
    assert "排隊中" in response.text
    assert "queue_position=" not in response.text


def test_hit_record_api_lists_counts_and_clears_only_target_history(tmp_path: Path) -> None:
    """查看紀錄 API 可查詢與清空單一 target，且不清其他 runtime/debug 資料。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="第一個社團",
            )
        )
        second_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
                group_name="第二個社團",
            )
        )
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=first_target.id,
                group_id=first_target.group_id,
                group_name="第一個社團",
                item_kind=ItemKind.POST,
                item_key="first-1",
                author="王小明",
                text="這是一筆有票券關鍵字的命中紀錄",
                permalink="https://www.facebook.com/groups/111/posts/1",
                include_rule="票券",
            )
        )
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=first_target.id,
                group_id=first_target.group_id,
                group_name="第一個社團",
                item_kind=ItemKind.COMMENT,
                item_key="first-2",
                author="陳小華",
                text="留言也有票券關鍵字",
                permalink="https://www.facebook.com/groups/111/posts/1?comment_id=2",
                include_rule="票券",
            )
        )
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=second_target.id,
                group_id=second_target.group_id,
                item_kind=ItemKind.POST,
                item_key="second-1",
                text="另一個 target 的命中紀錄",
                include_rule="票券",
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            first_target.id,
            [
                LatestScanItem(
                    target_id=first_target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="latest-1",
                    item_index=0,
                    text="最近掃描仍應保留",
                    matched_keyword="票券",
                    debug_metadata={"textSource": "primary"},
                )
            ],
        )
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=first_target.id,
                status=ScanStatus.SUCCESS,
                item_count=1,
                matched_count=1,
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=first_target.scope_id,
                item_key="seen-1",
                item_kind=ItemKind.POST,
            )
        )
        app_context.repositories.notification_events.add(
            NotificationEvent(
                target_id=first_target.id,
                item_key="first-1",
                channel=NotificationChannel.NTFY,
                status=NotificationStatus.SENT,
                message="sent",
            )
        )
        app_context.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{first_target.id}:first-1:ntfy",
                target_id=first_target.id,
                item_key="first-1",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )

    app = create_app(db_path=db_path, profile_dir=tmp_path / "profile")
    with SqliteApplicationContext(db_path) as app_context:
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=first_target.id,
                group_id=first_target.group_id,
                group_name="第一個社團",
                item_kind=ItemKind.POST,
                item_key="first-current",
                author="林本次",
                text="本次啟動期間的票券命中",
                permalink="https://www.facebook.com/groups/111/posts/current",
                include_rule="票券",
                notified_at=app.state.session_started_at + timedelta(seconds=1),
                created_at=app.state.session_started_at + timedelta(seconds=1),
            )
        )
    client = TestClient(app)
    preview_response = client.get(f"/api/targets/{first_target.id}/hit-records/preview")
    count_response = client.get(f"/api/targets/{first_target.id}/hit-records/count")
    full_response = client.get(
        f"/api/targets/{first_target.id}/hit-records",
        params={"limit": 1, "offset": 1},
    )
    sidebar_response = client.get("/api/sidebar")
    card_response = client.get(f"/api/targets/{first_target.id}/card")
    revision_before_clear = client.get("/api/dashboard-revision").json()["revision"]
    clear_response = client.delete(f"/api/targets/{first_target.id}/hit-records")
    revision_after_clear = client.get("/api/dashboard-revision").json()["revision"]

    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert preview_payload["total_count"] == 1
    assert preview_payload["items"][0]["author_name"] == "林本次"
    assert preview_payload["items"][0]["badge_text"] == "命中: 票券"
    assert count_response.json() == {"target_id": first_target.id, "total_count": 1}
    full_payload = full_response.json()
    assert full_payload["total_count"] == 3
    assert full_payload["items"][0]["sequence_number"] == 2
    assert full_payload["items"][0]["item_type"] == "留言"
    assert full_payload["items"][0]["notified_at"]
    assert sidebar_response.status_code == 200
    sidebar_payload = sidebar_response.json()
    assert sidebar_payload["items"][0]["target_id"] == first_target.id
    assert sidebar_payload["items"][0]["hit_count"] == 1
    assert card_response.status_code == 200
    card_payload = card_response.json()
    assert card_payload["target_id"] == first_target.id
    assert card_payload["card_summary"]["hit_record_total_count"] == 1
    assert card_payload["hit_record_total_count"] == 1
    assert card_payload["card_summary"]["sections"][0]["label"] == "包含關鍵字"
    assert card_payload["card_summary"]["sections"][2]["lines"][0].startswith("刷新 ")
    assert len(card_payload["card_summary"]["sections"][3]["lines"]) == 1
    assert card_payload["card_summary"]["sections"][4]["lines"] == ["1 筆"]
    assert "latest_scan_items:" in card_payload["latest_scan_diagnostics_text"]
    assert "textSource=primary" in card_payload["latest_scan_diagnostics_text"]
    assert card_payload["hit_record_preview_rows"][0]["badge_text"] == "命中: 票券"
    assert card_payload["latest_scan_preview_rows"][0]["link_label"] == "開啟連結"
    assert card_payload["hit_record_preview_rows"][0]["link_label"] == "開啟連結"
    assert not card_payload["latest_scan_preview_rows"][0]["debug_summary"]
    assert not card_payload["latest_scan_preview_rows"][0]["debug_text"]
    assert not card_payload["latest_scan_preview_rows"][0]["has_debug"]
    assert not card_payload["hit_record_preview_rows"][0]["has_debug"]
    assert clear_response.status_code == 200
    assert clear_response.json() == {
        "target_id": first_target.id,
        "deleted_count": 3,
        "total_count": 0,
    }
    assert revision_before_clear != revision_after_clear
    with SqliteApplicationContext(db_path) as app_context:
        assert app_context.repositories.match_history.count_by_target(first_target.id) == 0
        assert app_context.repositories.match_history.count_by_target(second_target.id) == 1
        assert app_context.repositories.latest_scan_items.list_by_target(first_target.id)
        assert app_context.repositories.scan_runs.latest_by_target(first_target.id) is not None
        assert app_context.repositories.seen_items.has_seen(first_target.scope_id, "seen-1")
        assert app_context.repositories.notification_events.list_by_target(first_target.id)
        assert (
            app_context.repositories.notification_outbox.get_by_idempotency_key(
                f"{first_target.id}:first-1:ntfy"
            )
            is not None
        )


def test_webui_startup_keeps_full_history_but_resets_hit_preview(tmp_path: Path) -> None:
    """Web UI 重啟保留查看紀錄，但卡片命中 preview 只顯示本次 session。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="第一個社團",
            )
        )
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=target.id,
                group_id=target.group_id,
                group_name="第一個社團",
                item_kind=ItemKind.POST,
                item_key="persisted-1",
                text="重啟後仍保留的查看紀錄",
                include_rule="票券",
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="persisted-1",
                    item_index=0,
                )
            ],
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="persisted-1",
                item_kind=ItemKind.POST,
            )
        )

    app = create_app(
        db_path=db_path,
        profile_dir=tmp_path / "profile",
        reset_runtime_data_on_startup=True,
    )
    with TestClient(app) as client:
        preview_payload = client.get(f"/api/targets/{target.id}/hit-records/preview").json()
        full_payload = client.get(f"/api/targets/{target.id}/hit-records").json()

    assert preview_payload["total_count"] == 0
    assert full_payload["total_count"] == 1
    with SqliteApplicationContext(db_path) as app_context:
        assert app_context.repositories.match_history.count_by_target(target.id) == 1
        assert not app_context.repositories.latest_scan_items.list_by_target(target.id)
        assert not app_context.repositories.seen_items.has_seen(target.scope_id, "persisted-1")


def test_dashboard_view_model_includes_phase3_read_models(tmp_path: Path) -> None:
    """Phase 3 dashboard read model 會帶入 sidebar、hit preview 與設定摘要。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="測試社團",
                fixed_refresh_sec=None,
                min_refresh_sec=25,
                max_refresh_sec=35,
                jitter_enabled=True,
                enable_ntfy=True,
                ntfy_topic="phase0test",
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="latest-1",
                    item_index=0,
                    author="王小明",
                    text="最近掃描內容",
                    matched_keyword="票券",
                ),
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="latest-2",
                    item_index=1,
                    author="林小美",
                    text="較新的最近掃描內容",
                    matched_keyword="票券",
                ),
            ],
        )
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=target.id,
                group_id=target.group_id,
                item_kind=ItemKind.POST,
                item_key="history-1",
                author="陳小華",
                text="歷史命中內容",
                include_rule="票券",
            )
        )

    dashboard = get_dashboard_view(db_path)
    row = dashboard.rows[0]
    latest_preview = row.latest_scan_preview_rows[0]
    hit_preview = row.hit_record_preview_rows[0]

    assert dashboard.sidebar_items[0].display_name == "測試社團"
    assert dashboard.sidebar_items[0].hit_count == 1
    assert "命中 1 筆" in dashboard.sidebar_items[0].status_summary
    assert row.hit_record_total_count == 1
    assert row.hit_records_heading == "命中紀錄（1）"
    assert row.settings_summary.lines[0] == "刷新：浮動 25-35 秒"
    assert row.settings_summary.lines[-1] == "通知：ntfy"
    assert latest_preview.author_name == "王小明"
    assert latest_preview.badge_kind == "hit"
    assert latest_preview.link_label == "開啟連結"
    assert not latest_preview.has_debug
    assert hit_preview.author_name == "陳小華"
    assert hit_preview.badge_text == "命中: 票券"
    assert hit_preview.link_label == "開啟連結"
    assert not hit_preview.has_debug

    response = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile")).get("/")
    assert response.status_code == 200
    assert "最近掃描" in response.text
    assert "命中紀錄 0" in response.text
    assert "最近掃描內容" in response.text


def test_index_renders_scan_guard_skip_reason(tmp_path: Path) -> None:
    """首頁會顯示同 target 重入被 guard 擋下的原因。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="重入測試社團",
            )
        )
        app_context.services.targets.mark_target_running(target.id, "worker-a")
        locked_state = app_context.services.targets.try_mark_target_running(
            target.id,
            "worker-b",
        )

    assert locked_state is None

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "重入測試社團" in response.text
    assert "scan_guard_skipped: target_already_running" in response.text
    assert "active_worker_id=worker-a" in response.text


def test_update_config_route_updates_target_config(tmp_path: Path) -> None:
    """設定表單送出後會更新 target config。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        f"/targets/{target.id}/config",
        data={
            "include_keywords": "票,交換",
            "exclude_keywords": "售完",
            "fixed_refresh_sec": "90",
            "max_items_per_scan": "30",
            "auto_adjust_sort": "on",
            "enable_desktop_notification": "on",
            "enable_ntfy": "on",
            "ntfy_topic": "phase0test",
            "enable_discord_notification": "on",
            "discord_webhook": "https://discord.com/api/webhooks/example",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
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


def test_update_config_route_supports_fixed_and_floating_refresh_modes(
    tmp_path: Path,
) -> None:
    """Web UI 設定表單可保存固定與浮動刷新模式。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    floating_response = client.post(
        f"/targets/{target.id}/config",
        data={
            "refresh_mode": "floating",
            "fixed_refresh_sec": "90",
            "min_refresh_sec": "25",
            "max_refresh_sec": "35",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )
    index_response = client.get("/")
    with SqliteApplicationContext(db_path) as app_context:
        floating_config = app_context.repositories.configs.get_for_target(target)
    fixed_response = client.post(
        f"/targets/{target.id}/config",
        data={
            "refresh_mode": "fixed",
            "fixed_refresh_sec": "120",
            "min_refresh_sec": "20",
            "max_refresh_sec": "40",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert floating_response.status_code == 303
    assert "浮動 25-35 秒" in index_response.text
    assert floating_config is not None
    assert floating_config.fixed_refresh_sec is None
    assert floating_config.jitter_enabled
    assert floating_config.min_refresh_sec == 25
    assert floating_config.max_refresh_sec == 35
    assert fixed_response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        fixed_config = app_context.repositories.configs.get_for_target(target)
    assert fixed_config is not None
    assert fixed_config.fixed_refresh_sec == 120
    assert not fixed_config.jitter_enabled
    assert fixed_config.min_refresh_sec == 20
    assert fixed_config.max_refresh_sec == 40


def test_update_config_route_rejects_invalid_floating_refresh_range(
    tmp_path: Path,
) -> None:
    """浮動刷新最小秒數大於最大秒數時，Web UI 會拒絕保存。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        f"/targets/{target.id}/config",
        data={
            "refresh_mode": "floating",
            "fixed_refresh_sec": "60",
            "min_refresh_sec": "35",
            "max_refresh_sec": "25",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    with SqliteApplicationContext(db_path) as app_context:
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert config.fixed_refresh_sec == 60


def test_create_target_route_adds_group_posts_target(tmp_path: Path) -> None:
    """Web UI 會依 Facebook group URL 自動建立 posts target 並補社團名稱。"""

    db_path = tmp_path / "app.db"
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=lambda _profile_dir, _url: "測試社團",
        )
    )

    form_response = client.get("/targets/new")
    create_response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "include_keywords": "票",
            "exclude_keywords": "售完",
            "fixed_refresh_sec": "75",
            "max_items_per_scan": "25",
            "auto_load_more": "on",
            "auto_adjust_sort": "on",
            "enable_desktop_notification": "on",
            "enable_ntfy": "on",
            "ntfy_topic": "phase0test",
            "enable_discord_notification": "on",
            "discord_webhook": "https://discord.com/api/webhooks/example",
        },
        follow_redirects=False,
    )

    assert form_response.status_code == 200
    assert "Facebook group URL" in form_response.text
    assert "自訂顯示名稱" in form_response.text
    assert f'value="{PYTHON_TARGET_CONFIG_DEFAULTS.fixed_refresh_sec}"' in form_response.text
    assert f'value="{PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan}"' in form_response.text
    assert 'name="auto_adjust_sort" type="checkbox" checked' in form_response.text
    assert "Target kind" not in form_response.text
    assert create_response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
        assert target is not None
        config = app_context.repositories.configs.get_for_target(target)
    assert target.group_name == "測試社團"
    assert target.name == "測試社團"
    assert config is not None
    assert config.include_keywords == ("票",)
    assert config.exclude_keywords == ("售完",)
    assert config.fixed_refresh_sec == 75
    assert config.max_items_per_scan == 10
    assert config.auto_load_more
    assert config.auto_adjust_sort
    assert config.enable_desktop_notification
    assert config.enable_ntfy
    assert config.ntfy_topic == "phase0test"
    assert config.enable_discord_notification
    assert config.discord_webhook == "https://discord.com/api/webhooks/example"


def test_create_target_route_uses_custom_display_name_without_resolver(tmp_path: Path) -> None:
    """有填自訂顯示名稱時不需要自動解析 Facebook title。"""

    db_path = tmp_path / "app.db"

    def failing_resolver(_profile_dir: Path, _url: str) -> str:
        raise AssertionError("resolver should not be called")

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=failing_resolver,
        )
    )

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "display_name": "我的票券社團",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "20",
            "auto_load_more": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
    assert target is not None
    assert target.name == "我的票券社團"
    assert target.group_name == ""


def test_create_target_route_adds_comments_target_and_resolves_group_name(
    tmp_path: Path,
) -> None:
    """Web UI 會依單篇貼文 URL 自動建立 comments target 並補社團名稱。"""

    db_path = tmp_path / "app.db"
    resolver_calls: list[str] = []

    def fake_resolver(_profile_dir: Path, url: str) -> str:
        resolver_calls.append(url)
        return "留言測試社團"

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=fake_resolver,
        )
    )

    form_response = client.get("/targets/new")
    create_response = client.post(
        "/targets",
        data={
            "group_url": (
                "https://www.facebook.com/groups/222518561920110/posts/2187454285426518/"
                "?comment_id=123456789"
            ),
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
            "auto_load_more": "on",
        },
        follow_redirects=False,
    )

    assert form_response.status_code == 200
    assert "Target kind" not in form_response.text
    assert create_response.status_code == 303
    assert resolver_calls == ["https://www.facebook.com/groups/222518561920110"]
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.COMMENTS,
            scope_id="222518561920110:post:2187454285426518:comments",
        )
        assert target is not None
        config = app_context.repositories.configs.get_for_target(target)
        state = app_context.repositories.runtime_states.get(target.id)

    assert target.group_id == "222518561920110"
    assert target.parent_post_id == "2187454285426518"
    assert target.canonical_url == (
        "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"
    )
    assert target.name == "留言測試社團"
    assert target.group_name == "留言測試社團"
    assert target.paused
    assert config is not None
    assert state is not None

    index_response = client.get("/")
    assert index_response.status_code == 200
    assert "留言測試社團" in index_response.text
    assert "社團留言" in index_response.text
    assert "comments · group=222518561920110" not in index_response.text
    assert "parent_post=2187454285426518" not in index_response.text
    assert "scope=222518561920110:post:2187454285426518:comments" not in index_response.text
    assert "target_kind=comments" in index_response.text
    assert "已停止" in index_response.text
    assert "開始" in index_response.text
    assert "comments D3 已建立 sort/load-more" not in index_response.text


def test_create_target_route_ignores_target_kind_form_field_and_detects_url(
    tmp_path: Path,
) -> None:
    """舊表單若仍送 target_kind，後端仍以 URL 自動判斷 target 類型。"""

    db_path = tmp_path / "app.db"
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=lambda _profile_dir, _url: "測試社團",
        )
    )

    response = client.post(
        "/targets",
        data={
            "target_kind": "comments",
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        posts_target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
        comments_target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.COMMENTS,
            scope_id="222518561920110:post::comments",
        )
    assert posts_target is not None
    assert comments_target is None


def test_settings_routes_control_profile_window(tmp_path: Path) -> None:
    """設定頁可開啟與關閉 Facebook automation profile 視窗。"""

    db_path = tmp_path / "app.db"
    profile_manager = FakeProfileManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            profile_manager=profile_manager,
        )
    )

    settings_response = client.get("/settings")
    open_response = client.post("/settings/facebook/open", follow_redirects=False)
    active_index_response = client.get("/")

    assert settings_response.status_code == 200
    assert "Facebook automation profile" in settings_response.text
    assert open_response.status_code == 303
    assert profile_manager.active
    assert "設定 · 開啟中" in active_index_response.text
    close_response = client.post("/settings/facebook/close", follow_redirects=False)
    assert close_response.status_code == 303
    assert not profile_manager.active


def test_settings_updates_tests_and_applies_global_notifications(tmp_path: Path) -> None:
    """設定頁可保存通知預設值、送測試通知，並批次套用到 target。"""

    db_path = tmp_path / "app.db"
    sent: list[str] = []

    def fake_desktop_sender(title: str, message: str) -> DesktopNotificationResult:
        """記錄桌面測試通知。"""

        sent.append(f"desktop:{title}:{message}")
        return DesktopNotificationResult(ok=True, status_code=None, message="desktop_sent")

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        """記錄 ntfy 測試通知。"""

        sent.append(f"ntfy:{config.topic}:{title}:{message}")
        return NtfyResult(ok=True, status_code=200, message="sent")

    def fake_discord_sender(
        config: DiscordConfig,
        title: str,
        message: str,
    ) -> DiscordResult:
        """記錄 Discord 測試通知。"""

        sent.append(f"discord:{config.webhook_url}:{title}:{message}")
        return DiscordResult(ok=True, status_code=204, message="discord_sent")

    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            desktop_sender=fake_desktop_sender,
            ntfy_sender=fake_ntfy_sender,
            discord_sender=fake_discord_sender,
        )
    )
    settings_page = client.get("/settings")
    save_response = client.post(
        "/settings/notifications",
        data={
            "enable_desktop_notification": "on",
            "enable_ntfy": "on",
            "ntfy_topic": "phase0test",
            "enable_discord_notification": "on",
            "discord_webhook": "https://discord.com/api/webhooks/example",
        },
        follow_redirects=False,
    )
    form_response = client.get("/targets/new")
    test_response = client.post(
        "/settings/notifications/test",
        follow_redirects=True,
    )
    apply_response = client.post(
        "/settings/notifications/apply-to-targets",
        follow_redirects=False,
    )

    assert save_response.status_code == 303
    assert "通知預設值" in settings_page.text
    assert "未填寫也不影響功能" in settings_page.text
    assert form_response.status_code == 200
    assert "value=\"phase0test\"" in form_response.text
    assert "https://discord.com/api/webhooks/example" in form_response.text
    assert test_response.status_code == 200
    assert "desktop_sent / ntfy_sent / discord_sent" in test_response.text
    assert any(item.startswith("desktop:") for item in sent)
    assert any(item.startswith("ntfy:phase0test:") for item in sent)
    assert any(item.startswith("discord:https://discord.com/api/webhooks/example:") for item in sent)
    assert apply_response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert config.enable_desktop_notification
    assert config.enable_ntfy
    assert config.ntfy_topic == "phase0test"
    assert config.enable_discord_notification
    assert config.discord_webhook == "https://discord.com/api/webhooks/example"


def test_target_settings_modal_can_test_notifications_without_saving(
    tmp_path: Path,
) -> None:
    """target 設定 modal 的測試通知會使用表單值，但不保存 target 設定。"""

    db_path = tmp_path / "app.db"
    sent: list[str] = []

    def fake_desktop_sender(title: str, message: str) -> DesktopNotificationResult:
        """記錄桌面測試通知。"""

        sent.append(f"desktop:{title}:{message}")
        return DesktopNotificationResult(ok=True, status_code=None, message="desktop_sent")

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        """記錄 ntfy 測試通知。"""

        sent.append(f"ntfy:{config.topic}:{title}:{message}")
        return NtfyResult(ok=True, status_code=200, message="sent")

    def fake_discord_sender(
        config: DiscordConfig,
        title: str,
        message: str,
    ) -> DiscordResult:
        """記錄 Discord 測試通知。"""

        sent.append(f"discord:{config.webhook_url}:{title}:{message}")
        return DiscordResult(ok=True, status_code=204, message="discord_sent")

    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            desktop_sender=fake_desktop_sender,
            ntfy_sender=fake_ntfy_sender,
            discord_sender=fake_discord_sender,
        )
    )
    index_response = client.get("/")
    test_response = client.post(
        f"/targets/{target.id}/notifications/test",
        data={
            "enable_desktop_notification": "on",
            "enable_ntfy": "on",
            "ntfy_topic": "modal-topic",
            "enable_discord_notification": "on",
            "discord_webhook": "https://discord.com/api/webhooks/modal",
        },
        follow_redirects=True,
    )

    assert index_response.status_code == 200
    assert "掃描設定" in index_response.text
    assert "刷新設定" in index_response.text
    assert "通知設定" in index_response.text
    assert "測試通知" in index_response.text
    assert f'form="config-{target.id}"' in index_response.text
    assert f'name="refresh_mode" type="radio" value="fixed" form="config-{target.id}"' in (
        index_response.text
    )
    assert f'name="fixed_refresh_sec" type="number" min="5" value="60" form="config-{target.id}"' in (
        index_response.text
    )
    assert test_response.status_code == 200
    assert "desktop_sent / ntfy_sent / discord_sent" in test_response.text
    assert any(item.startswith("desktop:") for item in sent)
    assert any(item.startswith("ntfy:modal-topic:") for item in sent)
    assert any(item.startswith("discord:https://discord.com/api/webhooks/modal:") for item in sent)
    with SqliteApplicationContext(db_path) as app_context:
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert not config.enable_desktop_notification
    assert not config.enable_ntfy
    assert config.ntfy_topic == ""
    assert not config.enable_discord_notification
    assert config.discord_webhook == ""


def test_settings_open_pauses_scheduler_until_profile_closes(tmp_path: Path) -> None:
    """設定頁開 profile 時會由 Web UI 內部暫停並在關閉後恢復 scheduler。"""

    db_path = tmp_path / "app.db"
    profile_manager = FakeProfileManager()
    scheduler_manager = FakeSchedulerManager()
    scheduler_manager.running = True
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            profile_manager=profile_manager,
            scheduler_manager=scheduler_manager,
        )
    )

    response = client.post("/settings/facebook/open", follow_redirects=False)

    assert response.status_code == 303
    assert profile_manager.active
    assert scheduler_manager.stopped_count == 1
    assert not scheduler_manager.running

    close_response = client.post("/settings/facebook/close", follow_redirects=False)

    assert close_response.status_code == 303
    assert scheduler_manager.started_count == 1
    assert scheduler_manager.running


def test_create_target_temporarily_pauses_scheduler_for_auto_name_resolve(
    tmp_path: Path,
) -> None:
    """背景掃描執行中時，新增 target 會短暫暫停 scheduler 再解析社團名稱。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    scheduler_manager.running = True
    resolver_calls: list[str] = []

    def fake_resolver(_profile_dir: Path, url: str) -> str:
        resolver_calls.append(url)
        return "測試社團"

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
            group_name_resolver=fake_resolver,
        )
    )

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
            "auto_load_more": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert resolver_calls == ["https://www.facebook.com/groups/222518561920110"]
    assert scheduler_manager.stopped_count == 1
    assert scheduler_manager.started_count == 1
    assert scheduler_manager.running


def test_scheduler_routes_control_background_scan(tmp_path: Path) -> None:
    """Web UI 啟停內建背景 scheduler 時一律走 resident 路徑。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )

    start_response = client.post("/scheduler/start", follow_redirects=False)
    index_response = client.get("/")
    stop_response = client.post("/scheduler/stop", follow_redirects=False)

    assert start_response.status_code == 303
    assert scheduler_manager.started_count == 1
    assert scheduler_manager.options is not None
    assert scheduler_manager.options.profile_dir == tmp_path / "profile"
    assert "背景掃描服務" not in index_response.text
    assert "啟動自動掃描" not in index_response.text
    assert "停止自動掃描" not in index_response.text
    assert stop_response.status_code == 303
    assert scheduler_manager.stopped_count == 1
    assert not scheduler_manager.running


def test_webui_startup_resets_targets_to_stopped(tmp_path: Path) -> None:
    """正式 Web UI 啟動時會停止 target，但不覆蓋浮動刷新設定。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                fixed_refresh_sec=None,
                min_refresh_sec=25,
                max_refresh_sec=35,
                jitter_enabled=True,
            )
        )

    with TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
            reset_targets_on_startup=True,
        )
    ) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "已停止" in response.text
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        state = app_context.repositories.runtime_states.get(target.id)
        config = app_context.repositories.configs.get_for_target(target)
    assert loaded is not None
    assert loaded.paused
    assert state is not None
    assert state.desired_state.value == "stopped"
    assert config is not None
    assert config.fixed_refresh_sec is None
    assert config.jitter_enabled
    assert config.min_refresh_sec == 25
    assert config.max_refresh_sec == 35


def test_webui_startup_can_clear_runtime_debug_data(tmp_path: Path) -> None:
    """Web UI 啟動時可清除上一輪 runtime/debug data，保留 target 設定。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="seen-before-startup",
                item_kind=ItemKind.POST,
            )
        )
        scan_run_id = app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
                item_count=1,
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=scan_run_id,
                    item_kind=ItemKind.POST,
                    item_key="seen-before-startup",
                    item_index=0,
                )
            ],
        )
        app_context.repositories.notification_events.add(
            NotificationEvent(
                target_id=target.id,
                item_key="seen-before-startup",
                channel=NotificationChannel.NTFY,
                status=NotificationStatus.SENT,
                message="sent",
            )
        )

    with TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
            reset_runtime_data_on_startup=True,
        )
    ) as client:
        response = client.get("/")

    assert response.status_code == 200
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        config = app_context.repositories.configs.get_for_target(target)
        latest_scan = app_context.repositories.scan_runs.latest_by_target(target.id)
        latest_items = app_context.repositories.latest_scan_items.list_by_target(target.id)
        notifications = app_context.repositories.notification_events.list_by_target(target.id)
        has_seen = app_context.repositories.seen_items.has_seen(
            target.scope_id,
            "seen-before-startup",
        )

    assert loaded is not None
    assert config is not None
    assert latest_scan is None
    assert latest_items == []
    assert notifications == []
    assert not has_seen


def test_start_and_stop_routes_update_target_status(tmp_path: Path) -> None:
    """Web UI 開始/停止 route 對齊 restart/pause 語義。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="seen-before-start",
                item_kind=ItemKind.POST,
            )
        )

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    stop_response = client.post(f"/targets/{target.id}/stop", follow_redirects=False)
    start_response = client.post(
        f"/targets/{target.id}/start",
        data={"return_to": f"#target-{target.id}"},
        follow_redirects=False,
    )

    assert stop_response.status_code == 303
    assert start_response.status_code == 303
    assert start_response.headers["location"].endswith(f"#target-{target.id}")
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        state = app_context.repositories.runtime_states.get(target.id)
        has_seen = app_context.repositories.seen_items.has_seen(
            target.scope_id,
            "seen-before-start",
        )
    assert loaded is not None
    assert loaded.enabled
    assert not loaded.paused
    assert state is not None
    assert state.scan_requested_at is not None
    assert not has_seen
    assert scheduler_manager.woken_count == 2


def test_start_route_supports_comments_target(tmp_path: Path) -> None:
    """Web UI comments target 的開始 route 會清 comments seen 並喚醒 scheduler。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="comment-before-start",
                item_kind=ItemKind.COMMENT,
            )
        )

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.post(
        f"/targets/{target.id}/start",
        data={"return_to": f"#target-{target.id}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        state = app_context.repositories.runtime_states.get(target.id)
        has_seen = app_context.repositories.seen_items.has_seen(
            target.scope_id,
            "comment-before-start",
        )
    assert loaded is not None
    assert not loaded.paused
    assert state is not None
    assert state.scan_requested_at is not None
    assert not has_seen
    assert scheduler_manager.woken_count == 1


def test_scan_once_requests_resident_scan_for_posts_and_comments(tmp_path: Path) -> None:
    """Web UI scan-once 只排入 resident scan request，不啟動 one-shot fallback。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        posts_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        comments_target = app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
            )
        )
        app_context.services.targets.restart_target_monitoring(comments_target.id)

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    posts_response = client.post(f"/targets/{posts_target.id}/scan-once", follow_redirects=False)
    comments_response = client.post(
        f"/targets/{comments_target.id}/scan-once",
        follow_redirects=False,
    )

    assert posts_response.status_code == 303
    assert comments_response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        posts_state = app_context.repositories.runtime_states.get(posts_target.id)
        comments_state = app_context.repositories.runtime_states.get(comments_target.id)
    assert posts_state is not None
    assert posts_state.scan_requested_at is not None
    assert comments_state is not None
    assert comments_state.scan_requested_at is not None
    assert scheduler_manager.started_count == 1
    assert scheduler_manager.woken_count == 2


def test_scan_once_requires_started_target(tmp_path: Path) -> None:
    """停止中的 target 不會被 scan-once 暗中送進 fallback worker。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
            )
        )

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.post(f"/targets/{target.id}/scan-once", follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert scheduler_manager.started_count == 0
    assert scheduler_manager.woken_count == 0


def test_dashboard_revision_endpoint_changes_after_target_update(tmp_path: Path) -> None:
    """dashboard revision endpoint 只在資料有變更時供前端刷新。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    first_revision = client.get("/api/dashboard-revision").json()["revision"]
    response = client.post(
        f"/targets/{target.id}/config",
        data={
            "return_to": f"#target-{target.id}",
            "include_keywords": "票券",
            "exclude_keywords": "",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )
    second_revision = client.get("/api/dashboard-revision").json()["revision"]

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"#target-{target.id}")
    assert first_revision != second_revision


def test_dashboard_revision_read_path_does_not_initialize_schema(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """SSE revision read path 不應建立 application context 或重跑 schema init。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("dashboard revision should use direct read-only connection")

    monkeypatch.setattr(query_service, "SqliteApplicationContext", fail_if_called)

    revision = query_service.get_dashboard_revision(db_path)

    assert int(revision.revision) > 0


def test_dashboard_sidebar_read_path_does_not_initialize_schema(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Sidebar partial update read path 不應在掃描寫入期間重跑 schema init。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("dashboard read path should not initialize schema")

    monkeypatch.setattr(application_context, "initialize_schema", fail_if_called)

    items = query_service.list_sidebar_items(db_path)

    assert len(items) == 1


def test_dashboard_revision_endpoint_ignores_temporary_sqlite_lock(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """dashboard polling endpoint 遇到短暫 DB lock 時回 503，前端可忽略該輪。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path):
        pass

    def raise_locked(*args: object, **kwargs: object) -> object:
        raise query_service.DashboardRevisionUnavailable("database is locked")

    monkeypatch.setattr(dashboard_routes, "get_dashboard_revision", raise_locked)
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.get("/api/dashboard-revision")

    assert response.status_code == 503


def test_dashboard_sidebar_endpoint_ignores_temporary_sqlite_lock(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Sidebar partial update 遇到短暫 DB lock 時回 503，避免 ASGI traceback。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path):
        pass

    def raise_locked(*args: object, **kwargs: object) -> object:
        raise query_service.DashboardReadUnavailable("database is locked")

    monkeypatch.setattr(dashboard_routes, "list_sidebar_items", raise_locked)
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.get("/api/sidebar")

    assert response.status_code == 503


def test_dashboard_events_streams_revision_event(tmp_path: Path) -> None:
    """dashboard SSE endpoint 與 event 格式會提供 Phase 10A revision event。"""

    db_path = tmp_path / "app.db"
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    openapi = client.get("/openapi.json").json()
    event_text = _format_dashboard_revision_event(
        {"revision": "rev-1", "last_changed_at": "2026-05-08T00:00:00"}
    )

    assert "/api/dashboard-events" in openapi["paths"]
    assert event_text.startswith("event: dashboard_revision\n")
    assert event_text.endswith("\n\n")
    data_line = next(line for line in event_text.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload == {"revision": "rev-1", "last_changed_at": "2026-05-08T00:00:00"}


def test_index_shows_latest_items_up_to_target_max_items(tmp_path: Path) -> None:
    """右側最近掃描項目顯示上限會跟 target max_items_per_scan 對齊。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                max_items_per_scan=7,
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key=f"item-{index}",
                    item_index=index,
                    author=f"作者 {index}",
                    text=f"貼文 {index}",
                )
                for index in range(7)
            ],
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "作者 0" in response.text
    assert "作者 6" in response.text


def test_delete_route_removes_only_selected_target(tmp_path: Path) -> None:
    """Web UI 刪除 route 只刪除指定 target。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app_context.services.targets.pause_target_monitoring(second.id)

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(f"/targets/{first.id}/delete", follow_redirects=False)

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        assert app_context.repositories.targets.get(first.id) is None
        loaded_second = app_context.repositories.targets.get(second.id)
    assert loaded_second is not None
    assert loaded_second.enabled
    assert loaded_second.paused

