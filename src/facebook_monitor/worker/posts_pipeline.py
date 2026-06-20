"""Group posts scan pipeline。

職責：處理 posts target 的頁面檢查、排序、載入更多與 DOM 抽取。
seen、keyword、history、notification、latest scan 與 scan run 寫入交由
shared scan finalize layer，避免 posts/comments 後處理語義漂移。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.defaults import PYTHON_PERSISTENCE_RETENTION_DEFAULTS
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import EXTRACTOR_EMPTY_REASON
from facebook_monitor.facebook.feed_extraction_models import ExtractCollectionMeta
from facebook_monitor.facebook.feed_extraction_models import ExtractRoundStats
from facebook_monitor.facebook.feed_extractor import SeenItemPredicate
from facebook_monitor.facebook.feed_extractor import collect_items_with_diagnostics_async
from facebook_monitor.facebook.feed_extractor import collect_items_with_diagnostics
from facebook_monitor.facebook.extracted_item import ExtractedItem
from facebook_monitor.facebook.sort_results import FEED_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_results import SortAdjustResult
from facebook_monitor.facebook.sort_runtime import ensure_preferred_feed_sort_async
from facebook_monitor.facebook.sort_runtime import ensure_preferred_feed_sort
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.scan_orchestration import ensure_async_page_scannable
from facebook_monitor.worker.scan_orchestration import ensure_sync_page_scannable
from facebook_monitor.worker.scan_orchestration import AsyncScannablePageLike
from facebook_monitor.worker.scan_orchestration import resolve_effective_scan_scroll_rounds
from facebook_monitor.worker.scan_orchestration import SyncScannablePageLike
from facebook_monitor.worker.posts_scan_metadata import build_scan_metadata
from facebook_monitor.worker.posts_scan_metadata import build_sort_unconfirmed_skip_metadata
from facebook_monitor.worker.scan_finalize import finalize_scan_items
from facebook_monitor.worker.scan_finalize import normalize_extracted_scan_items
from facebook_monitor.worker.scan_finalize import record_guarded_skipped_scan
from facebook_monitor.worker.scan_finalize import record_unguarded_skipped_scan_for_one_shot
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_pipeline_results import ProtectiveSkipScanResult
from facebook_monitor.worker.scan_pipeline_results import SuccessScanResult
from facebook_monitor.worker.scan_sort_policy import should_skip_scan_for_unconfirmed_sort
from facebook_monitor.worker.scan_sort_policy import sort_control_absent_without_observed_label


@dataclass(frozen=True)
class PostsScanSummary:
    """保存正式 worker 單輪 group posts 掃描摘要。"""

    target_id: str
    url: str
    item_count: int
    new_count: int
    matched_count: int
    scan_run_id: int
    round_stats: tuple[ExtractRoundStats, ...]
    scan_skipped: bool = False


def scan_posts_page_sync_and_finalize(
    *,
    page: SyncScannablePageLike,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    scroll_rounds: int,
    scroll_wait_ms: int,
    commit_guard: ScanCommitGuard | None = None,
) -> PostsScanSummary:
    """sync/one-shot/fallback 掃描目前 page，並直接寫入 visible scan state。"""

    ensure_sync_page_scannable(page)

    sort_adjust_result = ensure_preferred_feed_sort(
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
        allow_absent_sort_control_without_label=True,
    ):
        skip_metadata = build_sort_unconfirmed_skip_metadata(
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
        return PostsScanSummary(
            target_id=target.id,
            url=str(page.url),
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=finalize_result.scan_run_id,
            round_stats=(),
            scan_skipped=True,
        )
    items, round_stats, collection_meta = collect_items_with_diagnostics(
        page=page,
        max_items=config.max_items_per_scan,
        scroll_rounds=effective_scroll_rounds,
        scroll_wait_ms=scroll_wait_ms,
        seen_item_predicate=build_feed_seen_stop_predicate(
            app=app,
            target=target,
            config=config,
            scroll_rounds=effective_scroll_rounds,
            sort_adjust_result=sort_adjust_result,
        ),
    )
    if not items:
        raise WorkerFailure(EXTRACTOR_EMPTY_REASON, "No post-like items were extracted.")

    return finalize_posts_pipeline_scan(
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


async def scan_posts_page_async_commit_ready(
    *,
    page: AsyncScannablePageLike,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    scroll_rounds: int,
    scroll_wait_ms: int,
) -> SuccessScanResult | ProtectiveSkipScanResult:
    """formal async resident 掃描 page；visible scan state 交由 coordinator commit。"""

    await ensure_async_page_scannable(page)

    sort_adjust_result = await ensure_preferred_feed_sort_async(
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
        allow_absent_sort_control_without_label=True,
    ):
        return ProtectiveSkipScanResult(
            target_id=target.id,
            url=str(page.url),
            metadata=build_sort_unconfirmed_skip_metadata(
                config=config,
                sort_adjust_result=sort_adjust_result,
                scroll_rounds=effective_scroll_rounds,
                requested_scroll_rounds=scroll_rounds,
                scroll_wait_ms=scroll_wait_ms,
            ),
        )
    items, round_stats, collection_meta = await collect_items_with_diagnostics_async(
        page=page,
        max_items=config.max_items_per_scan,
        scroll_rounds=effective_scroll_rounds,
        scroll_wait_ms=scroll_wait_ms,
        seen_item_predicate=build_feed_seen_stop_predicate(
            app=app,
            target=target,
            config=config,
            scroll_rounds=effective_scroll_rounds,
            sort_adjust_result=sort_adjust_result,
        ),
    )
    if not items:
        raise WorkerFailure(EXTRACTOR_EMPTY_REASON, "No post-like items were extracted.")

    return build_posts_pipeline_success_result(
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


def finalize_posts_pipeline_scan(
    *,
    page_url: str,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    items: list[ExtractedItem],
    collection_meta: ExtractCollectionMeta,
    sort_adjust_result: SortAdjustResult,
    round_stats: list[ExtractRoundStats],
    scroll_rounds: int,
    requested_scroll_rounds: int,
    scroll_wait_ms: int,
    auto_load_more: bool,
    commit_guard: ScanCommitGuard | None = None,
) -> PostsScanSummary:
    """將 posts scan items 交給 shared finalize 層寫入後處理狀態。"""

    success_result = build_posts_pipeline_success_result(
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
    return PostsScanSummary(
        target_id=target.id,
        url=success_result.url,
        item_count=success_result.item_count,
        new_count=finalize_result.new_count,
        matched_count=finalize_result.matched_count,
        scan_run_id=finalize_result.scan_run_id,
        round_stats=tuple(round_stats),
    )


def build_posts_pipeline_success_result(
    *,
    page_url: str,
    target: TargetDescriptor,
    config: TargetConfig,
    items: list[ExtractedItem],
    collection_meta: ExtractCollectionMeta,
    sort_adjust_result: SortAdjustResult,
    round_stats: list[ExtractRoundStats],
    scroll_rounds: int,
    requested_scroll_rounds: int,
    scroll_wait_ms: int,
    auto_load_more: bool,
) -> SuccessScanResult:
    """建立 posts success commit-ready result，不直接寫 DB。"""

    return SuccessScanResult(
        target_id=target.id,
        url=page_url,
        items=tuple(
            normalize_extracted_scan_items(
                items=items,
                item_kind=ItemKind.POST,
                target=target,
            )
        ),
        item_count=len(items),
        metadata=build_scan_metadata(
            items_count=len(items),
            max_items_per_scan=config.max_items_per_scan,
            scroll_rounds=scroll_rounds,
            requested_scroll_rounds=requested_scroll_rounds,
            scroll_wait_ms=scroll_wait_ms,
            auto_load_more=auto_load_more,
            sort_adjust_result=sort_adjust_result,
            round_stats=round_stats,
            collection_meta=collection_meta,
        ),
    )


def build_feed_seen_stop_predicate(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    scroll_rounds: int,
    sort_adjust_result: SortAdjustResult,
) -> SeenItemPredicate | None:
    """只在可信的新貼文排序情境啟用 seen-stop，避免置頂貼文造成漏掃。"""

    if not config.auto_load_more or max(scroll_rounds, 0) <= 0:
        return None
    if not _feed_seen_stop_sort_is_trusted(sort_adjust_result):
        return None

    def has_seen_item(item_aliases: tuple[str, ...]) -> bool:
        legacy_cutoff = utc_now() - timedelta(
            days=PYTHON_PERSISTENCE_RETENTION_DEFAULTS.logical_dedupe_horizon_days
        )
        return app.repositories.logical_items.has_seen_any(
            target_id=target.id,
            scope_id=target.scope_id,
            item_keys=item_aliases,
        ) or app.repositories.seen_items.has_seen_any_since(
            target.scope_id,
            item_aliases,
            legacy_cutoff,
        )

    return has_seen_item


def _feed_seen_stop_sort_is_trusted(sort_adjust_result: SortAdjustResult) -> bool:
    """判斷 posts feed 是否可安全套用 seen-stop 的排序前提。"""

    return (
        sort_adjust_result.after_label == FEED_SORT_NEWEST_LABEL
        or sort_control_absent_without_observed_label(sort_adjust_result)
    )
