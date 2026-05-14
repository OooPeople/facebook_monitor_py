"""Dashboard rendering tests。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationStatus
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
    assert "最近通知" in text
    assert "ntfy: sent" not in text
    assert "王小明" in text
    assert "命中: 票券" in text
    assert "陳小華" in text
    assert "未命中" in text
    assert "未取得連結" in text
    assert "這是一篇有票券關鍵字的貼文" in text
    assert '這是一篇有<mark class="keyword-highlight">票券</mark>關鍵字的貼文' in text
    assert "開啟連結" in text


def test_index_renders_latest_notification_status_by_channel(tmp_path: Path) -> None:
    """首頁最近通知會顯示各通道最新狀態，不只顯示最後一筆事件。"""

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
    assert "最近通知 桌面 failed / ntfy sent / Discord sent" in response.text


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


def test_index_keeps_internal_scheduler_and_old_debug_ui_hidden(
    tmp_path: Path,
) -> None:
    """首頁不暴露內部 scheduler 狀態、舊 debug UI 或 secret masking 控制。"""

    text, _target_id = render_seeded_index(tmp_path)

    assert "背景掃描服務" not in text
    assert "running=1 · queued=0 · slots=2" not in text
    assert "data-secret-input" not in text
    assert "除錯" not in text
    assert "複製除錯資訊" not in text
    assert "監視中" not in text
    assert "掃描一次" not in text


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
    assert "包含關鍵字" in text
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
