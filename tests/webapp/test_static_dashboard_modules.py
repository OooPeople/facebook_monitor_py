"""Dashboard static module contract tests。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.webapp.assets import ASSET_VERSION
from facebook_monitor.webapp.assets import DASHBOARD_MODULE_FILENAMES
from facebook_monitor.webapp.assets import build_dashboard_module_imports


def _css_rule_body(css: str, selector: str) -> str:
    """擷取單一 selector 規則內容，讓樣式契約測試只檢查局部宣告。"""

    return css.split(f"{selector} {{", 1)[1].split("}", 1)[0]


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
    assert "controlSignature" in utils_js
    assert "onDirtyChange(dirty)" in utils_js
    assert "[data-dirty-submit]" in utils_js


def test_theme_toggle_contract_is_loaded_by_all_page_entrypoints() -> None:
    """Theme toggle module 必須走 DB-backed API 並被三個正式頁面入口載入。"""

    dashboard_dir = Path("src/facebook_monitor/webapp/static/dashboard")
    theme_js = (dashboard_dir / "theme.js").read_text(encoding="utf-8")

    assert 'fetch("/settings/theme"' in theme_js
    assert '"/static/dashboard/csrf.js"' in theme_js
    assert "csrfHeaders" in theme_js
    assert 'document.documentElement.dataset.theme' in theme_js
    assert "[data-theme-toggle]" in theme_js
    assert 'return "light";' in theme_js
    assert 'lightIcon?.toggleAttribute("hidden", isDark)' in theme_js
    assert 'darkIcon?.toggleAttribute("hidden", !isDark)' in theme_js
    assert "prefers-color-scheme" not in theme_js
    assert "localStorage" not in theme_js
    assert "document.cookie" not in theme_js
    for filename in ("main.js", "settings.js", "new_target.js"):
        text = (dashboard_dir / filename).read_text(encoding="utf-8")
        assert '"/static/dashboard/theme.js"' in text
        assert "setupThemeToggle();" in text


def test_theme_bootstrap_defaults_to_light_mode() -> None:
    """未保存使用者選擇時，server 注入主題預設必須固定為淺色。"""

    bootstrap = Path(
        "src/facebook_monitor/webapp/templates/_theme_bootstrap.html"
    ).read_text(encoding="utf-8")

    assert 'default("light")' in bootstrap
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
    assert "[data-keyword-help-button]" in modals_js
    assert "[data-keyword-help-modal]" in modals_js


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

    assert 'dialog.addEventListener("cancel", () => finish(false), { once: true });' in dialogs_js
    assert 'dialog.addEventListener("close", () => finish(false), { once: true });' in dialogs_js
    assert 'dialog.addEventListener("cancel", () => finish(null), { once: true });' in dialogs_js
    assert 'dialog.addEventListener("close", () => finish(null), { once: true });' in dialogs_js
    assert "let settled = false;" in dialogs_js
    assert "event.stopImmediatePropagation();" in dialogs_js
    assert main_js.index("setupConfirmSubmitForms();") < main_js.index("setupFormSubmitTracking();")


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

    assert 'import Sortable from "/static/vendor/sortablejs/sortable.esm.js";' not in sidebar_sorting_js
    assert 'const SORTABLE_MODULE_PATH = "/static/vendor/sortablejs/sortable.esm.js";' in (
        sidebar_sorting_js
    )
    assert "const loadSortable = async () =>" in sidebar_sorting_js
    assert "await import(SORTABLE_MODULE_PATH)" in sidebar_sorting_js
    assert "await setupSortables();" in sidebar_sorting_js
    assert "const SORTABLE_SWAP_THRESHOLD = 0.75;" in sidebar_sorting_js
    assert "animation: sortableAnimation()" in sidebar_sorting_js
    assert "prefers-reduced-motion: reduce" in sidebar_sorting_js
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
    assert ".sidebar-sort-ghost" in sidebar_css
    assert ".sidebar-sort-fallback" in sidebar_css
    assert ".sidebar-sort-chosen" in sidebar_css
    assert ".sidebar-sort-drag" in sidebar_css
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
    assert ".sidebar-group-actions .button--icon,\n.sidebar-group-actions-toggle" in sidebar_css
    assert "box-shadow: none;" in sidebar_css


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
    assert 'aria-label="監看清單操作"' in sidebar_template
    assert '<path d="M4 7h16"/>' in sidebar_template
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
    assert ".sidebar-menu-panel" in sidebar_css
    assert "position: fixed;" in sidebar_css
    assert "left: var(--sidebar-menu-left" in sidebar_css
    assert "top: var(--sidebar-menu-top" in sidebar_css
    assert "const positionSidebarMenuPanel" in sidebar_js
    assert "trigger.getBoundingClientRect()" in sidebar_js
    assert "setupSidebarMenuPosition();" in sidebar_js


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
    assert "可以一鍵套用設定到群組內所有 target" in template
    assert ".sidebar-template-modal,\n.sidebar-template-modal .modal-shell" in modals_css
    assert ".sidebar-template-modal .modal-shell" in modals_css
    assert "grid-template-rows: auto minmax(0, 1fr) auto;" in modals_css
    assert ".sidebar-template-modal .modal-body" in modals_css
    assert "background: var(--surface);" in modals_css


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
    assert 'class="button button--toolbar" type="submit">{{ row.monitoring_button_label }}</button>' in template_text
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
