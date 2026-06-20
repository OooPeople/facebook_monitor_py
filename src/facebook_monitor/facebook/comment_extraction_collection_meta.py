"""Facebook comments extraction collection metadata builders。

職責：將 comments 跨視窗 diagnostics 彙整成 latest scan metadata。
"""

from __future__ import annotations

from facebook_monitor.facebook.collection_policy import get_dynamic_max_windows
from facebook_monitor.facebook.comment_extraction_models import CommentCollectionMeta
from facebook_monitor.facebook.comment_extraction_models import CommentExtractRoundStats


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
        filtered_empty_text_count=sum(
            stat.filtered_empty_text_count for stat in round_stats
        ),
        filtered_non_post_count=sum(
            stat.filtered_non_post_count for stat in round_stats
        ),
        article_element_count=sum(stat.article_element_count for stat in round_stats),
        comments_with_comment_id_count=sum(
            stat.comments_with_comment_id_count for stat in round_stats
        ),
        filtered_out_of_scope_count=sum(
            stat.filtered_out_of_scope_count for stat in round_stats
        ),
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


__all__ = ["build_comment_collection_meta"]
