"""Facebook group metadata resolver。

職責：在新增 target 時用 automation profile 開啟 group URL，
解析 Facebook page title，補上使用者未手動輸入的社團名稱。
"""

from __future__ import annotations

from dataclasses import dataclass
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
from facebook_monitor.core.defaults import PYTHON_BROWSER_RUNTIME_DEFAULTS
from facebook_monitor.core.external_url_policy import sanitize_facebook_group_cover_image_url
from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.facebook.browser_capture import get_start_page
from facebook_monitor.facebook.group_metadata_validation import body_mentions_unavailable_page
from facebook_monitor.facebook.group_metadata_validation import final_url_matches_expected_group
from facebook_monitor.facebook.group_metadata_validation import is_invalid_facebook_group_name
from facebook_monitor.facebook.route_detection import clean_facebook_page_title


GROUP_METADATA_WAIT_MS = PYTHON_BROWSER_RUNTIME_DEFAULTS.group_metadata_wait_ms


class GroupMetadataError(RuntimeError):
    """表示無法從 Facebook 頁面解析 group metadata。"""


@dataclass(frozen=True)
class GroupMetadata:
    """保存 Facebook 社團 metadata resolver 的最小結果。"""

    group_name: str = ""
    group_cover_image_url: str = ""


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
    wait_ms: int = GROUP_METADATA_WAIT_MS,
) -> str:
    """使用既有 async browser context 解析 Facebook group name。"""

    return (
        await resolve_group_metadata_with_context(
            context,
            canonical_url=canonical_url,
            wait_ms=wait_ms,
        )
    ).group_name


async def resolve_group_metadata_with_context(
    context: AsyncBrowserContextLike,
    *,
    canonical_url: str,
    wait_ms: int = GROUP_METADATA_WAIT_MS,
) -> GroupMetadata:
    """使用既有 async browser context 解析 Facebook group name 與 cover image URL。"""

    try:
        page = await context.new_page()
        try:
            await page.goto(canonical_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(wait_ms)
            body_text = await page.locator("body").inner_text(timeout=10000)
            if "log into facebook" in body_text.lower() or "登入 facebook" in body_text.lower():
                raise GroupMetadataError("Facebook 尚未登入，請先到設定頁開啟登入視窗完成登入")
            page_title = await page.title()
            group_name = clean_facebook_page_title(page_title)
            _ensure_valid_group_metadata_page(
                canonical_url=canonical_url,
                final_url=getattr(page, "url", ""),
                page_title=page_title,
                group_name=group_name,
                body_text=body_text,
            )
            cover_image_url = await _extract_cover_image_url_async(page)
        finally:
            await page.close()
    except GroupMetadataError:
        raise
    except (AsyncPlaywrightTimeoutError, AsyncPlaywrightError) as exc:
        raise GroupMetadataError(
            "無法自動抓取社團名稱："
            + format_failure_message_text(str(exc))
        ) from exc

    if not group_name:
        raise GroupMetadataError("無法自動抓取社團名稱，請稍後重試或填入自訂顯示名稱")
    return GroupMetadata(
        group_name=group_name,
        group_cover_image_url=cover_image_url,
    )


async def resolve_group_cover_image_with_context(
    context: AsyncBrowserContextLike,
    *,
    canonical_url: str,
    wait_ms: int = GROUP_METADATA_WAIT_MS,
) -> str:
    """使用既有 async browser context 只解析 Facebook group cover image URL。"""

    try:
        page = await context.new_page()
        try:
            await page.goto(canonical_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(wait_ms)
            body_text = await page.locator("body").inner_text(timeout=10000)
            if "log into facebook" in body_text.lower() or "登入 facebook" in body_text.lower():
                raise GroupMetadataError("Facebook 尚未登入，請先到設定頁開啟登入視窗完成登入")
            _ensure_valid_group_metadata_page(
                canonical_url=canonical_url,
                final_url=getattr(page, "url", ""),
                page_title=await page.title(),
                group_name="",
                body_text=body_text,
                require_group_name=False,
            )
            cover_image_url = await _extract_cover_image_url_async(page)
        finally:
            await page.close()
    except GroupMetadataError:
        raise
    except (AsyncPlaywrightTimeoutError, AsyncPlaywrightError) as exc:
        raise GroupMetadataError(
            "無法自動抓取社團封面："
            + format_failure_message_text(str(exc))
        ) from exc
    if not cover_image_url:
        raise GroupMetadataError("無法自動抓取社團封面，請稍後重試")
    return cover_image_url


def resolve_group_name_with_profile(
    *,
    profile_dir: Path,
    canonical_url: str,
    wait_ms: int = GROUP_METADATA_WAIT_MS,
) -> str:
    """使用 automation profile 開啟 group URL 並回傳清理後的社團名稱。"""

    return resolve_group_metadata_with_profile(
        profile_dir=profile_dir,
        canonical_url=canonical_url,
        wait_ms=wait_ms,
    ).group_name


def resolve_group_metadata_with_profile(
    *,
    profile_dir: Path,
    canonical_url: str,
    wait_ms: int = GROUP_METADATA_WAIT_MS,
) -> GroupMetadata:
    """使用 automation profile 開啟 group URL 並回傳社團名稱與 cover image URL。"""

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
                    page_title = page.title()
                    group_name = clean_facebook_page_title(page_title)
                    _ensure_valid_group_metadata_page(
                        canonical_url=canonical_url,
                        final_url=getattr(page, "url", ""),
                        page_title=page_title,
                        group_name=group_name,
                        body_text=body_text,
                    )
                    cover_image_url = _extract_cover_image_url_sync(page)
                finally:
                    context.close()
    except GroupMetadataError:
        raise
    except ProfileLeaseError as exc:
        raise GroupMetadataError(format_failure_message_text(str(exc))) from exc
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        message = str(exc).lower()
        if "user data directory is already in use" in message or "processsingleton" in message:
            raise GroupMetadataError("automation profile 目前被其他 Playwright 視窗使用中") from exc
        raise GroupMetadataError(
            "無法自動抓取社團名稱："
            + format_failure_message_text(str(exc))
        ) from exc

    if not group_name:
        raise GroupMetadataError("無法自動抓取社團名稱，請稍後重試或填入自訂顯示名稱")
    return GroupMetadata(
        group_name=group_name,
        group_cover_image_url=cover_image_url,
    )


async def _extract_cover_image_url_async(page: object) -> str:
    """從 Facebook group header 抽取 cover image URL；失敗時回傳空字串。"""

    if not hasattr(page, "evaluate"):
        return ""
    try:
        return _normalize_cover_image_url(
            await page.evaluate(_COVER_IMAGE_EXTRACTOR_SCRIPT)
        )
    except (AsyncPlaywrightTimeoutError, AsyncPlaywrightError, TypeError):
        return ""


def _extract_cover_image_url_sync(page: object) -> str:
    """同步版 cover image URL 抽取，供 Web UI 短期 profile 工作使用。"""

    if not hasattr(page, "evaluate"):
        return ""
    try:
        return _normalize_cover_image_url(page.evaluate(_COVER_IMAGE_EXTRACTOR_SCRIPT))
    except (PlaywrightTimeoutError, PlaywrightError, TypeError):
        return ""


def _normalize_cover_image_url(value: object) -> str:
    """整理 extractor 回傳值，避免非 URL 內容進入 metadata。"""

    result = sanitize_facebook_group_cover_image_url(value)
    return result.url if result.ok else ""


def _ensure_valid_group_metadata_page(
    *,
    canonical_url: str,
    final_url: object,
    page_title: str,
    group_name: str,
    body_text: str,
    require_group_name: bool = True,
) -> None:
    """拒絕 Facebook 錯誤頁或導向到非目標社團的 metadata。"""

    if body_mentions_unavailable_page(body_text):
        raise GroupMetadataError("Facebook 回傳無法使用的頁面，未更新 target metadata")
    if not final_url_matches_expected_group(
        final_url=final_url,
        canonical_url=canonical_url,
    ):
        raise GroupMetadataError("Facebook 導向非目標社團頁，未更新 target metadata")
    if is_invalid_facebook_group_name(page_title) or is_invalid_facebook_group_name(group_name):
        raise GroupMetadataError("Facebook 回傳錯誤頁，未更新 target metadata")
    if require_group_name and not group_name:
        raise GroupMetadataError("無法自動抓取社團名稱，請稍後重試或填入自訂顯示名稱")


_COVER_IMAGE_EXTRACTOR_SCRIPT = """
() => {
  const direct = document.querySelector('img[data-imgperflogname="profileCoverPhoto"]');
  if (direct) return direct.currentSrc || direct.getAttribute("src") || "";

  const candidates = [];
  for (const img of Array.from(document.images || [])) {
    const src = img.currentSrc || img.getAttribute("src") || "";
    if (!src) continue;
    const rect = img.getBoundingClientRect();
    let score = 0;
    if (/fbcdn\\.net|scontent\\./i.test(src)) score += 80;
    if (rect.width >= 300 && rect.height >= 120) score += 70;
    if (img.naturalWidth >= 500 && img.naturalHeight >= 180) score += 50;
    if (rect.top >= -80 && rect.top <= 650) score += 30;
    if (score > 0) candidates.push({ src, score });
  }
  candidates.sort((a, b) => b.score - a.score);
  return candidates[0]?.src || "";
}
"""
