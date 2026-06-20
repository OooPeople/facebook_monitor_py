"""Posts scan metadata builders.

職責：組裝 posts pipeline 的 latest scan diagnostics metadata。
本模組只處理資料轉換，不執行 browser、DB、notification 或 scan commit side effects。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from facebook_monitor.core.models import TargetConfig
from facebook_monitor.facebook.collection_policy import (
    CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT,
)
from facebook_monitor.facebook.collection_policy import get_dynamic_max_windows
from facebook_monitor.facebook.feed_extraction_models import ExtractCollectionMeta
from facebook_monitor.facebook.feed_extraction_models import ExtractRoundStats
from facebook_monitor.facebook.sort_results import SortAdjustResult
from facebook_monitor.worker.scan_finalize import SORT_ADJUST_UNCONFIRMED_SKIP_REASON
from facebook_monitor.worker.scan_finalize import SORT_ADJUST_UNCONFIRMED_STOP_REASON
from facebook_monitor.worker.scan_metadata import PostScanMetadata
from facebook_monitor.worker.scan_metadata import PostScanRoundMetadata
from facebook_monitor.worker.scan_metadata import SORT_ADJUST_SKIP_COLLECTION_MODE
from facebook_monitor.worker.scan_metadata import build_sort_adjust_skip_meta
from facebook_monitor.worker.scan_metadata import with_scan_skipped_reason


def build_scan_metadata(
    *,
    items_count: int,
    max_items_per_scan: int,
    scroll_rounds: int,
    requested_scroll_rounds: int,
    scroll_wait_ms: int,
    auto_load_more: bool,
    sort_adjust_result: SortAdjustResult,
    round_stats: list[ExtractRoundStats],
    collection_meta: ExtractCollectionMeta,
) -> dict[str, Any]:
    """整理單輪掃描診斷資料，維持 latest scan 摘要語義。"""

    normalized_rounds = _build_post_scan_round_metadata(
        round_stats=round_stats,
        scroll_rounds=scroll_rounds,
    )
    candidate_count = _resolve_post_candidate_count(
        round_stats=round_stats,
        items_count=items_count,
    )
    stop_reason = _resolve_post_stop_reason(
        collection_meta=collection_meta,
        items_count=items_count,
        max_items_per_scan=max_items_per_scan,
        scroll_rounds=scroll_rounds,
        round_stats=round_stats,
    )
    return PostScanMetadata(
        worker="posts_scan",
        collection_strategy="feed_scroll_rounds"
        if auto_load_more and max(scroll_rounds, 0) > 0
        else "feed_visible_window",
        auto_load_more=auto_load_more,
        scroll_collection_enabled=auto_load_more and max(scroll_rounds, 0) > 0,
        target_count=max_items_per_scan,
        scanned_count=items_count,
        candidate_count=candidate_count,
        round_count=len(round_stats),
        max_window_count=get_dynamic_max_windows(max_items_per_scan) if auto_load_more else 1,
        requested_scroll_rounds=max(requested_scroll_rounds, 0),
        scroll_rounds=max(scroll_rounds, 0),
        scroll_wait_ms=max(scroll_wait_ms, 0),
        load_more_mode=collection_meta.load_more_mode,
        stop_reason=stop_reason,
        collected_meta=collection_meta.to_metadata() | {"stopReason": stop_reason},
        sort_adjust=sort_adjust_result.to_metadata(),
        rounds=normalized_rounds,
    ).to_metadata()


def build_sort_unconfirmed_skip_metadata(
    *,
    config: TargetConfig,
    sort_adjust_result: SortAdjustResult,
    scroll_rounds: int,
    requested_scroll_rounds: int,
    scroll_wait_ms: int,
) -> dict[str, Any]:
    """建立排序未確認時的保護性跳過診斷。"""

    metadata = PostScanMetadata(
        worker="posts_scan",
        collection_strategy=SORT_ADJUST_SKIP_COLLECTION_MODE,
        auto_load_more=config.auto_load_more,
        scroll_collection_enabled=False,
        target_count=config.max_items_per_scan,
        scanned_count=0,
        candidate_count=0,
        round_count=0,
        max_window_count=0,
        requested_scroll_rounds=max(requested_scroll_rounds, 0),
        scroll_rounds=max(scroll_rounds, 0),
        scroll_wait_ms=max(scroll_wait_ms, 0),
        load_more_mode="skipped",
        stop_reason=SORT_ADJUST_UNCONFIRMED_STOP_REASON,
        collected_meta=build_sort_adjust_skip_meta(
            stop_reason=SORT_ADJUST_UNCONFIRMED_STOP_REASON,
        ),
        sort_adjust=sort_adjust_result.to_metadata(),
        rounds=(),
    ).to_metadata()
    return with_scan_skipped_reason(
        metadata,
        skip_reason=SORT_ADJUST_UNCONFIRMED_SKIP_REASON,
    )


def _build_post_scan_round_metadata(
    *,
    round_stats: Sequence[ExtractRoundStats],
    scroll_rounds: int,
) -> tuple[PostScanRoundMetadata, ...]:
    """將 extractor round stats 正規化為 latest scan metadata。"""

    include_scroll_details = max(scroll_rounds, 0) > 0
    return tuple(
        _post_scan_round_metadata(
            stat,
            include_scroll_details=include_scroll_details,
        )
        for stat in round_stats
    )


def _post_scan_round_metadata(
    stat: ExtractRoundStats,
    *,
    include_scroll_details: bool,
) -> PostScanRoundMetadata:
    """建立單一 posts scan round metadata。"""

    include_scroll_target = include_scroll_details and stat.scroll_target_label
    return PostScanRoundMetadata(
        round_index=stat.round_index,
        raw_item_count=stat.raw_item_count,
        unique_item_count=stat.unique_item_count,
        scroll_y=stat.scroll_y,
        scroll_height=stat.scroll_height,
        scroll_target_label=stat.scroll_target_label if include_scroll_target else "",
        scroll_target_top=stat.scroll_target_top if include_scroll_target else None,
        added_count=stat.added_count if include_scroll_target else None,
        stagnant_windows=stat.stagnant_windows if include_scroll_target else None,
        scroll_moved=stat.scroll_moved,
        scroll_before_top=stat.scroll_before_top,
        scroll_after_top=stat.scroll_after_top,
        scroll_moved_distance=stat.scroll_moved_distance,
        scroll_step=stat.scroll_step,
        load_more_mode=stat.load_more_mode,
    )


def _resolve_post_candidate_count(
    *,
    round_stats: Sequence[ExtractRoundStats],
    items_count: int,
) -> int:
    """依 extractor raw count 推導 posts scan candidate count。"""

    raw_counts = [stat.raw_item_count for stat in round_stats]
    return max(raw_counts) if raw_counts else items_count


def _resolve_post_stop_reason(
    *,
    collection_meta: ExtractCollectionMeta,
    items_count: int,
    max_items_per_scan: int,
    scroll_rounds: int,
    round_stats: Sequence[ExtractRoundStats],
) -> str:
    """保留 extractor stop reason，缺值時套用既有推導規則。"""

    return collection_meta.stop_reason or infer_scan_stop_reason(
        items_count=items_count,
        max_items_per_scan=max_items_per_scan,
        scroll_rounds=scroll_rounds,
        round_stats=list(round_stats),
    )


def infer_scan_stop_reason(
    *,
    items_count: int,
    max_items_per_scan: int,
    scroll_rounds: int,
    round_stats: list[ExtractRoundStats],
) -> str:
    """依目前可觀測資料推斷掃描停止原因，供 UI 診斷使用。"""

    if items_count >= max_items_per_scan:
        return "target_count_reached"
    if not round_stats:
        return "no_round_stats"
    if round_stats[-1].scroll_moved is False:
        return "scroll_stalled"
    if (
        max(scroll_rounds, 0) > 0
        and round_stats[-1].stagnant_windows >= CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT
    ):
        return "stagnant_windows"
    if round_stats[-1].round_index >= max(scroll_rounds, 0):
        return "scroll_rounds_completed"
    return "collection_stopped"


__all__ = [
    "build_scan_metadata",
    "build_sort_unconfirmed_skip_metadata",
    "infer_scan_stop_reason",
]
