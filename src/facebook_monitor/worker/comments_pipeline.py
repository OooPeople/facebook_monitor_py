"""Group comments scan pipeline。

職責：處理 comments target 的頁面檢查、留言排序、nested load-more 與
comment extractor。seen、keyword、history、notification、latest scan 與
scan run 寫入交由 shared scan finalize layer，避免 comments 與 posts 後處理漂移。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.scan_failures import EXTRACTOR_EMPTY_REASON
from facebook_monitor.core.scan_failures import TARGET_INVALID_REASON
from facebook_monitor.core.scan_failures import TARGET_KIND_UNSUPPORTED_REASON
from facebook_monitor.facebook.comment_extractor import CommentCollectionMeta
from facebook_monitor.facebook.comment_extractor import CommentExtractRoundStats
from facebook_monitor.facebook.comment_extractor import collect_comment_items_with_diagnostics
from facebook_monitor.facebook.comment_extractor import collect_comment_items_with_diagnostics_async
from facebook_monitor.facebook.extracted_item import ExtractedItem
from facebook_monitor.facebook.sort_results import SortAdjustResult
from facebook_monitor.facebook.sort_runtime import ensure_preferred_comment_sort
from facebook_monitor.facebook.sort_runtime import ensure_preferred_comment_sort_async
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.scan_orchestration import ensure_async_page_scannable
from facebook_monitor.worker.scan_orchestration import ensure_sync_page_scannable
from facebook_monitor.worker.scan_orchestration import AsyncScannablePageLike
from facebook_monitor.worker.scan_orchestration import resolve_effective_scan_scroll_rounds
from facebook_monitor.worker.scan_orchestration import SyncScannablePageLike
from facebook_monitor.worker.scan_metadata import CommentScanMetadata
from facebook_monitor.worker.scan_metadata import CommentScanRoundMetadata
from facebook_monitor.worker.scan_metadata import SORT_ADJUST_SKIP_COLLECTION_MODE
from facebook_monitor.worker.scan_metadata import build_sort_adjust_skip_meta
from facebook_monitor.worker.scan_metadata import with_scan_skipped_reason
from facebook_monitor.worker.scan_finalize import finalize_scan_items
from facebook_monitor.worker.scan_finalize import normalize_extracted_scan_items
from facebook_monitor.worker.scan_finalize import record_guarded_skipped_scan
from facebook_monitor.worker.scan_finalize import record_unguarded_skipped_scan_for_one_shot
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_finalize import SORT_ADJUST_UNCONFIRMED_SKIP_REASON
from facebook_monitor.worker.scan_finalize import SORT_ADJUST_UNCONFIRMED_STOP_REASON
from facebook_monitor.worker.scan_pipeline_results import ProtectiveSkipScanResult
from facebook_monitor.worker.scan_pipeline_results import SuccessScanResult
from facebook_monitor.worker.scan_sort_policy import should_skip_scan_for_unconfirmed_sort


@dataclass(frozen=True)
class CommentsScanSummary:
    """保存 comments 單輪掃描摘要。"""

    target_id: str
    url: str
    item_count: int
    new_count: int
    matched_count: int
    scan_run_id: int
    round_stats: tuple[CommentExtractRoundStats, ...] = ()


def build_comments_scan_metadata(
    *,
    items_count: int,
    max_items_per_scan: int,
    collection_meta: CommentCollectionMeta,
    sort_adjust_result: SortAdjustResult,
    round_stats: list[CommentExtractRoundStats],
    scroll_rounds: int,
    requested_scroll_rounds: int,
    scroll_wait_ms: int,
    auto_load_more: bool,
) -> dict[str, Any]:
    """整理 comments latest scan metadata。"""

    return CommentScanMetadata(
        worker="comments_scan",
        collection_strategy=collection_meta.mode,
        comment_count=items_count,
        target_count=max_items_per_scan,
        candidate_count=collection_meta.candidate_count,
        round_count=len(round_stats),
        requested_scroll_rounds=max(requested_scroll_rounds, 0),
        scroll_rounds=max(scroll_rounds, 0),
        scroll_wait_ms=max(scroll_wait_ms, 0),
        auto_load_more=auto_load_more,
        load_more_mode=collection_meta.load_more_mode,
        comment_scroll_collection_enabled=auto_load_more and max(scroll_rounds, 0) > 0,
        stop_reason=collection_meta.stop_reason,
        comment_sort=sort_adjust_result.to_metadata(),
        comment_extract_rounds=tuple(
            CommentScanRoundMetadata(
                round_index=stat.round_index,
                raw_item_count=stat.raw_item_count,
                unique_item_count=stat.unique_item_count,
                candidate_count=stat.candidate_count,
                parsed_count=stat.parsed_count,
                accumulated_count=stat.accumulated_count,
                filtered_empty_text_count=stat.filtered_empty_text_count,
                filtered_non_post_count=stat.filtered_non_post_count,
                comments_with_comment_id_count=stat.comments_with_comment_id_count,
                scroll_moved=stat.scroll_moved,
                scroll_target_label=stat.scroll_target_label,
                scroll_before_top=stat.scroll_before_top,
                scroll_after_top=stat.scroll_after_top,
                scroll_moved_distance=stat.scroll_moved_distance,
                scroll_step=stat.scroll_step,
                load_more_mode=stat.load_more_mode,
                added_count=stat.added_count,
                stagnant_windows=stat.stagnant_windows,
                dom_settle_attempted=stat.dom_settle_attempted,
                dom_settle_stable=stat.dom_settle_stable,
                dom_settle_observations=stat.dom_settle_observations,
                dom_settle_wait_ms=stat.dom_settle_wait_ms,
                dom_settle_candidate_count=stat.dom_settle_candidate_count,
            )
            for stat in round_stats
        ),
        comments_meta=collection_meta.to_metadata(),
    ).to_metadata()


def build_comments_sort_unconfirmed_skip_metadata(
    *,
    config: TargetConfig,
    sort_adjust_result: SortAdjustResult,
    scroll_rounds: int,
    requested_scroll_rounds: int,
    scroll_wait_ms: int,
) -> dict[str, Any]:
    """建立留言排序未確認時的保護性跳過診斷。"""

    metadata = CommentScanMetadata(
        worker="comments_scan",
        collection_strategy=SORT_ADJUST_SKIP_COLLECTION_MODE,
        comment_count=0,
        target_count=config.max_items_per_scan,
        candidate_count=0,
        round_count=0,
        requested_scroll_rounds=max(requested_scroll_rounds, 0),
        scroll_rounds=max(scroll_rounds, 0),
        scroll_wait_ms=max(scroll_wait_ms, 0),
        auto_load_more=config.auto_load_more,
        load_more_mode="skipped",
        comment_scroll_collection_enabled=False,
        stop_reason=SORT_ADJUST_UNCONFIRMED_STOP_REASON,
        comment_sort=sort_adjust_result.to_metadata(),
        comment_extract_rounds=(),
        comments_meta=build_sort_adjust_skip_meta(
            stop_reason=SORT_ADJUST_UNCONFIRMED_STOP_REASON,
        ),
    ).to_metadata()
    return with_scan_skipped_reason(
        metadata,
        skip_reason=SORT_ADJUST_UNCONFIRMED_SKIP_REASON,
    )


def scan_comments_target_page_sync_and_finalize(
    *,
    page: SyncScannablePageLike,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    scroll_rounds: int = 0,
    scroll_wait_ms: int = 0,
    commit_guard: ScanCommitGuard | None = None,
) -> CommentsScanSummary:
    """sync/fallback 掃描目前留言頁，並直接寫入 visible scan state。"""

    ensure_comments_target(target)
    ensure_sync_page_scannable(page)

    sort_adjust_result = ensure_preferred_comment_sort(
        page,
        enabled=config.auto_adjust_sort,
    )
    effective_scroll_rounds = resolve_effective_scan_scroll_rounds(
        config=config,
        requested_scroll_rounds=scroll_rounds,
    )
    if should_skip_scan_for_unconfirmed_sort(
        config=config,
        sort_adjust_result=sort_adjust_result,
    ):
        skip_metadata = build_comments_sort_unconfirmed_skip_metadata(
            config=config,
            sort_adjust_result=sort_adjust_result,
            scroll_rounds=effective_scroll_rounds,
            requested_scroll_rounds=scroll_rounds,
            scroll_wait_ms=scroll_wait_ms,
        )
        if commit_guard is None:
            finalize_result = record_unguarded_skipped_scan_for_one_shot(
                app=app,
                target=target,
                metadata=skip_metadata,
            )
        else:
            finalize_result = record_guarded_skipped_scan(
                app=app,
                target=target,
                commit_guard=commit_guard,
                metadata=skip_metadata,
            )
        return CommentsScanSummary(
            target_id=target.id,
            url=str(page.url),
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=finalize_result.scan_run_id,
            round_stats=(),
        )
    items, round_stats, collection_meta = collect_comment_items_with_diagnostics(
        page=page,
        group_id=target.group_id,
        parent_post_id=target.parent_post_id,
        max_items=config.max_items_per_scan,
        scroll_rounds=effective_scroll_rounds,
        scroll_wait_ms=scroll_wait_ms,
        auto_load_more=config.auto_load_more,
    )
    if not items:
        raise WorkerFailure(EXTRACTOR_EMPTY_REASON, "No comment-like items were extracted.")
    return finalize_comments_pipeline_scan(
        page_url=str(page.url),
        app=app,
        target=target,
        config=config,
        items=items,
        collection_meta=collection_meta,
        sort_adjust_result=sort_adjust_result,
        round_stats=round_stats,
        scroll_rounds=effective_scroll_rounds,
        requested_scroll_rounds=scroll_rounds,
        scroll_wait_ms=scroll_wait_ms,
        auto_load_more=config.auto_load_more,
        commit_guard=commit_guard,
    )


async def scan_comments_target_page_async_commit_ready(
    *,
    page: AsyncScannablePageLike,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    scroll_rounds: int = 0,
    scroll_wait_ms: int = 0,
) -> SuccessScanResult | ProtectiveSkipScanResult:
    """formal async resident 掃描留言；visible scan state 交由 coordinator commit。"""

    ensure_comments_target(target)
    await ensure_async_page_scannable(page)

    sort_adjust_result = await ensure_preferred_comment_sort_async(
        page,
        enabled=config.auto_adjust_sort,
    )
    effective_scroll_rounds = resolve_effective_scan_scroll_rounds(
        config=config,
        requested_scroll_rounds=scroll_rounds,
    )
    if should_skip_scan_for_unconfirmed_sort(
        config=config,
        sort_adjust_result=sort_adjust_result,
    ):
        return ProtectiveSkipScanResult(
            target_id=target.id,
            url=str(page.url),
            metadata=build_comments_sort_unconfirmed_skip_metadata(
                config=config,
                sort_adjust_result=sort_adjust_result,
                scroll_rounds=effective_scroll_rounds,
                requested_scroll_rounds=scroll_rounds,
                scroll_wait_ms=scroll_wait_ms,
            ),
        )
    items, round_stats, collection_meta = await collect_comment_items_with_diagnostics_async(
        page=page,
        group_id=target.group_id,
        parent_post_id=target.parent_post_id,
        max_items=config.max_items_per_scan,
        scroll_rounds=effective_scroll_rounds,
        scroll_wait_ms=scroll_wait_ms,
        auto_load_more=config.auto_load_more,
    )
    if not items:
        raise WorkerFailure(EXTRACTOR_EMPTY_REASON, "No comment-like items were extracted.")
    return build_comments_pipeline_success_result(
        page_url=str(page.url),
        target=target,
        config=config,
        items=items,
        collection_meta=collection_meta,
        sort_adjust_result=sort_adjust_result,
        round_stats=round_stats,
        scroll_rounds=effective_scroll_rounds,
        requested_scroll_rounds=scroll_rounds,
        scroll_wait_ms=scroll_wait_ms,
        auto_load_more=config.auto_load_more,
    )


def ensure_comments_target(target: TargetDescriptor) -> None:
    """確認呼叫端傳入 comments target。"""

    if target.target_kind != TargetKind.COMMENTS:
        raise WorkerFailure(
            TARGET_KIND_UNSUPPORTED_REASON,
            "Only comments targets are supported.",
        )
    if not target.parent_post_id or not target.scope_id:
        raise WorkerFailure(
            TARGET_INVALID_REASON,
            "Comments target requires parent_post_id and scope_id.",
        )


def finalize_comments_pipeline_scan(
    *,
    page_url: str,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    items: list[ExtractedItem],
    collection_meta: CommentCollectionMeta,
    sort_adjust_result: SortAdjustResult,
    round_stats: list[CommentExtractRoundStats],
    scroll_rounds: int,
    requested_scroll_rounds: int,
    scroll_wait_ms: int,
    auto_load_more: bool,
    commit_guard: ScanCommitGuard | None = None,
) -> CommentsScanSummary:
    """將 comments scan items 交給 shared finalize 層寫入後處理狀態。"""

    success_result = build_comments_pipeline_success_result(
        page_url=page_url,
        target=target,
        config=config,
        items=items,
        collection_meta=collection_meta,
        sort_adjust_result=sort_adjust_result,
        round_stats=round_stats,
        scroll_rounds=scroll_rounds,
        requested_scroll_rounds=requested_scroll_rounds,
        scroll_wait_ms=scroll_wait_ms,
        auto_load_more=auto_load_more,
    )
    finalize_result = finalize_scan_items(
        app=app,
        target=target,
        config=config,
        items=list(success_result.items),
        item_count=success_result.item_count,
        metadata=dict(success_result.metadata),
        commit_guard=commit_guard,
    )
    return CommentsScanSummary(
        target_id=target.id,
        url=success_result.url,
        item_count=success_result.item_count,
        new_count=finalize_result.new_count,
        matched_count=finalize_result.matched_count,
        scan_run_id=finalize_result.scan_run_id,
        round_stats=tuple(round_stats),
    )


def build_comments_pipeline_success_result(
    *,
    page_url: str,
    target: TargetDescriptor,
    config: TargetConfig,
    items: list[ExtractedItem],
    collection_meta: CommentCollectionMeta,
    sort_adjust_result: SortAdjustResult,
    round_stats: list[CommentExtractRoundStats],
    scroll_rounds: int,
    requested_scroll_rounds: int,
    scroll_wait_ms: int,
    auto_load_more: bool,
) -> SuccessScanResult:
    """建立 comments success commit-ready result，不直接寫 DB。"""

    return SuccessScanResult(
        target_id=target.id,
        url=page_url,
        items=tuple(
            normalize_extracted_scan_items(
                items=items,
                item_kind=ItemKind.COMMENT,
                target=target,
            )
        ),
        item_count=len(items),
        metadata=build_comments_scan_metadata(
            items_count=len(items),
            max_items_per_scan=config.max_items_per_scan,
            collection_meta=collection_meta,
            sort_adjust_result=sort_adjust_result,
            round_stats=round_stats,
            scroll_rounds=scroll_rounds,
            requested_scroll_rounds=requested_scroll_rounds,
            scroll_wait_ms=scroll_wait_ms,
            auto_load_more=auto_load_more,
        ),
    )
