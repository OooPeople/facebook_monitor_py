"""Updater 平台 layout 策略。

職責：集中描述 frozen onedir 在 Windows 與 macOS Apple Silicon 上的
檔案形狀，讓 updater apply / launcher 不把平台細節散落在流程中。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from facebook_monitor.runtime.bundled_browser import MACOS_BUNDLED_BROWSER_RELATIVE_PATHS


WINDOWS_APP_ENTRY = "facebook-monitor.exe"
WINDOWS_UPDATER_ENTRY = "facebook-monitor-updater.exe"
MACOS_APP_ENTRY = "facebook-monitor"
MACOS_UPDATER_ENTRY = "facebook-monitor-updater"
MACOS_APP_BUNDLE_NAME = "Facebook Monitor.app"
MACOS_APP_BUNDLE_INFO_PLIST = f"{MACOS_APP_BUNDLE_NAME}/Contents/Info.plist"
MACOS_APP_BUNDLE_LAUNCHER = (
    f"{MACOS_APP_BUNDLE_NAME}/Contents/MacOS/facebook-monitor-launcher"
)
MACOS_APP_BUNDLE_ICON = (
    f"{MACOS_APP_BUNDLE_NAME}/Contents/Resources/facebook-monitor.icns"
)
MACOS_APP_BUNDLE_LAUNCHER_ENV = "FACEBOOK_MONITOR_MACOS_APP_LAUNCHER"
MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE = "1"


@dataclass(frozen=True)
class UpdaterLayoutPolicy:
    """描述 updater 可辨識與替換的 frozen app layout。"""

    platform_key: str
    app_entry_name: str
    updater_entry_name: str
    required_staging_files: tuple[str, ...]
    required_current_paths: tuple[str, ...]
    required_staging_any_groups: tuple[tuple[str, ...], ...] = ()
    required_current_any_groups: tuple[tuple[str, ...], ...] = ()
    temp_copy_dirs: tuple[str, ...] = ("_internal",)
    restart_entry_name: str | None = None

    def app_entry(self, root: Path) -> Path:
        """回傳 app entry path。"""

        return root / self.app_entry_name

    def updater_entry(self, root: Path) -> Path:
        """回傳 updater entry path。"""

        return root / self.updater_entry_name

    def restart_entry(self, root: Path) -> Path:
        """回傳 updater 套用後重新啟動時應使用的 entry path。"""

        return root / (self.restart_entry_name or self.app_entry_name)


WINDOWS_LAYOUT_POLICY = UpdaterLayoutPolicy(
    platform_key="windows",
    app_entry_name=WINDOWS_APP_ENTRY,
    updater_entry_name=WINDOWS_UPDATER_ENTRY,
    required_staging_files=(
        WINDOWS_APP_ENTRY,
        WINDOWS_UPDATER_ENTRY,
        "_internal/browser/chrome.exe",
        "_internal/assets/facebook-monitor.ico",
        "_internal/assets/facebook-monitor-tray.ico",
    ),
    required_current_paths=(
        WINDOWS_APP_ENTRY,
        WINDOWS_UPDATER_ENTRY,
        "_internal",
    ),
)
MACOS_ARM64_LAYOUT_POLICY = UpdaterLayoutPolicy(
    platform_key="macos-arm64",
    app_entry_name=MACOS_APP_ENTRY,
    updater_entry_name=MACOS_UPDATER_ENTRY,
    required_staging_files=(
        MACOS_APP_ENTRY,
        MACOS_UPDATER_ENTRY,
        MACOS_APP_BUNDLE_INFO_PLIST,
        MACOS_APP_BUNDLE_LAUNCHER,
        MACOS_APP_BUNDLE_ICON,
    ),
    required_current_paths=(
        MACOS_APP_ENTRY,
        MACOS_UPDATER_ENTRY,
    ),
    required_staging_any_groups=(
        MACOS_BUNDLED_BROWSER_RELATIVE_PATHS,
    ),
    required_current_any_groups=(("_internal", "browser"),),
    restart_entry_name=MACOS_APP_BUNDLE_LAUNCHER,
)


def detect_layout_policy(app_base_dir: Path) -> UpdaterLayoutPolicy:
    """依現有 app root 判斷 updater layout；未知時保留 Windows 既有錯誤語義。"""

    if (app_base_dir / MACOS_APP_ENTRY).is_file():
        return MACOS_ARM64_LAYOUT_POLICY
    return WINDOWS_LAYOUT_POLICY


def layout_policy_for_updater_path(updater_path: Path) -> UpdaterLayoutPolicy:
    """依 updater 檔名判斷 layout policy。"""

    if updater_path.name == MACOS_UPDATER_ENTRY:
        return MACOS_ARM64_LAYOUT_POLICY
    return WINDOWS_LAYOUT_POLICY


def supported_layout_policies() -> tuple[UpdaterLayoutPolicy, ...]:
    """回傳目前 updater 可辨識的 layout policies。"""

    return (WINDOWS_LAYOUT_POLICY, MACOS_ARM64_LAYOUT_POLICY)


def missing_required_paths(
    root: Path,
    *,
    required_paths: tuple[str, ...],
    any_groups: tuple[tuple[str, ...], ...] = (),
) -> tuple[Path, ...]:
    """檢查必要 path；any group 只需其中一個存在。"""

    missing: list[Path] = [root / path for path in required_paths if not (root / path).exists()]
    for group in any_groups:
        if not any((root / path).exists() for path in group):
            missing.append(root / group[0])
    return tuple(missing)
