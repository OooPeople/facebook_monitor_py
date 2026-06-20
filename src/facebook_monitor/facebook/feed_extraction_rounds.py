"""Facebook feed extraction round diagnostics builders。

職責：把單輪 DOM 抽取、捲動與 collection order 資訊整理成模型。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from facebook_monitor.facebook.extracted_item import ExtractedItem
from facebook_monitor.facebook.feed_extraction_models import ExtractRoundStats


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
    "build_extract_round_stats",
    "with_collection_debug_metadata",
]
