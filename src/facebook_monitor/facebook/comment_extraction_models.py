"""Facebook comments extraction diagnostics data models。

職責：保存 comments extractor 的 metadata / round stats vocabulary，
不包含 DOM 抽取流程或跨視窗彙整演算法。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CommentCollectionMeta:
    """保存 comments visible-window 抽取統計。"""

    target_count: int
    candidate_count: int
    parsed_count: int
    accumulated_count: int
    filtered_empty_text_count: int = 0
    filtered_non_post_count: int = 0
    article_element_count: int = 0
    comments_with_comment_id_count: int = 0
    filtered_out_of_scope_count: int = 0
    comment_search_root_strategy: str = ""
    current_route_post_id: str = ""
    current_route_matches_target: bool = False
    mode: str = "comments_visible_window"
    attempted: bool = False
    attempts: int = 0
    before_count: int = 0
    after_count: int = 0
    window_count: int = 1
    max_window_count: int = 1
    stagnant_windows: int = 0
    load_more_mode: str = "off"
    guard_reason: str = ""
    stop_reason: str = "visible_window_completed"
    dom_settle_attempted: bool = False
    dom_settle_stable: bool = False
    dom_settle_observations: int = 0
    dom_settle_wait_ms: int = 0
    dom_settle_candidate_count: int = 0

    def to_metadata(self) -> dict[str, Any]:
        """轉成 latest scan metadata 使用的 comments vocabulary。"""

        return {
            "mode": self.mode,
            "targetCount": self.target_count,
            "attempted": self.attempted,
            "attempts": self.attempts,
            "beforeCount": self.before_count,
            "afterCount": self.after_count,
            "windowCount": self.window_count,
            "candidateCount": self.candidate_count,
            "parsedCount": self.parsed_count,
            "accumulatedCount": self.accumulated_count,
            "maxWindowCount": self.max_window_count,
            "stagnantWindows": self.stagnant_windows,
            "loadMoreMode": self.load_more_mode,
            "guardReason": self.guard_reason,
            "filteredEmptyTextCount": self.filtered_empty_text_count,
            "filteredNonPostCount": self.filtered_non_post_count,
            "articleElementCount": self.article_element_count,
            "commentsWithCommentIdCount": self.comments_with_comment_id_count,
            "filteredOutOfScopeCount": self.filtered_out_of_scope_count,
            "commentSearchRootStrategy": self.comment_search_root_strategy,
            "currentRoutePostId": self.current_route_post_id,
            "currentRouteMatchesTarget": self.current_route_matches_target,
            "stopReason": self.stop_reason,
            "domSettleAttempted": self.dom_settle_attempted,
            "domSettleStable": self.dom_settle_stable,
            "domSettleObservations": self.dom_settle_observations,
            "domSettleWaitMs": self.dom_settle_wait_ms,
            "domSettleCandidateCount": self.dom_settle_candidate_count,
        }


@dataclass(frozen=True)
class CommentExtractRoundStats:
    """保存 comments 跨視窗收集單輪診斷。"""

    round_index: int
    raw_item_count: int
    unique_item_count: int
    candidate_count: int
    parsed_count: int
    accumulated_count: int
    filtered_empty_text_count: int = 0
    filtered_non_post_count: int = 0
    article_element_count: int = 0
    comments_with_comment_id_count: int = 0
    filtered_out_of_scope_count: int = 0
    scroll_moved: bool | None = None
    scroll_target_label: str = ""
    scroll_before_top: int | None = None
    scroll_after_top: int | None = None
    scroll_moved_distance: int | None = None
    scroll_step: int | None = None
    scroll_height: int | None = None
    load_more_mode: str = "off"
    added_count: int = 0
    stagnant_windows: int = 0
    dom_settle_attempted: bool = False
    dom_settle_stable: bool = False
    dom_settle_observations: int = 0
    dom_settle_wait_ms: int = 0
    dom_settle_candidate_count: int = 0


@dataclass(frozen=True)
class CommentDomSettleResult:
    """保存 comments DOM settle 的非破壞性觀察結果。"""

    attempted: bool
    stable: bool
    observations: int
    wait_ms: int
    candidate_count: int = 0


__all__ = [
    "CommentCollectionMeta",
    "CommentDomSettleResult",
    "CommentExtractRoundStats",
]
