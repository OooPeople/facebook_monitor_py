"""Facebook feed extractor helpers。

職責：提供 group feed 貼文候選抽取與匿名診斷。
此模組保留早期可行性驗證過的 heuristic，後續可在這裡集中調整。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from facebook_monitor.core.dedupe import aliases_overlap
from facebook_monitor.facebook.collection_runner import CollectedItems
from facebook_monitor.facebook.collection_runner import CollectionRoundContext
from facebook_monitor.facebook.collection_runner import CollectionRoundObservation
from facebook_monitor.facebook.collection_runner import run_collection_loop
from facebook_monitor.facebook.collection_runner import run_collection_loop_async
from facebook_monitor.facebook.collection_policy import get_candidate_collection_limit
from facebook_monitor.facebook.feed_dom_scripts import POST_LIKE_ITEMS_SCRIPT
from facebook_monitor.facebook.feed_extraction_collection_meta import build_collection_meta
from facebook_monitor.facebook.feed_extraction_models import ExtractCollectionMeta
from facebook_monitor.facebook.feed_extraction_models import ExtractRoundStats
from facebook_monitor.facebook.feed_extraction_models import FeedSeenStopState
from facebook_monitor.facebook.feed_extraction_rounds import build_extract_round_stats
from facebook_monitor.facebook.feed_extraction_rounds import with_collection_debug_metadata
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

    rounds = max(scroll_rounds, 0)
    wait_ms = max(scroll_wait_ms, 0)
    candidate_limit = get_candidate_collection_limit(max_items)
    seen_stop_state = FeedSeenStopState(enabled=seen_item_predicate is not None)

    def collect_round(
        _round_index: int,
    ) -> CollectionRoundObservation[dict[str, Any], None]:
        round_items, round_meta = extract_post_like_items_with_meta(
            page,
            candidate_limit,
        )
        return CollectionRoundObservation(items=round_items, meta=round_meta)

    def merge_round_items(
        collected: CollectedItems,
        round_items: list[ExtractedItem],
        round_index: int,
    ) -> int:
        return collect_unique_feed_items(
            collected=collected,
            round_items=round_items,
            round_index=round_index,
            seen_stop_state=seen_stop_state,
            seen_item_predicate=seen_item_predicate,
        )

    def build_stats(
        context: CollectionRoundContext[dict[str, Any], None],
    ) -> ExtractRoundStats:
        return build_extract_round_stats(
            round_index=context.round_index,
            round_items=context.items,
            round_meta=context.meta,
            unique_item_count=context.accumulated_count,
            scroll_metrics=context.scroll_metrics,
            scroll_action=context.scroll_action,
            scroll_rounds=rounds,
            added_count=context.added_count,
            stagnant_windows=context.stagnant_windows,
        )

    def should_scroll(
        context: CollectionRoundContext[dict[str, Any], None],
    ) -> bool:
        return (
            context.round_index < rounds
            and context.accumulated_count < max_items
            and not seen_stop_state.triggered
        )

    def should_stop(
        _context: CollectionRoundContext[dict[str, Any], None],
    ) -> bool:
        return seen_stop_state.triggered

    def wait(milliseconds: int) -> None:
        page.wait_for_timeout(milliseconds)

    result = run_collection_loop(
        rounds=rounds,
        wait_ms=wait_ms,
        target_count=max_items,
        collect_round=collect_round,
        merge_items=merge_round_items,
        build_round_stats=build_stats,
        should_scroll=should_scroll,
        should_stop=should_stop,
        wait=wait,
        scroll=lambda: scroll_load_more(page),
        get_scroll_metrics=lambda: get_scroll_metrics(page),
        capture_snapshot=(
            (lambda: capture_load_more_scroll_snapshot(page)) if rounds > 0 else None
        ),
        restore_snapshot=(
            (lambda: restore_load_more_scroll_snapshot(page)) if rounds > 0 else None
        ),
    )

    items = [item for _, item in result.collected[:max_items]]
    return items, result.round_stats, build_collection_meta(
        target_count=max_items,
        scroll_rounds=rounds,
        round_stats=result.round_stats,
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

    rounds = max(scroll_rounds, 0)
    wait_ms = max(scroll_wait_ms, 0)
    candidate_limit = get_candidate_collection_limit(max_items)
    seen_stop_state = FeedSeenStopState(enabled=seen_item_predicate is not None)

    async def collect_round(
        _round_index: int,
    ) -> CollectionRoundObservation[dict[str, Any], None]:
        round_items, round_meta = await extract_post_like_items_with_meta_async(
            page,
            candidate_limit,
        )
        return CollectionRoundObservation(items=round_items, meta=round_meta)

    def merge_round_items(
        collected: CollectedItems,
        round_items: list[ExtractedItem],
        round_index: int,
    ) -> int:
        return collect_unique_feed_items(
            collected=collected,
            round_items=round_items,
            round_index=round_index,
            seen_stop_state=seen_stop_state,
            seen_item_predicate=seen_item_predicate,
        )

    def build_stats(
        context: CollectionRoundContext[dict[str, Any], None],
    ) -> ExtractRoundStats:
        return build_extract_round_stats(
            round_index=context.round_index,
            round_items=context.items,
            round_meta=context.meta,
            unique_item_count=context.accumulated_count,
            scroll_metrics=context.scroll_metrics,
            scroll_action=context.scroll_action,
            scroll_rounds=rounds,
            added_count=context.added_count,
            stagnant_windows=context.stagnant_windows,
        )

    def should_scroll(
        context: CollectionRoundContext[dict[str, Any], None],
    ) -> bool:
        return (
            context.round_index < rounds
            and context.accumulated_count < max_items
            and not seen_stop_state.triggered
        )

    def should_stop(
        _context: CollectionRoundContext[dict[str, Any], None],
    ) -> bool:
        return seen_stop_state.triggered

    async def wait(milliseconds: int) -> None:
        await page.wait_for_timeout(milliseconds)

    result = await run_collection_loop_async(
        rounds=rounds,
        wait_ms=wait_ms,
        target_count=max_items,
        collect_round=collect_round,
        merge_items=merge_round_items,
        build_round_stats=build_stats,
        should_scroll=should_scroll,
        should_stop=should_stop,
        wait=wait,
        scroll=lambda: scroll_load_more_async(page),
        get_scroll_metrics=lambda: get_scroll_metrics_async(page),
        capture_snapshot=(
            (lambda: capture_load_more_scroll_snapshot_async(page))
            if rounds > 0
            else None
        ),
        restore_snapshot=(
            (lambda: restore_load_more_scroll_snapshot_async(page))
            if rounds > 0
            else None
        ),
    )

    items = [item for _, item in result.collected[:max_items]]
    return items, result.round_stats, build_collection_meta(
        target_count=max_items,
        scroll_rounds=rounds,
        round_stats=result.round_stats,
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
