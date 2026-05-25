"""Dashboard static module contract tests。"""

from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from facebook_monitor.webapp.assets import ASSET_VERSION
from facebook_monitor.webapp.assets import DASHBOARD_MODULE_FILENAMES
from facebook_monitor.webapp.assets import build_dashboard_module_imports


def _css_rule_body(css: str, selector: str) -> str:
    """擷取單一 selector 規則內容，讓樣式契約測試只檢查局部宣告。"""

    return css.split(f"{selector} {{", 1)[1].split("}", 1)[0]


def _input_tags(template: str, field_name: str) -> list[str]:
    """回傳指定 name 的 input tags，供靜態模板契約測試檢查屬性。"""

    return re.findall(
        rf'<input\b(?=[^>]*\bname="{re.escape(field_name)}")[^>]*>',
        template,
    )


def test_dashboard_import_map_covers_all_dashboard_modules() -> None:
    """新增 dashboard ES module 時必須進 import map，避免子模組快取漂移。"""

    dashboard_dir = Path("src/facebook_monitor/webapp/static/dashboard")
    module_filenames = tuple(sorted(path.name for path in dashboard_dir.glob("*.js")))

    assert tuple(sorted(DASHBOARD_MODULE_FILENAMES)) == module_filenames

    import_map = build_dashboard_module_imports()
    dashboard_imports = {
        f"/static/dashboard/{filename}" for filename in DASHBOARD_MODULE_FILENAMES
    }
    assert dashboard_imports.issubset(set(import_map))
    assert "/static/vendor/sortablejs/sortable.esm.js" in import_map
    assert all(value.endswith(f"?v={ASSET_VERSION}") for value in import_map.values())


def test_dirty_form_helper_uses_form_elements_for_external_form_controls() -> None:
    """dirty helper 必須支援 modal 內用 form= 綁定的外部欄位。"""

    utils_js = Path("src/facebook_monitor/webapp/static/dashboard/utils.js").read_text(
        encoding="utf-8"
    )

    assert "export const getFormControls" in utils_js
    assert "form.elements" in utils_js
    assert "getFormControls(form).forEach" in utils_js
    assert "statusElements = []" in utils_js
    assert "allStatusElements.forEach" in utils_js
    assert "controlSignature" in utils_js
    assert "onDirtyChange(dirty)" in utils_js
    assert "[data-dirty-submit]" in utils_js
    assert "data-dirty-status-for" in Path(
        "src/facebook_monitor/webapp/static/dashboard/forms.js"
    ).read_text(encoding="utf-8")


def test_masked_secret_clear_buttons_are_loaded_by_form_pages() -> None:
    """notification secret 清除按鈕必須接到正式頁面入口與 dirty state。"""

    dashboard_dir = Path("src/facebook_monitor/webapp/static/dashboard")
    forms_js = (dashboard_dir / "forms.js").read_text(encoding="utf-8")
    forms_css = Path("src/facebook_monitor/webapp/static/styles/forms.css").read_text(
        encoding="utf-8"
    )
    notification_fields = Path(
        "src/facebook_monitor/webapp/templates/_notification_settings_fields.html"
    ).read_text(encoding="utf-8")

    assert "export const setupSecretClearButtons" in forms_js
    assert "[data-secret-clear-button]" in forms_js
    assert "data-secret-clear-input" in notification_fields
    assert "name=\"clear_ntfy_topic\"" in notification_fields
    assert "name=\"clear_discord_webhook\"" in notification_fields
    assert 'name="ntfy_topic" type="text"' in notification_fields
    assert 'name="discord_webhook" type="text"' in notification_fields
    assert 'name="ntfy_topic" type="password"' not in notification_fields
    assert 'name="discord_webhook" type="password"' not in notification_fields
    assert "清除已保存 Discord webhook" not in notification_fields
    assert ".secret-input-row [data-secret-input]" in forms_css
    clear_button_rule = forms_css.split(".secret-clear-button {", 1)[1].split("}", 1)[0]
    assert "color: var(--text);" in clear_button_rule
    assert "color: var(--danger);" not in clear_button_rule
    for filename in ("main.js", "new_target.js"):
        text = (dashboard_dir / filename).read_text(encoding="utf-8")
        assert '"/static/dashboard/forms.js"' in text
        assert "setupSecretClearButtons();" in text


def test_clear_feedback_params_removes_stable_feedback_code() -> None:
    """one-shot feedback code 顯示後需從 URL 清除，避免 reload 重播舊提示。"""

    utils_js = Path("src/facebook_monitor/webapp/static/dashboard/utils.js").read_text(
        encoding="utf-8"
    )

    assert "pageFeedback.feedback" in utils_js
    assert 'url.searchParams.delete("feedback")' in utils_js


def test_theme_toggle_contract_is_loaded_by_all_page_entrypoints() -> None:
    """Theme toggle module 必須走 DB-backed API 並被三個正式頁面入口載入。"""

    dashboard_dir = Path("src/facebook_monitor/webapp/static/dashboard")
    theme_js = (dashboard_dir / "theme.js").read_text(encoding="utf-8")

    assert 'fetch("/settings/theme"' in theme_js
    assert '"/static/dashboard/csrf.js"' in theme_js
    assert "csrfHeaders" in theme_js
    assert 'document.documentElement.dataset.theme' in theme_js
    assert "[data-theme-toggle]" in theme_js
    assert 'return "dark";' in theme_js
    assert 'lightIcon?.toggleAttribute("hidden", isDark)' in theme_js
    assert 'darkIcon?.toggleAttribute("hidden", !isDark)' in theme_js
    assert "prefers-color-scheme" not in theme_js
    assert "localStorage" not in theme_js
    assert "document.cookie" not in theme_js
    for filename in ("main.js", "settings.js", "new_target.js"):
        text = (dashboard_dir / filename).read_text(encoding="utf-8")
        assert '"/static/dashboard/theme.js"' in text
        assert "setupThemeToggle();" in text


def test_theme_bootstrap_defaults_to_dark_mode() -> None:
    """未保存使用者選擇時，server 注入主題預設必須固定為深色。"""

    bootstrap = Path(
        "src/facebook_monitor/webapp/templates/_theme_bootstrap.html"
    ).read_text(encoding="utf-8")

    assert 'default("dark")' in bootstrap
    assert "initial_theme" in bootstrap
    assert "prefers-color-scheme" not in bootstrap


def test_theme_templates_are_present_on_all_formal_pages() -> None:
    """正式頁面需要在載入 CSS 前套用主題，避免切換時閃爍。"""

    templates_dir = Path("src/facebook_monitor/webapp/templates")

    for filename in ("index.html", "settings.html", "new_target.html"):
        text = (templates_dir / filename).read_text(encoding="utf-8")
        assert '{% include "_theme_bootstrap.html" %}' in text
        assert '{% include "_theme_toggle.html" %}' in text


def test_web_ui_control_icons_use_inline_svg_not_text_glyphs() -> None:
    """正式 Web UI 控制圖示使用 inline SVG，避免不同字型造成對齊漂移。"""

    webapp_dir = Path("src/facebook_monitor/webapp")
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for pattern in ("*.js", "*.html", "*.css")
        for path in webapp_dir.rglob(pattern)
    )

    for glyph in ("☰", "⋯", "×", "✎", "⚙", "▾", "＋", "›", "◐", "☾", "☼"):
        assert glyph not in text
    assert 'data-theme-icon-light' in text
    assert 'data-theme-icon-dark' in text
    assert 'createCloseIcon' in text
    assert 'class="ui-icon' in text
    assert 'class="sidebar-action-icon' in text


def test_keyword_rule_tabs_are_initialized_by_dashboard_entrypoint() -> None:
    """左側 keyword 設定 tabs 與說明 modal 需由 dashboard entrypoint 初始化。"""

    dashboard_dir = Path("src/facebook_monitor/webapp/static/dashboard")
    tabs_js = (dashboard_dir / "tabs.js").read_text(encoding="utf-8")
    main_js = (dashboard_dir / "main.js").read_text(encoding="utf-8")
    modals_js = (dashboard_dir / "modals.js").read_text(encoding="utf-8")

    assert "export const setupKeywordTabs" in tabs_js
    assert "[data-keyword-tabs]" in tabs_js
    assert "[data-keyword-panel]" in tabs_js
    assert "setupKeywordTabs();" in main_js
    assert "[data-include-keyword-help-button]" in modals_js
    assert "[data-include-keyword-help-modal]" in modals_js
    assert "[data-keyword-help-button]" in modals_js
    assert "[data-keyword-help-modal]" in modals_js


def test_cover_image_refresh_handles_avatar_image_errors() -> None:
    """dashboard 壞圖處理需即時 fallback 並以 JSON hint 排程背景刷新。"""

    dashboard_dir = Path("src/facebook_monitor/webapp/static/dashboard")
    cover_js = (dashboard_dir / "cover_image_refresh.js").read_text(encoding="utf-8")
    main_js = (dashboard_dir / "main.js").read_text(encoding="utf-8")

    assert "document.addEventListener(\"error\", handleImageError, true)" in cover_js
    assert "scanAlreadyFailedImages" in cover_js
    assert "image.complete && image.naturalWidth === 0" in cover_js
    assert "requestAnimationFrame(scanAlreadyFailedImages)" in cover_js
    assert "reportedImageFailures" in cover_js
    assert "fallbackAvatar(image)" in cover_js
    assert "/cover-image/load-failure" in cover_js
    assert "requestJson" in cover_js
    assert '"/static/dashboard/cover_image_refresh.js"' in main_js
    assert "setupCoverImageRefresh();" in main_js


def test_notification_help_is_loaded_by_formal_page_entrypoints() -> None:
    """ntfy / Discord 說明按鈕需在首頁、設定頁與新增 target 頁可用。"""

    dashboard_dir = Path("src/facebook_monitor/webapp/static/dashboard")
    help_js = (dashboard_dir / "notification_help.js").read_text(encoding="utf-8")
    notification_fields = Path(
        "src/facebook_monitor/webapp/templates/_notification_settings_fields.html"
    ).read_text(encoding="utf-8")
    modals_css = Path("src/facebook_monitor/webapp/static/styles/modals.css").read_text(
        encoding="utf-8"
    )

    assert "export const setupNotificationHelp" in help_js
    assert "[data-notification-help-button]" in help_js
    assert "[data-notification-help-modal]" in help_js
    assert "bindDialogDismiss({" in help_js
    assert "data-notification-help-button=\"ntfy\"" in notification_fields
    assert "data-notification-help-button=\"discord\"" in notification_fields
    assert "ntfy 說明" in notification_fields
    assert "Discord Webhook 說明" in notification_fields
    assert "ntfy 是一個簡單能讓手機收到通知訊息的 app。" in notification_fields
    assert "這個設定可以讓通知訊息發送到 Discord 的頻道。" in notification_fields
    assert "未勾選 <code>ntfy</code>" not in notification_fields
    assert "未勾選 Discord Webhook" not in notification_fields
    assert "未勾選通道或留空 URL" not in notification_fields
    assert "測試通知" not in notification_fields
    assert ".notification-help-modal" in modals_css
    assert ".notification-help-steps ol" in modals_css
    for filename in ("main.js", "new_target.js"):
        text = (dashboard_dir / filename).read_text(encoding="utf-8")
        assert '"/static/dashboard/notification_help.js"' in text
        assert "setupNotificationHelp();" in text


def test_notification_test_uses_async_route_without_reloading_dashboard() -> None:
    """Target 測試通知按鈕需沿用既有 route，但由前端攔截避免關閉設定 modal。"""

    dashboard_dir = Path("src/facebook_monitor/webapp/static/dashboard")
    notification_test_js = (dashboard_dir / "notification_test.js").read_text(
        encoding="utf-8"
    )
    main_js = (dashboard_dir / "main.js").read_text(encoding="utf-8")
    route = Path("src/facebook_monitor/webapp/routes/targets.py").read_text(
        encoding="utf-8"
    )

    assert "export const setupNotificationTest" in notification_test_js
    assert "[data-notification-test]" in notification_test_js
    assert "event.preventDefault();" in notification_test_js
    assert "event.stopImmediatePropagation();" not in notification_test_js
    assert 'document.addEventListener("click"' in notification_test_js
    assert "document.getElementById(button.dataset.notificationTestFormId" in (
        notification_test_js
    )
    assert "button.dataset.notificationTestAction" in notification_test_js
    assert "new FormData(form)" in notification_test_js
    assert 'Accept: "application/json"' in notification_test_js
    assert '"/static/dashboard/notification_test.js"' in main_js
    assert "setupNotificationTest();" in main_js
    assert "def _wants_json_response" in route
    assert "JSONResponse({\"ok\": True" in route


def test_unsafe_dashboard_fetches_use_shared_csrf_helper() -> None:
    """會改狀態的 dashboard fetch 必須透過共用 CSRF helper。"""

    dashboard_dir = Path("src/facebook_monitor/webapp/static/dashboard")
    api_js = (dashboard_dir / "api.js").read_text(encoding="utf-8")
    csrf_js = (dashboard_dir / "csrf.js").read_text(encoding="utf-8")
    hit_records_js = (dashboard_dir / "hit_records.js").read_text(encoding="utf-8")
    sidebar_layout_js = (dashboard_dir / "sidebar_layout.js").read_text(encoding="utf-8")

    assert "export const csrfHeaders" in csrf_js
    assert 'meta[name="csrf-token"]' in csrf_js
    assert '"X-CSRF-Token"' in csrf_js
    assert '"/static/dashboard/csrf.js"' in api_js
    assert "export const requestJson" in api_js
    assert "headers: jsonHeaders()" in api_js
    assert '"/static/dashboard/csrf.js"' in hit_records_js
    assert "headers: csrfHeaders()" in hit_records_js
    assert '"/static/dashboard/api.js"' in sidebar_layout_js
    assert "const requestJson" not in sidebar_layout_js


def test_dashboard_modals_use_shared_dismiss_helper() -> None:
    """Modal 關閉按鈕與 backdrop click 必須走共用 helper。"""

    dashboard_dir = Path("src/facebook_monitor/webapp/static/dashboard")
    utils_js = (dashboard_dir / "utils.js").read_text(encoding="utf-8")
    modals_js = (dashboard_dir / "modals.js").read_text(encoding="utf-8")
    hit_records_js = (dashboard_dir / "hit_records.js").read_text(encoding="utf-8")

    assert "export const bindDialogDismiss" in utils_js
    assert "event.target === modal" in utils_js
    assert "bindDialogDismiss({" in modals_js
    assert "bindDialogDismiss({" in hit_records_js


def test_dashboard_scroll_restore_runs_before_dashboard_module_graph() -> None:
    """提交後 scroll restore 必須早於 dashboard module graph，避免頁面載入後跳動。"""

    index_template = Path("src/facebook_monitor/webapp/templates/index.html").read_text(
        encoding="utf-8"
    )
    head_bootstrap = Path(
        "src/facebook_monitor/webapp/templates/_dashboard_scroll_restore_head.html"
    ).read_text(encoding="utf-8")
    body_bootstrap = Path(
        "src/facebook_monitor/webapp/templates/_dashboard_scroll_restore_body.html"
    ).read_text(encoding="utf-8")

    assert '{% include "_dashboard_scroll_restore_head.html" %}' in index_template
    assert '{% include "_dashboard_scroll_restore_body.html" %}' in index_template
    assert index_template.index('{% include "_dashboard_scroll_restore_body.html" %}') < (
        index_template.index('{% include "_dashboard_importmap.html" %}')
    )
    assert index_template.index('{% include "_dashboard_importmap.html" %}') < (
        index_template.index('/static/dashboard/main.js')
    )
    assert "facebookMonitor.dashboard.scrollY" in head_bootstrap
    assert "facebookMonitor.dashboard.scrollSavedAt" in head_bootstrap
    assert 'window.history.scrollRestoration = "manual";' in head_bootstrap
    assert "dataset.dashboardRestoringScroll" in head_bootstrap
    assert 'html[data-dashboard-restoring-scroll="1"] body' in head_bootstrap
    assert "facebookMonitor.dashboard.scrollY" in body_bootstrap
    assert "window.scrollTo(0, savedScrollY)" in body_bootstrap
    assert "window.requestAnimationFrame" in body_bootstrap


def test_modal_close_controls_use_one_visible_dismiss_pattern() -> None:
    """read-only modal 用右上角關閉；action/form modal 用底部取消，不同時顯示兩套。"""

    target_card = Path("src/facebook_monitor/webapp/templates/_target_card.html").read_text(
        encoding="utf-8"
    )
    target_settings = Path(
        "src/facebook_monitor/webapp/templates/_target_settings_modal.html"
    ).read_text(encoding="utf-8")
    hit_records = Path(
        "src/facebook_monitor/webapp/templates/_hit_records_modal.html"
    ).read_text(encoding="utf-8")
    sidebar_template = Path(
        "src/facebook_monitor/webapp/templates/_sidebar_group_settings_modal.html"
    ).read_text(encoding="utf-8")
    dialogs_js = Path("src/facebook_monitor/webapp/static/dashboard/dialogs.js").read_text(
        encoding="utf-8"
    )

    assert target_settings.count("data-close-settings") == 1
    assert 'class="button--icon" type="button" data-close-settings' not in target_settings
    assert ">取消</button>" in target_settings

    assert target_card.count("data-close-rename-target") == 1
    assert 'class="button--icon" type="button" data-close-rename-target' not in target_card
    assert ">取消</button>" in target_card

    assert sidebar_template.count("data-sidebar-template-close") == 1
    assert (
        'class="button--icon" type="button" data-sidebar-template-close'
        not in sidebar_template
    )
    assert ">取消</button>" in sidebar_template

    assert target_card.count("data-close-keyword-help") == 1
    assert 'class="button--icon" type="button" data-close-keyword-help' in target_card
    assert '<button type="button" data-close-keyword-help>關閉</button>' not in target_card
    assert target_card.count("data-close-include-keyword-help") == 1
    assert (
        'class="button--icon" type="button" data-close-include-keyword-help'
        in target_card
    )
    assert '<button type="button" data-close-include-keyword-help>關閉</button>' not in (
        target_card
    )

    assert target_card.count("data-close-scan-diagnostics") == 1
    assert 'class="button--icon" type="button" data-close-scan-diagnostics' in target_card

    assert hit_records.count("data-close-hit-records") == 1
    assert 'class="button--icon" type="button" data-close-hit-records' in hit_records
    assert '<button type="button" data-close-hit-records>關閉</button>' not in hit_records

    assert "showHeaderClose = false" in dialogs_js
    assert "if (showHeaderClose)" in dialogs_js
    assert "createDialogShell({ title, danger })" in dialogs_js
    assert "createDialogShell({ title })" in dialogs_js


def test_dashboard_does_not_use_native_browser_dialogs() -> None:
    """互動確認與文字輸入不可回到瀏覽器原生 alert/confirm/prompt。"""

    webapp_dir = Path("src/facebook_monitor/webapp")
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for pattern in ("*.js", "*.html")
        for path in webapp_dir.rglob(pattern)
    )

    assert "window.confirm" not in text
    assert "window.prompt" not in text
    assert "window.alert" not in text
    assert "return confirm(" not in text


def test_confirm_dialog_renders_message_once_in_body() -> None:
    """共用確認彈窗的 message 不可同時出現在 header 與 body。"""

    dialogs_js = Path("src/facebook_monitor/webapp/static/dashboard/dialogs.js").read_text(
        encoding="utf-8"
    )

    assert "titleBlock.appendChild(description)" not in dialogs_js
    assert "body.appendChild(messageNode)" in dialogs_js


def test_dynamic_dialogs_resolve_native_close_and_confirm_submit_intercepts_tracking() -> None:
    """共用動態 dialog 必須處理 Esc/close，確認送出攔截要早於表單追蹤。"""

    dialogs_js = Path("src/facebook_monitor/webapp/static/dashboard/dialogs.js").read_text(
        encoding="utf-8"
    )
    main_js = Path("src/facebook_monitor/webapp/static/dashboard/main.js").read_text(
        encoding="utf-8"
    )
    settings_js = Path("src/facebook_monitor/webapp/static/dashboard/settings.js").read_text(
        encoding="utf-8"
    )

    assert 'dialog.addEventListener("cancel", () => finish(false), { once: true });' in dialogs_js
    assert 'dialog.addEventListener("close", () => finish(false), { once: true });' in dialogs_js
    assert 'dialog.addEventListener("cancel", () => finish(null), { once: true });' in dialogs_js
    assert 'dialog.addEventListener("close", () => finish(null), { once: true });' in dialogs_js
    assert "let settled = false;" in dialogs_js
    assert "event.stopImmediatePropagation();" in dialogs_js
    assert main_js.index("setupConfirmSubmitForms();") < main_js.index("setupFormSubmitTracking();")
    assert "setupConfirmSubmitForms();" in settings_js


def test_settings_failed_outbox_clear_requires_dynamic_confirmation() -> None:
    """Settings 失敗通知清除需走共用確認彈窗。"""

    settings_template = Path(
        "src/facebook_monitor/webapp/templates/settings.html"
    ).read_text(encoding="utf-8")

    assert 'action="/settings/notifications/clear-failed"' in settings_template
    assert "data-confirm-submit" in settings_template
    assert 'data-confirm-title="清除失敗通知"' in settings_template
    assert "不會重置已看紀錄" in settings_template
    assert "不會因為這個操作再次通知" in settings_template
    assert 'data-confirm-danger="1"' in settings_template
    assert "重試失敗通知" not in settings_template
    assert "通知 outbox" not in settings_template


def test_sidebar_sort_mode_does_not_reserve_drag_column_when_inactive() -> None:
    """sidebar 排序模式不靠額外欄位顯示 drag handle，避免壓縮文字。"""

    sidebar_css = Path("src/facebook_monitor/webapp/static/styles/sidebar.css").read_text(
        encoding="utf-8"
    )
    sidebar_template = Path(
        "src/facebook_monitor/webapp/templates/_target_sidebar.html"
    ).read_text(encoding="utf-8")

    assert ".sidebar-list-item {\n  align-items: center;" in sidebar_css
    assert "grid-template-columns: minmax(0, 1fr);\n" in sidebar_css
    assert ".target-sidebar.sorting .sidebar-list-item" in sidebar_css
    assert "grid-template-columns: minmax(0, 1fr) 30px;" not in sidebar_css
    assert "position: relative;" in _css_rule_body(sidebar_css, ".sidebar-list-item")
    assert "data-sidebar-confirm-sort hidden>確認</button>\n      <details" in sidebar_template


def test_sidebar_sorting_uses_sortablejs_with_handle_threshold_and_animation() -> None:
    """sidebar 排序互動由 SortableJS 模組統一處理 handle、交換門檻與動畫。"""

    sidebar_dom_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/sidebar_dom.js"
    ).read_text(encoding="utf-8")
    sidebar_sorting_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/sidebar_sorting.js"
    ).read_text(encoding="utf-8")
    sidebar_layout_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/sidebar_layout.js"
    ).read_text(encoding="utf-8")
    sidebar_css = Path("src/facebook_monitor/webapp/static/styles/sidebar.css").read_text(
        encoding="utf-8"
    )
    sidebar_template = Path(
        "src/facebook_monitor/webapp/templates/_target_sidebar.html"
    ).read_text(encoding="utf-8")
    sortable_license = Path(
        "src/facebook_monitor/webapp/static/vendor/sortablejs/LICENSE"
    ).read_text(encoding="utf-8")

    assert "export const listTargetIds" in sidebar_dom_js
    assert "export const prefersReducedMotion" in sidebar_dom_js
    assert "prefers-reduced-motion: reduce" in sidebar_dom_js
    assert '"/static/dashboard/sidebar_dom.js"' in sidebar_sorting_js
    assert '"/static/dashboard/sidebar_dom.js"' in sidebar_layout_js
    assert 'import Sortable from "/static/vendor/sortablejs/sortable.esm.js";' not in sidebar_sorting_js
    assert 'const SORTABLE_MODULE_PATH = "/static/vendor/sortablejs/sortable.esm.js";' in (
        sidebar_sorting_js
    )
    assert "const loadSortable = async () =>" in sidebar_sorting_js
    assert "await import(SORTABLE_MODULE_PATH)" in sidebar_sorting_js
    assert "await setupSortables();" in sidebar_sorting_js
    assert "const SORTABLE_SWAP_THRESHOLD = 0.75;" in sidebar_sorting_js
    assert "animation: sortableAnimation()" in sidebar_sorting_js
    assert 'handle: "[data-sidebar-drag-handle]"' in sidebar_sorting_js
    assert 'handle: "[data-sidebar-group-drag-handle]"' in sidebar_sorting_js
    assert 'draggable: "[data-sidebar-item]"' in sidebar_sorting_js
    assert 'draggable: "[data-sidebar-group][data-group-id]"' in sidebar_sorting_js
    assert 'direction: "vertical"' in sidebar_sorting_js
    assert "invertSwap" not in sidebar_sorting_js
    assert "swapThreshold: SORTABLE_SWAP_THRESHOLD" in sidebar_sorting_js
    assert "invertedSwapThreshold" not in sidebar_sorting_js
    assert 'fallbackClass: "sidebar-sort-fallback"' in sidebar_sorting_js
    assert "fallbackOnBody: true" in sidebar_sorting_js
    assert "forceFallback: true" in sidebar_sorting_js
    assert 'requestJson("/api/sidebar/layout"' in sidebar_sorting_js
    assert 'requestJson("/api/sidebar/groups/order"' not in sidebar_sorting_js
    assert 'requestJson("/api/sidebar/placements"' not in sidebar_sorting_js
    assert "setupSidebarSorting({" in sidebar_layout_js
    assert "getDragAfterItem" not in sidebar_layout_js
    assert "getDragAfterGroup" not in sidebar_layout_js
    assert 'draggable="true"' not in sidebar_template
    assert "data-sidebar-move-target" not in sidebar_template
    assert "data-sidebar-move-group" not in sidebar_template
    assert ".sidebar-sort-ghost" in sidebar_css
    assert ".sidebar-sort-fallback" in sidebar_css
    assert ".sidebar-sort-chosen" in sidebar_css
    assert ".sidebar-sort-drag" in sidebar_css
    assert ".sidebar-sort-control" not in sidebar_css
    assert "MIT License" in sortable_license


def test_sidebar_sort_handle_is_plain_three_line_grip_and_keeps_item_height() -> None:
    """排序把手只作為三線拖曳入口，排序卡片視覺不得增加 item 外框高度。"""

    sidebar_css = Path("src/facebook_monitor/webapp/static/styles/sidebar.css").read_text(
        encoding="utf-8"
    )
    sidebar_template = Path(
        "src/facebook_monitor/webapp/templates/_target_sidebar.html"
    ).read_text(encoding="utf-8")

    assert "sidebar-action-icon--drag" in sidebar_template
    assert '<path d="M5 7h14"/>' in sidebar_template
    assert '<path d="M5 12h14"/>' in sidebar_template
    assert '<path d="M5 17h14"/>' in sidebar_template
    assert 'd="m4 ' not in sidebar_template
    assert "box-shadow: inset 0 0 0 1px var(--border-soft), var(--shadow-subtle);" in sidebar_css
    assert ".target-sidebar.sorting .sidebar-list-item {\n" in sidebar_css
    sorting_item_rule = _css_rule_body(sidebar_css, ".target-sidebar.sorting .sidebar-list-item")
    assert "border:" not in sorting_item_rule
    sorting_handle_rule = _css_rule_body(
        sidebar_css,
        ".target-sidebar.sorting .sidebar-drag-handle",
    )
    assert "background: transparent;" in sorting_handle_rule
    assert "border-color: transparent;" in sorting_handle_rule
    assert "box-shadow: none;" in sorting_handle_rule
    assert "position: absolute;" in sorting_handle_rule
    assert "inset-inline-end: 6px;" in sorting_handle_rule
    assert "transform: translateY(-50%);" in sorting_handle_rule
    assert ".target-sidebar.sorting .sidebar-drag-handle:hover" in sidebar_css
    sorting_target_rule = _css_rule_body(sidebar_css, ".target-sidebar.sorting .sidebar-target")
    assert "padding-inline-end: 46px;" in sorting_target_rule


def test_sidebar_sort_drag_item_stays_opaque_and_placeholder_is_empty() -> None:
    """拖曳本體保持不透明；預計落點保留空間但不顯示半透明預覽。"""

    sidebar_css = Path("src/facebook_monitor/webapp/static/styles/sidebar.css").read_text(
        encoding="utf-8"
    )

    ghost_rule = _css_rule_body(sidebar_css, ".sidebar-sort-ghost")
    fallback_rule = _css_rule_body(sidebar_css, ".sidebar-sort-fallback")
    fallback_target_rule = _css_rule_body(
        sidebar_css,
        ".sidebar-sort-fallback .sidebar-target,\n.sidebar-sort-fallback .sidebar-target.active",
    )
    drag_title_space_rule = _css_rule_body(
        sidebar_css,
        ".sidebar-sort-drag .sidebar-target,\n.sidebar-sort-fallback .sidebar-target,\n.sidebar-sort-fallback .sidebar-target.active",
    )
    fallback_handle_rule = _css_rule_body(
        sidebar_css,
        ".sidebar-sort-fallback .sidebar-drag-handle",
    )
    drag_rule = _css_rule_body(sidebar_css, ".sidebar-sort-drag,\n.sidebar-sort-drag .sidebar-drag-handle,\n.sidebar-sort-drag .sidebar-group-collapse")

    assert "opacity: 0;" in ghost_rule
    assert "opacity: 1 !important;" in fallback_rule
    assert "position: relative;" in fallback_rule
    assert "border-color: transparent;" in fallback_target_rule
    assert "box-shadow: none;" in fallback_target_rule
    assert "padding-inline-end: 46px;" in drag_title_space_rule
    assert "position: absolute;" in fallback_handle_rule
    assert "transform: translateY(-50%);" in fallback_handle_rule
    assert "opacity: 1;" in drag_rule


def test_sidebar_group_actions_expand_inline_and_delete_icon_is_danger_colored() -> None:
    """群組操作平常收成 ⋯，展開後才水平顯示三個按鈕；刪除只讓符號變紅。"""

    sidebar_css = Path("src/facebook_monitor/webapp/static/styles/sidebar.css").read_text(
        encoding="utf-8"
    )
    sidebar_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/sidebar_layout.js"
    ).read_text(encoding="utf-8")
    sidebar_template = Path(
        "src/facebook_monitor/webapp/templates/_target_sidebar.html"
    ).read_text(encoding="utf-8")

    assert "data-sidebar-group-actions-toggle" in sidebar_template
    assert "data-sidebar-group-monitoring" in sidebar_template
    assert "sidebar-group-count" not in sidebar_template
    assert "sidebar-menu-trigger" in sidebar_template
    assert "sidebar-group-action-strip" in sidebar_template
    assert ".sidebar-group-action-strip" in sidebar_css
    assert "max-width: 0;" in sidebar_css
    assert ".sidebar-group-actions.expanded .sidebar-group-action-strip" in sidebar_css
    assert ".sidebar-group-delete {\n  color: var(--danger);" in sidebar_css
    assert "border-color: color-mix(in srgb, var(--danger)" not in sidebar_css
    assert "const setupGroupActionToggles" in sidebar_js
    assert "closeExpandedGroupActions();" in sidebar_js


def test_sidebar_group_operation_buttons_are_borderless_by_default() -> None:
    """群組收合、⋯ 與展開後操作按鈕預設不顯示外框。"""

    sidebar_css = Path("src/facebook_monitor/webapp/static/styles/sidebar.css").read_text(
        encoding="utf-8"
    )
    assert ".sidebar-group-collapse,\n.sidebar-drag-handle,\n.sidebar-group-menu," in sidebar_css
    assert "background: transparent;" in sidebar_css
    assert "border: 1px solid transparent;" in sidebar_css
    assert ".sidebar-group-collapse:hover,\n.sidebar-drag-handle:hover," in sidebar_css
    assert "background: var(--surface-soft);" in sidebar_css
    assert "border-color: transparent;" in sidebar_css
    assert ".sidebar-group-actions .button--icon," in sidebar_css
    assert ".sidebar-group-actions .button--icon:not(:disabled):hover," in sidebar_css
    assert "box-shadow: none;" in sidebar_css
    assert "color: var(--muted);" in sidebar_css
    assert "color: var(--text);" in sidebar_css
    actions_hover_rule = _css_rule_body(
        sidebar_css,
        ".sidebar-group-actions .button--icon:not(:disabled):hover,\n.sidebar-group-actions-toggle:hover",
    )
    assert "color: var(--text);" in actions_hover_rule
    assert ".sidebar-group-monitoring:disabled:hover" in sidebar_css
    disabled_hover_rule = _css_rule_body(
        sidebar_css, ".sidebar-group-monitoring:disabled:hover"
    )
    assert "background: transparent;" in disabled_hover_rule
    assert "border-color: transparent;" in disabled_hover_rule
    assert ".sidebar-group-monitoring.is-active" not in sidebar_css
    assert ".sidebar-group-monitoring {\n  color: var(--success);" not in sidebar_css
    assert "grid-template-columns: 30px minmax(0, 1fr) auto;" in sidebar_css


def test_sidebar_group_monitoring_sync_preserves_pending_button() -> None:
    """群組開始/停止送出中時，背景 partial sync 不可重新啟用按鈕。"""

    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("node is required to execute dashboard ES module behavior tests")
    module_path = Path(
        "src/facebook_monitor/webapp/static/dashboard/sidebar_status.js"
    ).resolve()
    script = textwrap.dedent(
        """
        import assert from "node:assert/strict";
        import { pathToFileURL } from "node:url";

        const { syncSidebarGroupMonitoringButtons } = await import(
          pathToFileURL(process.argv[1]).href
        );

        const makeIcon = () => ({
          hidden: false,
          toggleAttribute(name, force) {
            if (name === "hidden") {
              this.hidden = Boolean(force);
            }
          },
        });

        const makeButton = () => {
          const play = makeIcon();
          const stop = makeIcon();
          const classes = new Set();
          return {
            dataset: {},
            disabled: false,
            title: "",
            attrs: {},
            play,
            stop,
            classList: {
              toggle(name, force) {
                if (force) {
                  classes.add(name);
                } else {
                  classes.delete(name);
                }
              },
              contains(name) {
                return classes.has(name);
              },
            },
            setAttribute(name, value) {
              this.attrs[name] = String(value);
            },
            getAttribute(name) {
              return this.attrs[name];
            },
            querySelector(selector) {
              if (selector === ".sidebar-action-icon--play") return play;
              if (selector === ".sidebar-action-icon--stop") return stop;
              return null;
            },
          };
        };

        const button = makeButton();
        let items = [{ dataset: { sidebarItemActive: "0" } }];
        const root = {
          querySelectorAll(selector) {
            assert.equal(selector, "[data-sidebar-group][data-group-id]");
            return [group];
          },
        };
        const group = {
          matches(selector) {
            return selector === "[data-sidebar-group][data-group-id]";
          },
          querySelectorAll(selector) {
            if (selector === "[data-sidebar-group][data-group-id]") return [];
            assert.equal(selector, "[data-sidebar-item]");
            return items;
          },
          querySelector(selector) {
            assert.equal(selector, "[data-sidebar-group-monitoring]");
            return button;
          },
        };

        syncSidebarGroupMonitoringButtons(root);
        assert.equal(button.dataset.sidebarGroupMonitoring, "start");
        assert.equal(button.disabled, false);
        assert.equal(button.getAttribute("aria-label"), "開始群組");
        assert.equal(button.play.hidden, false);
        assert.equal(button.stop.hidden, true);

        button.dataset.sidebarGroupMonitoringPending = "1";
        button.disabled = true;
        items = [{ dataset: { sidebarItemActive: "1" } }];
        syncSidebarGroupMonitoringButtons(root);
        assert.equal(button.dataset.sidebarGroupMonitoring, "start");
        assert.equal(button.disabled, true);
        assert.equal(button.getAttribute("aria-label"), "開始群組");
        assert.equal(button.play.hidden, false);
        assert.equal(button.stop.hidden, true);

        delete button.dataset.sidebarGroupMonitoringPending;
        syncSidebarGroupMonitoringButtons(root);
        assert.equal(button.dataset.sidebarGroupMonitoring, "stop");
        assert.equal(button.disabled, false);
        assert.equal(button.getAttribute("aria-label"), "停止群組");
        assert.equal(button.play.hidden, true);
        assert.equal(button.stop.hidden, false);

        items = [];
        syncSidebarGroupMonitoringButtons(group);
        assert.equal(button.dataset.sidebarGroupMonitoring, "start");
        assert.equal(button.disabled, true);
        assert.equal(button.getAttribute("aria-label"), "開始群組");
        assert.equal(button.play.hidden, false);
        assert.equal(button.stop.hidden, true);
      """
    )

    subprocess.run(
        [node_bin, "--input-type=module", "-e", script, str(module_path)],
        check=True,
        text=True,
        capture_output=True,
    )


def test_sidebar_group_operation_icons_are_slightly_larger() -> None:
    """群組收合、⋯ 與展開操作 icon 使用 SVG，避免文字 glyph 視覺偏移。"""

    sidebar_css = Path("src/facebook_monitor/webapp/static/styles/sidebar.css").read_text(
        encoding="utf-8"
    )
    sidebar_template = Path(
        "src/facebook_monitor/webapp/templates/_target_sidebar.html"
    ).read_text(encoding="utf-8")

    assert "sidebar-action-icon--chevron" in sidebar_template
    assert "sidebar-action-icon--dots" in sidebar_template
    assert "sidebar-action-icon--play" in sidebar_template
    assert "sidebar-action-icon--stop" in sidebar_template
    assert 'aria-label="監看清單操作"' in sidebar_template
    assert '<path d="M4 7h16"/>' in sidebar_template
    assert '<path d="M8 5v14l11-7Z"/>' in sidebar_template
    assert "aria-label=\"重新命名群組\"" in sidebar_template
    assert "aria-label=\"群組設定模板\"" in sidebar_template
    assert "aria-label=\"刪除群組\"" in sidebar_template
    assert ".sidebar-action-icon {" in sidebar_css
    assert "display: block;" in sidebar_css
    assert "height: 17px;" in sidebar_css
    assert "width: 17px;" in sidebar_css
    assert ".sidebar-action-icon--chevron" in sidebar_css
    assert "height: 19px;" in sidebar_css
    assert ".sidebar-action-icon--dots" in sidebar_css
    assert "fill: currentcolor;" in sidebar_css


def test_sidebar_status_render_keeps_mode_chip_between_status_and_detail() -> None:
    """partial update 重繪 sidebar status 時保留貼文/留言 mode chip。"""

    sidebar_status_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/sidebar_status.js"
    ).read_text(encoding="utf-8")
    partial_updates_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/partial_updates.js"
    ).read_text(encoding="utf-8")
    sidebar_template = Path(
        "src/facebook_monitor/webapp/templates/_target_sidebar.html"
    ).read_text(encoding="utf-8")
    sidebar_css = Path("src/facebook_monitor/webapp/static/styles/sidebar.css").read_text(
        encoding="utf-8"
    )

    assert "data-sidebar-mode-label" in sidebar_template
    assert "data-sidebar-mode-class" in sidebar_template
    assert "sidebar-status-token target-mode-chip sidebar-mode-chip" in sidebar_template
    assert "modeLabel" in sidebar_status_js
    assert "modeClass" in sidebar_status_js
    assert "sidebar-mode-chip" in sidebar_status_js
    assert "mode_label" in partial_updates_js
    assert "mode_class" in partial_updates_js
    assert "sidebar-status-token sidebar-status-pill" in sidebar_status_js
    assert "sidebar-status-token target-mode-chip sidebar-mode-chip" in sidebar_status_js
    assert ".sidebar-status .sidebar-status-token" in sidebar_css


def test_sidebar_and_card_menus_share_panel_and_action_styles() -> None:
    """sidebar 漢堡選單與卡片更多選單共用卡片系的 panel/action 樣式。"""

    sidebar_template = Path(
        "src/facebook_monitor/webapp/templates/_target_sidebar.html"
    ).read_text(encoding="utf-8")
    card_template = Path(
        "src/facebook_monitor/webapp/templates/_target_card.html"
    ).read_text(encoding="utf-8")
    sidebar_css = Path("src/facebook_monitor/webapp/static/styles/sidebar.css").read_text(
        encoding="utf-8"
    )
    target_card_css = Path(
        "src/facebook_monitor/webapp/static/styles/target-card.css"
    ).read_text(encoding="utf-8")
    sidebar_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/sidebar_layout.js"
    ).read_text(encoding="utf-8")

    assert 'class="sidebar-menu-panel menu-panel"' in sidebar_template
    assert 'class="sidebar-menu-action menu-action"' in sidebar_template
    assert 'class="more-menu-panel menu-panel"' in card_template
    assert 'class="more-menu-action menu-action"' in card_template
    assert ".menu-panel {" in target_card_css
    assert "border: 1px solid var(--border);" in target_card_css
    assert "gap: 8px;" in target_card_css
    assert ".menu-action {" in target_card_css
    assert ".sidebar-menu-action:hover" not in sidebar_css
    assert ".sidebar-menu-panel.menu-panel" not in target_card_css
    sidebar_menu_rule = _css_rule_body(sidebar_css, ".sidebar-menu-panel,\n.sidebar-menu-panel.menu-panel")
    assert "position: fixed;" in sidebar_menu_rule
    assert "left: var(--sidebar-menu-left" in sidebar_menu_rule
    assert "inline-size: max-content;" in sidebar_menu_rule
    assert "min-inline-size: 108px;" in sidebar_menu_rule
    assert "max-inline-size: 160px;" in sidebar_menu_rule
    assert "right: auto;" in sidebar_menu_rule
    assert "top: var(--sidebar-menu-top" in sidebar_menu_rule
    assert "const positionSidebarMenuPanel" in sidebar_js
    assert "trigger.getBoundingClientRect()" in sidebar_js
    assert "const setSidebarMenuOpen" in sidebar_js
    assert "event.preventDefault();" in sidebar_js
    assert 'trigger?.setAttribute("aria-expanded", String(open));' in sidebar_js
    assert "setupSidebarMenuPosition();" in sidebar_js


def test_target_header_status_and_mode_are_grouped_in_subtitle() -> None:
    """卡片標題只放名稱；狀態與貼文/留言模式一起放在副標題。"""

    card_template = Path(
        "src/facebook_monitor/webapp/templates/_target_card.html"
    ).read_text(encoding="utf-8")
    target_card_css = Path(
        "src/facebook_monitor/webapp/static/styles/target-card.css"
    ).read_text(encoding="utf-8")

    heading_rule = _css_rule_body(target_card_css, ".target-header h2")
    status_rule = _css_rule_body(target_card_css, "\n.status")

    title_line = card_template.split('<div class="target-title-line">', 1)[1].split("</div>", 1)[0]
    subtitle = card_template.split('<p class="target-subtitle"', 1)[1].split("</p>", 1)[0]

    assert "data-target-title" in title_line
    assert "data-card-status" not in title_line
    assert subtitle.index("data-card-status") < subtitle.index("data-target-mode")
    assert 'class="status {{ row.status_class }}" data-card-status' in subtitle
    assert 'class="target-mode-chip {{ row.mode_class }}" data-target-mode' in subtitle
    assert "display: block;" in heading_rule
    assert "overflow-wrap: anywhere;" in heading_rule
    assert "display: inline-flex;" in status_rule


def test_next_refresh_countdown_runs_on_frontend_with_thresholded_resync() -> None:
    """下次刷新秒數由前端本地倒數，partial update 只在差距超過 1 秒時校準。"""

    card_template = Path(
        "src/facebook_monitor/webapp/templates/_target_card.html"
    ).read_text(encoding="utf-8")
    routes = Path("src/facebook_monitor/webapp/routes/dashboard.py").read_text(
        encoding="utf-8"
    )
    assets = Path("src/facebook_monitor/webapp/assets.py").read_text(encoding="utf-8")
    dashboard_models = Path(
        "src/facebook_monitor/webapp/dashboard_models.py"
    ).read_text(encoding="utf-8")
    main_js = Path("src/facebook_monitor/webapp/static/dashboard/main.js").read_text(
        encoding="utf-8"
    )
    partial_updates_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/partial_updates.js"
    ).read_text(encoding="utf-8")
    countdown_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/next_refresh_countdown.js"
    ).read_text(encoding="utf-8")

    assert "data-next-refresh-seconds" in card_template
    assert "row.next_refresh_seconds" in card_template
    assert '"next_refresh_seconds": row.next_refresh_seconds' in routes
    assert "class NextRefreshDisplay" in dashboard_models
    assert "@cached_property\n    def next_refresh_display" in dashboard_models
    assert "self.runtime_state.display_next_due_at" in dashboard_models
    assert '"next_refresh_countdown.js"' in assets
    assert "setupNextRefreshCountdowns" in main_js
    assert "syncNextRefreshCountdown" in partial_updates_js
    assert "payload.next_refresh_seconds" in partial_updates_js
    assert "const SYNC_THRESHOLD_SECONDS = 1;" in countdown_js
    assert "Math.abs(localSeconds - incomingSeconds) <= SYNC_THRESHOLD_SECONDS" in (
        countdown_js
    )
    assert "window.setInterval(tickCountdowns, 1000)" in countdown_js
    assert "下次刷新：即將刷新" in countdown_js
    assert "incomingSeconds <= 0" in countdown_js
    assert "remainingSeconds <= 0" in countdown_js
    assert "clearCountdown(node, SOON_LABEL)" in countdown_js


def test_dashboard_partial_updates_are_coalesced_while_in_flight() -> None:
    """revision 更新過密時，前端不應重疊發出 dashboard partial update。"""

    state_js = Path("src/facebook_monitor/webapp/static/dashboard/state.js").read_text(
        encoding="utf-8"
    )
    revision_client_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/revision_client.js"
    ).read_text(encoding="utf-8")

    assert "partialUpdateInFlight: false" in state_js
    assert "state.partialUpdateInFlight || isFormDirty(state)" in state_js
    assert "state.pendingRefresh = true;" in revision_client_js


def test_scan_diagnostics_is_opened_from_card_more_menu() -> None:
    """掃描診斷入口收進卡片更多選單，內容顯示在共用 dialog 行為的 modal。"""

    card_template = Path(
        "src/facebook_monitor/webapp/templates/_target_card.html"
    ).read_text(encoding="utf-8")
    target_card_css = Path(
        "src/facebook_monitor/webapp/static/styles/target-card.css"
    ).read_text(encoding="utf-8")
    modals_js = Path("src/facebook_monitor/webapp/static/dashboard/modals.js").read_text(
        encoding="utf-8"
    )
    partial_updates_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/partial_updates.js"
    ).read_text(encoding="utf-8")

    assert "data-scan-diagnostics-button" in card_template
    assert "data-rename-target-button" in card_template
    assert ">刪除</button>" in card_template
    assert "清除 baseline" not in card_template
    assert "清除命中紀錄" not in card_template
    assert "清除通知紀錄" in card_template
    assert 'action="/targets/{{ row.target.id }}/notifications/clear"' in card_template
    assert 'data-confirm-title="清除通知紀錄"' in card_template
    assert "不會重置已看紀錄" in card_template
    assert "不會影響命中紀錄或設定" in card_template
    assert "不會因為這個操作再次通知" in card_template
    assert "data-scan-diagnostics-modal" in card_template
    assert 'class="settings-modal scan-diagnostics-modal"' in card_template
    assert 'class="settings-modal scan-diagnostics-modal scan-debug-details"' not in card_template
    assert 'class="modal-body scan-diagnostics-body scan-debug-details"' in card_template
    assert 'class="button button--toolbar button--toolbar-icon more-menu-trigger"' in card_template
    more_trigger_rule = _css_rule_body(target_card_css, ".more-menu-trigger")
    assert "color: var(--text-soft);" in more_trigger_rule
    assert "list-style: none;" in more_trigger_rule
    for duplicated_button_property in ("border-radius:", "min-height:", "min-width:", "padding:"):
        assert duplicated_button_property not in more_trigger_rule
    assert "scan-debug-details" in card_template
    assert "<details class=\"debug-details scan-debug-details\">" not in card_template
    assert "data-close-scan-diagnostics" in card_template
    assert "[data-scan-diagnostics-modal]" in modals_js
    assert "[data-close-scan-diagnostics]" in modals_js
    assert ".scan-debug-details .debug-summary" in partial_updates_js


def test_target_card_footer_stays_compact_after_moving_scan_diagnostics() -> None:
    """掃描診斷移入 modal 後，卡片底部 footer 不應保留過大的空白列。"""

    layout_css = Path("src/facebook_monitor/webapp/static/styles/layout.css").read_text(
        encoding="utf-8"
    )
    collapse_css = Path(
        "src/facebook_monitor/webapp/static/styles/target-collapse.css"
    ).read_text(encoding="utf-8")

    assert ".target-card {\n  padding-bottom: 12px;\n}" in layout_css
    assert ".target-footer-controls {\n  align-items: center;" in collapse_css
    assert "margin-top: 8px;" in collapse_css
    assert "height: 32px;" in collapse_css
    assert "width: 32px;" in collapse_css


def test_sidebar_template_modal_keeps_shell_background_and_title_note() -> None:
    """群組設定 modal 由 body 內部捲動，避免 footer 附近露出非 modal 背景。"""

    template = Path(
        "src/facebook_monitor/webapp/templates/_sidebar_group_settings_modal.html"
    ).read_text(encoding="utf-8")
    modals_css = Path("src/facebook_monitor/webapp/static/styles/modals.css").read_text(
        encoding="utf-8"
    )

    assert "sidebar-template-title-row" in template
    assert "獨立模板，只在套用時覆蓋群組內 target" in template
    assert "新群組會帶入系統預設與當下關鍵字預設" not in template
    assert "通知設定不會自動繼承全域通知或任一 target" not in template
    assert ".sidebar-template-modal,\n.sidebar-template-modal .modal-shell" in modals_css
    assert ".sidebar-template-modal .modal-shell" in modals_css
    assert "grid-template-rows: auto minmax(0, 1fr) auto;" in modals_css
    assert ".sidebar-template-modal .modal-body" in modals_css
    assert "background: var(--surface);" in modals_css


def test_sidebar_placement_strategy_stays_lazy_without_repository_backfill_helper() -> None:
    """缺失 placement 採 read-model lazy fallback，不保留 repository 補寫 helper。"""

    repository = Path(
        "src/facebook_monitor/persistence/repositories/sidebar_layout.py"
    ).read_text(encoding="utf-8")
    architecture = Path("docs/ARCHITECTURE.md").read_text(encoding="utf-8")

    assert "ensure_default_placements" not in repository
    assert "不得為缺失 placement 寫入 DB" in architecture
    assert "缺失 placement 採 lazy fallback 顯示在未分組區" in architecture


def test_target_settings_modal_uses_scroll_body_and_right_footer_actions() -> None:
    """target 設定 modal footer 常駐底部，取消/儲存按鈕靠右。"""

    template = Path(
        "src/facebook_monitor/webapp/templates/_target_settings_modal.html"
    ).read_text(encoding="utf-8")
    notification_test_button = Path(
        "src/facebook_monitor/webapp/templates/_notification_test_button.html"
    ).read_text(encoding="utf-8")
    modals_css = Path("src/facebook_monitor/webapp/static/styles/modals.css").read_text(
        encoding="utf-8"
    )

    assert 'class="settings-modal target-settings-modal"' in template
    assert "Target 資訊" not in template
    assert "重新抓取名稱與封面" in template
    assert "/metadata/refresh" in template
    assert ".target-settings-modal,\n.target-settings-modal .modal-shell" in modals_css
    assert ".target-settings-modal .modal-shell" in modals_css
    assert ".target-settings-modal .modal-body" in modals_css
    assert ".target-settings-modal .modal-footer" in modals_css
    assert ".target-settings-modal .modal-footer-actions" in modals_css
    assert ".target-metadata-refresh-button" in modals_css
    assert "margin-left: auto;" in modals_css
    assert "modal-section-header" in template
    assert 'data-dirty-status-for="{{ config_form_id }}"' in template
    assert '" ~ row.target.id ~ "/notifications/test"' in template
    assert '{% include "_notification_test_button.html" %}' in template
    assert "formaction" not in notification_test_button
    assert "formmethod" not in notification_test_button
    assert 'type="submit"' not in notification_test_button
    assert 'type="button"' in notification_test_button
    assert 'data-notification-test-action="{{ notification_test_action }}"' in (
        notification_test_button
    )
    assert 'data-notification-test-form-id="{{ notification_test_form_id }}"' in (
        notification_test_button
    )
    assert notification_test_button.index("data-notification-test-status") < (
        notification_test_button.index("data-notification-test\n")
    )
    assert "data-notification-test" in notification_test_button
    assert "data-notification-test-status" in notification_test_button
    assert "測試通知" in notification_test_button
    assert ".notification-test-button" in modals_css


def test_target_settings_modal_attaches_controls_to_config_form() -> None:
    """target 設定 modal 的外部欄位必須掛回同一個 config form。"""

    template = Path(
        "src/facebook_monitor/webapp/templates/_target_settings_modal.html"
    ).read_text(encoding="utf-8")
    scan_fields = Path(
        "src/facebook_monitor/webapp/templates/_scan_settings_fields.html"
    ).read_text(encoding="utf-8")
    refresh_fields = Path(
        "src/facebook_monitor/webapp/templates/_refresh_settings_fields.html"
    ).read_text(encoding="utf-8")
    notification_fields = Path(
        "src/facebook_monitor/webapp/templates/_notification_settings_fields.html"
    ).read_text(encoding="utf-8")

    assert '{% set scan_form_id = config_form_id %}' in template
    assert '{% set refresh_form_id = config_form_id %}' in template
    assert '{% set notification_test_form_id = config_form_id %}' in template
    assert '{% set notification_form_id = config_form_id %}' in template

    for field_name in (
        "auto_load_more",
        "auto_adjust_sort",
        "max_items_per_scan",
    ):
        tags = _input_tags(scan_fields, field_name)
        assert tags
        assert all('form="{{ scan_form_id }}"' in tag for tag in tags)

    assert 'name="{{ refresh_mode_name }}"' in refresh_fields
    assert "data-refresh-mode-input" in refresh_fields
    assert 'form="{{ refresh_form_id }}"' in refresh_fields

    for field_name in (
        "fixed_refresh_sec",
        "min_refresh_sec",
        "max_refresh_sec",
    ):
        tags = _input_tags(refresh_fields, field_name)
        assert tags
        assert all('form="{{ refresh_form_id }}"' in tag for tag in tags)

    for field_name in (
        "enable_desktop_notification",
        "enable_ntfy",
        "ntfy_topic",
        "ntfy_topic_keep",
        "clear_ntfy_topic",
        "enable_discord_notification",
        "discord_webhook",
        "discord_webhook_keep",
        "clear_discord_webhook",
    ):
        tags = _input_tags(notification_fields, field_name)
        assert tags
        assert all('form="{{ notification_form_id }}"' in tag for tag in tags)


def test_sidebar_template_apply_confirmation_shows_batch_impact() -> None:
    """群組模板套用確認必須列出批次覆蓋範圍與影響 target 摘要。"""

    sidebar_layout_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/sidebar_layout.js"
    ).read_text(encoding="utf-8")
    dialogs_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/dialogs.js"
    ).read_text(encoding="utf-8")
    modals_css = Path("src/facebook_monitor/webapp/static/styles/modals.css").read_text(
        encoding="utf-8"
    )

    assert "套用範圍：" in sidebar_layout_js
    assert "影響 target：" in sidebar_layout_js
    assert "會覆蓋這些 target 既有設定。" in sidebar_layout_js
    assert "不會影響群組外 target。" in sidebar_layout_js
    assert "此操作沒有自動復原。" in sidebar_layout_js
    assert "以及另外" in sidebar_layout_js
    assert "details = []" in dialogs_js
    assert "app-dialog-detail-list" in dialogs_js
    assert ".app-dialog-detail-list" in modals_css
    assert 'import { saveScrollPosition } from "/static/dashboard/state.js";' in (
        sidebar_layout_js
    )
    assert "const reloadDashboardPreservingScroll = () =>" in sidebar_layout_js
    assert "saveScrollPosition();\n  window.location.reload();" in sidebar_layout_js
    assert "reloadDashboardPreservingScroll();" in sidebar_layout_js


def test_keyword_ignore_phrase_placeholder_uses_semicolon_separator() -> None:
    """排除字忽略片語 placeholder 必須使用和 parser 一致的分號範例。"""

    card_template = Path(
        "src/facebook_monitor/webapp/templates/_target_card.html"
    ).read_text(encoding="utf-8")

    assert 'placeholder="例如：全收;回收"' in card_template
    assert "例如：全收, 回收" not in card_template


def test_keyword_ignore_phrase_help_uses_short_examples_without_footer_note() -> None:
    """排除字忽略片語說明只保留簡短規則與兩個例子。"""

    card_template = Path(
        "src/facebook_monitor/webapp/templates/_target_card.html"
    ).read_text(encoding="utf-8")
    modals_css = Path("src/facebook_monitor/webapp/static/styles/modals.css").read_text(
        encoding="utf-8"
    )

    assert "避免排除關鍵字誤判。" in card_template
    assert "系統會先保護忽略片語，再檢查排除關鍵字。" in card_template
    assert "<strong>例：</strong>" in card_template
    assert "<code>收一張票</code> → <strong>排除</strong>" in card_template
    assert "<code>售一張票，贈品回收</code> → <strong>不排除</strong>" in card_template
    assert "預設忽略片語" not in card_template
    assert "用來避免排除字誤傷" not in card_template
    assert ".keyword-help-body ul" in modals_css


def test_include_keyword_help_matches_keyword_rule_copy() -> None:
    """包含關鍵字說明維持分號 OR / 空格 AND 語義。"""

    card_template = Path(
        "src/facebook_monitor/webapp/templates/_target_card.html"
    ).read_text(encoding="utf-8")
    target_card_css = Path(
        "src/facebook_monitor/webapp/static/styles/target-card.css"
    ).read_text(encoding="utf-8")
    modals_css = Path("src/facebook_monitor/webapp/static/styles/modals.css").read_text(
        encoding="utf-8"
    )

    assert "data-include-keyword-help-button" in card_template
    assert "data-include-keyword-help-modal" in card_template
    assert "include-keywords-{{ row.target.id }}" in card_template
    assert "關鍵字輸入規則" in card_template
    assert '<div class="keyword-help-rule-list">' in card_template
    assert '<section class="modal-section-card keyword-help-rule-list">' not in (
        card_template
    )
    assert "<code>;</code> 表示 <strong>OR</strong>" in card_template
    assert "空格表示 <strong>AND</strong>" in card_template
    assert "<code>搖滾;6880;5880</code>" in card_template
    assert "只要出現 <code>搖滾</code> 或 <code>6880</code> 或 <code>5880</code> 就通知。" in (
        card_template
    )
    assert "<code>搖滾 6880;搖滾 5880</code>" in card_template
    assert "代表 <code>搖滾</code> 且 <code>6880</code>，或 <code>搖滾</code> 且 <code>5880</code> 才通知。" in (
        card_template
    )
    assert "排除關鍵字也使用同樣規則。" in card_template
    assert ".keyword-field-header" in target_card_css
    assert ".keyword-help-example" in modals_css


def test_button_variants_use_shared_button_modifier_classes() -> None:
    """跨頁 primary / danger / icon buttons 使用一致 modifier class。"""

    templates_dir = Path("src/facebook_monitor/webapp/templates")
    styles = Path("src/facebook_monitor/webapp/static/styles/feedback.css").read_text(
        encoding="utf-8"
    )
    target_card_css = Path(
        "src/facebook_monitor/webapp/static/styles/target-card.css"
    ).read_text(encoding="utf-8")
    template_text = "\n".join(
        path.read_text(encoding="utf-8") for path in templates_dir.glob("*.html")
    )

    assert ".button--primary" in styles
    assert ".button--danger" in styles
    assert ".button--icon" in styles
    assert ".button--toolbar" in styles
    assert ".button--toolbar-icon" in styles
    assert "min-height: 42px;" in styles
    assert "min-width: 68px;" in styles
    assert "min-width: 52px;" in styles
    assert 'class="button button--toolbar button--toolbar-icon more-menu-trigger"' in template_text
    assert (
        'class="button button--toolbar" type="submit" '
        'data-monitoring-button>{{ row.monitoring_button_label }}</button>'
    ) in template_text
    assert 'class="button button--toolbar" type="submit" form="config-{{ row.target.id }}"' in template_text
    assert 'class="button button--toolbar" type="button" data-view-records-button' in template_text
    assert 'class="button button--toolbar" type="button" data-settings-button' in template_text
    assert 'class="button theme-toggle"' in template_text
    assert 'class="button button--icon sidebar-menu-trigger"' in template_text
    assert 'class="button button--primary sidebar-sort-confirm"' in template_text
    assert "button primary" not in template_text
    assert "icon-button" not in template_text
    assert 'class="danger"' not in template_text

    more_trigger_rule = _css_rule_body(target_card_css, ".more-menu-trigger")
    settings_summary_rule = _css_rule_body(target_card_css, ".settings-summary-button")
    for rule in (more_trigger_rule, settings_summary_rule):
        for duplicated_button_property in (
            "background:",
            "border:",
            "border-radius:",
            "display:",
        ):
            assert duplicated_button_property not in rule


def test_refresh_mode_options_put_floating_before_fixed() -> None:
    """所有設定頁面的刷新模式 radio 都先顯示浮動刷新，再顯示固定刷新。"""

    target_refresh_template = Path(
        "src/facebook_monitor/webapp/templates/_refresh_settings_fields.html"
    ).read_text(encoding="utf-8")
    sidebar_group_template = Path(
        "src/facebook_monitor/webapp/templates/_sidebar_group_settings_modal.html"
    ).read_text(encoding="utf-8")
    forms_js = Path("src/facebook_monitor/webapp/static/dashboard/forms.js").read_text(
        encoding="utf-8"
    )

    assert target_refresh_template.index('value="floating"') < target_refresh_template.index(
        'value="fixed"'
    )
    assert target_refresh_template.index("<strong>浮動刷新</strong>") < target_refresh_template.index(
        "<strong>固定刷新</strong>"
    )
    assert (
        '{% set refresh_mode_value = refresh_source.refresh_mode if refresh_source and '
        'refresh_source.refresh_mode else "floating" %}'
    ) in target_refresh_template
    assert '{% set floating_checked = refresh_mode_value != "fixed" %}' in target_refresh_template
    assert 'name="{{ refresh_mode_name }}"' in target_refresh_template
    assert "data-refresh-mode-input" in target_refresh_template
    assert '{% include "_refresh_settings_fields.html" %}' in sidebar_group_template
    assert '{% set refresh_mode_name = "refresh_mode_" ~ group.group_id %}' in (
        sidebar_group_template
    )
    assert '{% set refresh_mode_payload_name = "refresh_mode" %}' in sidebar_group_template
    assert "sidebarTemplatePayloadName" in Path(
        "src/facebook_monitor/webapp/static/dashboard/sidebar_layout.js"
    ).read_text(encoding="utf-8")
    assert '|| "floating"' in forms_js


def test_sidebar_template_apply_confirmation_mentions_full_template_save() -> None:
    """群組模板 section 套用前會明確提示會先儲存整份模板。"""

    sidebar_layout_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/sidebar_layout.js"
    ).read_text(encoding="utf-8")

    assert "套用前會先儲存目前整份群組模板；本次只會覆蓋所選區段。" in sidebar_layout_js
    assert "套用前會先儲存目前整份群組模板；本次會覆蓋全部區段。" in sidebar_layout_js
    assert "section === \"all\"" in sidebar_layout_js


def test_hit_records_modal_renders_keyword_segments_without_inner_html() -> None:
    """完整命中紀錄 modal 必須用 DOM nodes render keyword highlight。"""

    hit_records_js = Path("src/facebook_monitor/webapp/static/dashboard/hit_records.js").read_text(
        encoding="utf-8"
    )

    assert "const appendTextSegments" in hit_records_js
    assert "document.createElement(\"mark\")" in hit_records_js
    assert "keyword-highlight" in hit_records_js
    assert "item.content_segments" in hit_records_js
    assert "content.innerHTML" not in hit_records_js


def test_partial_update_syncs_rename_modal_name_without_overwriting_active_input() -> None:
    """背景 partial update 更新 target 名稱時，要同步更名 modal 的預填值。"""

    partial_updates_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/partial_updates.js"
    ).read_text(encoding="utf-8")

    assert "const updateRenameInput" in partial_updates_js
    assert 'input[name="display_name"]' in partial_updates_js
    assert "payload.rename_display_name" in partial_updates_js
    assert "document.activeElement === input" in partial_updates_js
    assert "input.setAttribute(\"value\", nextValue)" in partial_updates_js


def test_partial_update_syncs_runtime_action_and_guard_messages() -> None:
    """背景 partial update 必須同步開始/停止按鈕與 runtime error/skip reason。"""

    card_template = Path(
        "src/facebook_monitor/webapp/templates/_target_card.html"
    ).read_text(encoding="utf-8")
    partial_updates_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/partial_updates.js"
    ).read_text(encoding="utf-8")
    dashboard_routes = Path(
        "src/facebook_monitor/webapp/routes/dashboard.py"
    ).read_text(encoding="utf-8")

    assert "data-monitoring-form" in card_template
    assert "data-monitoring-button" in card_template
    assert "data-runtime-error" in card_template
    assert "data-runtime-skip-reason" in card_template
    assert "data-latest-error-indicator" in card_template
    assert "data-latest-error-kind" in card_template
    assert "data-latest-error-separator" in card_template
    assert "monitoring_action" in dashboard_routes
    assert "monitoring_button_label" in dashboard_routes
    assert "runtime_error" in dashboard_routes
    assert "runtime_skip_reason" in dashboard_routes
    assert "has_latest_failed_scan" in dashboard_routes
    assert "latest_error_indicator_label" in dashboard_routes
    assert "latest_error_indicator_title" in dashboard_routes
    assert "latest_error_indicator_kind" in dashboard_routes
    assert "const updateMonitoringAction" in partial_updates_js
    assert "payload.monitoring_action" in partial_updates_js
    assert "payload.monitoring_button_label" in partial_updates_js
    assert "const updateRuntimeMessages" in partial_updates_js
    assert "payload.runtime_error" in partial_updates_js
    assert "payload.runtime_skip_reason" in partial_updates_js
    assert "payload.has_latest_failed_scan" in partial_updates_js
    assert "payload.latest_error_indicator_label" in partial_updates_js
    assert "payload.latest_error_indicator_title" in partial_updates_js
    assert "payload.latest_error_indicator_kind" in partial_updates_js
    assert "[data-latest-error-indicator]" in partial_updates_js
