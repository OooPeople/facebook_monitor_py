"""Frozen bundled browser path helpers.

職責：集中描述 PyInstaller onedir 內隨附 browser executable 的相對路徑，
避免 runtime、updater layout 與 release validation 各自維護一套清單。
"""

from __future__ import annotations

from pathlib import Path


WINDOWS_BUNDLED_BROWSER_RELATIVE_PATHS = (
    "browser/chrome.exe",
    "_internal/browser/chrome.exe",
    "browser/chrome-win64/chrome.exe",
    "_internal/browser/chrome-win64/chrome.exe",
)
MACOS_BROWSER_APP_EXECUTABLES = (
    "Chromium.app/Contents/MacOS/Chromium",
    "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
    "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    (
        "chrome-mac-arm64/Google Chrome for Testing.app/"
        "Contents/MacOS/Google Chrome for Testing"
    ),
)
MACOS_BUNDLED_BROWSER_RELATIVE_PATHS = tuple(
    f"{prefix}/{relative_path}"
    for prefix in ("browser", "_internal/browser")
    for relative_path in MACOS_BROWSER_APP_EXECUTABLES
)
BUNDLED_BROWSER_RELATIVE_PATHS = (
    WINDOWS_BUNDLED_BROWSER_RELATIVE_PATHS + MACOS_BUNDLED_BROWSER_RELATIVE_PATHS
)


def find_bundled_browser_executable(app_base_dir: Path) -> Path | None:
    """在 frozen onedir root 中尋找隨附 browser executable。"""

    for relative_path in BUNDLED_BROWSER_RELATIVE_PATHS:
        candidate = app_base_dir / relative_path
        if candidate.is_file():
            return candidate
    return None
