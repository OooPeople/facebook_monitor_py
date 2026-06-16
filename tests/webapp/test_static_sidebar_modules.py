"""Dashboard static module contract tests。"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.webapp.static_contract_helpers import css_rule_body as _css_rule_body


def test_sidebar_sort_mode_does_not_reserve_drag_column_when_inactive() -> None:
    """sidebar 排序模式不靠額外欄位顯示 drag handle，避免壓縮文字。"""

    sidebar_css = Path("src/facebook_monitor/webapp/static/styles/sidebar.css").read_text(
        encoding="utf-8"
    )
    sidebar_template = Path("src/facebook_monitor/webapp/templates/_target_sidebar.html").read_text(
        encoding="utf-8"
    )

    assert ".sidebar-list-item {\n  align-items: center;" in sidebar_css
    assert "grid-template-columns: minmax(0, 1fr);\n" in sidebar_css
    assert ".target-sidebar.sorting .sidebar-list-item" in sidebar_css
    assert "grid-template-columns: minmax(0, 1fr) 30px;" not in sidebar_css
    assert "position: relative;" in _css_rule_body(sidebar_css, ".sidebar-list-item")
    assert "data-sidebar-confirm-sort hidden>確認</button>\n      <details" in sidebar_template


def test_sidebar_sorting_uses_sortablejs_with_handle_threshold_and_animation() -> None:
    """sidebar 排序互動由 SortableJS 模組統一處理 handle、交換門檻與動畫。"""

    sidebar_dom_js = Path("src/facebook_monitor/webapp/static/dashboard/sidebar_dom.js").read_text(
        encoding="utf-8"
    )
    sidebar_sorting_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/sidebar_sorting.js"
    ).read_text(encoding="utf-8")
    sidebar_layout_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/sidebar_layout.js"
    ).read_text(encoding="utf-8")
    sidebar_css = Path("src/facebook_monitor/webapp/static/styles/sidebar.css").read_text(
        encoding="utf-8"
    )
    sidebar_template = Path("src/facebook_monitor/webapp/templates/_target_sidebar.html").read_text(
        encoding="utf-8"
    )
    sortable_license = Path(
        "src/facebook_monitor/webapp/static/vendor/sortablejs/LICENSE"
    ).read_text(encoding="utf-8")

    assert "export const listTargetIds" in sidebar_dom_js
    assert "export const prefersReducedMotion" in sidebar_dom_js
    assert "prefers-reduced-motion: reduce" in sidebar_dom_js
    assert '"/static/dashboard/sidebar_dom.js"' in sidebar_sorting_js
    assert '"/static/dashboard/sidebar_dom.js"' in sidebar_layout_js
    assert (
        'import Sortable from "/static/vendor/sortablejs/sortable.esm.js";'
        not in sidebar_sorting_js
    )
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
    sidebar_template = Path("src/facebook_monitor/webapp/templates/_target_sidebar.html").read_text(
        encoding="utf-8"
    )

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
    drag_rule = _css_rule_body(
        sidebar_css,
        ".sidebar-sort-drag,\n.sidebar-sort-drag .sidebar-drag-handle,\n.sidebar-sort-drag .sidebar-group-collapse",
    )

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
    sidebar_js = Path("src/facebook_monitor/webapp/static/dashboard/sidebar_layout.js").read_text(
        encoding="utf-8"
    )
    sidebar_template = Path("src/facebook_monitor/webapp/templates/_target_sidebar.html").read_text(
        encoding="utf-8"
    )

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
    disabled_hover_rule = _css_rule_body(sidebar_css, ".sidebar-group-monitoring:disabled:hover")
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
    module_path = Path("src/facebook_monitor/webapp/static/dashboard/sidebar_status.js").resolve()
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
    sidebar_template = Path("src/facebook_monitor/webapp/templates/_target_sidebar.html").read_text(
        encoding="utf-8"
    )

    assert "sidebar-action-icon--chevron" in sidebar_template
    assert "sidebar-action-icon--dots" in sidebar_template
    assert "sidebar-action-icon--play" in sidebar_template
    assert "sidebar-action-icon--stop" in sidebar_template
    assert 'aria-label="監看清單操作"' in sidebar_template
    assert '<path d="M4 7h16"/>' in sidebar_template
    assert '<path d="M8 5v14l11-7Z"/>' in sidebar_template
    assert 'aria-label="重新命名群組"' in sidebar_template
    assert 'aria-label="群組設定模板"' in sidebar_template
    assert 'aria-label="刪除群組"' in sidebar_template
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
    sidebar_template = Path("src/facebook_monitor/webapp/templates/_target_sidebar.html").read_text(
        encoding="utf-8"
    )
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


def test_sidebar_template_save_reloads_dashboard_shell() -> None:
    """模板儲存成功後需 reload，避免 server-coerced template modal 留在舊狀態。"""

    sidebar_layout_js = Path(
        "src/facebook_monitor/webapp/static/dashboard/sidebar_layout.js"
    ).read_text(encoding="utf-8")
    save_block = sidebar_layout_js.split(
        'modal.querySelector("[data-sidebar-template-save]")?.addEventListener("click"',
        1,
    )[1].split('modal.querySelectorAll("[data-sidebar-template-apply]")', 1)[0]

    assert "/template" in save_block
    assert 'showToast?.("群組模板已儲存", "success");' in save_block
    assert "reloadDashboardPreservingScroll();" in save_block


