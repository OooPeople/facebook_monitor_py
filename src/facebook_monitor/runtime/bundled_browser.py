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
MACOS_BUNDLED_BROWSER_RELATIVE_PATHS = (
    "browser/Chromium.app/Contents/MacOS/Chromium",
    "_internal/browser/Chromium.app/Contents/MacOS/Chromium",
    "browser/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
    "_internal/browser/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
    "browser/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "_internal/browser/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    (
        "browser/chrome-mac-arm64/Google Chrome for Testing.app/"
        "Contents/MacOS/Google Chrome for Testing"
    ),
    (
        "_internal/browser/chrome-mac-arm64/Google Chrome for Testing.app/"
        "Contents/MacOS/Google Chrome for Testing"
    ),
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
