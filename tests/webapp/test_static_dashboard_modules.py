"""Dashboard static module contract tests。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.webapp.assets import ASSET_VERSION
from facebook_monitor.webapp.assets import DASHBOARD_MODULE_FILENAMES
from facebook_monitor.webapp.assets import build_dashboard_module_imports


def test_dashboard_import_map_covers_all_dashboard_modules() -> None:
    """新增 dashboard ES module 時必須進 import map，避免子模組快取漂移。"""

    dashboard_dir = Path("src/facebook_monitor/webapp/static/dashboard")
    module_filenames = tuple(sorted(path.name for path in dashboard_dir.glob("*.js")))

    assert tuple(sorted(DASHBOARD_MODULE_FILENAMES)) == module_filenames

    import_map = build_dashboard_module_imports()
    assert set(import_map) == {
        f"/static/dashboard/{filename}" for filename in DASHBOARD_MODULE_FILENAMES
    }
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
    csrf_js = (dashboard_dir / "csrf.js").read_text(encoding="utf-8")
    hit_records_js = (dashboard_dir / "hit_records.js").read_text(encoding="utf-8")

    assert "export const csrfHeaders" in csrf_js
    assert 'meta[name="csrf-token"]' in csrf_js
    assert '"X-CSRF-Token"' in csrf_js
    assert '"/static/dashboard/csrf.js"' in hit_records_js
    assert "headers: csrfHeaders()" in hit_records_js


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


def test_button_variants_use_shared_button_modifier_classes() -> None:
    """跨頁 primary / danger / icon buttons 使用一致 modifier class。"""

    templates_dir = Path("src/facebook_monitor/webapp/templates")
    styles = Path("src/facebook_monitor/webapp/static/styles/feedback.css").read_text(
        encoding="utf-8"
    )
    template_text = "\n".join(
        path.read_text(encoding="utf-8") for path in templates_dir.glob("*.html")
    )

    assert ".button--primary" in styles
    assert ".button--danger" in styles
    assert ".button--icon" in styles
    assert "button primary" not in template_text
    assert "icon-button" not in template_text
    assert 'class="danger"' not in template_text


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
