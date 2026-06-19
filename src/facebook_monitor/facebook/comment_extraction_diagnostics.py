"""Facebook comments extraction diagnostics models and builders。

職責：保存 comments extractor 的 metadata / round stats vocabulary，並將
visible-window 或 nested-scroll 結果整理成 latest scan metadata 可用的 shape。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from facebook_monitor.facebook.collection_policy import get_dynamic_max_windows


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


def build_comment_round_stats(
    *,
    round_index: int,
    items: list[Any],
    meta: CommentCollectionMeta,
    accumulated_count: int,
    scroll_action: dict[str, Any] | None = None,
    added_count: int = 0,
    stagnant_windows: int = 0,
    dom_settle: CommentDomSettleResult | None = None,
) -> CommentExtractRoundStats:
    """把單輪 comments 抽取與捲動結果整理成診斷資料。"""

    action = scroll_action or {}
    settle = dom_settle or CommentDomSettleResult(
        attempted=False,
        stable=False,
        observations=0,
        wait_ms=0,
    )
    return CommentExtractRoundStats(
        round_index=round_index,
        raw_item_count=len(items),
        unique_item_count=accumulated_count,
        candidate_count=meta.candidate_count,
        parsed_count=meta.parsed_count,
        accumulated_count=accumulated_count,
        filtered_empty_text_count=meta.filtered_empty_text_count,
        filtered_non_post_count=meta.filtered_non_post_count,
        article_element_count=meta.article_element_count,
        comments_with_comment_id_count=meta.comments_with_comment_id_count,
        filtered_out_of_scope_count=meta.filtered_out_of_scope_count,
        scroll_moved=bool(action.get("moved")) if action else None,
        scroll_target_label=str(action.get("targetLabel") or "") if action else "",
        scroll_before_top=int(action.get("beforeTop") or 0) if action else None,
        scroll_after_top=int(action.get("afterTop") or 0) if action else None,
        scroll_moved_distance=int(action.get("movedDistance") or 0) if action else None,
        scroll_step=int(action.get("scrollStep") or 0) if action else None,
        scroll_height=int(action.get("scrollHeight") or 0) if action else None,
        load_more_mode=str(
            action.get("loadMoreMode") or ("comment_nested_scroll" if action else "off")
        ),
        added_count=added_count,
        stagnant_windows=stagnant_windows,
        dom_settle_attempted=settle.attempted,
        dom_settle_stable=settle.stable,
        dom_settle_observations=settle.observations,
        dom_settle_wait_ms=settle.wait_ms,
        dom_settle_candidate_count=settle.candidate_count,
    )


def build_comment_collection_meta(
    *,
    target_count: int,
    round_stats: list[CommentExtractRoundStats],
    accumulated_count: int,
    stop_reason: str,
    auto_load_more: bool,
    guard_reason: str = "",
) -> CommentCollectionMeta:
    """彙整 comments 跨視窗 collected meta。"""

    dom_settle_attempted = any(stat.dom_settle_attempted for stat in round_stats)
    return CommentCollectionMeta(
        target_count=max(int(target_count), 1),
        candidate_count=sum(stat.candidate_count for stat in round_stats),
        parsed_count=sum(stat.parsed_count for stat in round_stats),
        accumulated_count=accumulated_count,
        filtered_empty_text_count=sum(stat.filtered_empty_text_count for stat in round_stats),
        filtered_non_post_count=sum(stat.filtered_non_post_count for stat in round_stats),
        article_element_count=sum(stat.article_element_count for stat in round_stats),
        comments_with_comment_id_count=sum(
            stat.comments_with_comment_id_count for stat in round_stats
        ),
        filtered_out_of_scope_count=sum(stat.filtered_out_of_scope_count for stat in round_stats),
        mode="comments_nested_scroll" if auto_load_more else "comments_visible_window",
        attempted=any(stat.scroll_moved is not None for stat in round_stats),
        attempts=sum(1 for stat in round_stats if stat.scroll_moved is not None),
        before_count=round_stats[0].candidate_count if round_stats else 0,
        after_count=max((stat.candidate_count for stat in round_stats), default=0),
        window_count=len(round_stats),
        max_window_count=get_dynamic_max_windows(target_count) if auto_load_more else 1,
        stagnant_windows=round_stats[-1].stagnant_windows if round_stats else 0,
        load_more_mode=next(
            (stat.load_more_mode for stat in round_stats if stat.load_more_mode != "off"),
            "comment_nested_scroll" if auto_load_more else "off",
        ),
        guard_reason=guard_reason,
        stop_reason=stop_reason,
        dom_settle_attempted=dom_settle_attempted,
        dom_settle_stable=all(
            stat.dom_settle_stable
            for stat in round_stats
            if stat.dom_settle_attempted
        )
        if dom_settle_attempted
        else False,
        dom_settle_observations=sum(stat.dom_settle_observations for stat in round_stats),
        dom_settle_wait_ms=sum(stat.dom_settle_wait_ms for stat in round_stats),
        dom_settle_candidate_count=max(
            (stat.dom_settle_candidate_count for stat in round_stats),
            default=0,
        ),
    )


__all__ = [
    "CommentCollectionMeta",
    "CommentDomSettleResult",
    "CommentExtractRoundStats",
    "build_comment_collection_meta",
    "build_comment_round_stats",
]
