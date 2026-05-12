"""Playwright browser runtime abstraction。

職責：集中正式主路徑的 persistent browser context 啟動參數，
讓未來 EXE / portable app 要調整 browser backend 時只改一層。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


DEFAULT_VIEWPORT_WIDTH = 1366
DEFAULT_VIEWPORT_HEIGHT = 900
DEFAULT_TIMEOUT_SECONDS = 120.0


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
    timeout_seconds = max(float(options.timeout_seconds), 0.0)
    return {
        "user_data_dir": str(options.profile_dir),
        "headless": options.headless,
        "viewport": {
            "width": int(options.viewport_width),
            "height": int(options.viewport_height),
        },
        "timeout": timeout_seconds * 1000,
    }


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
    """目前正式支援 Playwright bundled Chromium；其他 backend 先保留介面。"""

    if options.mode == BrowserMode.PLAYWRIGHT_CHROMIUM and options.executable_path is None:
        return
    raise BrowserRuntimeError(
        "Only browser_mode=playwright_chromium without executable_path is supported for now."
    )
