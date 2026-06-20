"""Dashboard static module contract tests。"""

from __future__ import annotations

from pathlib import Path

from tests.webapp.static_contract_helpers import css_rule_body as _css_rule_body
from tests.webapp.static_contract_helpers import target_card_template_family_text


def test_dashboard_partial_update_reloads_when_sidebar_shell_signature_changes() -> None:
    """partial update 不可覆蓋已改變的 sidebar structure / template shell。"""

    partial_updates_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/partial_updates.js"
    ).read_text(encoding="utf-8")
    sidebar_template = Path("src/facebook_monitor/webapp/templates/_target_sidebar.html").read_text(
        encoding="utf-8"
    )

    assert "data-sidebar-layout-signature" in sidebar_template
    assert "data-sidebar-template-signature" in sidebar_template
    assert "payload.layout_signature" in partial_updates_js
    assert "payload.template_signature" in partial_updates_js
    assert "sidebarLayoutSignature" in partial_updates_js
    assert "sidebarTemplateSignature" in partial_updates_js
    assert "partial_update_requires_reload:target_list_changed" in partial_updates_js


def test_sidebar_and_card_menus_share_panel_and_action_styles() -> None:
    """sidebar 漢堡選單與卡片更多選單共用卡片系的 panel/action 樣式。"""

    sidebar_template = Path("src/facebook_monitor/webapp/templates/_target_sidebar.html").read_text(
        encoding="utf-8"
    )
    card_template = target_card_template_family_text()
    sidebar_css = Path("src/facebook_monitor/webapp/static/styles/sidebar.css").read_text(
        encoding="utf-8"
    )
    target_card_css = Path("src/facebook_monitor/webapp/static/styles/target-card.css").read_text(
        encoding="utf-8"
    )
    sidebar_menu_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/sidebar_menu.js"
    ).read_text(encoding="utf-8")
    sidebar_layout_js = Path(
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
    sidebar_menu_rule = _css_rule_body(
        sidebar_css, ".sidebar-menu-panel,\n.sidebar-menu-panel.menu-panel"
    )
    assert "position: fixed;" in sidebar_menu_rule
    assert "left: var(--sidebar-menu-left" in sidebar_menu_rule
    assert "inline-size: max-content;" in sidebar_menu_rule
    assert "min-inline-size: 108px;" in sidebar_menu_rule
    assert "max-inline-size: 160px;" in sidebar_menu_rule
    assert "right: auto;" in sidebar_menu_rule
    assert "top: var(--sidebar-menu-top" in sidebar_menu_rule
    assert "const positionSidebarMenuPanel" in sidebar_menu_js
    assert "trigger.getBoundingClientRect()" in sidebar_menu_js
    assert "const setSidebarMenuOpen" in sidebar_menu_js
    assert "event.preventDefault();" in sidebar_menu_js
    assert 'trigger?.setAttribute("aria-expanded", String(open));' in sidebar_menu_js
    assert "setupSidebarMenuPosition();" in sidebar_layout_js


def test_target_header_status_and_mode_are_grouped_in_subtitle() -> None:
    """卡片標題只放名稱；狀態與貼文/留言模式一起放在副標題。"""

    card_template = target_card_template_family_text()
    target_card_css = Path("src/facebook_monitor/webapp/static/styles/target-card.css").read_text(
        encoding="utf-8"
    )

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

    card_template = target_card_template_family_text()
    dashboard_payloads = Path("src/facebook_monitor/webapp/dashboard_payloads.py").read_text(
        encoding="utf-8"
    )
    assets = Path("src/facebook_monitor/webapp/assets.py").read_text(encoding="utf-8")
    dashboard_models = Path("src/facebook_monitor/webapp/dashboard_models.py").read_text(
        encoding="utf-8"
    )
    dashboard_target_refresh = Path(
        "src/facebook_monitor/webapp/dashboard_target_refresh.py"
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
    assert '"next_refresh_seconds": row.next_refresh_seconds' in dashboard_payloads
    assert "class NextRefreshDisplay" in dashboard_target_refresh
    assert "@cached_property\n    def next_refresh_display" in dashboard_models
    assert "row.runtime_state.display_next_due_at" in dashboard_target_refresh
    assert '"next_refresh_countdown.js"' in assets
    assert "setupNextRefreshCountdowns" in main_js
    assert "syncNextRefreshCountdown" in partial_updates_js
    assert "payload.next_refresh_seconds" in partial_updates_js
    assert "const SYNC_THRESHOLD_SECONDS = 1;" in countdown_js
    assert "Math.abs(localSeconds - incomingSeconds) <= SYNC_THRESHOLD_SECONDS" in (countdown_js)
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
    assert "state.pendingRefresh && !shouldDelayRefresh(state)" in revision_client_js
    assert "void updateWhenSafe(state);" in revision_client_js


def test_revision_client_uses_eventsource_as_primary_transport() -> None:
    """revision client 預設使用 EventSource 訂閱 dashboard revision event。"""

    revision_client_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/revision_client.js"
    ).read_text(encoding="utf-8")

    assert 'new EventSource("/api/dashboard-events")' in revision_client_js
    assert 'source.addEventListener("open"' in revision_client_js
    assert 'source.addEventListener("dashboard_revision"' in revision_client_js
    assert 'source.addEventListener("error"' in revision_client_js
    assert 'fetch("/api/dashboard-revision", { cache: "no-store" })' in revision_client_js
    revision_handler = revision_client_js.split(
        'source.addEventListener("dashboard_revision", (event) => {',
        1,
    )[1].split("});", 1)[0]
    assert "if (runtime.closed) return;" in revision_handler


def test_revision_client_falls_back_to_polling_without_eventsource() -> None:
    """沒有 EventSource 支援時，revision client 才直接啟動 polling fallback。"""

    revision_client_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/revision_client.js"
    ).read_text(encoding="utf-8")

    assert 'if (!("EventSource" in window)) {' in revision_client_js
    no_eventsource_branch = revision_client_js.split(
        'if (!("EventSource" in window)) {',
        1,
    )[1].split("}", 1)[0]
    assert "startPollingFallback(state, runtime);" in no_eventsource_branch
    assert "const pollingIntervalMs = 3000;" in revision_client_js
    assert 'fetch("/api/dashboard-revision", { cache: "no-store" })' in revision_client_js


def test_revision_client_delays_polling_after_sse_error() -> None:
    """SSE error 後延遲啟動 polling，避免正常 reconnect 時兩套主路徑並行。"""

    revision_client_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/revision_client.js"
    ).read_text(encoding="utf-8")

    error_handler = revision_client_js.split(
        'source.addEventListener("error", () => {',
        1,
    )[1].split("});", 1)[0]
    assert "setSseState(state, transportStates.sseReconnecting);" in error_handler
    assert "schedulePollingFallback(state, runtime);" in error_handler
    assert "startPollingFallback(state, runtime);" not in error_handler
    assert "if (runtime.pollingIntervalId) {" in error_handler
    assert "setPollingState(state);" in error_handler
    assert "clearFallbackTimer(runtime);" in error_handler
    assert "if (runtime.closed || runtime.fallbackTimerId || runtime.pollingIntervalId) return;" in (
        revision_client_js
    )
    assert "window.setTimeout(() => {" in revision_client_js


def test_revision_client_stops_polling_when_sse_opens() -> None:
    """EventSource open 後會取消 fallback timer 並停止 polling。"""

    revision_client_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/revision_client.js"
    ).read_text(encoding="utf-8")

    open_handler = revision_client_js.split(
        'source.addEventListener("open", () => {',
        1,
    )[1].split("});", 1)[0]
    assert "state.sseConnected = true;" in open_handler
    assert "setSseState(state, transportStates.sseOpen);" in open_handler
    assert "clearFallbackTimer(runtime);" in open_handler
    assert "stopPollingFallback(runtime);" in open_handler


def test_revision_client_does_not_create_duplicate_polling_intervals() -> None:
    """polling fallback interval 需有 guard，停止時清除並重設 id。"""

    revision_client_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/revision_client.js"
    ).read_text(encoding="utf-8")

    assert "if (runtime.pollingIntervalId) return;" in revision_client_js
    assert "runtime.pollingIntervalId = window.setInterval(" in revision_client_js
    assert "window.clearInterval(runtime.pollingIntervalId);" in revision_client_js
    assert "runtime.pollingIntervalId = 0;" in revision_client_js


def test_revision_client_teardown_closes_sse_and_clears_timers() -> None:
    """page teardown 會關閉 EventSource 並清除 revision client timers。"""

    revision_client_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/revision_client.js"
    ).read_text(encoding="utf-8")

    teardown = revision_client_js.split(
        "const teardownRevisionClient = (state, runtime) => {",
        1,
    )[1].split("};", 1)[0]
    assert "clearFallbackTimer(runtime);" in teardown
    assert "stopPollingFallback(runtime);" in teardown
    assert "window.clearInterval(runtime.pendingRefreshIntervalId);" in teardown
    assert "window.clearInterval(runtime.safetyPollIntervalId);" in teardown
    assert "runtime.source.close();" in teardown
    assert "state.revisionTransportState = transportStates.closed;" in teardown
    assert 'window.addEventListener("pagehide", teardown, { once: true });' in (
        revision_client_js
    )
    assert 'window.addEventListener("beforeunload", teardown, { once: true });' in (
        revision_client_js
    )


def test_revision_client_supports_polling_only_internal_transport_switch() -> None:
    """internal polling-only switch 不建立 EventSource，直接使用 revision endpoint。"""

    revision_client_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/revision_client.js"
    ).read_text(encoding="utf-8")

    assert "window.__DASHBOARD_REVISION_TRANSPORT__" in revision_client_js
    assert 'return configured === "polling" ? "polling" : "sse";' in revision_client_js
    polling_branch = revision_client_js.split(
        'if (selectedRevisionTransport() === "polling") {',
        1,
    )[1].split("}", 1)[0]
    assert "startPollingFallback(state, runtime);" in polling_branch
    assert "new EventSource" not in polling_branch


def test_dashboard_state_exposes_revision_transport_state() -> None:
    """dashboard state 保留粗分類 transport，並新增明確細狀態。"""

    state_js = Path("src/facebook_monitor/webapp/static/dashboard/state.js").read_text(
        encoding="utf-8"
    )
    main_js = Path("src/facebook_monitor/webapp/static/dashboard/main.js").read_text(
        encoding="utf-8"
    )

    assert 'revisionTransport: "initializing"' in state_js
    assert 'revisionTransportState: "sse_connecting"' in state_js
    assert "sseConnected: false" in state_js
    assert "setupRevisionClient(state);" in main_js


def test_dashboard_partial_update_toggles_database_invariant_warning_safely() -> None:
    """資料 invariant warning partial update 只用 textContent 與 hidden toggle。"""

    partial_updates_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/partial_updates.js"
    ).read_text(encoding="utf-8")

    warning_update = partial_updates_js.split(
        "const updateDatabaseInvariantWarning = (payload) => {",
        1,
    )[1].split("};", 1)[0]
    assert "[data-database-invariant-warning]" in warning_update
    assert "Boolean(payload?.has_violations)" in warning_update
    assert "warning.textContent = hasViolations ? (payload.message || \"\") : \"\";" in (
        warning_update
    )
    assert 'warning.toggleAttribute("hidden", !hasViolations);' in warning_update
    assert "innerHTML" not in warning_update


def test_dashboard_partial_update_reloads_when_degraded_empty_state_changes() -> None:
    """degraded 空狀態與正常空狀態切換時，partial update 必須重載整頁。"""

    index_template = Path("src/facebook_monitor/webapp/templates/index.html").read_text(
        encoding="utf-8"
    )
    partial_updates_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/partial_updates.js"
    ).read_text(encoding="utf-8")

    assert "data-dashboard-degraded-empty" in index_template
    assert "dashboard.dashboard_degraded" in index_template
    assert "dashboardPayload.dashboard_degraded" in partial_updates_js
    assert "[data-dashboard-degraded-empty]" in partial_updates_js
    assert "partial_update_requires_reload:dashboard_degraded_changed" in partial_updates_js


def test_scan_diagnostics_is_opened_from_card_more_menu() -> None:
    """掃描診斷入口收進卡片更多選單，內容顯示在共用 dialog 行為的 modal。"""

    card_template = target_card_template_family_text()
    target_card_css = Path("src/facebook_monitor/webapp/static/styles/target-card.css").read_text(
        encoding="utf-8"
    )
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
    assert "重置通知狀態" in card_template
    assert 'action="/targets/{{ row.target.id }}/notifications/clear"' in card_template
    assert 'data-confirm-title="重置通知狀態"' in card_template
    assert "通知紀錄與已看狀態" in card_template
    assert "可能再次通知" in card_template
    assert "不會刪除命中紀錄或設定" in card_template
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
    assert '<details class="debug-details scan-debug-details">' not in card_template
    assert "data-close-scan-diagnostics" in card_template
    assert "[data-scan-diagnostics-modal]" in modals_js
    assert "[data-close-scan-diagnostics]" in modals_js
    assert ".scan-debug-details .debug-summary" in partial_updates_js


def test_target_card_footer_stays_compact_after_moving_scan_diagnostics() -> None:
    """掃描診斷移入 modal 後，卡片底部 footer 不應保留過大的空白列。"""

    layout_css = Path("src/facebook_monitor/webapp/static/styles/layout.css").read_text(
        encoding="utf-8"
    )
    collapse_css = Path("src/facebook_monitor/webapp/static/styles/target-collapse.css").read_text(
        encoding="utf-8"
    )

    assert ".target-card {\n  padding-bottom: 12px;\n}" in layout_css
    assert ".target-footer-controls {\n  align-items: center;" in collapse_css
    assert "margin-top: 8px;" in collapse_css
    assert "height: 32px;" in collapse_css
    assert "width: 32px;" in collapse_css
