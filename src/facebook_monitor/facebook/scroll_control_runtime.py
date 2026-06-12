"""Facebook load-more scroll runtime wrappers。

職責：提供 Playwright page.evaluate 的同步 / async Python 入口；大型
JavaScript payload 由 posts / comments / guard script modules 維護。
"""

from __future__ import annotations

from typing import Any

import facebook_monitor.facebook.scroll_comment_scripts as _comment_scripts
import facebook_monitor.facebook.scroll_guard_scripts as _guard_scripts
import facebook_monitor.facebook.scroll_post_scripts as _post_scripts


def _coerce_evaluate_result(result: Any) -> dict[str, Any]:
    """將 Playwright evaluate 結果收斂成 dict，避免呼叫端重複防禦。"""

    return result if isinstance(result, dict) else {}


def get_scroll_position(page: Any) -> dict[str, Any]:
    """取得目前文件捲動位置與尺寸，供每輪 scan metadata 使用。"""

    return _coerce_evaluate_result(page.evaluate(_post_scripts.SCROLL_POSITION_SCRIPT))


async def get_scroll_position_async(page: Any) -> dict[str, Any]:
    """resident main worker 取得目前文件捲動位置與尺寸。"""

    return _coerce_evaluate_result(await page.evaluate(_post_scripts.SCROLL_POSITION_SCRIPT))


def capture_load_more_scroll_snapshot(page: Any) -> dict[str, Any]:
    """在深度掃描前保存 scroll 位置，避免干擾使用者視窗。"""

    return _coerce_evaluate_result(
        page.evaluate(_post_scripts.CAPTURE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT)
    )


async def capture_load_more_scroll_snapshot_async(page: Any) -> dict[str, Any]:
    """resident main worker 在深度掃描前保存 scroll 位置。"""

    return _coerce_evaluate_result(
        await page.evaluate(_post_scripts.CAPTURE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT)
    )


def restore_load_more_scroll_snapshot(page: Any) -> dict[str, Any]:
    """深度掃描結束後復原 scroll 位置。"""

    return _coerce_evaluate_result(
        page.evaluate(_post_scripts.RESTORE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT)
    )


async def restore_load_more_scroll_snapshot_async(page: Any) -> dict[str, Any]:
    """resident main worker 深度掃描結束後復原 scroll 位置。"""

    return _coerce_evaluate_result(
        await page.evaluate(_post_scripts.RESTORE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT)
    )


def scroll_load_more(page: Any) -> dict[str, Any]:
    """執行一次 posts load-more 捲動並回傳目標與位移診斷。"""

    return _coerce_evaluate_result(page.evaluate(_post_scripts.SCROLL_LOAD_MORE_SCRIPT))


async def scroll_load_more_async(page: Any) -> dict[str, Any]:
    """resident main worker 執行一次 posts load-more 捲動。"""

    return _coerce_evaluate_result(await page.evaluate(_post_scripts.SCROLL_LOAD_MORE_SCRIPT))


def begin_comment_load_more_guard(page: Any) -> dict[str, Any]:
    """取得 comment-specific load-more guard，避免同頁留言捲動互相打架。"""

    return _coerce_evaluate_result(
        page.evaluate(_guard_scripts.BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT)
    )


async def begin_comment_load_more_guard_async(page: Any) -> dict[str, Any]:
    """async 版本：取得 comment-specific load-more guard。"""

    return _coerce_evaluate_result(
        await page.evaluate(_guard_scripts.BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT)
    )


def end_comment_load_more_guard(page: Any) -> dict[str, Any]:
    """釋放 comment-specific load-more guard。"""

    return _coerce_evaluate_result(
        page.evaluate(_guard_scripts.END_COMMENT_LOAD_MORE_GUARD_SCRIPT)
    )


async def end_comment_load_more_guard_async(page: Any) -> dict[str, Any]:
    """async 版本：釋放 comment-specific load-more guard。"""

    return _coerce_evaluate_result(
        await page.evaluate(_guard_scripts.END_COMMENT_LOAD_MORE_GUARD_SCRIPT)
    )


def capture_comment_scroll_snapshot(page: Any) -> dict[str, Any]:
    """保存 comments 可能碰到的 nested scroll targets 位置。"""

    return _coerce_evaluate_result(
        page.evaluate(_comment_scripts.CAPTURE_COMMENT_SCROLL_SNAPSHOT_SCRIPT)
    )


async def capture_comment_scroll_snapshot_async(page: Any) -> dict[str, Any]:
    """async 版本：保存 comments nested scroll targets 位置。"""

    return _coerce_evaluate_result(
        await page.evaluate(_comment_scripts.CAPTURE_COMMENT_SCROLL_SNAPSHOT_SCRIPT)
    )


def restore_comment_scroll_snapshot(page: Any) -> dict[str, Any]:
    """復原 comments nested scroll targets 位置。"""

    return _coerce_evaluate_result(
        page.evaluate(_comment_scripts.RESTORE_COMMENT_SCROLL_SNAPSHOT_SCRIPT)
    )


async def restore_comment_scroll_snapshot_async(page: Any) -> dict[str, Any]:
    """async 版本：復原 comments nested scroll targets 位置。"""

    return _coerce_evaluate_result(
        await page.evaluate(_comment_scripts.RESTORE_COMMENT_SCROLL_SNAPSHOT_SCRIPT)
    )


def scroll_comment_load_more(page: Any) -> dict[str, Any]:
    """對 comments nested scroll candidates 執行一次保守 load-more。"""

    return _coerce_evaluate_result(
        page.evaluate(_comment_scripts.COMMENT_SCROLL_LOAD_MORE_SCRIPT)
    )


async def scroll_comment_load_more_async(page: Any) -> dict[str, Any]:
    """async 版本：對 comments nested scroll candidates 執行一次 load-more。"""

    return _coerce_evaluate_result(
        await page.evaluate(_comment_scripts.COMMENT_SCROLL_LOAD_MORE_SCRIPT)
    )


__all__ = [
    "begin_comment_load_more_guard",
    "begin_comment_load_more_guard_async",
    "capture_comment_scroll_snapshot",
    "capture_comment_scroll_snapshot_async",
    "capture_load_more_scroll_snapshot",
    "capture_load_more_scroll_snapshot_async",
    "end_comment_load_more_guard",
    "end_comment_load_more_guard_async",
    "get_scroll_position",
    "get_scroll_position_async",
    "restore_comment_scroll_snapshot",
    "restore_comment_scroll_snapshot_async",
    "restore_load_more_scroll_snapshot",
    "restore_load_more_scroll_snapshot_async",
    "scroll_comment_load_more",
    "scroll_comment_load_more_async",
    "scroll_load_more",
    "scroll_load_more_async",
]
