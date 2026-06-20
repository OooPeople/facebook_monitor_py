"""Facebook comments extraction round diagnostics builders。

職責：把單輪 comments 抽取、捲動與 DOM settle 結果整理成模型。
"""

from __future__ import annotations

from typing import Any

from facebook_monitor.facebook.comment_extraction_models import CommentCollectionMeta
from facebook_monitor.facebook.comment_extraction_models import CommentDomSettleResult
from facebook_monitor.facebook.comment_extraction_models import CommentExtractRoundStats


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


__all__ = ["build_comment_round_stats"]
