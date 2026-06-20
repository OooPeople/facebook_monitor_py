"""Facebook comment extractor helpers。

職責：提供 comments 的可見留言抽取、comment identity、診斷整理與
D3 nested scroll/load-more 收集流程。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from facebook_monitor.core.dedupe import aliases_overlap
from facebook_monitor.facebook.comment_dom_scripts import COMMENTS_LIKE_ITEMS_SCRIPT
from facebook_monitor.facebook.comment_extraction_collection_meta import (
    build_comment_collection_meta,
)
from facebook_monitor.facebook.comment_extraction_models import CommentCollectionMeta
from facebook_monitor.facebook.comment_extraction_models import CommentDomSettleResult
from facebook_monitor.facebook.comment_extraction_models import CommentExtractRoundStats
from facebook_monitor.facebook.comment_extraction_rounds import build_comment_round_stats
from facebook_monitor.facebook.comment_extraction_normalizer import (
    normalize_comment_debug_metadata as normalize_comment_debug_metadata,
)
from facebook_monitor.facebook.comment_extraction_normalizer import (
    normalize_comment_extraction_payload,
)
from facebook_monitor.facebook.collection_policy import (
    CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT,
)
from facebook_monitor.facebook.extracted_item import ExtractedItem
from facebook_monitor.facebook.extracted_item import make_item_key_aliases
from facebook_monitor.facebook.comment_dom_settle_script import COMMENT_DOM_SETTLE_SCRIPT
from facebook_monitor.facebook.scroll_control_runtime import begin_comment_load_more_guard
from facebook_monitor.facebook.scroll_control_runtime import begin_comment_load_more_guard_async
from facebook_monitor.facebook.scroll_control_runtime import capture_comment_scroll_snapshot
from facebook_monitor.facebook.scroll_control_runtime import capture_comment_scroll_snapshot_async
from facebook_monitor.facebook.scroll_control_runtime import end_comment_load_more_guard
from facebook_monitor.facebook.scroll_control_runtime import end_comment_load_more_guard_async
from facebook_monitor.facebook.scroll_control_runtime import restore_comment_scroll_snapshot
from facebook_monitor.facebook.scroll_control_runtime import restore_comment_scroll_snapshot_async
from facebook_monitor.facebook.scroll_control_runtime import scroll_comment_load_more
from facebook_monitor.facebook.scroll_control_runtime import scroll_comment_load_more_async

COMMENT_DOM_SETTLE_INITIAL_WAIT_MS = 700
COMMENT_DOM_SETTLE_POLL_INTERVAL_MS = 500
COMMENT_DOM_SETTLE_MAX_WAIT_MS = 2500
COMMENT_DOM_SETTLE_STABLE_OBSERVATIONS = 2


def extract_visible_comment_items(
    page: Any,
    *,
    group_id: str,
    parent_post_id: str,
    max_items: int,
) -> tuple[list[ExtractedItem], CommentCollectionMeta]:
    """從目前頁面已載入 DOM 抽取可見留言候選。"""

    raw_items = page.evaluate(
        COMMENTS_LIKE_ITEMS_SCRIPT,
        {
            "groupId": group_id,
            "parentPostId": parent_post_id,
            "limit": max(max_items, 1),
        },
    )
    return normalize_comment_extraction_payload(raw_items, max_items=max_items)


def wait_for_comment_dom_settle(page: Any, *, max_items: int) -> CommentDomSettleResult:
    """等待 comments DOM 短暫穩定；失敗時回傳診斷但不中斷既有抽取。"""

    return _wait_for_comment_dom_settle_with_waiter(
        evaluate=page.evaluate,
        wait_for_timeout=getattr(page, "wait_for_timeout", None),
        max_items=max_items,
    )


async def wait_for_comment_dom_settle_async(
    page: Any,
    *,
    max_items: int,
) -> CommentDomSettleResult:
    """async 版本：等待 comments DOM 短暫穩定。"""

    return await _wait_for_comment_dom_settle_with_waiter_async(
        evaluate=page.evaluate,
        wait_for_timeout=getattr(page, "wait_for_timeout", None),
        max_items=max_items,
    )


def _wait_for_comment_dom_settle_with_waiter(
    *,
    evaluate: Any,
    wait_for_timeout: Any,
    max_items: int,
) -> CommentDomSettleResult:
    """同步 settle 實作，將 Playwright 例外降級成不阻斷診斷。"""

    wait_ms = 0
    observations = 0
    last_signature = ""
    stable_observations = 0
    candidate_count = 0
    try:
        _call_waiter(wait_for_timeout, COMMENT_DOM_SETTLE_INITIAL_WAIT_MS)
        wait_ms += COMMENT_DOM_SETTLE_INITIAL_WAIT_MS
        while wait_ms <= COMMENT_DOM_SETTLE_MAX_WAIT_MS:
            payload = evaluate(
                COMMENT_DOM_SETTLE_SCRIPT,
                {"limit": max(max_items, 1)},
            )
            signature, candidate_count = _normalize_comment_dom_settle_payload(payload)
            observations += 1
            if signature and signature == last_signature:
                stable_observations += 1
            else:
                stable_observations = 1
                last_signature = signature
            if stable_observations >= COMMENT_DOM_SETTLE_STABLE_OBSERVATIONS:
                return CommentDomSettleResult(
                    attempted=True,
                    stable=True,
                    observations=observations,
                    wait_ms=wait_ms,
                    candidate_count=candidate_count,
                )
            if wait_ms >= COMMENT_DOM_SETTLE_MAX_WAIT_MS:
                break
            _call_waiter(wait_for_timeout, COMMENT_DOM_SETTLE_POLL_INTERVAL_MS)
            wait_ms += COMMENT_DOM_SETTLE_POLL_INTERVAL_MS
    except Exception:
        return CommentDomSettleResult(
            attempted=True,
            stable=False,
            observations=observations,
            wait_ms=wait_ms,
            candidate_count=candidate_count,
        )
    return CommentDomSettleResult(
        attempted=True,
        stable=False,
        observations=observations,
        wait_ms=wait_ms,
        candidate_count=candidate_count,
    )


async def _wait_for_comment_dom_settle_with_waiter_async(
    *,
    evaluate: Any,
    wait_for_timeout: Any,
    max_items: int,
) -> CommentDomSettleResult:
    """async settle 實作，保持失敗不阻斷 comments extractor。"""

    wait_ms = 0
    observations = 0
    last_signature = ""
    stable_observations = 0
    candidate_count = 0
    try:
        await _call_waiter_async(wait_for_timeout, COMMENT_DOM_SETTLE_INITIAL_WAIT_MS)
        wait_ms += COMMENT_DOM_SETTLE_INITIAL_WAIT_MS
        while wait_ms <= COMMENT_DOM_SETTLE_MAX_WAIT_MS:
            payload = await evaluate(
                COMMENT_DOM_SETTLE_SCRIPT,
                {"limit": max(max_items, 1)},
            )
            signature, candidate_count = _normalize_comment_dom_settle_payload(payload)
            observations += 1
            if signature and signature == last_signature:
                stable_observations += 1
            else:
                stable_observations = 1
                last_signature = signature
            if stable_observations >= COMMENT_DOM_SETTLE_STABLE_OBSERVATIONS:
                return CommentDomSettleResult(
                    attempted=True,
                    stable=True,
                    observations=observations,
                    wait_ms=wait_ms,
                    candidate_count=candidate_count,
                )
            if wait_ms >= COMMENT_DOM_SETTLE_MAX_WAIT_MS:
                break
            await _call_waiter_async(wait_for_timeout, COMMENT_DOM_SETTLE_POLL_INTERVAL_MS)
            wait_ms += COMMENT_DOM_SETTLE_POLL_INTERVAL_MS
    except Exception:
        return CommentDomSettleResult(
            attempted=True,
            stable=False,
            observations=observations,
            wait_ms=wait_ms,
            candidate_count=candidate_count,
        )
    return CommentDomSettleResult(
        attempted=True,
        stable=False,
        observations=observations,
        wait_ms=wait_ms,
        candidate_count=candidate_count,
    )


def _normalize_comment_dom_settle_payload(payload: object) -> tuple[str, int]:
    """整理 settle script payload。"""

    if not isinstance(payload, Mapping):
        return "", 0
    return (
        str(payload.get("signature") or ""),
        int(payload.get("candidateCount") or 0),
    )


def _call_waiter(wait_for_timeout: Any, milliseconds: int) -> None:
    """呼叫 Playwright wait_for_timeout；測試 fake 缺方法時安靜略過。"""

    if callable(wait_for_timeout):
        wait_for_timeout(milliseconds)


async def _call_waiter_async(wait_for_timeout: Any, milliseconds: int) -> None:
    """async 呼叫 Playwright wait_for_timeout；相容同步 fake。"""

    if not callable(wait_for_timeout):
        return
    result = wait_for_timeout(milliseconds)
    if hasattr(result, "__await__"):
        await result


def collect_comment_items_with_diagnostics(
    page: Any,
    *,
    group_id: str,
    parent_post_id: str,
    max_items: int,
    scroll_rounds: int,
    scroll_wait_ms: int,
    auto_load_more: bool,
) -> tuple[list[ExtractedItem], list[CommentExtractRoundStats], CommentCollectionMeta]:
    """依 D3 comments 規則跨可見視窗累積留言。"""

    rounds = max(int(scroll_rounds), 0) if auto_load_more else 0
    wait_ms = max(int(scroll_wait_ms), 0)
    if rounds <= 0:
        return collect_visible_comment_window_with_diagnostics(
            page,
            group_id=group_id,
            parent_post_id=parent_post_id,
            max_items=max_items,
            stop_reason="visible_window_completed",
            auto_load_more=False,
        )

    guard = begin_comment_load_more_guard(page)
    if not guard.get("acquired"):
        reason = str(guard.get("reason") or "comment_load_more_guard_active")
        return collect_visible_comment_window_with_diagnostics(
            page,
            group_id=group_id,
            parent_post_id=parent_post_id,
            max_items=max_items,
            stop_reason=reason,
            auto_load_more=True,
            guard_reason=reason,
        )

    return collect_comment_items_with_load_more_guard_held(
        page=page,
        group_id=group_id,
        parent_post_id=parent_post_id,
        max_items=max_items,
        scroll_rounds=rounds,
        scroll_wait_ms=wait_ms,
        auto_load_more=True,
    )


async def extract_visible_comment_items_async(
    page: Any,
    *,
    group_id: str,
    parent_post_id: str,
    max_items: int,
) -> tuple[list[ExtractedItem], CommentCollectionMeta]:
    """async 版本：抽取目前頁面已載入留言候選。"""

    raw_items = await page.evaluate(
        COMMENTS_LIKE_ITEMS_SCRIPT,
        {
            "groupId": group_id,
            "parentPostId": parent_post_id,
            "limit": max(max_items, 1),
        },
    )
    return normalize_comment_extraction_payload(raw_items, max_items=max_items)


async def collect_comment_items_with_diagnostics_async(
    page: Any,
    *,
    group_id: str,
    parent_post_id: str,
    max_items: int,
    scroll_rounds: int,
    scroll_wait_ms: int,
    auto_load_more: bool,
) -> tuple[list[ExtractedItem], list[CommentExtractRoundStats], CommentCollectionMeta]:
    """resident main 版本：跨可見視窗累積留言。"""

    rounds = max(int(scroll_rounds), 0) if auto_load_more else 0
    wait_ms = max(int(scroll_wait_ms), 0)
    if rounds <= 0:
        return await collect_visible_comment_window_with_diagnostics_async(
            page,
            group_id=group_id,
            parent_post_id=parent_post_id,
            max_items=max_items,
            stop_reason="visible_window_completed",
            auto_load_more=False,
        )

    guard = await begin_comment_load_more_guard_async(page)
    if not guard.get("acquired"):
        reason = str(guard.get("reason") or "comment_load_more_guard_active")
        return await collect_visible_comment_window_with_diagnostics_async(
            page,
            group_id=group_id,
            parent_post_id=parent_post_id,
            max_items=max_items,
            stop_reason=reason,
            auto_load_more=True,
            guard_reason=reason,
        )

    return await collect_comment_items_with_load_more_guard_held_async(
        page=page,
        group_id=group_id,
        parent_post_id=parent_post_id,
        max_items=max_items,
        scroll_rounds=rounds,
        scroll_wait_ms=wait_ms,
        auto_load_more=True,
    )


def collect_visible_comment_window_with_diagnostics(
    page: Any,
    *,
    group_id: str,
    parent_post_id: str,
    max_items: int,
    stop_reason: str,
    auto_load_more: bool,
    guard_reason: str = "",
) -> tuple[list[ExtractedItem], list[CommentExtractRoundStats], CommentCollectionMeta]:
    """收集單一 comments visible window，供 fallback 與 non-load-more 共用。"""

    settle = wait_for_comment_dom_settle(page, max_items=max_items)
    items, meta = extract_visible_comment_items(
        page,
        group_id=group_id,
        parent_post_id=parent_post_id,
        max_items=max_items,
    )
    round_stats = [
        build_comment_round_stats(
            round_index=0,
            items=items,
            meta=meta,
            accumulated_count=len(items),
            dom_settle=settle,
        )
    ]
    return items, round_stats, build_comment_collection_meta(
        target_count=max_items,
        round_stats=round_stats,
        accumulated_count=len(items),
        stop_reason=stop_reason,
        auto_load_more=auto_load_more,
        guard_reason=guard_reason,
    )


async def collect_visible_comment_window_with_diagnostics_async(
    page: Any,
    *,
    group_id: str,
    parent_post_id: str,
    max_items: int,
    stop_reason: str,
    auto_load_more: bool,
    guard_reason: str = "",
) -> tuple[list[ExtractedItem], list[CommentExtractRoundStats], CommentCollectionMeta]:
    """async 版本：收集單一 comments visible window。"""

    settle = await wait_for_comment_dom_settle_async(page, max_items=max_items)
    items, meta = await extract_visible_comment_items_async(
        page,
        group_id=group_id,
        parent_post_id=parent_post_id,
        max_items=max_items,
    )
    round_stats = [
        build_comment_round_stats(
            round_index=0,
            items=items,
            meta=meta,
            accumulated_count=len(items),
            dom_settle=settle,
        )
    ]
    return items, round_stats, build_comment_collection_meta(
        target_count=max_items,
        round_stats=round_stats,
        accumulated_count=len(items),
        stop_reason=stop_reason,
        auto_load_more=auto_load_more,
        guard_reason=guard_reason,
    )


def merge_comment_items(
    *,
    collected: list[tuple[tuple[str, ...], ExtractedItem]],
    items: list[ExtractedItem],
    max_items: int,
) -> int:
    """將單視窗 comments 依 aliases 併入跨視窗累積結果。"""

    added_count = 0
    for item in items:
        item_aliases = make_item_key_aliases(item)
        if not item_aliases:
            continue
        if any(aliases_overlap(item_aliases, aliases) for aliases, _ in collected):
            continue
        collected.append((item_aliases, item))
        added_count += 1
        if len(collected) >= max(max_items, 1):
            break
    return added_count


def infer_comment_stop_reason(
    *,
    accumulated_count: int,
    target_count: int,
    round_stats: list[CommentExtractRoundStats],
    scroll_rounds: int,
    auto_load_more: bool,
) -> str:
    """依 comments 跨視窗狀態推斷停止原因。"""

    if accumulated_count >= target_count:
        return "target_count_reached"
    if not auto_load_more:
        return "auto_load_more_disabled"
    if not round_stats:
        return "no_comment_round_stats"
    if round_stats[-1].scroll_moved is False:
        return "comment_scroll_stalled"
    if round_stats[-1].stagnant_windows >= CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT:
        return "comment_stagnant_windows"
    if round_stats[-1].round_index >= max(scroll_rounds, 0):
        return "comment_scroll_rounds_completed"
    return "comment_collection_stopped"


def collect_comment_items_with_load_more_guard_held(
    *,
    page: Any,
    group_id: str,
    parent_post_id: str,
    max_items: int,
    scroll_rounds: int,
    scroll_wait_ms: int,
    auto_load_more: bool,
) -> tuple[list[ExtractedItem], list[CommentExtractRoundStats], CommentCollectionMeta]:
    """在已取得 guard 時執行 comments nested scroll 收集。"""

    collected: list[tuple[tuple[str, ...], ExtractedItem]] = []
    round_stats: list[CommentExtractRoundStats] = []
    stagnant_windows = 0
    snapshot_captured = False
    try:
        capture_comment_scroll_snapshot(page)
        snapshot_captured = True
        for round_index in range(max(scroll_rounds, 0) + 1):
            settle = wait_for_comment_dom_settle(page, max_items=max_items)
            items, meta = extract_visible_comment_items(
                page,
                group_id=group_id,
                parent_post_id=parent_post_id,
                max_items=max_items,
            )
            added_count = merge_comment_items(
                collected=collected,
                items=items,
                max_items=max_items,
            )
            stagnant_windows = stagnant_windows + 1 if added_count == 0 else 0
            scroll_action: dict[str, Any] = {}
            should_scroll = round_index < max(scroll_rounds, 0) and len(collected) < max_items
            if should_scroll:
                scroll_action = scroll_comment_load_more(page)
            round_stats.append(
                build_comment_round_stats(
                    round_index=round_index,
                    items=items,
                    meta=meta,
                    accumulated_count=len(collected),
                    scroll_action=scroll_action,
                    added_count=added_count,
                    stagnant_windows=stagnant_windows,
                    dom_settle=settle,
                )
            )
            if round_index >= max(scroll_rounds, 0) or len(collected) >= max_items:
                break
            if scroll_action and not bool(scroll_action.get("moved")):
                break
            if stagnant_windows >= CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT:
                break
            page.wait_for_timeout(max(scroll_wait_ms, 0))
    finally:
        try:
            if snapshot_captured:
                restore_comment_scroll_snapshot(page)
        finally:
            end_comment_load_more_guard(page)

    items = [item for _aliases, item in collected[:max(max_items, 1)]]
    stop_reason = infer_comment_stop_reason(
        accumulated_count=len(items),
        target_count=max(max_items, 1),
        round_stats=round_stats,
        scroll_rounds=scroll_rounds,
        auto_load_more=auto_load_more,
    )
    return items, round_stats, build_comment_collection_meta(
        target_count=max_items,
        round_stats=round_stats,
        accumulated_count=len(items),
        stop_reason=stop_reason,
        auto_load_more=auto_load_more,
    )

async def collect_comment_items_with_load_more_guard_held_async(
    *,
    page: Any,
    group_id: str,
    parent_post_id: str,
    max_items: int,
    scroll_rounds: int,
    scroll_wait_ms: int,
    auto_load_more: bool,
) -> tuple[list[ExtractedItem], list[CommentExtractRoundStats], CommentCollectionMeta]:
    """async 版本：在已取得 guard 時執行 comments nested scroll 收集。"""

    collected: list[tuple[tuple[str, ...], ExtractedItem]] = []
    round_stats: list[CommentExtractRoundStats] = []
    stagnant_windows = 0
    snapshot_captured = False
    try:
        await capture_comment_scroll_snapshot_async(page)
        snapshot_captured = True
        for round_index in range(max(scroll_rounds, 0) + 1):
            settle = await wait_for_comment_dom_settle_async(page, max_items=max_items)
            items, meta = await extract_visible_comment_items_async(
                page,
                group_id=group_id,
                parent_post_id=parent_post_id,
                max_items=max_items,
            )
            added_count = merge_comment_items(
                collected=collected,
                items=items,
                max_items=max_items,
            )
            stagnant_windows = stagnant_windows + 1 if added_count == 0 else 0
            scroll_action: dict[str, Any] = {}
            should_scroll = round_index < max(scroll_rounds, 0) and len(collected) < max_items
            if should_scroll:
                scroll_action = await scroll_comment_load_more_async(page)
            round_stats.append(
                build_comment_round_stats(
                    round_index=round_index,
                    items=items,
                    meta=meta,
                    accumulated_count=len(collected),
                    scroll_action=scroll_action,
                    added_count=added_count,
                    stagnant_windows=stagnant_windows,
                    dom_settle=settle,
                )
            )
            if round_index >= max(scroll_rounds, 0) or len(collected) >= max_items:
                break
            if scroll_action and not bool(scroll_action.get("moved")):
                break
            if stagnant_windows >= CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT:
                break
            await page.wait_for_timeout(max(scroll_wait_ms, 0))
    finally:
        try:
            if snapshot_captured:
                await restore_comment_scroll_snapshot_async(page)
        finally:
            await end_comment_load_more_guard_async(page)

    items = [item for _aliases, item in collected[:max(max_items, 1)]]
    stop_reason = infer_comment_stop_reason(
        accumulated_count=len(items),
        target_count=max(max_items, 1),
        round_stats=round_stats,
        scroll_rounds=scroll_rounds,
        auto_load_more=auto_load_more,
    )
    return items, round_stats, build_comment_collection_meta(
        target_count=max_items,
        round_stats=round_stats,
        accumulated_count=len(items),
        stop_reason=stop_reason,
        auto_load_more=auto_load_more,
    )
