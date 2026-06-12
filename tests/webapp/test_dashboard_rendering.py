"""Dashboard rendering tests。"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.scan_recording_service import RecordScanRequest
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.scan_failures import CONTENT_UNAVAILABLE_REASON
from facebook_monitor.webapp.app import create_app
from tests.helpers.webapp import render_seeded_index
from tests.helpers.webapp import seed_dashboard_index_target


def test_index_renders_target_identity_status_and_actions(tmp_path: Path) -> None:
    """首頁 target card 顯示清理後名稱、狀態與主要操作契約。"""

    text, target_id = render_seeded_index(tmp_path)

    assert "測試社團" in text
    assert "(3) 測試社團" not in text
    assert "222518561920110" in text
    assert "group=222518561920110" not in text
    assert "scope=222518561920110" not in text
    assert "已停止" in text
    assert "最近掃描" in text
    assert "命中紀錄 0" in text
    assert f'data-hit-records-modal="{target_id}"' in text
    assert f'data-clear-hit-records data-target-id="{target_id}"' in text
    assert "/static/dashboard/main.js" in text
    assert "查看紀錄" in text
    assert "設定" in text
    assert "監看清單快速跳轉" in text
    assert f'data-sidebar-target="target-{target_id}"' in text
    assert f'data-action-anchor="target-{target_id}"' in text
    assert f'id="target-{target_id}"' in text
    assert f'value="#target-{target_id}"' not in text
    assert "啟動自動掃描" not in text
    assert "停止自動掃描" not in text


def test_index_renders_latest_preview_hits_and_highlights(tmp_path: Path) -> None:
    """首頁顯示最近掃描 preview、命中狀態與 keyword highlight。"""

    text, _target_id = render_seeded_index(tmp_path)

    assert "最近掃描貼文" not in text
    assert "貼文模式" in text
    assert "下次刷新：未排程" in text
    assert "本輪：已完成深度掃描" in text
    assert "最近通知" not in text
    assert "ntfy: sent" not in text
    assert "王小明" in text
    assert "命中: 票券" in text
    assert "陳小華" in text
    assert "未命中" in text
    assert "未取得連結" in text
    assert "這是一篇有票券關鍵字的貼文" in text
    assert '這是一篇有<mark class="keyword-highlight">票券</mark>關鍵字的貼文' in text
    assert "開啟連結" in text


def test_index_header_omits_latest_notification_status_by_channel(tmp_path: Path) -> None:
    """首頁 header 不顯示最近通知，避免擠壓模式與刷新資訊。"""

    db_path = tmp_path / "app.db"
    target = seed_dashboard_index_target(db_path)
    with SqliteApplicationContext(db_path) as app_context:
        app_context.repositories.notification_events.add(
            NotificationEvent(
                target_id=target.id,
                item_key="item-1",
                channel=NotificationChannel.DESKTOP,
                status=NotificationStatus.FAILED,
                message="desktop_failed",
            )
        )
        app_context.repositories.notification_events.add(
            NotificationEvent(
                target_id=target.id,
                item_key="item-1",
                channel=NotificationChannel.DISCORD,
                status=NotificationStatus.SENT,
                message="discord_sent",
            )
        )

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            enforce_csrf=False,
        )
    )
    response = client.get("/")

    assert response.status_code == 200
    assert "最近通知 桌面 failed / ntfy sent / Discord sent" not in response.text
    assert "貼文模式" in response.text
    assert "最近掃描" in response.text
    assert "下次刷新：未排程" in response.text


def test_index_renders_scan_diagnostics_without_legacy_debug_json(
    tmp_path: Path,
) -> None:
    """首頁掃描診斷在更多選單 modal 內可複製，但不回到舊 debug_json 區塊。"""

    text, _target_id = render_seeded_index(tmp_path)

    assert "掃描診斷" in text
    assert "data-scan-diagnostics-button" in text
    assert "data-scan-diagnostics-modal" in text
    assert "rounds=1 · candidates=2 · stop=完成捲動輪數" in text
    assert "collection_strategy=feed_visible_window" in text
    assert "round=0 raw=2 unique=2" in text
    assert "複製掃描診斷" in text
    assert "最近有錯誤" in text
    assert "latest_scan_items:" in text
    assert "textSource=primary" in text
    assert "expandCount=1" in text
    assert "linkDiagnostics=" in text
    assert "debug_json=" not in text
    assert "https://www.facebook.com/groups/1/user/2" in text
    assert '<details class="debug-details scan-debug-details">' not in text


def test_index_renders_content_unavailable_alert(tmp_path: Path) -> None:
    """Facebook 內容不可見時，卡片與收合摘要顯示連結已失效。"""

    db_path = tmp_path / "app.db"
    target = seed_dashboard_index_target(db_path)
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.FAILED,
                error_message=(
                    "content_unavailable: Facebook content is unavailable or no "
                    "longer visible."
                ),
                metadata={
                    "reason": CONTENT_UNAVAILABLE_REASON,
                    "worker": "resident_main",
                    "target_kind": "posts",
                    "retryable": False,
                },
            )
        )
        app_context.services.targets.restart_target_monitoring(target.id)
        app_context.services.targets.mark_target_error(
            target.id,
            "content_unavailable: Facebook content is unavailable or no longer visible.",
        )

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            enforce_csrf=False,
        )
    )
    response = client.get("/")

    assert response.status_code == 200
    assert "連結已失效" in response.text
    assert "連結已失效：Facebook 顯示目前無法查看此內容，可能已刪除或權限變更。" in response.text
    assert (
        '<div class="runtime-error" data-runtime-error >'
        "連結已失效：Facebook 顯示目前無法查看此內容，可能已刪除或權限變更。</div>"
    ) in response.text
    assert (
        '<div class="runtime-error" data-runtime-error >'
        "content_unavailable: Facebook content is unavailable"
    ) not in response.text
    assert 'data-sidebar-status-detail="連結已失效"' in response.text
    assert 'data-latest-error-kind="content-unavailable"' in response.text
    assert "下次刷新：未排程" in response.text
    assert "Facebook 顯示目前無法查看此內容" in response.text
    assert "status=failed · reason=連結已失效" in response.text
    assert "failure_reason=連結已失效" in response.text


def test_index_keeps_internal_scheduler_and_old_debug_ui_hidden(
    tmp_path: Path,
) -> None:
    """首頁不暴露內部 scheduler 狀態、舊 debug UI 或 secret masking 控制。"""

    text, _target_id = render_seeded_index(tmp_path)

    assert "背景掃描服務" not in text
    assert "running=1 · queued=0 · slots=2" not in text
    assert "除錯" not in text
    assert "複製除錯資訊" not in text
    assert "監視中" not in text
    assert "掃描一次" not in text


def test_index_masks_saved_notification_secrets_in_target_settings(
    tmp_path: Path,
) -> None:
    """首頁 target 設定 modal 不把已保存 notification secret 原文送到前端。"""

    db_path = tmp_path / "app.db"
    ntfy_topic = "private-topic"
    discord_webhook = "https://discord.com/api/webhooks/1234567890/private-token"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="測試社團",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic=ntfy_topic,
                    enable_discord_notification=True,
                    discord_webhook=discord_webhook,
                ),
            )
        )

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            enforce_csrf=False,
        )
    )
    response = client.get("/")

    assert response.status_code == 200
    assert ntfy_topic not in response.text
    assert discord_webhook not in response.text
    assert 'name="ntfy_topic" type="text" value=""' in response.text
    assert 'name="discord_webhook" type="text" value=""' in response.text
    assert 'name="ntfy_topic_keep" type="hidden" value="on"' in response.text
    assert 'name="discord_webhook_keep" type="hidden" value="on"' in response.text
    assert "data-secret-clear-button" in response.text


def test_index_renders_target_settings_summary_and_collapse_controls(
    tmp_path: Path,
) -> None:
    """首頁 target card 保留設定摘要、keyword tabs 與 icon-only collapse controls。"""

    text, _target_id = render_seeded_index(tmp_path)

    assert "最新排序" in text
    assert "data-dirty-submit" in text
    assert 'class="collapse-toggle"' in text
    assert 'aria-label="收合 target"' in text
    assert 'class="collapse-toggle-icon"' in text
    assert (
        '<dl class="target-collapsed-summary field-grid field-grid--summary" '
        "data-collapsed-summary hidden>"
    ) in text
    assert "target-collapsed-summary-field" in text
    assert "關鍵字 1" in text
    assert "關鍵字 2" in text
    assert "關鍵字 3" in text
    assert "data-include-keyword-help-button" in text
    assert "data-include-keyword-help-modal" in text
    assert "關鍵字輸入規則</h3>" in text
    assert "<code>;</code> 表示 <strong>OR</strong>" in text
    assert "空格表示 <strong>AND</strong>" in text
    assert "排除關鍵字" in text
    assert "排除字忽略片語" in text
    assert "設定摘要" in text
    assert "data-keyword-tabs" in text
    assert 'data-keyword-tab="exclude"' in text
    assert 'data-keyword-tab="ignore"' in text
    assert "data-keyword-help-button" in text
    assert "data-keyword-help-modal" in text
    assert "排除字忽略片語</h3>" in text
    assert "避免排除關鍵字誤判。" in text
    assert "系統會先保護忽略片語，再檢查排除關鍵字。" in text
    assert "收一張票" in text
    assert "售一張票，贈品回收" in text
    assert "預設忽略片語" not in text
    assert 'data-keyword-panel="ignore" hidden' in text
    assert '</div>\n\n  <div class="target-footer-controls">' in text
    assert 'placeholder="例如：全收;回收"' in text
    assert ">收合</button>" not in text


def test_index_renders_sidebar_without_targets(tmp_path: Path) -> None:
    """無 target 時仍保留 sidebar，讓新增入口位置穩定。"""

    db_path = tmp_path / "app.db"
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.get("/")

    assert response.status_code == 200
    assert 'class="shell has-sidebar"' in response.text
    assert "監看清單快速跳轉" in response.text
    assert "尚未建立 target" in response.text
    assert "新增 Facebook target" in response.text
    assert "建立後按「開始」才會掃描" in response.text


def test_index_renders_sidebar_group_monitoring_without_target_count(
    tmp_path: Path,
) -> None:
    """sidebar 群組 header 不顯示 target 數量，並顯示群組開始/停止 icon。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="第一群 target",
            )
        )
        empty_group = app_context.services.sidebar_layout.create_group("空群組")
        active_group = app_context.services.sidebar_layout.create_group("啟用群組")
        app_context.services.sidebar_layout.save_placements(
            [
                (active_group.id, [first.id]),
                (empty_group.id, []),
                (None, []),
            ]
        )
        app_context.services.targets.restart_target_monitoring(first.id)

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "sidebar-group-count" not in response.text
    assert 'data-sidebar-group-monitoring="stop"' in response.text
    assert 'aria-label="停止群組"' in response.text
    assert 'data-sidebar-group-monitoring="start"' in response.text
    assert 'aria-label="開始群組"' in response.text
    assert "disabled" in response.text
    assert re.search(
        rf'name="refresh_mode_{re.escape(active_group.id)}" type="radio" value="floating"[^>]*checked',
        response.text,
    )
