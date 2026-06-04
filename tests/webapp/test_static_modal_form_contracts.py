"""Dashboard static module contract tests。"""

from __future__ import annotations

from pathlib import Path

from tests.webapp.static_contract_helpers import css_rule_body as _css_rule_body
from tests.webapp.static_contract_helpers import input_tags as _input_tags


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

    repository = Path("src/facebook_monitor/persistence/repositories/sidebar_layout.py").read_text(
        encoding="utf-8"
    )
    architecture = Path("docs/ARCHITECTURE.md").read_text(encoding="utf-8")

    assert "ensure_default_placements" not in repository
    assert "不得為缺失 placement 寫入 DB" in architecture
    assert "缺失 placement 採 lazy fallback 顯示在未分組區" in architecture


def test_target_settings_modal_uses_scroll_body_and_right_footer_actions() -> None:
    """target 設定 modal footer 常駐底部，取消/儲存按鈕靠右。"""

    template = Path("src/facebook_monitor/webapp/templates/_target_settings_modal.html").read_text(
        encoding="utf-8"
    )
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

    template = Path("src/facebook_monitor/webapp/templates/_target_settings_modal.html").read_text(
        encoding="utf-8"
    )
    scan_fields = Path(
        "src/facebook_monitor/webapp/templates/_scan_settings_fields.html"
    ).read_text(encoding="utf-8")
    refresh_fields = Path(
        "src/facebook_monitor/webapp/templates/_refresh_settings_fields.html"
    ).read_text(encoding="utf-8")
    notification_fields = Path(
        "src/facebook_monitor/webapp/templates/_notification_settings_fields.html"
    ).read_text(encoding="utf-8")

    assert "{% set scan_form_id = config_form_id %}" in template
    assert "{% set refresh_form_id = config_form_id %}" in template
    assert "{% set notification_test_form_id = config_form_id %}" in template
    assert "{% set notification_form_id = config_form_id %}" in template

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
    dialogs_js = Path("src/facebook_monitor/webapp/static/dashboard/dialogs.js").read_text(
        encoding="utf-8"
    )
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
    assert 'import { saveScrollPosition } from "/static/dashboard/state.js";' in (sidebar_layout_js)
    assert "const reloadDashboardPreservingScroll = () =>" in sidebar_layout_js
    assert "saveScrollPosition();\n  window.location.reload();" in sidebar_layout_js
    assert "reloadDashboardPreservingScroll();" in sidebar_layout_js


def test_keyword_ignore_phrase_placeholder_uses_semicolon_separator() -> None:
    """排除字忽略片語 placeholder 必須使用和 parser 一致的分號範例。"""

    card_template = Path("src/facebook_monitor/webapp/templates/_target_card.html").read_text(
        encoding="utf-8"
    )

    assert 'placeholder="例如：全收;回收"' in card_template
    assert "例如：全收, 回收" not in card_template


def test_keyword_ignore_phrase_help_uses_short_examples_without_footer_note() -> None:
    """排除字忽略片語說明只保留簡短規則與兩個例子。"""

    card_template = Path("src/facebook_monitor/webapp/templates/_target_card.html").read_text(
        encoding="utf-8"
    )
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

    card_template = Path("src/facebook_monitor/webapp/templates/_target_card.html").read_text(
        encoding="utf-8"
    )
    target_card_css = Path("src/facebook_monitor/webapp/static/styles/target-card.css").read_text(
        encoding="utf-8"
    )
    modals_css = Path("src/facebook_monitor/webapp/static/styles/modals.css").read_text(
        encoding="utf-8"
    )

    assert "data-include-keyword-help-button" in card_template
    assert "data-include-keyword-help-modal" in card_template
    assert "include-keywords-{{ row.target.id }}" in card_template
    assert "include_keywords_2" in card_template
    assert "include_keywords_3" in card_template
    assert "關鍵字輸入規則" in card_template
    assert '<div class="keyword-help-rule-list">' in card_template
    assert '<section class="modal-section-card keyword-help-rule-list">' not in (card_template)
    assert "<code>;</code> 表示 <strong>OR</strong>" in card_template
    assert "空格表示 <strong>AND</strong>" in card_template
    assert "關鍵字 1 / 2 / 3 之間也會套用 <strong>AND</strong>" in card_template
    assert "<code>搖滾;6880;5880</code>" in card_template
    assert "只要出現 <code>搖滾</code> 或 <code>6880</code> 或 <code>5880</code> 就通知。" in (
        card_template
    )
    assert "<code>搖滾 6880;搖滾 5880</code>" in card_template
    assert (
        "代表 <code>搖滾</code> 且 <code>6880</code>，或 <code>搖滾</code> 且 <code>5880</code> 才通知。"
        in (card_template)
    )
    assert "排除關鍵字只沿用分號 OR 與空格 AND，不套用關鍵字 1 / 2 / 3 分組。" in card_template
    assert ".keyword-rule-field-label" in target_card_css
    assert ".keyword-help-example" in modals_css


def test_button_variants_use_shared_button_modifier_classes() -> None:
    """跨頁 primary / danger / icon buttons 使用一致 modifier class。"""

    templates_dir = Path("src/facebook_monitor/webapp/templates")
    styles = Path("src/facebook_monitor/webapp/static/styles/feedback.css").read_text(
        encoding="utf-8"
    )
    target_card_css = Path("src/facebook_monitor/webapp/static/styles/target-card.css").read_text(
        encoding="utf-8"
    )
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
        "data-monitoring-button>{{ row.monitoring_button_label }}</button>"
    ) in template_text
    assert (
        'class="button button--toolbar" type="submit" form="config-{{ row.target.id }}"'
        in template_text
    )
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
    assert target_refresh_template.index(
        "<strong>浮動刷新</strong>"
    ) < target_refresh_template.index("<strong>固定刷新</strong>")
    assert (
        "{% set refresh_mode_value = refresh_source.refresh_mode if refresh_source and "
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
    assert 'section === "all"' in sidebar_layout_js


def test_hit_records_modal_renders_keyword_segments_without_inner_html() -> None:
    """完整命中紀錄 modal 必須用 DOM nodes render keyword highlight。"""

    hit_records_js = Path("src/facebook_monitor/webapp/static/dashboard/hit_records.js").read_text(
        encoding="utf-8"
    )
    modals_css = Path("src/facebook_monitor/webapp/static/styles/modals.css").read_text(
        encoding="utf-8"
    )

    assert "const appendTextSegments" in hit_records_js
    assert 'document.createElement("mark")' in hit_records_js
    assert "keyword-highlight" in hit_records_js
    assert "item.content_segments" in hit_records_js
    assert "content.innerHTML" not in hit_records_js
    content_rule = _css_rule_body(modals_css, ".hit-record-content p")
    assert "white-space: pre-line;" in content_rule


def test_partial_update_syncs_rename_modal_name_without_overwriting_active_input() -> None:
    """背景 partial update 更新 target 名稱時，要同步更名 modal 的預填值。"""

    partial_updates_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/partial_updates.js"
    ).read_text(encoding="utf-8")

    assert "const updateRenameInput" in partial_updates_js
    assert 'input[name="display_name"]' in partial_updates_js
    assert "payload.rename_display_name" in partial_updates_js
    assert "document.activeElement === input" in partial_updates_js
    assert 'input.setAttribute("value", nextValue)' in partial_updates_js


def test_partial_update_syncs_runtime_action_and_guard_messages() -> None:
    """背景 partial update 必須同步開始/停止按鈕與 runtime error/skip reason。"""

    card_template = Path("src/facebook_monitor/webapp/templates/_target_card.html").read_text(
        encoding="utf-8"
    )
    partial_updates_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/partial_updates.js"
    ).read_text(encoding="utf-8")
    dashboard_payloads = Path("src/facebook_monitor/webapp/dashboard_payloads.py").read_text(
        encoding="utf-8"
    )

    assert "data-monitoring-form" in card_template
    assert "data-monitoring-button" in card_template
    assert "data-runtime-error" in card_template
    assert "data-runtime-skip-reason" in card_template
    assert "data-latest-error-indicator" in card_template
    assert "data-latest-error-kind" in card_template
    assert "data-latest-error-separator" in card_template
    assert "monitoring_action" in dashboard_payloads
    assert "monitoring_button_label" in dashboard_payloads
    assert "runtime_error" in dashboard_payloads
    assert "runtime_skip_reason" in dashboard_payloads
    assert "has_latest_failed_scan" in dashboard_payloads
    assert "latest_error_indicator_label" in dashboard_payloads
    assert "latest_error_indicator_title" in dashboard_payloads
    assert "latest_error_indicator_kind" in dashboard_payloads
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
