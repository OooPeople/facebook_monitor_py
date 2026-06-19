"""Facebook feed extraction diagnostics models and builders。

職責：保存 feed extractor 的 round stats / collected meta vocabulary，並
把 DOM 抽取、捲動與 seen-stop 狀態整理成 worker latest scan metadata。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import replace
from typing import Any

from facebook_monitor.facebook.collection_policy import get_dynamic_max_windows
from facebook_monitor.facebook.extracted_item import ExtractedItem

FEED_SEEN_STOP_CONSECUTIVE_SEEN_THRESHOLD = 4


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


def build_extract_round_stats(
    *,
    round_index: int,
    round_items: list[ExtractedItem],
    round_meta: Mapping[str, Any],
    unique_item_count: int,
    scroll_metrics: Mapping[str, Any],
    scroll_action: Mapping[str, Any] | None,
    scroll_rounds: int,
    added_count: int,
    stagnant_windows: int,
) -> ExtractRoundStats:
    """把單輪 feed 抽取、捲動與 DOM meta 整理成診斷資料。"""

    action = scroll_action or {}
    return ExtractRoundStats(
        round_index=round_index,
        raw_item_count=len(round_items),
        unique_item_count=unique_item_count,
        scroll_y=int(scroll_metrics.get("scrollY") or 0),
        scroll_height=int(scroll_metrics.get("scrollHeight") or 0),
        scroll_target_label=str(scroll_metrics.get("scrollTargetLabel") or ""),
        scroll_target_top=int(scroll_metrics.get("scrollTargetTop") or 0),
        scroll_moved=bool(action.get("moved")) if action else None,
        scroll_before_top=int(action.get("beforeTop") or 0) if action else None,
        scroll_after_top=int(action.get("afterTop") or 0) if action else None,
        scroll_moved_distance=(
            int(action.get("movedDistance") or 0) if action else None
        ),
        scroll_step=int(action.get("scrollStep") or 0) if action else None,
        load_more_mode=str(action.get("loadMoreMode") or "")
        if action
        else ("scroll" if scroll_rounds > 0 else "off"),
        added_count=added_count,
        stagnant_windows=stagnant_windows,
        candidate_count=int(round_meta.get("candidateCount") or len(round_items)),
        parsed_count=int(round_meta.get("parsedCount") or len(round_items)),
        filtered_empty_text_count=int(round_meta.get("filteredEmptyTextCount") or 0),
        filtered_non_post_count=int(round_meta.get("filteredNonPostCount") or 0),
        filtered_feed_sort_control_count=int(
            round_meta.get("filteredFeedSortControlCount") or 0
        ),
        article_element_count=int(round_meta.get("articleElementCount") or 0),
        posts_with_post_id_count=int(round_meta.get("postsWithPostIdCount") or 0),
    )


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


__all__ = [
    "FEED_SEEN_STOP_CONSECUTIVE_SEEN_THRESHOLD",
    "ExtractCollectionMeta",
    "ExtractRoundStats",
    "FeedSeenStopState",
    "build_collection_meta",
    "build_extract_round_stats",
    "with_collection_debug_metadata",
]
