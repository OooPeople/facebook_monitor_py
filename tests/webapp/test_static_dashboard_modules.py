"""Dashboard static module contract tests。"""

from __future__ import annotations

from pathlib import Path
import re


from facebook_monitor.webapp.assets import ASSET_VERSION
from facebook_monitor.webapp.assets import DASHBOARD_MODULE_FILENAMES
from facebook_monitor.webapp.assets import build_dashboard_module_imports
from tests.webapp.static_contract_helpers import css_rule_body as _css_rule_body
from tests.webapp.static_contract_helpers import sidebar_template_family_text
from tests.webapp.static_contract_helpers import target_card_template_family_text


def test_dashboard_import_map_covers_all_dashboard_modules() -> None:
    """新增 dashboard ES module 時必須進 import map，避免子模組快取漂移。"""

    dashboard_dir = Path("src/facebook_monitor/webapp/static/dashboard")
    module_filenames = tuple(sorted(path.name for path in dashboard_dir.glob("*.js")))

    assert tuple(sorted(DASHBOARD_MODULE_FILENAMES)) == module_filenames

    import_map = build_dashboard_module_imports()
    dashboard_imports = {f"/static/dashboard/{filename}" for filename in DASHBOARD_MODULE_FILENAMES}
    assert dashboard_imports.issubset(set(import_map))
    assert "/static/vendor/sortablejs/sortable.esm.js" in import_map
    assert all(value.endswith(f"?v={ASSET_VERSION}") for value in import_map.values())


def test_collapse_animation_helper_is_shared_by_card_and_new_target() -> None:
    """收合動畫 primitive 應獨立於 target card 狀態邏輯供表單頁共用。"""

    dashboard_dir = Path("src/facebook_monitor/webapp/static/dashboard")
    animation_js = (dashboard_dir / "collapse_animation.js").read_text(encoding="utf-8")
    card_collapse_js = (dashboard_dir / "card_collapse.js").read_text(encoding="utf-8")
    new_target_js = (dashboard_dir / "new_target.js").read_text(encoding="utf-8")

    assert "collapse_animation.js" in DASHBOARD_MODULE_FILENAMES
    assert "export const animateElementVisibility" in animation_js
    assert "--collapse-panel-duration" in animation_js
    assert "getCollapseAnimationDurationMs" in animation_js
    assert "collapseAnimationFallbackBufferMs" in animation_js
    assert 'element.addEventListener("transitionend", transitionEndHandler);' in animation_js
    assert 'event.propertyName === "height"' in animation_js
    assert "data-collapse-animating" in animation_js
    assert "collapseAnimationTimer" in animation_js
    assert "afterFinish?.();" in animation_js
    assert 'import { animateElementVisibility } from "/static/dashboard/collapse_animation.js";' in (
        card_collapse_js
    )
    assert 'import { animateElementVisibility } from "/static/dashboard/collapse_animation.js";' in (
        new_target_js
    )
    assert "const collapseAnimationMs" not in card_collapse_js
    assert '"/static/dashboard/state.js"' not in new_target_js
    assert "isTargetDirty" not in new_target_js
    assert "setTargetCollapsed" not in new_target_js


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
    assert 'name="clear_ntfy_topic"' in notification_fields
    assert 'name="clear_discord_webhook"' in notification_fields
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


def test_settings_keyword_defaults_feedback_uses_saved_copy() -> None:
    """Settings keyword defaults 成功回饋需對齊 route 成功訊息。"""

    settings_js = Path("src/facebook_monitor/webapp/static/dashboard/settings.js").read_text(
        encoding="utf-8"
    )

    assert 'pageFeedback.feedback === "target_keyword_defaults_saved"' in settings_js
    assert 'showInlineStatus(targetKeywordStatus, "預設值已儲存", "saved", 2500)' in (
        settings_js
    )


def test_theme_toggle_contract_is_loaded_by_all_page_entrypoints() -> None:
    """Theme toggle module 必須走 DB-backed API 並被三個正式頁面入口載入。"""

    dashboard_dir = Path("src/facebook_monitor/webapp/static/dashboard")
    theme_js = (dashboard_dir / "theme.js").read_text(encoding="utf-8")

    assert 'fetch("/settings/theme"' in theme_js
    assert '"/static/dashboard/csrf.js"' in theme_js
    assert "csrfHeaders" in theme_js
    assert "document.documentElement.dataset.theme" in theme_js
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

    bootstrap = Path("src/facebook_monitor/webapp/templates/_theme_bootstrap.html").read_text(
        encoding="utf-8"
    )
    bootstrap_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/theme_bootstrap.js"
    ).read_text(encoding="utf-8")

    assert "default('dark')" in bootstrap
    assert "initial_theme" in bootstrap
    assert "/static/dashboard/theme_bootstrap.js" in bootstrap
    assert 'meta[name="app-theme"]' in bootstrap_js
    assert 'return "dark";' not in bootstrap_js
    assert "prefers-color-scheme" not in bootstrap + bootstrap_js


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
    assert "data-theme-icon-light" in text
    assert "data-theme-icon-dark" in text
    assert "createCloseIcon" in text
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
    assert "keywordDefaultTab" in tabs_js
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

    assert 'document.addEventListener("error", handleImageError, true)' in cover_js
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
    assert 'data-notification-help-button="ntfy"' in notification_fields
    assert 'data-notification-help-button="discord"' in notification_fields
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
    notification_test_js = (dashboard_dir / "notification_test.js").read_text(encoding="utf-8")
    main_js = (dashboard_dir / "main.js").read_text(encoding="utf-8")
    utils_js = (dashboard_dir / "utils.js").read_text(encoding="utf-8")
    route = Path("src/facebook_monitor/webapp/routes/target_notifications.py").read_text(
        encoding="utf-8"
    )

    assert "export const setupNotificationTest" in notification_test_js
    assert "[data-notification-test]" in notification_test_js
    assert "event.preventDefault();" in notification_test_js
    assert "event.stopImmediatePropagation();" not in notification_test_js
    assert 'document.addEventListener("click"' in notification_test_js
    assert "document.getElementById(button.dataset.notificationTestFormId" in (notification_test_js)
    assert "button.dataset.notificationTestAction" in notification_test_js
    assert 'button.closest(".notification-test-actions")' in notification_test_js
    assert "?.parentElement" in notification_test_js
    assert "new FormData(form)" in notification_test_js
    assert 'Accept: "application/json"' in notification_test_js
    assert "payload?.timeout_ms" in notification_test_js
    assert "payload?.all_ok" in notification_test_js
    assert "payload?.sticky === true" in notification_test_js
    assert "payload?.message || payload?.error" in notification_test_js
    assert "notificationTestStatusKind" in notification_test_js
    assert "notificationTestTimeoutMs" in notification_test_js
    assert "notificationTestMessage" in notification_test_js
    assert "payload.ok === false" in notification_test_js
    assert "inlineStatusTimers" in utils_js
    assert "window.clearTimeout(previousTimer)" in utils_js
    assert '"/static/dashboard/notification_test.js"' in main_js
    assert "setupNotificationTest();" in main_js
    assert "def _wants_json_response" in route
    assert "build_notification_test_feedback" in route
    assert "feedback.to_payload()" in route


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


def test_hit_records_clear_refreshes_dashboard_from_read_model() -> None:
    """清空命中紀錄後 sidebar/card 應由 dashboard read model 更新，不在 JS 推導。"""

    dashboard_dir = Path("src/facebook_monitor/webapp/static/dashboard")
    hit_records_js = (dashboard_dir / "hit_records.js").read_text(encoding="utf-8")
    main_js = (dashboard_dir / "main.js").read_text(encoding="utf-8")

    assert "renderSidebarStatus" not in hit_records_js
    assert "data-sidebar-status" not in hit_records_js
    assert "refreshDashboard" in hit_records_js
    assert 'import { saveScrollPosition } from "/static/dashboard/state.js";' in hit_records_js
    assert "saveScrollPosition();\n  window.location.reload();" in hit_records_js
    assert 'import { applyDashboardPartialUpdate } from "/static/dashboard/partial_updates.js";' in (
        main_js
    )
    assert "refreshDashboard: () => applyDashboardPartialUpdate(state)" in main_js


def test_sidebar_menu_panel_floats_outside_sidebar_scroll_layer() -> None:
    """漢堡選單維持向右展開，但需脫離 sidebar scroll layer 避免被裁切。"""

    sidebar_layout_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/sidebar_layout.js"
    ).read_text(encoding="utf-8")
    sidebar_menu_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/sidebar_menu.js"
    ).read_text(encoding="utf-8")
    sidebar_css = Path("src/facebook_monitor/webapp/static/styles/sidebar.css").read_text(
        encoding="utf-8"
    )
    sidebar_template = sidebar_template_family_text()

    assert "document.body.appendChild(panel);" in sidebar_menu_js
    assert "data-sidebar-menu-floating" in sidebar_menu_js
    assert "SIDEBAR_MENU_ACTION_SELECTOR" in sidebar_menu_js
    assert "focusFirstSidebarMenuAction(panel);" in sidebar_menu_js
    assert "focusSidebarMenuTrigger(menu);" in sidebar_menu_js
    assert 'aria-controls="sidebar-menu-panel"' in sidebar_template
    assert 'id="sidebar-menu-panel"' in sidebar_template
    assert "rect.right + gap" in sidebar_menu_js
    assert 'event.target.closest?.(".sidebar-menu-panel")' in sidebar_menu_js
    assert "sidebarRect.right - panelWidth - viewportPadding" not in sidebar_menu_js
    assert (
        'panel.style.setProperty("--sidebar-menu-left", `${Math.max(viewportPadding, left)}px`);'
        in (sidebar_menu_js)
    )
    assert "closeSidebarMenu," in sidebar_layout_js
    assert (
        _css_rule_body(sidebar_css, ".sidebar-menu-panel,\n.sidebar-menu-panel.menu-panel").count(
            "z-index: 30;"
        )
        == 1
    )


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


def test_hit_records_modal_initial_focus_uses_safe_close_button() -> None:
    """查看紀錄 modal 開啟後不可讓清空紀錄成為初始 focus。"""

    hit_records_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/hit_records.js"
    ).read_text(encoding="utf-8")
    hit_records_template = Path(
        "src/facebook_monitor/webapp/templates/_hit_records_modal.html"
    ).read_text(encoding="utf-8")

    assert "const focusHitRecordsInitialControl" in hit_records_js
    assert 'modal.querySelector("[data-close-hit-records]")?.focus' in hit_records_js
    assert "preventScroll: true" in hit_records_js
    assert "openDialog(modal);\n      focusHitRecordsInitialControl(modal);" in hit_records_js
    assert "data-clear-hit-records autofocus" not in hit_records_template
    assert "autofocus data-clear-hit-records" not in hit_records_template


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
        index_template.index("/static/dashboard/main.js")
    )
    head_js = Path("src/facebook_monitor/webapp/static/dashboard/scroll_restore_head.js").read_text(
        encoding="utf-8"
    )
    body_js = Path("src/facebook_monitor/webapp/static/dashboard/scroll_restore_body.js").read_text(
        encoding="utf-8"
    )
    base_css = Path("src/facebook_monitor/webapp/static/styles/base.css").read_text(
        encoding="utf-8"
    )
    assert "/static/dashboard/scroll_restore_head.js" in head_bootstrap
    assert "/static/dashboard/scroll_restore_body.js" in body_bootstrap
    assert "facebookMonitor.dashboard.scrollY" in head_js
    assert "facebookMonitor.dashboard.scrollSavedAt" in head_js
    assert 'window.history.scrollRestoration = "manual";' in head_js
    assert "dataset.dashboardRestoringScroll" in head_js
    assert 'html[data-dashboard-restoring-scroll="1"] body' in base_css
    assert "facebookMonitor.dashboard.scrollY" in body_js
    assert "window.scrollTo(0, savedScrollY)" in body_js
    assert "window.requestAnimationFrame" in body_js


def test_modal_close_controls_use_one_visible_dismiss_pattern() -> None:
    """read-only modal 用右上角關閉；action/form modal 用底部取消，不同時顯示兩套。"""

    target_card = target_card_template_family_text()
    target_settings = Path(
        "src/facebook_monitor/webapp/templates/_target_settings_modal.html"
    ).read_text(encoding="utf-8")
    hit_records = Path("src/facebook_monitor/webapp/templates/_hit_records_modal.html").read_text(
        encoding="utf-8"
    )
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
    assert 'class="button--icon" type="button" data-sidebar-template-close' not in sidebar_template
    assert ">取消</button>" in sidebar_template

    assert target_card.count("data-close-keyword-help") == 1
    assert 'class="button--icon" type="button" data-close-keyword-help' in target_card
    assert '<button type="button" data-close-keyword-help>關閉</button>' not in target_card
    assert target_card.count("data-close-include-keyword-help") == 1
    assert 'class="button--icon" type="button" data-close-include-keyword-help' in target_card
    assert '<button type="button" data-close-include-keyword-help>關閉</button>' not in (
        target_card
    )

    assert target_card.count("data-close-scan-diagnostics") == 1
    assert re.search(
        r'<button[^>]*class="button--icon"[^>]*type="button"'
        r"[^>]*data-close-scan-diagnostics",
        target_card,
    )

    assert hit_records.count("data-close-hit-records") == 1
    assert re.search(
        r'<button[^>]*class="button--icon"[^>]*type="button"'
        r"[^>]*data-close-hit-records",
        hit_records,
    )
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

    settings_template = Path("src/facebook_monitor/webapp/templates/settings.html").read_text(
        encoding="utf-8"
    )

    assert 'action="/settings/notifications/clear-failed"' in settings_template
    assert "data-confirm-submit" in settings_template
    assert 'data-confirm-title="清除失敗通知"' in settings_template
    assert "不會重置已看紀錄" in settings_template
    assert "不會因為這個操作再次通知" in settings_template
    assert 'data-confirm-danger="1"' in settings_template
    assert "重試失敗通知" not in settings_template
    assert "通知 outbox" not in settings_template
