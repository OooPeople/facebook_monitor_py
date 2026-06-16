"""FastAPI Web UI tests。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.application.scan_recording_service import RecordScanRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import SeenItem


from tests.webapp.app_test_helpers import create_app


def test_hit_records_modal_matches_preview_typography_and_link_style() -> None:
    """查看紀錄 modal 字級與連結樣式需對齊最近掃描 / 命中紀錄 preview。"""

    modal_styles = Path("src/facebook_monitor/webapp/static/styles/modals.css").read_text(
        encoding="utf-8"
    )
    hit_records_js = Path("src/facebook_monitor/webapp/static/dashboard/hit_records.js").read_text(
        encoding="utf-8"
    )

    assert ".hit-record-summary-list" in modal_styles
    assert ".hit-record-summary-item dt::after" in modal_styles
    assert 'content: "：";' in modal_styles
    assert "grid-template-columns: 5em minmax(0, 1fr);" in modal_styles
    assert 'item.className = "hit-record-summary-item";' in hit_records_js
    assert 'fields.className = "hit-record-fields hit-record-summary-list";' in hit_records_js
    assert modal_styles.count("font-size: 14px;") >= 4
    assert ".hit-record-row a" in modal_styles
    assert "border-radius: 999px;" in modal_styles
    assert "font-weight: 650;" in modal_styles
    assert 'missing.className = "missing-link";' in hit_records_js


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
                permalink="https://www.facebook.com/groups/111/posts/1234567890",
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
                display_text="留言也有票券關鍵字\n補充第二行",
                permalink="https://www.facebook.com/groups/111/posts/1234567890?comment_id=2222222222",
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
                display_text="本次啟動期間的票券命中\n補充第二行",
                permalink="https://www.facebook.com/groups/111/posts/3333333333",
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
    dashboard_after_clear_response = client.get("/api/dashboard-cards")
    revision_after_clear = client.get("/api/dashboard-revision").json()["revision"]

    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert preview_payload["total_count"] == 1
    assert preview_payload["items"][0]["author_name"] == "林本次"
    assert preview_payload["items"][0]["badge_text"] == "命中: 票券"
    assert preview_payload["items"][0]["content_preview"] == "本次啟動期間的票券命中 補充第二行"
    assert preview_payload["items"][0]["content_segments"] == [
        {"text": "本次啟動期間的", "highlighted": False},
        {"text": "票券", "highlighted": True},
        {"text": "命中 補充第二行", "highlighted": False},
    ]
    assert count_response.json() == {"target_id": first_target.id, "total_count": 1}
    full_payload = full_response.json()
    assert full_payload["total_count"] == 3
    assert full_payload["items"][0]["sequence_number"] == 2
    assert full_payload["items"][0]["item_type"] == "留言"
    assert full_payload["items"][0]["recorded_at"]
    assert "notified_at" not in full_payload["items"][0]
    assert "notification_summary" in full_payload["items"][0]
    assert full_payload["items"][0]["content"] == "留言也有票券關鍵字\n補充第二行"
    assert full_payload["items"][0]["content_preview"] == "留言也有票券關鍵字 補充第二行"
    assert {"text": "票券", "highlighted": True} in full_payload["items"][0]["content_segments"]
    hit_records_js = Path("src/facebook_monitor/webapp/static/dashboard/hit_records.js").read_text(
        encoding="utf-8"
    )
    assert "通知狀態" not in hit_records_js
    assert "item.notified_at" not in hit_records_js
    assert sidebar_response.status_code == 200
    sidebar_payload = sidebar_response.json()
    assert sidebar_payload["items"][0]["target_id"] == first_target.id
    assert sidebar_payload["items"][0]["hit_count"] == 1
    assert card_response.status_code == 200
    card_payload = card_response.json()
    assert card_payload["target_id"] == first_target.id
    assert card_payload["hit_record_total_count"] == 1
    assert "target-collapsed-summary-field" in card_payload["card_summary_html"]
    assert "關鍵字" in card_payload["card_summary_html"]
    assert "命中 1 筆" in card_payload["card_summary_html"]
    assert "preview-list" in card_payload["latest_scan_preview_html"]
    assert "preview-list" in card_payload["hit_record_preview_html"]
    assert "latest_scan_items:" in card_payload["latest_scan_diagnostics_text"]
    assert "textSource=primary" in card_payload["latest_scan_diagnostics_text"]
    assert "命中: 票券" in card_payload["hit_record_preview_html"]
    assert '<mark class="keyword-highlight">票券</mark>' in card_payload["hit_record_preview_html"]
    assert "開啟連結" in card_payload["hit_record_preview_html"]
    assert "latest_scan_preview_rows" not in card_payload
    assert "hit_record_preview_rows" not in card_payload
    assert "card_summary" not in card_payload
    assert clear_response.status_code == 200
    assert clear_response.json() == {
        "target_id": first_target.id,
        "deleted_count": 3,
        "total_count": 0,
    }
    dashboard_after_clear = dashboard_after_clear_response.json()
    sidebar_item_after_clear = dashboard_after_clear["sidebar"]["items"][0]
    card_after_clear = dashboard_after_clear["cards"][0]
    assert sidebar_item_after_clear["target_id"] == first_target.id
    assert sidebar_item_after_clear["hit_count"] == 0
    assert "命中 1 筆" not in sidebar_item_after_clear["status_detail"]
    assert card_after_clear["target_id"] == first_target.id
    assert card_after_clear["hit_record_total_count"] == 0
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


def test_hit_record_api_returns_404_for_inactive_corrupt_target_row(
    tmp_path: Path,
) -> None:
    """命中紀錄 API 對 inactive 壞 target row 應回 404，不應 500。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        corrupt = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="inactive-hit-corrupt",
                canonical_url="https://www.facebook.com/groups/inactive-hit-corrupt",
                group_name="inactive hit 壞資料",
            )
        )
        connection = app_context.repositories.targets.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE targets SET target_kind = 'invalid-kind', paused = 1 WHERE id = ?",
            (corrupt.id,),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    assert client.get(f"/api/targets/{corrupt.id}/hit-records/preview").status_code == 404
    assert client.get(f"/api/targets/{corrupt.id}/hit-records/count").status_code == 404
    assert client.get(f"/api/targets/{corrupt.id}/hit-records").status_code == 404


def test_hit_record_api_returns_404_for_inactive_corrupt_runtime_row(
    tmp_path: Path,
) -> None:
    """命中紀錄 API 對 inactive 壞 runtime row 應回 404，不應 500。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        corrupt = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="inactive-hit-runtime-corrupt",
                canonical_url="https://www.facebook.com/groups/inactive-hit-runtime-corrupt",
                group_name="inactive hit runtime 壞資料",
            )
        )
        app_context.services.targets.pause_target_monitoring(corrupt.id)
        connection = app_context.repositories.runtime_states.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE target_runtime_state SET runtime_status = 'invalid' WHERE target_id = ?",
            (corrupt.id,),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    assert client.get(f"/api/targets/{corrupt.id}/hit-records/preview").status_code == 404
    assert client.get(f"/api/targets/{corrupt.id}/hit-records/count").status_code == 404
    assert client.get(f"/api/targets/{corrupt.id}/hit-records").status_code == 404


def test_hit_record_api_returns_503_for_active_corrupt_runtime_row(
    tmp_path: Path,
) -> None:
    """命中紀錄 API 對 active 壞 runtime row 應明確 503，不可 404 或修復。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        corrupt = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="active-hit-runtime-corrupt",
                canonical_url="https://www.facebook.com/groups/active-hit-runtime-corrupt",
                group_name="active hit runtime 壞資料",
            )
        )
        app_context.services.targets.restart_target_monitoring(corrupt.id)
        connection = app_context.repositories.runtime_states.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE target_runtime_state SET runtime_status = 'invalid' WHERE target_id = ?",
            (corrupt.id,),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    assert client.get(f"/api/targets/{corrupt.id}/hit-records/preview").status_code == 503
    assert client.get(f"/api/targets/{corrupt.id}/hit-records/count").status_code == 503
    assert client.get(f"/api/targets/{corrupt.id}/hit-records").status_code == 503


def test_hit_record_api_returns_503_for_corrupt_match_history_datetime(
    tmp_path: Path,
) -> None:
    """命中紀錄 mapper datetime 壞掉時，API 應回 503 而不是未處理例外。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="hit-history-datetime-corrupt",
                canonical_url="https://www.facebook.com/groups/hit-history-datetime-corrupt",
                group_name="hit history 日期壞資料",
            )
        )
    app = create_app(db_path=db_path, profile_dir=tmp_path / "profile")
    with SqliteApplicationContext(db_path) as app_context:
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=target.id,
                group_id=target.group_id,
                group_name=target.group_name,
                item_kind=ItemKind.POST,
                item_key="bad-history-datetime",
                text="日期壞掉的命中紀錄",
                include_rule="日期",
                notified_at=app.state.session_started_at + timedelta(seconds=1),
                created_at=app.state.session_started_at + timedelta(seconds=1),
            )
        )
        app_context.repositories.match_history.connection.execute(
            "UPDATE match_history SET created_at = ? WHERE target_id = ?",
            ("not-a-datetime", target.id),
        )

    client = TestClient(app)

    preview_response = client.get(f"/api/targets/{target.id}/hit-records/preview")
    count_response = client.get(f"/api/targets/{target.id}/hit-records/count")
    full_response = client.get(f"/api/targets/{target.id}/hit-records")

    assert preview_response.status_code == 503
    assert count_response.status_code == 503
    assert full_response.status_code == 503


def test_hit_record_count_ignores_other_target_corrupt_match_history_datetime(
    tmp_path: Path,
) -> None:
    """其他 target 的 hit history datetime 壞資料，不應拖垮本 target count。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        corrupt = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="corrupt-hit-history",
                canonical_url="https://www.facebook.com/groups/corrupt-hit-history",
                group_name="corrupt hit history",
            )
        )
        normal = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="normal-hit-history",
                canonical_url="https://www.facebook.com/groups/normal-hit-history",
                group_name="normal hit history",
            )
        )
    app = create_app(db_path=db_path, profile_dir=tmp_path / "profile")
    with SqliteApplicationContext(db_path) as app_context:
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=corrupt.id,
                group_id=corrupt.group_id,
                group_name=corrupt.group_name,
                item_kind=ItemKind.POST,
                item_key="other-target-bad-history-datetime",
                text="其他 target 日期壞掉的命中紀錄",
                include_rule="日期",
                notified_at=app.state.session_started_at + timedelta(seconds=1),
                created_at=app.state.session_started_at + timedelta(seconds=1),
            )
        )
        app_context.repositories.match_history.connection.execute(
            "UPDATE match_history SET created_at = ? WHERE target_id = ?",
            ("not-a-datetime", corrupt.id),
        )

    client = TestClient(app)

    response = client.get(f"/api/targets/{normal.id}/hit-records/count")

    assert response.status_code == 200
    assert response.json()["total_count"] == 0


def test_hit_record_count_returns_503_for_corrupt_match_history_notified_at(
    tmp_path: Path,
) -> None:
    """notified_at 壞掉時，count 也不可宣稱 read model 可用。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="hit-history-notified-at-corrupt",
                canonical_url="https://www.facebook.com/groups/hit-history-notified-at-corrupt",
                group_name="hit history notified_at corrupt",
            )
        )
    app = create_app(db_path=db_path, profile_dir=tmp_path / "profile")
    with SqliteApplicationContext(db_path) as app_context:
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=target.id,
                group_id=target.group_id,
                group_name=target.group_name,
                item_kind=ItemKind.POST,
                item_key="bad-history-notified-at",
                text="通知時間壞掉的命中紀錄",
                include_rule="日期",
                notified_at=app.state.session_started_at + timedelta(seconds=1),
                created_at=app.state.session_started_at + timedelta(seconds=1),
            )
        )
        app_context.repositories.match_history.connection.execute(
            "UPDATE match_history SET notified_at = ? WHERE target_id = ?",
            ("not-a-datetime", target.id),
        )

    client = TestClient(app)

    response = client.get(f"/api/targets/{target.id}/hit-records/count")

    assert response.status_code == 503


def test_hit_record_preview_count_ignores_corrupt_match_history_before_session(
    tmp_path: Path,
) -> None:
    """本次 session 前的壞 hit history row 不影響 preview/count，但 full list 仍不可讀。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="old-hit-history-datetime-corrupt",
                canonical_url="https://www.facebook.com/groups/old-hit-history-datetime-corrupt",
                group_name="old hit history datetime corrupt",
            )
        )
    app = create_app(db_path=db_path, profile_dir=tmp_path / "profile")
    with SqliteApplicationContext(db_path) as app_context:
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=target.id,
                group_id=target.group_id,
                group_name=target.group_name,
                item_kind=ItemKind.POST,
                item_key="old-bad-history-datetime",
                text="舊 session 日期壞掉的命中紀錄",
                include_rule="日期",
                notified_at=app.state.session_started_at - timedelta(seconds=1),
                created_at=app.state.session_started_at - timedelta(seconds=1),
            )
        )
        app_context.repositories.match_history.connection.execute(
            "UPDATE match_history SET created_at = ? WHERE target_id = ?",
            ("not-a-datetime", target.id),
        )

    client = TestClient(app)
    preview_response = client.get(f"/api/targets/{target.id}/hit-records/preview")
    count_response = client.get(f"/api/targets/{target.id}/hit-records/count")
    full_response = client.get(f"/api/targets/{target.id}/hit-records")

    assert preview_response.status_code == 200
    assert count_response.status_code == 200
    assert count_response.json()["total_count"] == 0
    assert full_response.status_code == 503


def test_webui_sanitizes_preview_and_hit_record_permalinks(tmp_path: Path) -> None:
    """Web read model 不把非 Facebook permalink 輸出成可點擊連結。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="第一個社團",
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="latest-unsafe",
                    item_index=0,
                    author="<b>unsafe author</b>",
                    text='<img src=x onerror="alert(1)"> unsafe latest permalink',
                    permalink="https://www.facebook.com/l.php?u=https%3A%2F%2Fevil.example",
                    matched_keyword="",
                ),
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="latest-safe-mobile",
                    item_index=1,
                    text="safe latest permalink",
                    permalink="https://m.facebook.com/groups/111/posts/2222222222",
                    matched_keyword="",
                ),
            ],
        )
    app = create_app(db_path=db_path, profile_dir=tmp_path / "profile")
    with SqliteApplicationContext(db_path) as app_context:
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=target.id,
                group_id=target.group_id,
                group_name="第一個社團",
                item_kind=ItemKind.POST,
                item_key="hit-unsafe",
                author="<script>badAuthor()</script>",
                text='<svg onload="alert(1)"></svg> unsafe hit permalink',
                permalink="javascript:alert(1)",
                include_rule="unsafe",
                notified_at=app.state.session_started_at + timedelta(seconds=1),
                created_at=app.state.session_started_at + timedelta(seconds=1),
            )
        )

    client = TestClient(app)
    preview_payload = client.get(f"/api/targets/{target.id}/hit-records/preview").json()
    full_payload = client.get(f"/api/targets/{target.id}/hit-records").json()
    card_payload = client.get(f"/api/targets/{target.id}/card").json()

    assert preview_payload["items"][0]["permalink"] == ""
    assert full_payload["items"][0]["permalink"] == ""
    assert "javascript:alert" not in card_payload["hit_record_preview_html"]
    assert "l.php" not in card_payload["latest_scan_preview_html"]
    assert "https://evil.example" not in card_payload["latest_scan_preview_html"]
    assert "<img" not in card_payload["latest_scan_preview_html"]
    assert "&lt;img" in card_payload["latest_scan_preview_html"]
    assert "<script>" not in card_payload["hit_record_preview_html"]
    assert "&lt;script&gt;" in card_payload["hit_record_preview_html"]
    assert "<svg" not in card_payload["hit_record_preview_html"]
    assert "&lt;svg" in card_payload["hit_record_preview_html"]
    assert (
        "https://www.facebook.com/groups/111/posts/2222222222"
        in card_payload["latest_scan_preview_html"]
    )


def test_hit_record_preview_splits_multiple_matched_keyword_badges(tmp_path: Path) -> None:
    """命中紀錄 preview 會把多組命中 keyword 拆成多個 badge 並全部高亮。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
    app = create_app(db_path=db_path, profile_dir=tmp_path / "profile")
    with SqliteApplicationContext(db_path) as app_context:
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=target.id,
                group_id=target.group_id,
                group_name=target.group_name,
                item_kind=ItemKind.POST,
                item_key="multi-keyword",
                author="王小明",
                text="售6/5,6/6的票各一張",
                include_rule="6/5;6/6",
                notified_at=app.state.session_started_at + timedelta(seconds=1),
                created_at=app.state.session_started_at + timedelta(seconds=1),
            )
        )

    client = TestClient(app)
    response = client.get(f"/api/targets/{target.id}/hit-records/preview")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["badge_text"] == "命中: 6/5;6/6"
    assert item["badge_labels"] == ["命中: 6/5", "命中: 6/6"]
    assert {"text": "6/5", "highlighted": True} in item["content_segments"]
    assert {"text": "6/6", "highlighted": True} in item["content_segments"]


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
        app_context.repositories.scan_scope_state.mark_initialized(target.scope_id)
        app_context.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:persisted-1:ntfy",
                target_id=target.id,
                item_key="persisted-1",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
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
        assert app_context.repositories.seen_items.has_seen(target.scope_id, "persisted-1")
        assert app_context.repositories.scan_scope_state.is_initialized(target.scope_id)
        assert (
            app_context.repositories.notification_outbox.get_by_idempotency_key(
                f"{target.id}:persisted-1:ntfy"
            )
            is not None
        )


def test_index_shows_latest_items_up_to_target_max_items(tmp_path: Path) -> None:
    """右側最近掃描項目顯示上限會跟 target max_items_per_scan 對齊。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(max_items_per_scan=7),
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
