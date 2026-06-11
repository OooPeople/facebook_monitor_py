"""FastAPI Web UI tests。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import TargetConfigPatch
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.application.services import RecordScanRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.scan_failures import CONTENT_UNAVAILABLE_REASON
from facebook_monitor.persistence.repositories.targets import TargetRepository
from facebook_monitor.persistence.repositories.app_settings import ProfileSessionState
from facebook_monitor.webapp.query_service import get_dashboard_view
from facebook_monitor.webapp.assets import ASSET_VERSION
from tests.helpers.webapp import FakeSchedulerManager


from tests.webapp.app_test_helpers import create_app


def test_index_and_partial_payload_show_profile_needs_login_warning(
    tmp_path: Path,
) -> None:
    """Facebook session 失效時，首頁與 partial update 都帶右上角警告。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        status = app_context.repositories.app_settings.mark_profile_needs_login(
            reason="login_required",
            source="resident_main",
        )
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    index_response = client.get("/")
    cards_response = client.get("/api/dashboard-cards")

    assert status.state == ProfileSessionState.NEEDS_LOGIN
    assert index_response.status_code == 200
    assert "Facebook 需要重新登入" in index_response.text
    payload = cards_response.json()
    warning = payload["profile_session_warning"]
    assert warning["needs_login"] is True
    assert warning["reason"] == "login_required"
    assert "重新開啟程式" in warning["message"]


def test_index_and_partial_payload_show_database_invariant_warning(
    tmp_path: Path,
) -> None:
    """污染資料只顯示 invariant 警告與支援包提示，不在 read path 靜默修復。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="corrupt",
                canonical_url="https://www.facebook.com/groups/corrupt",
                group_name="異常測試社團",
            )
        )
        connection = app_context.repositories.configs.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE target_configs SET auto_load_more = 2 WHERE target_id = ?",
            (target.id,),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    index_response = client.get("/")
    cards_response = client.get("/api/dashboard-cards")

    assert index_response.status_code == 200
    assert "異常測試社團" in index_response.text
    assert "資料庫偵測到 1 個資料 invariant 異常" in index_response.text
    assert "設定下載支援包" in index_response.text
    payload = cards_response.json()
    warning = payload["database_invariant_warning"]
    assert payload["dashboard_degraded"] is False
    assert warning["has_violations"] is True
    assert warning["violation_count"] == 1
    assert warning["tables"] == ["target_configs"]
    assert "系統不會自動修復資料" in warning["message"]
    assert target.id not in warning["message"]
    assert target.group_id not in warning["message"]


def test_database_invariant_warning_degrades_mapper_breaking_rows_without_ids(
    tmp_path: Path,
) -> None:
    """mapper 無法載入的壞 enum row 不可讓 dashboard 500 或洩漏 row id。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="mapper-corrupt",
                canonical_url="https://www.facebook.com/groups/mapper-corrupt",
                group_name="mapper 異常測試社團",
            )
        )
        connection = app_context.repositories.targets.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE targets SET target_kind = 'invalid-kind' WHERE id = ?",
            (target.id,),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    index_response = client.get("/")
    cards_response = client.get("/api/dashboard-cards")

    assert index_response.status_code == 200
    assert "資料庫偵測到 1 個資料 invariant 異常" in index_response.text
    assert "資料暫時無法載入" in index_response.text
    assert "目前沒有 target" not in index_response.text
    payload = cards_response.json()
    warning = payload["database_invariant_warning"]
    assert payload["dashboard_degraded"] is True
    assert warning["has_violations"] is True
    assert warning["violation_count"] == 1
    assert warning["tables"] == ["targets"]
    assert "系統不會自動修復資料" in warning["message"]
    assert target.id not in warning["message"]
    assert target.group_id not in warning["message"]
    assert payload["cards"] == []


def test_database_invariant_warning_does_not_degrade_unrelated_value_error(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """即使 DB 有 invariant violation，非 enum mapper 錯誤仍不可被降級吞掉。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="mapper-corrupt",
                canonical_url="https://www.facebook.com/groups/mapper-corrupt",
                group_name="mapper 異常測試社團",
            )
        )
        connection = app_context.repositories.targets.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE targets SET target_kind = 'invalid-kind' WHERE id = ?",
            (target.id,),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    def raise_unrelated_value_error(self: TargetRepository) -> list[object]:
        raise ValueError("'x' is not a valid SurpriseBug")

    monkeypatch.setattr(TargetRepository, "list_all", raise_unrelated_value_error)

    with pytest.raises(ValueError, match="SurpriseBug"):
        get_dashboard_view(db_path)


def test_dashboard_uses_external_versioned_scripts_without_importmap(tmp_path: Path) -> None:
    """Dashboard HTML 不再需要 inline importmap，入口 script 仍保留版本 key。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.get("/")

    assert response.status_code == 200
    assert '<script type="importmap">' not in response.text
    assert f"/static/dashboard/main.js?v={ASSET_VERSION}" in response.text
    assert '<script id="page-feedback" type="application/json">' not in response.text


def test_target_card_panels_share_preview_height_contract() -> None:
    """Target card 左右 panel 必須共用高度約束，避免底部錯位回歸。"""

    styles = Path("src/facebook_monitor/webapp/static/styles/target-card.css").read_text(
        encoding="utf-8"
    )

    assert "grid-auto-rows: var(--preview-panel-height);" in styles
    assert ".target-settings" in styles
    assert ".match-panel" in styles
    assert ".section-title .form-status" in styles
    assert "overflow-y: auto;" in styles
    assert ".compact-config-form .keyword-rule-tabs" in styles
    assert ".keyword-rule-field-label" in styles
    assert ".keyword-rule-tab-row" in styles
    assert ".compact-config-form .keyword-rule-tab" in styles
    assert ".keyword-help-button" in styles
    assert ".more-menu-trigger" in styles
    more_trigger_rule = styles.split(".more-menu-trigger {", 1)[1].split("}", 1)[0]
    assert "color: var(--text-soft);" in more_trigger_rule
    assert "list-style: none;" in more_trigger_rule
    for duplicated_button_property in ("border-radius:", "min-height:", "min-width:", "padding:"):
        assert duplicated_button_property not in more_trigger_rule
    assert ".menu-panel form" in styles
    assert ".menu-action" in styles
    assert ".compact-config-form .keyword-rule-panel[hidden]" in styles
    assert styles.count("height: var(--preview-panel-height);") >= 2
    assert styles.count("max-height: var(--preview-panel-height);") >= 2


def test_settings_keyword_defaults_use_compact_two_column_layout() -> None:
    """設定頁關鍵字預設值維持左右雙欄，textarea 不提供拖曳縮放。"""

    forms_css = Path("src/facebook_monitor/webapp/static/styles/forms.css").read_text(
        encoding="utf-8"
    )
    pages_css = Path("src/facebook_monitor/webapp/static/styles/pages.css").read_text(
        encoding="utf-8"
    )
    diagnostics_css = Path("src/facebook_monitor/webapp/static/styles/diagnostics.css").read_text(
        encoding="utf-8"
    )

    assert "textarea {\n  resize: none;\n}" in forms_css
    assert (
        ".settings-form-grid--two {\n  grid-template-columns: repeat(2, minmax(0, 1fr));\n}"
        in forms_css
    )
    assert ".settings-actions--right" in forms_css
    assert ".settings-actions--left" in forms_css
    assert "  .settings-form-grid--two {\n    grid-template-columns: 1fr;\n  }" in pages_css
    assert ".debug-copy-source {\n  min-height: 150px;\n  resize: none;" in diagnostics_css


def test_index_renders_runtime_state_and_error(tmp_path: Path) -> None:
    """首頁會顯示 scheduler runtime state 與 last error。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        running_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="掃描測試社團",
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
        idle_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="444",
                canonical_url="https://www.facebook.com/groups/444",
                group_name="啟用等待社團",
            )
        )
        app_context.services.targets.restart_target_monitoring(running_target.id)
        app_context.services.targets.mark_target_running(running_target.id, "worker-1")
        app_context.services.targets.restart_target_monitoring(error_target.id)
        app_context.services.targets.mark_target_error(error_target.id, "login_required: 需要登入")
        app_context.services.targets.pause_target_monitoring(stopped_target.id)
        app_context.services.targets.restart_target_monitoring(idle_target.id)

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "已啟用" in response.text
    assert "掃描中" in response.text
    assert "錯誤" in response.text
    assert "需要重新登入" in response.text
    assert "Facebook 要求重新登入" in response.text
    assert "login_required: 需要登入" not in response.text
    assert "已停止" in response.text
    assert "閒置" not in response.text
    assert "執行中" not in response.text


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
        app_context.services.targets.restart_target_monitoring(target.id)
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


def test_dashboard_card_payload_labels_content_unavailable_failure(
    tmp_path: Path,
) -> None:
    """dashboard card partial payload 會保留連結失效警示，避免刷新後退回泛用錯誤。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="測試社團",
            )
        )
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.FAILED,
                error_message=(
                    "content_unavailable: Facebook content is unavailable or no longer visible."
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

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/api/dashboard-cards")

    assert response.status_code == 200
    card_payload = response.json()["cards"][0]
    assert card_payload["has_latest_failed_scan"] is True
    assert card_payload["latest_error_indicator_label"] == "連結已失效"
    assert card_payload["latest_error_indicator_kind"] == "content-unavailable"
    assert card_payload["status_label"] == "錯誤"
    assert (
        card_payload["runtime_error"]
        == "連結已失效：Facebook 顯示目前無法查看此內容，可能已刪除或權限變更。"
    )
    assert card_payload["next_refresh_label"] == "下次刷新：未排程"
    assert "Facebook 顯示目前無法查看此內容" in card_payload["latest_error_indicator_title"]
    assert "status=failed · reason=連結已失效" in card_payload["latest_scan_diagnostics_summary"]
    assert "failure_reason=連結已失效" in card_payload["latest_scan_diagnostics_text"]
    assert "連結已失效" in card_payload["card_summary_html"]


def test_dashboard_card_payload_does_not_keep_content_unavailable_after_success(
    tmp_path: Path,
) -> None:
    """連結失效後若已有更新成功掃描，不應繼續顯示目前連結已失效。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="測試社團",
            )
        )
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.FAILED,
                error_message=(
                    "content_unavailable: Facebook content is unavailable or no longer visible."
                ),
                metadata={
                    "reason": CONTENT_UNAVAILABLE_REASON,
                    "worker": "resident_main",
                    "target_kind": "posts",
                    "retryable": False,
                },
            )
        )
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
                item_count=1,
                matched_count=0,
                metadata={
                    "worker": "posts_scan",
                    "collection_strategy": "feed_visible_window",
                    "candidate_count": 1,
                    "round_count": 1,
                    "stop_reason": "target_count_reached",
                },
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/api/dashboard-cards")

    assert response.status_code == 200
    card_payload = response.json()["cards"][0]
    assert card_payload["has_latest_failed_scan"] is True
    assert card_payload["latest_error_indicator_label"] == "最近有錯誤"
    assert card_payload["latest_error_indicator_kind"] == "error"
    assert "曾偵測到連結失效" in card_payload["card_summary_html"]
    assert (
        "status=failed · reason=連結已失效" not in card_payload["latest_scan_diagnostics_summary"]
    )


def test_dashboard_card_payload_localizes_page_load_timeout_errors(
    tmp_path: Path,
) -> None:
    """Playwright raw navigation error 不應直接出現在 dashboard payload。"""

    db_path = tmp_path / "app.db"
    raw_error = (
        "page_load_timeout: Page.evaluate: Execution context was destroyed, "
        "most likely because of a navigation."
    )
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="測試社團",
            )
        )
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.FAILED,
                error_message=raw_error,
                metadata={
                    "reason": "page_load_timeout",
                    "worker": "resident_main",
                    "target_kind": "posts",
                    "retryable": False,
                },
            )
        )
        app_context.services.targets.restart_target_monitoring(target.id)
        app_context.services.targets.mark_target_error(target.id, raw_error)

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/api/dashboard-cards")

    assert response.status_code == 200
    card_payload = response.json()["cards"][0]
    assert "頁面載入逾時" in card_payload["runtime_error"]
    assert "頁面載入逾時" in card_payload["latest_error_indicator_title"]
    assert "error=頁面載入逾時" in card_payload["latest_scan_diagnostics_text"]
    assert "Page.evaluate" not in card_payload["runtime_error"]
    assert "Execution context was destroyed" not in card_payload["latest_error_indicator_title"]
    assert "most likely because of a navigation" not in card_payload["latest_scan_diagnostics_text"]


def test_dashboard_card_payload_shows_retrying_page_load_timeout(
    tmp_path: Path,
) -> None:
    """未達上限的 page_load_timeout 只顯示將重試，不顯示 runtime error。"""

    db_path = tmp_path / "app.db"
    raw_error = (
        "page_load_timeout: Page.evaluate: Execution context was destroyed, "
        "most likely because of a navigation."
    )
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="測試社團",
            )
        )
        app_context.services.targets.restart_target_monitoring(target.id)
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.FAILED,
                error_message=raw_error,
                metadata={
                    "reason": "page_load_timeout",
                    "worker": "resident_main",
                    "target_kind": "posts",
                    "retryable": True,
                    "runtime_action": "will_retry",
                    "retry_streak": 1,
                    "retry_limit": 3,
                },
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/api/dashboard-cards")

    assert response.status_code == 200
    card_payload = response.json()["cards"][0]
    assert card_payload["runtime_error"] == ""
    assert card_payload["latest_error_indicator_label"] == "將重試"
    assert card_payload["latest_error_indicator_kind"] == "retrying"
    assert "1/3" in card_payload["latest_error_indicator_title"]
    assert "頁面載入逾時" in card_payload["latest_error_indicator_title"]
    assert "retryable=True" in card_payload["latest_scan_diagnostics_text"]
    assert "runtime_action=will_retry" in card_payload["latest_scan_diagnostics_text"]
    assert "retry_streak=1" in card_payload["latest_scan_diagnostics_text"]
    assert "Page.evaluate" not in card_payload["latest_error_indicator_title"]
    assert "Execution context was destroyed" not in card_payload["latest_scan_diagnostics_text"]


def test_dashboard_view_model_includes_sidebar_preview_and_settings_summary(
    tmp_path: Path,
) -> None:
    """dashboard read model 會帶入 sidebar、hit preview 與設定摘要。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="測試社團",
                config=TargetConfigPatch(
                    fixed_refresh_sec=None,
                    min_refresh_sec=25,
                    max_refresh_sec=35,
                    jitter_enabled=True,
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
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
                    display_text="最近掃描內容\n第二行",
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
                display_text="歷史命中內容\n第二行",
                include_rule="票券",
            )
        )

    dashboard = get_dashboard_view(db_path)
    row = dashboard.rows[0]
    latest_preview = row.latest_scan_preview_rows[0]
    hit_preview = row.hit_record_preview_rows[0]

    assert dashboard.sidebar_items[0].display_name == "測試社團"
    assert dashboard.sidebar_items[0].mode_label == "貼文"
    assert dashboard.sidebar_items[0].mode_class == "posts"
    assert dashboard.sidebar_items[0].hit_count == 1
    assert "命中 1 筆" in dashboard.sidebar_items[0].status_summary
    assert row.hit_record_total_count == 1
    assert row.hit_records_heading == "命中紀錄（1）"
    assert row.settings_summary.lines[0].icon_key == "refresh"
    assert row.settings_summary.lines[0].label == "刷新"
    assert row.settings_summary.lines[0].value == "浮動 25-35 秒"
    assert row.settings_summary.lines[-1].icon_key == "notification"
    assert row.settings_summary.lines[-1].label == "通知"
    assert row.settings_summary.lines[-1].value == "ntfy"
    assert latest_preview.author_name == "王小明"
    assert latest_preview.badge_kind == "hit"
    assert latest_preview.content_preview == "最近掃描內容 第二行"
    assert latest_preview.link_label == "開啟連結"
    assert not latest_preview.has_debug
    assert hit_preview.author_name == "陳小華"
    assert hit_preview.badge_text == "命中: 票券"
    assert hit_preview.content_preview == "歷史命中內容 第二行"
    assert hit_preview.link_label == "開啟連結"
    assert not hit_preview.has_debug

    response = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile")).get("/")
    assert response.status_code == 200
    assert "最近掃描" in response.text
    assert "命中紀錄 0" in response.text
    assert "最近掃描內容 第二行" in response.text


def test_dashboard_partial_payload_changes_sidebar_layout_signature_for_groups(
    tmp_path: Path,
) -> None:
    """dashboard partial payload 需帶 group/order 簽章，讓前端遇到結構變更時 reload。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="第一個社團",
            )
        )
        second = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
                group_name="第二個社團",
            )
        )
        group = app_context.services.sidebar_layout.create_group("工作")
        app_context.services.sidebar_layout.save_placements(
            [(group.id, [first.id]), (None, [second.id])]
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    page_response = client.get("/")
    first_payload = client.get("/api/dashboard-cards").json()
    first_signature = first_payload["sidebar"]["layout_signature"]

    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.sidebar_layout.save_placements(
            [(group.id, [first.id, second.id]), (None, [])]
        )
    second_payload = client.get("/api/dashboard-cards").json()
    second_signature = second_payload["sidebar"]["layout_signature"]

    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.sidebar_layout.rename_group(group.id, "重新命名")
    renamed_payload = client.get("/api/dashboard-cards").json()

    assert page_response.status_code == 200
    assert f'data-sidebar-layout-signature="{first_signature}"' in page_response.text
    assert first_signature
    assert second_signature != first_signature
    assert renamed_payload["sidebar"]["layout_signature"] != second_signature
    assert [item["target_id"] for item in second_payload["sidebar"]["items"]] == [
        first.id,
        second.id,
    ]


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
    assert "監視項目已在掃描中，本輪排程已略過。" in response.text
    assert "scan_guard_skipped: target_already_running" not in response.text
