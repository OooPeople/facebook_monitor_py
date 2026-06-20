"""Facebook feed extraction collection metadata builders。

職責：將多輪 feed diagnostics 彙整成 latest scan 可保存的 collected meta。
"""

from __future__ import annotations

from facebook_monitor.facebook.collection_policy import get_dynamic_max_windows
from facebook_monitor.facebook.feed_extraction_models import ExtractCollectionMeta
from facebook_monitor.facebook.feed_extraction_models import ExtractRoundStats
from facebook_monitor.facebook.feed_extraction_models import FeedSeenStopState


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
        posts_with_post_id_count=sum(
            stat.posts_with_post_id_count for stat in round_stats
        ),
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


__all__ = ["build_collection_meta"]
