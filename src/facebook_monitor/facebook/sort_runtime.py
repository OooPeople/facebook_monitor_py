"""Sort adjustment runtime orchestration。

職責：銜接 enabled 判斷、native attempt、fallback recovery 與 JS fallback
normalization；不直接保存 DOM selector 細節。
"""

from __future__ import annotations

from typing import Any

from facebook_monitor.facebook.sort_menu_diagnostics import recover_sort_menu_before_js_fallback
from facebook_monitor.facebook.sort_menu_diagnostics import (
    recover_sort_menu_before_js_fallback_async,
)
from facebook_monitor.facebook.sort_native_click import try_native_comment_sort_click
from facebook_monitor.facebook.sort_native_click import try_native_comment_sort_click_async
from facebook_monitor.facebook.sort_native_click import try_native_feed_sort_click
from facebook_monitor.facebook.sort_native_click import try_native_feed_sort_click_async
from facebook_monitor.facebook.sort_results import COMMENT_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_results import FEED_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_results import SORT_METHOD_JS_FALLBACK
from facebook_monitor.facebook.sort_results import SortAdjustResult
from facebook_monitor.facebook.sort_results import build_disabled_sort_adjust_result
from facebook_monitor.facebook.sort_results import normalize_sort_adjust_result
from facebook_monitor.facebook.sort_results import with_sort_diagnostics
from facebook_monitor.facebook.sort_adjust_scripts import COMMENT_SORT_ADJUST_SCRIPT
from facebook_monitor.facebook.sort_adjust_scripts import FEED_SORT_ADJUST_SCRIPT


def ensure_preferred_feed_sort(page: Any, *, enabled: bool) -> SortAdjustResult:
    """掃描前保守嘗試把 group feed 切到新貼文排序。"""

    if not enabled:
        return build_disabled_sort_adjust_result(FEED_SORT_NEWEST_LABEL)

    native_attempt = try_native_feed_sort_click(page)
    if native_attempt.result is not None:
        return native_attempt.result

    recover_sort_menu_before_js_fallback(page, native_attempt.diagnostics)
    result = page.evaluate(FEED_SORT_ADJUST_SCRIPT, FEED_SORT_NEWEST_LABEL)
    fallback_result = normalize_sort_adjust_result(
        result,
        preferred_label=FEED_SORT_NEWEST_LABEL,
    )
    return _with_fallback_diagnostics(fallback_result, native_attempt.diagnostics)


def ensure_preferred_comment_sort(page: Any, *, enabled: bool) -> SortAdjustResult:
    """掃描前保守嘗試把單篇貼文留言切到由新到舊。"""

    if not enabled:
        return build_disabled_sort_adjust_result(COMMENT_SORT_NEWEST_LABEL)

    native_attempt = try_native_comment_sort_click(page)
    if native_attempt.result is not None:
        return native_attempt.result

    recover_sort_menu_before_js_fallback(page, native_attempt.diagnostics)
    result = page.evaluate(COMMENT_SORT_ADJUST_SCRIPT, COMMENT_SORT_NEWEST_LABEL)
    fallback_result = normalize_sort_adjust_result(
        result,
        preferred_label=COMMENT_SORT_NEWEST_LABEL,
    )
    return _with_fallback_diagnostics(fallback_result, native_attempt.diagnostics)


async def ensure_preferred_feed_sort_async(page: Any, *, enabled: bool) -> SortAdjustResult:
    """resident main worker 掃描前嘗試把 group feed 切到新貼文排序。"""

    if not enabled:
        return build_disabled_sort_adjust_result(FEED_SORT_NEWEST_LABEL)

    native_attempt = await try_native_feed_sort_click_async(page)
    if native_attempt.result is not None:
        return native_attempt.result

    await recover_sort_menu_before_js_fallback_async(
        page,
        native_attempt.diagnostics,
    )
    result = await page.evaluate(FEED_SORT_ADJUST_SCRIPT, FEED_SORT_NEWEST_LABEL)
    fallback_result = normalize_sort_adjust_result(
        result,
        preferred_label=FEED_SORT_NEWEST_LABEL,
    )
    return _with_fallback_diagnostics(fallback_result, native_attempt.diagnostics)


async def ensure_preferred_comment_sort_async(
    page: Any,
    *,
    enabled: bool,
) -> SortAdjustResult:
    """resident main worker 掃描前嘗試把留言切到由新到舊。"""

    if not enabled:
        return build_disabled_sort_adjust_result(COMMENT_SORT_NEWEST_LABEL)

    native_attempt = await try_native_comment_sort_click_async(page)
    if native_attempt.result is not None:
        return native_attempt.result

    await recover_sort_menu_before_js_fallback_async(
        page,
        native_attempt.diagnostics,
    )
    result = await page.evaluate(COMMENT_SORT_ADJUST_SCRIPT, COMMENT_SORT_NEWEST_LABEL)
    fallback_result = normalize_sort_adjust_result(
        result,
        preferred_label=COMMENT_SORT_NEWEST_LABEL,
    )
    return _with_fallback_diagnostics(fallback_result, native_attempt.diagnostics)

def _with_fallback_diagnostics(
    result: SortAdjustResult,
    native_diagnostics: dict[str, Any],
) -> SortAdjustResult:
    """JS fallback 結果併入 native 失敗階段，方便判讀 fallback 是否啟動。"""

    if not native_diagnostics:
        return result
    diagnostics = {
        **native_diagnostics,
        "method": SORT_METHOD_JS_FALLBACK,
        "fallback_used": True,
    }
    return with_sort_diagnostics(result, diagnostics)
