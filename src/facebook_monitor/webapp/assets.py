"""Web UI static asset 版本設定。

職責：集中管理 template 入口 CSS / JS 與 ES module graph 的 cache key，
避免多個 template 或 JS import 各自手動維護版本字串。
"""

ASSET_VERSION = "0.3.0"

DASHBOARD_MODULE_FILENAMES = (
    "api.js",
    "card_collapse.js",
    "csrf.js",
    "debug_tools.js",
    "dialogs.js",
    "forms.js",
    "hit_records.js",
    "main.js",
    "modals.js",
    "new_target.js",
    "next_refresh_countdown.js",
    "notification_help.js",
    "notification_test.js",
    "partial_updates.js",
    "revision_client.js",
    "settings.js",
    "sidebar.js",
    "sidebar_dom.js",
    "sidebar_layout.js",
    "sidebar_sorting.js",
    "sidebar_status.js",
    "state.js",
    "tabs.js",
    "theme.js",
    "utils.js",
)

VENDOR_MODULE_PATHS = (
    "/static/vendor/sortablejs/sortable.esm.js",
)


def versioned_static_path(path: str) -> str:
    """回傳帶有目前 asset version 的 static URL。"""

    return f"{path}?v={ASSET_VERSION}"


def build_dashboard_module_imports() -> dict[str, str]:
    """產生 dashboard ES module import map，讓子模組 URL 也會版本化。"""

    imports = {
        f"/static/dashboard/{filename}": versioned_static_path(
            f"/static/dashboard/{filename}"
        )
        for filename in DASHBOARD_MODULE_FILENAMES
    }
    imports.update({
        path: versioned_static_path(path)
        for path in VENDOR_MODULE_PATHS
    })
    return imports


__all__ = [
    "ASSET_VERSION",
    "DASHBOARD_MODULE_FILENAMES",
    "build_dashboard_module_imports",
    "versioned_static_path",
]
