"""Facebook feed extraction diagnostics data models。

職責：保存 feed extractor 的 round stats / collected meta vocabulary，
不包含抽取流程或彙整演算法。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


__all__ = [
    "FEED_SEEN_STOP_CONSECUTIVE_SEEN_THRESHOLD",
    "ExtractCollectionMeta",
    "ExtractRoundStats",
    "FeedSeenStopState",
]
