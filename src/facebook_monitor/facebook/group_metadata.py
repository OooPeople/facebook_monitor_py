"""Facebook group metadata resolver。

職責：在新增 target 時用 automation profile 開啟 group URL，
解析 Facebook page title，補上使用者未手動輸入的社團名稱。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from playwright.async_api import Error as AsyncPlaywrightError
from playwright.async_api import TimeoutError as AsyncPlaywrightTimeoutError
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from facebook_monitor.automation.browser_runtime import BrowserRuntimeOptions
from facebook_monitor.automation.browser_runtime import launch_persistent_context_sync
from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.facebook.browser_capture import get_start_page
from facebook_monitor.facebook.route_detection import clean_facebook_page_title


class GroupMetadataError(RuntimeError):
    """表示無法從 Facebook 頁面解析 group metadata。"""


class AsyncLocatorLike(Protocol):
    """描述 metadata resolver 需要的 async locator 能力。"""

    async def inner_text(self, *, timeout: int) -> str:
        """讀取 locator 文字。"""


class AsyncPageLike(Protocol):
    """描述 metadata resolver 需要的 async page 能力。"""

    async def goto(self, url: str, *, wait_until: str) -> object:
        """前往指定 URL。"""

    async def wait_for_timeout(self, timeout: int) -> None:
        """等待指定毫秒。"""

    def locator(self, selector: str) -> AsyncLocatorLike:
        """取得 locator。"""

    async def title(self) -> str:
        """讀取頁面標題。"""

    async def close(self) -> None:
        """關閉 page。"""


class AsyncBrowserContextLike(Protocol):
    """描述 metadata resolver 需要的 async browser context 能力。"""

    async def new_page(self) -> AsyncPageLike:
        """建立新 page。"""


async def resolve_group_name_with_context(
    context: AsyncBrowserContextLike,
    *,
    canonical_url: str,
    wait_ms: int = 3000,
) -> str:
    """使用既有 async browser context 解析 Facebook group name。"""

    try:
        page = await context.new_page()
        try:
            await page.goto(canonical_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(wait_ms)
            body_text = await page.locator("body").inner_text(timeout=10000)
            if "log into facebook" in body_text.lower() or "登入 facebook" in body_text.lower():
                raise GroupMetadataError("Facebook 尚未登入，請先到設定頁開啟登入視窗完成登入")
            group_name = clean_facebook_page_title(await page.title())
        finally:
            await page.close()
    except GroupMetadataError:
        raise
    except (AsyncPlaywrightTimeoutError, AsyncPlaywrightError) as exc:
        raise GroupMetadataError(f"無法自動抓取社團名稱: {exc}") from exc

    if not group_name:
        raise GroupMetadataError("無法自動抓取社團名稱，請稍後重試或填入自訂顯示名稱")
    return group_name


def resolve_group_name_with_profile(
    *,
    profile_dir: Path,
    canonical_url: str,
    wait_ms: int = 3000,
) -> str:
    """使用 automation profile 開啟 group URL 並回傳清理後的社團名稱。"""

    if not profile_dir.exists():
        raise GroupMetadataError("automation profile 不存在，請先到設定頁開啟 Facebook 登入視窗並登入")

    try:
        with acquire_profile_lease(profile_dir, "社團名稱解析"):
            with sync_playwright() as playwright:
                context = launch_persistent_context_sync(
                    playwright,
                    BrowserRuntimeOptions(profile_dir=profile_dir, headless=True),
                )
                try:
                    page = get_start_page(context)
                    page.goto(canonical_url, wait_until="domcontentloaded")
                    page.wait_for_timeout(wait_ms)
                    body_text = page.locator("body").inner_text(timeout=10000)
                    if "log into facebook" in body_text.lower() or "登入 facebook" in body_text.lower():
                        raise GroupMetadataError("Facebook 尚未登入，請先到設定頁開啟登入視窗完成登入")
                    group_name = clean_facebook_page_title(page.title())
                finally:
                    context.close()
    except GroupMetadataError:
        raise
    except ProfileLeaseError as exc:
        raise GroupMetadataError(str(exc)) from exc
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        message = str(exc).lower()
        if "user data directory is already in use" in message or "processsingleton" in message:
            raise GroupMetadataError("automation profile 目前被其他 Playwright 視窗使用中") from exc
        raise GroupMetadataError(f"無法自動抓取社團名稱: {exc}") from exc

    if not group_name:
        raise GroupMetadataError("無法自動抓取社團名稱，請稍後重試或填入自訂顯示名稱")
    return group_name
