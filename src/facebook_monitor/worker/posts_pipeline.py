"""Group posts scan pipeline。

職責：處理 posts target 的頁面檢查、排序、載入更多與 DOM 抽取。
seen、keyword、history、notification、latest scan 與 scan run 寫入交由
shared scan finalize layer，避免 posts/comments 後處理語義漂移。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Protocol

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.scan_failures import EXTRACTOR_EMPTY_REASON
from facebook_monitor.facebook.collection_policy import (
    CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT,
)
from facebook_monitor.facebook.collection_policy import get_dynamic_max_windows
from facebook_monitor.facebook.feed_extractor import ExtractCollectionMeta
from facebook_monitor.facebook.feed_extractor import ExtractRoundStats
from facebook_monitor.facebook.feed_extractor import SeenItemPredicate
from facebook_monitor.facebook.feed_extractor import collect_items_with_diagnostics_async
from facebook_monitor.facebook.feed_extractor import collect_items_with_diagnostics
from facebook_monitor.facebook.sort_controls import FEED_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_controls import SortAdjustResult
from facebook_monitor.facebook.sort_controls import ensure_preferred_feed_sort_async
from facebook_monitor.facebook.sort_controls import ensure_preferred_feed_sort
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.channel_dispatch import DesktopSender
from facebook_monitor.notifications.channel_dispatch import DiscordSender
from facebook_monitor.notifications.channel_dispatch import NtfySender
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.scan_orchestration import ensure_async_page_scannable
from facebook_monitor.worker.scan_orchestration import ensure_sync_page_scannable
from facebook_monitor.worker.scan_orchestration import resolve_effective_scan_scroll_rounds
from facebook_monitor.worker.scan_metadata import PostScanMetadata
from facebook_monitor.worker.scan_metadata import PostScanRoundMetadata
from facebook_monitor.worker.scan_metadata import SORT_ADJUST_SKIP_COLLECTION_MODE
from facebook_monitor.worker.scan_metadata import build_sort_adjust_skip_meta
from facebook_monitor.worker.scan_metadata import with_scan_skipped_reason
from facebook_monitor.worker.scan_finalize import finalize_scan_items
from facebook_monitor.worker.scan_finalize import normalize_extracted_scan_items
from facebook_monitor.worker.scan_finalize import record_skipped_scan
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_finalize import SORT_ADJUST_UNCONFIRMED_SKIP_REASON
from facebook_monitor.worker.scan_finalize import SORT_ADJUST_UNCONFIRMED_STOP_REASON
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


class NotificationSender(NtfySender, Protocol):
    """定義 worker 可注入的通知發送函式介面。"""


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

    normalized_rounds: list[PostScanRoundMetadata] = []
    for stat in round_stats:
        include_scroll_target = max(scroll_rounds, 0) > 0 and stat.scroll_target_label
        normalized_rounds.append(
            PostScanRoundMetadata(
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
        )
    raw_counts = [stat.raw_item_count for stat in round_stats]
    candidate_count = max(raw_counts) if raw_counts else items_count
    stop_reason = collection_meta.stop_reason or infer_scan_stop_reason(
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
        rounds=tuple(normalized_rounds),
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
        and round_stats[-1].stagnant_windows
        >= CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT
    ):
        return "stagnant_windows"
    if round_stats[-1].round_index >= max(scroll_rounds, 0):
        return "scroll_rounds_completed"
    return "collection_stopped"


def scan_posts_page(
    *,
    page: Any,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    scroll_rounds: int,
    scroll_wait_ms: int,
    notification_sender: NotificationSender = send_ntfy_notification,
    desktop_notification_sender: DesktopSender = send_desktop_notification,
    discord_notification_sender: DiscordSender = send_discord_notification,
    commit_guard: ScanCommitGuard | None = None,
) -> PostsScanSummary:
    """掃描目前 page，並把結果寫入 application context。"""

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
        finalize_result = record_skipped_scan(
            app=app,
            target=target,
            commit_guard=commit_guard,
            metadata=build_sort_unconfirmed_skip_metadata(
                config=config,
                sort_adjust_result=sort_adjust_result,
                scroll_rounds=effective_scroll_rounds,
                requested_scroll_rounds=scroll_rounds,
                scroll_wait_ms=scroll_wait_ms,
            ),
        )
        return PostsScanSummary(
            target_id=target.id,
            url=str(page.url),
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=finalize_result.scan_run_id,
            round_stats=(),
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
        notification_sender=notification_sender,
        desktop_notification_sender=desktop_notification_sender,
        discord_notification_sender=discord_notification_sender,
        commit_guard=commit_guard,
    )


async def scan_posts_page_async(
    *,
    page: Any,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    scroll_rounds: int,
    scroll_wait_ms: int,
    notification_sender: NotificationSender = send_ntfy_notification,
    desktop_notification_sender: DesktopSender = send_desktop_notification,
    discord_notification_sender: DiscordSender = send_discord_notification,
    commit_guard: ScanCommitGuard | None = None,
) -> PostsScanSummary:
    """resident main worker 掃描目前 page，並寫入 application context。"""

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
        finalize_result = record_skipped_scan(
            app=app,
            target=target,
            commit_guard=commit_guard,
            metadata=build_sort_unconfirmed_skip_metadata(
                config=config,
                sort_adjust_result=sort_adjust_result,
                scroll_rounds=effective_scroll_rounds,
                requested_scroll_rounds=scroll_rounds,
                scroll_wait_ms=scroll_wait_ms,
            ),
        )
        return PostsScanSummary(
            target_id=target.id,
            url=str(page.url),
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=finalize_result.scan_run_id,
            round_stats=(),
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
        notification_sender=notification_sender,
        desktop_notification_sender=desktop_notification_sender,
        discord_notification_sender=discord_notification_sender,
        commit_guard=commit_guard,
    )


def finalize_posts_pipeline_scan(
    *,
    page_url: str,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    items: list[Any],
    collection_meta: ExtractCollectionMeta,
    sort_adjust_result: SortAdjustResult,
    round_stats: list[ExtractRoundStats],
    scroll_rounds: int,
    requested_scroll_rounds: int,
    scroll_wait_ms: int,
    auto_load_more: bool,
    notification_sender: NotificationSender,
    desktop_notification_sender: DesktopSender,
    discord_notification_sender: DiscordSender,
    commit_guard: ScanCommitGuard | None = None,
) -> PostsScanSummary:
    """將 posts scan items 交給 shared finalize 層寫入後處理狀態。"""

    finalize_result = finalize_scan_items(
        app=app,
        target=target,
        config=config,
        items=normalize_extracted_scan_items(
            items=items,
            item_kind=ItemKind.POST,
            target=target,
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
        notification_sender=notification_sender,
        desktop_notification_sender=desktop_notification_sender,
        discord_notification_sender=discord_notification_sender,
        commit_guard=commit_guard,
    )
    return PostsScanSummary(
        target_id=target.id,
        url=page_url,
        item_count=len(items),
        new_count=finalize_result.new_count,
        matched_count=finalize_result.matched_count,
        scan_run_id=finalize_result.scan_run_id,
        round_stats=tuple(round_stats),
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
        return app.repositories.seen_items.has_seen_any(target.scope_id, item_aliases)

    return has_seen_item


def _feed_seen_stop_sort_is_trusted(sort_adjust_result: SortAdjustResult) -> bool:
    """判斷 posts feed 是否可安全套用 seen-stop 的排序前提。"""

    return (
        sort_adjust_result.after_label == FEED_SORT_NEWEST_LABEL
        or sort_control_absent_without_observed_label(sort_adjust_result)
    )
