"""Facebook load-more scroll runtime wrappers。

職責：提供 Playwright page.evaluate 的同步 / async Python 入口；大型
JavaScript payload 由 `scroll_control_scripts.py` 維護。
"""

from __future__ import annotations

from typing import Any

from facebook_monitor.facebook.scroll_control_scripts import (
    BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT as BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT,
    CAPTURE_COMMENT_SCROLL_SNAPSHOT_SCRIPT as CAPTURE_COMMENT_SCROLL_SNAPSHOT_SCRIPT,
    CAPTURE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT as CAPTURE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT,
    COMMENT_SCROLL_HELPERS_SCRIPT as COMMENT_SCROLL_HELPERS_SCRIPT,
    COMMENT_SCROLL_LOAD_MORE_SCRIPT as COMMENT_SCROLL_LOAD_MORE_SCRIPT,
    END_COMMENT_LOAD_MORE_GUARD_SCRIPT as END_COMMENT_LOAD_MORE_GUARD_SCRIPT,
    RESTORE_COMMENT_SCROLL_SNAPSHOT_SCRIPT as RESTORE_COMMENT_SCROLL_SNAPSHOT_SCRIPT,
    RESTORE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT as RESTORE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT,
    SCROLL_HELPERS_SCRIPT as SCROLL_HELPERS_SCRIPT,
    SCROLL_LOAD_MORE_SCRIPT as SCROLL_LOAD_MORE_SCRIPT,
    SCROLL_POSITION_SCRIPT as SCROLL_POSITION_SCRIPT,
)


def _coerce_evaluate_result(result: Any) -> dict[str, Any]:
    """將 Playwright evaluate 結果收斂成 dict，避免呼叫端重複防禦。"""

    return result if isinstance(result, dict) else {}


def get_scroll_position(page: Any) -> dict[str, Any]:
    """取得目前文件捲動位置與尺寸，供每輪 scan metadata 使用。"""

    return _coerce_evaluate_result(page.evaluate(SCROLL_POSITION_SCRIPT))


async def get_scroll_position_async(page: Any) -> dict[str, Any]:
    """resident main worker 取得目前文件捲動位置與尺寸。"""

    return _coerce_evaluate_result(await page.evaluate(SCROLL_POSITION_SCRIPT))


def capture_load_more_scroll_snapshot(page: Any) -> dict[str, Any]:
    """在深度掃描前保存 scroll 位置，避免干擾使用者視窗。"""

    return _coerce_evaluate_result(page.evaluate(CAPTURE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT))


async def capture_load_more_scroll_snapshot_async(page: Any) -> dict[str, Any]:
    """resident main worker 在深度掃描前保存 scroll 位置。"""

    return _coerce_evaluate_result(
        await page.evaluate(CAPTURE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT)
    )


def restore_load_more_scroll_snapshot(page: Any) -> dict[str, Any]:
    """深度掃描結束後復原 scroll 位置。"""

    return _coerce_evaluate_result(page.evaluate(RESTORE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT))


async def restore_load_more_scroll_snapshot_async(page: Any) -> dict[str, Any]:
    """resident main worker 深度掃描結束後復原 scroll 位置。"""

    return _coerce_evaluate_result(
        await page.evaluate(RESTORE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT)
    )


def scroll_load_more(page: Any) -> dict[str, Any]:
    """執行一次 posts load-more 捲動並回傳目標與位移診斷。"""

    return _coerce_evaluate_result(page.evaluate(SCROLL_LOAD_MORE_SCRIPT))


async def scroll_load_more_async(page: Any) -> dict[str, Any]:
    """resident main worker 執行一次 posts load-more 捲動。"""

    return _coerce_evaluate_result(await page.evaluate(SCROLL_LOAD_MORE_SCRIPT))


def begin_comment_load_more_guard(page: Any) -> dict[str, Any]:
    """取得 comment-specific load-more guard，避免同頁留言捲動互相打架。"""

    return _coerce_evaluate_result(page.evaluate(BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT))


async def begin_comment_load_more_guard_async(page: Any) -> dict[str, Any]:
    """async 版本：取得 comment-specific load-more guard。"""

    return _coerce_evaluate_result(await page.evaluate(BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT))


def end_comment_load_more_guard(page: Any) -> dict[str, Any]:
    """釋放 comment-specific load-more guard。"""

    return _coerce_evaluate_result(page.evaluate(END_COMMENT_LOAD_MORE_GUARD_SCRIPT))


async def end_comment_load_more_guard_async(page: Any) -> dict[str, Any]:
    """async 版本：釋放 comment-specific load-more guard。"""

    return _coerce_evaluate_result(await page.evaluate(END_COMMENT_LOAD_MORE_GUARD_SCRIPT))


def capture_comment_scroll_snapshot(page: Any) -> dict[str, Any]:
    """保存 comments 可能碰到的 nested scroll targets 位置。"""

    return _coerce_evaluate_result(page.evaluate(CAPTURE_COMMENT_SCROLL_SNAPSHOT_SCRIPT))


async def capture_comment_scroll_snapshot_async(page: Any) -> dict[str, Any]:
    """async 版本：保存 comments nested scroll targets 位置。"""

    return _coerce_evaluate_result(await page.evaluate(CAPTURE_COMMENT_SCROLL_SNAPSHOT_SCRIPT))


def restore_comment_scroll_snapshot(page: Any) -> dict[str, Any]:
    """復原 comments nested scroll targets 位置。"""

    return _coerce_evaluate_result(page.evaluate(RESTORE_COMMENT_SCROLL_SNAPSHOT_SCRIPT))


async def restore_comment_scroll_snapshot_async(page: Any) -> dict[str, Any]:
    """async 版本：復原 comments nested scroll targets 位置。"""

    return _coerce_evaluate_result(await page.evaluate(RESTORE_COMMENT_SCROLL_SNAPSHOT_SCRIPT))


def scroll_comment_load_more(page: Any) -> dict[str, Any]:
    """對 comments nested scroll candidates 執行一次保守 load-more。"""

    return _coerce_evaluate_result(page.evaluate(COMMENT_SCROLL_LOAD_MORE_SCRIPT))


async def scroll_comment_load_more_async(page: Any) -> dict[str, Any]:
    """async 版本：對 comments nested scroll candidates 執行一次 load-more。"""

    return _coerce_evaluate_result(await page.evaluate(COMMENT_SCROLL_LOAD_MORE_SCRIPT))


__all__ = [
    "BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT",
    "CAPTURE_COMMENT_SCROLL_SNAPSHOT_SCRIPT",
    "CAPTURE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT",
    "COMMENT_SCROLL_HELPERS_SCRIPT",
    "COMMENT_SCROLL_LOAD_MORE_SCRIPT",
    "END_COMMENT_LOAD_MORE_GUARD_SCRIPT",
    "RESTORE_COMMENT_SCROLL_SNAPSHOT_SCRIPT",
    "RESTORE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT",
    "SCROLL_HELPERS_SCRIPT",
    "SCROLL_LOAD_MORE_SCRIPT",
    "SCROLL_POSITION_SCRIPT",
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
