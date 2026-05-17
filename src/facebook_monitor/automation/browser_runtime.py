"""Playwright browser runtime abstraction。

職責：集中正式主路徑的 persistent browser context 啟動參數，
讓未來 EXE / portable app 要調整 browser backend 時只改一層。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from facebook_monitor.core.defaults import PYTHON_BROWSER_RUNTIME_DEFAULTS

DEFAULT_VIEWPORT_WIDTH = PYTHON_BROWSER_RUNTIME_DEFAULTS.viewport_width
DEFAULT_VIEWPORT_HEIGHT = PYTHON_BROWSER_RUNTIME_DEFAULTS.viewport_height
DEFAULT_TIMEOUT_SECONDS = PYTHON_BROWSER_RUNTIME_DEFAULTS.timeout_seconds
BROWSER_EXECUTABLE_ENV = "FACEBOOK_MONITOR_BROWSER_EXECUTABLE"


class BrowserMode(StrEnum):
    """描述可預留的 browser backend 種類。"""

    PLAYWRIGHT_CHROMIUM = "playwright_chromium"
    CHROME = "chrome"
    EDGE = "edge"
    CUSTOM = "custom"
    AUTO = "auto"


class BrowserRuntimeError(RuntimeError):
    """表示 browser runtime options 無法建立 persistent context。"""


@dataclass(frozen=True)
class BrowserRuntimeOptions:
    """保存啟動 Playwright persistent context 的共用選項。"""

    profile_dir: Path
    mode: BrowserMode = BrowserMode.PLAYWRIGHT_CHROMIUM
    executable_path: Path | None = None
    headless: bool = True
    viewport_width: int = DEFAULT_VIEWPORT_WIDTH
    viewport_height: int = DEFAULT_VIEWPORT_HEIGHT
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


def build_persistent_context_kwargs(options: BrowserRuntimeOptions) -> dict[str, Any]:
    """建立 `launch_persistent_context` 共用 kwargs。"""

    _validate_supported_mode(options)
    executable_path = _resolve_executable_path(options)
    timeout_seconds = max(float(options.timeout_seconds), 0.0)
    kwargs: dict[str, Any] = {
        "user_data_dir": str(options.profile_dir),
        "headless": options.headless,
        "viewport": {
            "width": int(options.viewport_width),
            "height": int(options.viewport_height),
        },
        "timeout": timeout_seconds * 1000,
    }
    if executable_path is not None:
        kwargs["executable_path"] = str(executable_path)
    return kwargs


async def launch_persistent_context_async(
    playwright: Any,
    options: BrowserRuntimeOptions,
) -> Any:
    """以 async Playwright 啟動 persistent context。"""

    browser_type = _select_browser_type(playwright, options)
    return await browser_type.launch_persistent_context(
        **build_persistent_context_kwargs(options)
    )


def launch_persistent_context_sync(
    playwright: Any,
    options: BrowserRuntimeOptions,
) -> Any:
    """以 sync Playwright 啟動 persistent context。"""

    browser_type = _select_browser_type(playwright, options)
    return browser_type.launch_persistent_context(**build_persistent_context_kwargs(options))


def _select_browser_type(playwright: Any, options: BrowserRuntimeOptions) -> Any:
    """依 runtime options 取得 Playwright browser type。"""

    _validate_supported_mode(options)
    return playwright.chromium


def _validate_supported_mode(options: BrowserRuntimeOptions) -> None:
    """目前正式支援 Playwright Chromium，可選擇 Playwright 預設或指定 executable。"""

    if options.mode == BrowserMode.PLAYWRIGHT_CHROMIUM:
        return
    raise BrowserRuntimeError(
        "Only browser_mode=playwright_chromium is supported for now."
    )


def _resolve_executable_path(options: BrowserRuntimeOptions) -> Path | None:
    """解析可選 Chromium executable path，供 EXE 使用外部或隨附 browser。"""

    configured_path = options.executable_path
    if configured_path is None:
        env_value = os.environ.get(BROWSER_EXECUTABLE_ENV, "").strip()
        if env_value:
            configured_path = Path(env_value)
        else:
            configured_path = _bundled_browser_executable_path()
            if configured_path is None:
                return None
    resolved_path = configured_path.expanduser().resolve()
    if not resolved_path.is_file():
        raise BrowserRuntimeError(
            f"Browser executable does not exist: {resolved_path}"
        )
    return resolved_path


def _bundled_browser_executable_path() -> Path | None:
    """在 frozen portable folder 中尋找隨附 Chromium executable。"""

    if not getattr(sys, "frozen", False):
        return None
    app_base_dir = Path(sys.executable).resolve().parent
    candidates = (
        app_base_dir / "browser" / "chrome.exe",
        app_base_dir / "_internal" / "browser" / "chrome.exe",
        app_base_dir / "browser" / "chrome-win64" / "chrome.exe",
        app_base_dir / "_internal" / "browser" / "chrome-win64" / "chrome.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None
