"""Facebook feed extractor helpers。

職責：提供 group feed 貼文候選抽取與匿名診斷。
此模組保留早期可行性驗證過的 heuristic，後續可在這裡集中調整。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from facebook_monitor.core.dedupe import aliases_overlap
from facebook_monitor.facebook.collection_policy import (
    CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT,
)
from facebook_monitor.facebook.collection_policy import get_candidate_collection_limit
from facebook_monitor.facebook.feed_dom_scripts import POST_LIKE_ITEMS_SCRIPT
from facebook_monitor.facebook.feed_extraction_diagnostics import (
    FEED_SEEN_STOP_CONSECUTIVE_SEEN_THRESHOLD as FEED_SEEN_STOP_CONSECUTIVE_SEEN_THRESHOLD,
)
from facebook_monitor.facebook.feed_extraction_diagnostics import (
    ExtractCollectionMeta as ExtractCollectionMeta,
)
from facebook_monitor.facebook.feed_extraction_diagnostics import (
    ExtractRoundStats as ExtractRoundStats,
)
from facebook_monitor.facebook.feed_extraction_diagnostics import (
    FeedSeenStopState as FeedSeenStopState,
)
from facebook_monitor.facebook.feed_extraction_diagnostics import (
    build_collection_meta as build_collection_meta,
)
from facebook_monitor.facebook.feed_extraction_diagnostics import (
    build_extract_round_stats as build_extract_round_stats,
)
from facebook_monitor.facebook.feed_extraction_diagnostics import (
    with_collection_debug_metadata as with_collection_debug_metadata,
)
from facebook_monitor.facebook.feed_extraction_normalizer import (
    normalize_debug_metadata as normalize_debug_metadata,
)
from facebook_monitor.facebook.feed_extraction_normalizer import (
    normalize_feed_extraction_item as normalize_feed_extraction_item,
)
from facebook_monitor.facebook.feed_extraction_normalizer import (
    normalize_feed_extraction_payload,
)
from facebook_monitor.facebook.extracted_item import ExtractedItem
from facebook_monitor.facebook.extracted_item import make_item_key_aliases
from facebook_monitor.facebook.scroll_control_runtime import capture_load_more_scroll_snapshot
from facebook_monitor.facebook.scroll_control_runtime import capture_load_more_scroll_snapshot_async
from facebook_monitor.facebook.scroll_control_runtime import get_scroll_position as get_scroll_metrics
from facebook_monitor.facebook.scroll_control_runtime import (
    get_scroll_position_async as get_scroll_metrics_async,
)
from facebook_monitor.facebook.scroll_control_runtime import restore_load_more_scroll_snapshot
from facebook_monitor.facebook.scroll_control_runtime import restore_load_more_scroll_snapshot_async
from facebook_monitor.facebook.scroll_control_runtime import scroll_load_more
from facebook_monitor.facebook.scroll_control_runtime import scroll_load_more_async


SeenItemPredicate = Callable[[tuple[str, ...]], bool]


def extract_post_like_items_with_meta(
    page: Any,
    candidate_limit: int,
) -> tuple[list[ExtractedItem], dict[str, Any]]:
    """從目前 Facebook 頁面抽取候選貼文並回傳 DOM 層過濾統計。"""

    raw_items = page.evaluate(
        POST_LIKE_ITEMS_SCRIPT,
        candidate_limit,
    )
    return normalize_feed_extraction_payload(raw_items)


async def extract_post_like_items_with_meta_async(
    page: Any,
    candidate_limit: int,
) -> tuple[list[ExtractedItem], dict[str, Any]]:
    """resident main worker 從目前頁面抽取候選貼文與 DOM 過濾統計。"""

    raw_items = await page.evaluate(
        POST_LIKE_ITEMS_SCRIPT,
        candidate_limit,
    )
    return normalize_feed_extraction_payload(raw_items)


def collect_unique_feed_items(
    *,
    collected: list[tuple[tuple[str, ...], ExtractedItem]],
    round_items: list[ExtractedItem],
    round_index: int,
    seen_stop_state: FeedSeenStopState,
    seen_item_predicate: SeenItemPredicate | None,
) -> int:
    """將單輪 feed items 依 aliases 去重併入跨視窗累積結果。"""

    previous_count = len(collected)
    for round_item_index, item in enumerate(round_items):
        item_aliases = make_item_key_aliases(item)
        if not item_aliases:
            continue
        if any(aliases_overlap(item_aliases, aliases) for aliases, _ in collected):
            continue
        observe_seen_stop_item(
            state=seen_stop_state,
            item_aliases=item_aliases,
            seen_item_predicate=seen_item_predicate,
        )
        collected.append(
            (
                item_aliases,
                with_collection_debug_metadata(
                    item,
                    first_seen_round=round_index,
                    round_item_index=round_item_index,
                    collection_index=len(collected),
                ),
            )
        )
        if seen_stop_state.triggered:
            break
    return len(collected) - previous_count


def collect_items_with_diagnostics(
    page: Any,
    max_items: int,
    scroll_rounds: int,
    scroll_wait_ms: int,
    seen_item_predicate: SeenItemPredicate | None = None,
) -> tuple[list[ExtractedItem], list[ExtractRoundStats], ExtractCollectionMeta]:
    """多輪捲動 feed，並回傳每輪匿名診斷統計。"""

    collected: list[tuple[tuple[str, ...], ExtractedItem]] = []
    round_stats: list[ExtractRoundStats] = []
    rounds = max(scroll_rounds, 0)
    wait_ms = max(scroll_wait_ms, 0)
    candidate_limit = get_candidate_collection_limit(max_items)

    snapshot_captured = False
    stagnant_windows = 0
    seen_stop_state = FeedSeenStopState(enabled=seen_item_predicate is not None)
    if rounds > 0:
        capture_load_more_scroll_snapshot(page)
        snapshot_captured = True

    try:
        for round_index in range(rounds + 1):
            round_items, round_meta = extract_post_like_items_with_meta(
                page,
                candidate_limit,
            )
            added_count = collect_unique_feed_items(
                collected=collected,
                round_items=round_items,
                round_index=round_index,
                seen_stop_state=seen_stop_state,
                seen_item_predicate=seen_item_predicate,
            )
            if added_count == 0:
                stagnant_windows += 1
            else:
                stagnant_windows = 0
            scroll_metrics = get_scroll_metrics(page)
            scroll_action: dict[str, Any] = {}
            should_scroll = (
                round_index < rounds
                and len(collected) < max_items
                and not seen_stop_state.triggered
            )
            if should_scroll:
                scroll_action = scroll_load_more(page)
            round_stats.append(
                build_extract_round_stats(
                    round_index=round_index,
                    round_items=round_items,
                    round_meta=round_meta,
                    unique_item_count=len(collected),
                    scroll_metrics=scroll_metrics,
                    scroll_action=scroll_action,
                    scroll_rounds=rounds,
                    added_count=added_count,
                    stagnant_windows=stagnant_windows,
                )
            )
            if round_index >= rounds or len(collected) >= max_items:
                break
            if seen_stop_state.triggered:
                break
            if scroll_action and not bool(scroll_action.get("moved")):
                break
            if stagnant_windows >= CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT:
                break
            page.wait_for_timeout(wait_ms)
    finally:
        if snapshot_captured:
            restore_load_more_scroll_snapshot(page)

    items = [item for _, item in collected[:max_items]]
    return items, round_stats, build_collection_meta(
        target_count=max_items,
        scroll_rounds=rounds,
        round_stats=round_stats,
        accumulated_count=len(items),
        seen_stop_state=seen_stop_state,
    )


async def collect_items_with_diagnostics_async(
    page: Any,
    max_items: int,
    scroll_rounds: int,
    scroll_wait_ms: int,
    seen_item_predicate: SeenItemPredicate | None = None,
) -> tuple[list[ExtractedItem], list[ExtractRoundStats], ExtractCollectionMeta]:
    """resident main worker 多輪捲動 feed，並回傳匿名診斷統計。"""

    collected: list[tuple[tuple[str, ...], ExtractedItem]] = []
    round_stats: list[ExtractRoundStats] = []
    rounds = max(scroll_rounds, 0)
    wait_ms = max(scroll_wait_ms, 0)
    candidate_limit = get_candidate_collection_limit(max_items)

    snapshot_captured = False
    stagnant_windows = 0
    seen_stop_state = FeedSeenStopState(enabled=seen_item_predicate is not None)
    if rounds > 0:
        await capture_load_more_scroll_snapshot_async(page)
        snapshot_captured = True

    try:
        for round_index in range(rounds + 1):
            round_items, round_meta = await extract_post_like_items_with_meta_async(
                page,
                candidate_limit,
            )
            added_count = collect_unique_feed_items(
                collected=collected,
                round_items=round_items,
                round_index=round_index,
                seen_stop_state=seen_stop_state,
                seen_item_predicate=seen_item_predicate,
            )
            if added_count == 0:
                stagnant_windows += 1
            else:
                stagnant_windows = 0
            scroll_metrics = await get_scroll_metrics_async(page)
            scroll_action: dict[str, Any] = {}
            should_scroll = (
                round_index < rounds
                and len(collected) < max_items
                and not seen_stop_state.triggered
            )
            if should_scroll:
                scroll_action = await scroll_load_more_async(page)
            round_stats.append(
                build_extract_round_stats(
                    round_index=round_index,
                    round_items=round_items,
                    round_meta=round_meta,
                    unique_item_count=len(collected),
                    scroll_metrics=scroll_metrics,
                    scroll_action=scroll_action,
                    scroll_rounds=rounds,
                    added_count=added_count,
                    stagnant_windows=stagnant_windows,
                )
            )
            if round_index >= rounds or len(collected) >= max_items:
                break
            if seen_stop_state.triggered:
                break
            if scroll_action and not bool(scroll_action.get("moved")):
                break
            if stagnant_windows >= CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT:
                break
            await page.wait_for_timeout(wait_ms)
    finally:
        if snapshot_captured:
            await restore_load_more_scroll_snapshot_async(page)

    items = [item for _, item in collected[:max_items]]
    return items, round_stats, build_collection_meta(
        target_count=max_items,
        scroll_rounds=rounds,
        round_stats=round_stats,
        accumulated_count=len(items),
        seen_stop_state=seen_stop_state,
    )


def observe_seen_stop_item(
    *,
    state: FeedSeenStopState,
    item_aliases: tuple[str, ...],
    seen_item_predicate: SeenItemPredicate | None,
) -> None:
    """依 seen-stop 語義觀察新收集 item，但保守要求先看見新 item。"""

    if not state.enabled or seen_item_predicate is None:
        return
    if seen_item_predicate(item_aliases):
        state.seen_count += 1
        state.consecutive_seen_count += 1
    else:
        state.new_count += 1
        state.consecutive_seen_count = 0
    if state.consecutive_seen_count >= state.threshold:
        state.triggered = True

