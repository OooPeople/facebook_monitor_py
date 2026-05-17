"""Facebook feed extractor helpers。

職責：提供 group feed 貼文候選抽取與匿名診斷。
此模組保留早期可行性驗證過的 heuristic，後續可在這裡集中調整。
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace
from typing import Any

from facebook_monitor.core.dedupe import aliases_overlap
from facebook_monitor.core.dedupe import build_legacy_text_fingerprint
from facebook_monitor.facebook.collection_policy import (
    CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT,
)
from facebook_monitor.facebook.collection_policy import get_candidate_collection_limit
from facebook_monitor.facebook.collection_policy import get_dynamic_max_windows
from facebook_monitor.facebook.feed_dom import POST_LIKE_ITEMS_SCRIPT
from facebook_monitor.facebook.extracted_item import ExtractedItem
from facebook_monitor.facebook.extracted_item import make_item_key_aliases
from facebook_monitor.facebook.scroll_controls import capture_load_more_scroll_snapshot
from facebook_monitor.facebook.scroll_controls import capture_load_more_scroll_snapshot_async
from facebook_monitor.facebook.scroll_controls import get_scroll_position as get_scroll_metrics
from facebook_monitor.facebook.scroll_controls import get_scroll_position_async as get_scroll_metrics_async
from facebook_monitor.facebook.scroll_controls import restore_load_more_scroll_snapshot
from facebook_monitor.facebook.scroll_controls import restore_load_more_scroll_snapshot_async
from facebook_monitor.facebook.scroll_controls import scroll_load_more
from facebook_monitor.facebook.scroll_controls import scroll_load_more_async

FEED_SEEN_STOP_CONSECUTIVE_SEEN_THRESHOLD = 4


SeenItemPredicate = Callable[[tuple[str, ...]], bool]


@dataclass(frozen=True)
class ExtractRoundStats:
    """保存每輪捲動後的匿名 DOM 診斷統計。"""

    round_index: int
    raw_item_count: int
    unique_item_count: int
    scroll_y: int
    scroll_height: int
    scroll_target_label: str = ""
    scroll_target_top: int = 0
    scroll_moved: bool | None = None
    scroll_before_top: int | None = None
    scroll_after_top: int | None = None
    scroll_moved_distance: int | None = None
    scroll_step: int | None = None
    load_more_mode: str = ""
    added_count: int = 0
    stagnant_windows: int = 0
    candidate_count: int = 0
    parsed_count: int = 0
    filtered_empty_text_count: int = 0
    filtered_non_post_count: int = 0
    filtered_feed_sort_control_count: int = 0
    article_element_count: int = 0
    posts_with_post_id_count: int = 0


@dataclass(frozen=True)
class ExtractCollectionMeta:
    """保存跨視窗收集統計。"""

    target_count: int
    mode: str
    attempted: bool
    attempts: int
    before_count: int
    after_count: int
    window_count: int
    candidate_count: int
    cache_hit_count: int
    fresh_extract_count: int
    parsed_count: int
    accumulated_count: int
    max_window_count: int
    stagnant_windows: int
    stop_reason: str
    filtered_empty_text_count: int
    filtered_non_post_count: int
    filtered_feed_sort_control_count: int
    article_element_count: int
    posts_with_post_id_count: int
    load_more_mode: str = ""
    seen_stop_enabled: bool = False
    seen_stop_triggered: bool = False
    seen_stop_threshold: int = FEED_SEEN_STOP_CONSECUTIVE_SEEN_THRESHOLD
    seen_stop_consecutive_seen_count: int = 0
    seen_stop_seen_count: int = 0
    seen_stop_new_count: int = 0

    def to_metadata(self) -> dict[str, Any]:
        """轉成 scan metadata，保留穩定 collected meta 欄位語義。"""

        return {
            "targetCount": self.target_count,
            "mode": self.mode,
            "attempted": self.attempted,
            "attempts": self.attempts,
            "beforeCount": self.before_count,
            "afterCount": self.after_count,
            "windowCount": self.window_count,
            "candidateCount": self.candidate_count,
            "cacheHitCount": self.cache_hit_count,
            "freshExtractCount": self.fresh_extract_count,
            "parsedCount": self.parsed_count,
            "accumulatedCount": self.accumulated_count,
            "maxWindowCount": self.max_window_count,
            "stagnantWindows": self.stagnant_windows,
            "stopReason": self.stop_reason,
            "filteredEmptyTextCount": self.filtered_empty_text_count,
            "filteredNonPostCount": self.filtered_non_post_count,
            "filteredFeedSortControlCount": self.filtered_feed_sort_control_count,
            "articleElementCount": self.article_element_count,
            "postsWithPostIdCount": self.posts_with_post_id_count,
            "loadMoreMode": self.load_more_mode,
            "seenStopEnabled": self.seen_stop_enabled,
            "seenStopTriggered": self.seen_stop_triggered,
            "seenStopThreshold": self.seen_stop_threshold,
            "seenStopConsecutiveSeenCount": self.seen_stop_consecutive_seen_count,
            "seenStopSeenCount": self.seen_stop_seen_count,
            "seenStopNewCount": self.seen_stop_new_count,
        }


@dataclass
class FeedSeenStopState:
    """保存 feed seen-stop 的保守觀察狀態。"""

    enabled: bool
    threshold: int = FEED_SEEN_STOP_CONSECUTIVE_SEEN_THRESHOLD
    consecutive_seen_count: int = 0
    seen_count: int = 0
    new_count: int = 0
    triggered: bool = False


def normalize_text_fingerprint(raw_text: str) -> str:
    """產生不含原文保存的 fallback 文字 fingerprint。"""

    return build_legacy_text_fingerprint(raw_text)


def extract_post_like_items(page: Any, max_items: int) -> list[ExtractedItem]:
    """從目前 Facebook 頁面抽取最小可見貼文候選資料。"""

    items, _meta = extract_post_like_items_with_meta(page, max_items)
    return items


def extract_post_like_items_with_meta(
    page: Any,
    candidate_limit: int,
) -> tuple[list[ExtractedItem], dict[str, Any]]:
    """從目前 Facebook 頁面抽取候選貼文並回傳 DOM 層過濾統計。"""

    raw_items = page.evaluate(
        POST_LIKE_ITEMS_SCRIPT,
        candidate_limit,
    )
    raw_meta: dict[str, Any] = {}
    if isinstance(raw_items, Mapping):
        raw_meta = dict(raw_items.get("meta") or {})
        raw_items = raw_items.get("items") or []
    return [
        ExtractedItem(
            text=str(item.get("text") or ""),
            text_length=int(item.get("textLength") or 0),
            permalink=str(item.get("permalink") or ""),
            link_count=int(item.get("linkCount") or 0),
            author=str(item.get("author") or ""),
            debug_metadata=normalize_debug_metadata(item),
        )
        for item in raw_items
    ], raw_meta


async def extract_post_like_items_with_meta_async(
    page: Any,
    candidate_limit: int,
) -> tuple[list[ExtractedItem], dict[str, Any]]:
    """resident main worker 從目前頁面抽取候選貼文與 DOM 過濾統計。"""

    raw_items = await page.evaluate(
        POST_LIKE_ITEMS_SCRIPT,
        candidate_limit,
    )
    raw_meta: dict[str, Any] = {}
    if isinstance(raw_items, Mapping):
        raw_meta = dict(raw_items.get("meta") or {})
        raw_items = raw_items.get("items") or []
    return [
        ExtractedItem(
            text=str(item.get("text") or ""),
            text_length=int(item.get("textLength") or 0),
            permalink=str(item.get("permalink") or ""),
            link_count=int(item.get("linkCount") or 0),
            author=str(item.get("author") or ""),
            debug_metadata=normalize_debug_metadata(item),
        )
        for item in raw_items
    ], raw_meta


def normalize_debug_metadata(item: Any) -> dict[str, Any]:
    """整理 DOM extractor 回傳的診斷欄位，避免保存過大的任意 payload。"""

    if not isinstance(item, Mapping):
        return {}
    keys = (
        "source",
        "containerRole",
        "firstSeenRound",
        "roundItemIndex",
        "collectionIndex",
        "domIndex",
        "domPosition",
        "textSource",
        "textLength",
        "rawTextLength",
        "permalinkSource",
        "canonicalPermalinkCandidateCount",
        "postId",
        "postIdSource",
        "parentPostId",
        "commentId",
        "linkCount",
        "linkDiagnostics",
        "author",
        "hasStoryMessage",
        "hasCommentPermalink",
        "warmupAttempted",
        "warmupResolved",
        "warmupCandidateCount",
        "warmupDiagnostics",
        "expandAttempted",
        "expandCount",
    )
    return {key: item.get(key) for key in keys if item.get(key) not in (None, "")}


def get_scroll_position(page: Any) -> tuple[int, int]:
    """取得目前頁面的捲動位置與文件高度，用於匿名診斷。"""

    payload = get_scroll_metrics(page)
    return int(payload.get("scrollY") or 0), int(payload.get("scrollHeight") or 0)


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
            previous_count = len(collected)
            round_items, round_meta = extract_post_like_items_with_meta(
                page,
                candidate_limit,
            )
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
            added_count = len(collected) - previous_count
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
                ExtractRoundStats(
                    round_index=round_index,
                    raw_item_count=len(round_items),
                    unique_item_count=len(collected),
                    scroll_y=int(scroll_metrics.get("scrollY") or 0),
                    scroll_height=int(scroll_metrics.get("scrollHeight") or 0),
                    scroll_target_label=str(scroll_metrics.get("scrollTargetLabel") or ""),
                    scroll_target_top=int(scroll_metrics.get("scrollTargetTop") or 0),
                    scroll_moved=(
                        bool(scroll_action.get("moved")) if scroll_action else None
                    ),
                    scroll_before_top=(
                        int(scroll_action.get("beforeTop") or 0) if scroll_action else None
                    ),
                    scroll_after_top=(
                        int(scroll_action.get("afterTop") or 0) if scroll_action else None
                    ),
                    scroll_moved_distance=(
                        int(scroll_action.get("movedDistance") or 0) if scroll_action else None
                    ),
                    scroll_step=(
                        int(scroll_action.get("scrollStep") or 0) if scroll_action else None
                    ),
                    load_more_mode=str(scroll_action.get("loadMoreMode") or "")
                    if scroll_action
                    else ("scroll" if rounds > 0 else "off"),
                    added_count=added_count,
                    stagnant_windows=stagnant_windows,
                    candidate_count=int(round_meta.get("candidateCount") or len(round_items)),
                    parsed_count=int(round_meta.get("parsedCount") or len(round_items)),
                    filtered_empty_text_count=int(
                        round_meta.get("filteredEmptyTextCount") or 0
                    ),
                    filtered_non_post_count=int(
                        round_meta.get("filteredNonPostCount") or 0
                    ),
                    filtered_feed_sort_control_count=int(
                        round_meta.get("filteredFeedSortControlCount") or 0
                    ),
                    article_element_count=int(round_meta.get("articleElementCount") or 0),
                    posts_with_post_id_count=int(
                        round_meta.get("postsWithPostIdCount") or 0
                    ),
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
            previous_count = len(collected)
            round_items, round_meta = await extract_post_like_items_with_meta_async(
                page,
                candidate_limit,
            )
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
            added_count = len(collected) - previous_count
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
                ExtractRoundStats(
                    round_index=round_index,
                    raw_item_count=len(round_items),
                    unique_item_count=len(collected),
                    scroll_y=int(scroll_metrics.get("scrollY") or 0),
                    scroll_height=int(scroll_metrics.get("scrollHeight") or 0),
                    scroll_target_label=str(scroll_metrics.get("scrollTargetLabel") or ""),
                    scroll_target_top=int(scroll_metrics.get("scrollTargetTop") or 0),
                    scroll_moved=(
                        bool(scroll_action.get("moved")) if scroll_action else None
                    ),
                    scroll_before_top=(
                        int(scroll_action.get("beforeTop") or 0) if scroll_action else None
                    ),
                    scroll_after_top=(
                        int(scroll_action.get("afterTop") or 0) if scroll_action else None
                    ),
                    scroll_moved_distance=(
                        int(scroll_action.get("movedDistance") or 0) if scroll_action else None
                    ),
                    scroll_step=(
                        int(scroll_action.get("scrollStep") or 0) if scroll_action else None
                    ),
                    load_more_mode=str(scroll_action.get("loadMoreMode") or "")
                    if scroll_action
                    else ("scroll" if rounds > 0 else "off"),
                    added_count=added_count,
                    stagnant_windows=stagnant_windows,
                    candidate_count=int(round_meta.get("candidateCount") or len(round_items)),
                    parsed_count=int(round_meta.get("parsedCount") or len(round_items)),
                    filtered_empty_text_count=int(
                        round_meta.get("filteredEmptyTextCount") or 0
                    ),
                    filtered_non_post_count=int(
                        round_meta.get("filteredNonPostCount") or 0
                    ),
                    filtered_feed_sort_control_count=int(
                        round_meta.get("filteredFeedSortControlCount") or 0
                    ),
                    article_element_count=int(round_meta.get("articleElementCount") or 0),
                    posts_with_post_id_count=int(
                        round_meta.get("postsWithPostIdCount") or 0
                    ),
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


def build_collection_meta(
    *,
    target_count: int,
    scroll_rounds: int,
    round_stats: list[ExtractRoundStats],
    accumulated_count: int,
    seen_stop_state: FeedSeenStopState | None = None,
) -> ExtractCollectionMeta:
    """依每輪診斷彙整 collected meta 摘要。"""

    window_count = len(round_stats)
    attempted = any(stat.scroll_moved is not None for stat in round_stats)
    attempts = sum(1 for stat in round_stats if stat.scroll_moved is not None)
    max_window_count = get_dynamic_max_windows(target_count) if scroll_rounds > 0 else 1
    seen_state = seen_stop_state or FeedSeenStopState(enabled=False)
    return ExtractCollectionMeta(
        target_count=target_count,
        mode="scroll" if scroll_rounds > 0 else "off",
        attempted=attempted,
        attempts=attempts,
        before_count=round_stats[0].candidate_count if round_stats else 0,
        after_count=max((stat.candidate_count for stat in round_stats), default=0),
        window_count=window_count,
        candidate_count=sum(stat.candidate_count for stat in round_stats),
        cache_hit_count=0,
        fresh_extract_count=sum(stat.parsed_count for stat in round_stats),
        parsed_count=sum(stat.parsed_count for stat in round_stats),
        accumulated_count=accumulated_count,
        max_window_count=max_window_count,
        stagnant_windows=round_stats[-1].stagnant_windows if round_stats else 0,
        stop_reason="seen_stop_consecutive_seen" if seen_state.triggered else "",
        filtered_empty_text_count=sum(
            stat.filtered_empty_text_count for stat in round_stats
        ),
        filtered_non_post_count=sum(stat.filtered_non_post_count for stat in round_stats),
        filtered_feed_sort_control_count=sum(
            stat.filtered_feed_sort_control_count for stat in round_stats
        ),
        article_element_count=sum(stat.article_element_count for stat in round_stats),
        posts_with_post_id_count=sum(stat.posts_with_post_id_count for stat in round_stats),
        load_more_mode=next(
            (stat.load_more_mode for stat in round_stats if stat.load_more_mode),
            "scroll" if scroll_rounds > 0 else "off",
        ),
        seen_stop_enabled=seen_state.enabled,
        seen_stop_triggered=seen_state.triggered,
        seen_stop_threshold=seen_state.threshold,
        seen_stop_consecutive_seen_count=seen_state.consecutive_seen_count,
        seen_stop_seen_count=seen_state.seen_count,
        seen_stop_new_count=seen_state.new_count,
    )


def with_collection_debug_metadata(
    item: ExtractedItem,
    *,
    first_seen_round: int,
    round_item_index: int,
    collection_index: int,
) -> ExtractedItem:
    """補上跨視窗收集順序診斷，不改變 item identity。"""

    metadata = dict(item.debug_metadata or {})
    metadata["firstSeenRound"] = first_seen_round
    metadata["roundItemIndex"] = round_item_index
    metadata["collectionIndex"] = collection_index
    return replace(item, debug_metadata=metadata)


