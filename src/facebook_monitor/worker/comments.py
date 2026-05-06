"""Group comments worker scan workflow。

職責：執行 comments 掃描、dedupe、keyword 比對與 latest scan state 寫入。
D3 已接上 comment sort 與 nested scroll/load-more；mutation relevance 由獨立 helper
保存語義，不在本輪 polling worker 內啟用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Protocol

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.services import RecordScanRequest
from facebook_monitor.core.keyword_rules import evaluate_keyword_rules
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.facebook.collection_policy import get_effective_scroll_rounds
from facebook_monitor.facebook.comment_extractor import CommentCollectionMeta
from facebook_monitor.facebook.comment_extractor import CommentExtractRoundStats
from facebook_monitor.facebook.comment_extractor import collect_comment_items_with_diagnostics
from facebook_monitor.facebook.comment_extractor import collect_comment_items_with_diagnostics_async
from facebook_monitor.facebook.feed_extractor import ExtractedItem
from facebook_monitor.facebook.feed_extractor import make_item_key
from facebook_monitor.facebook.feed_extractor import make_item_key_aliases
from facebook_monitor.facebook.sort_controls import SortAdjustResult
from facebook_monitor.facebook.sort_controls import ensure_preferred_comment_sort
from facebook_monitor.facebook.sort_controls import ensure_preferred_comment_sort_async
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.dispatcher import DesktopSender
from facebook_monitor.notifications.dispatcher import DiscordSender
from facebook_monitor.notifications.dispatcher import NtfySender
from facebook_monitor.notifications.dispatcher import notify_match_if_enabled
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.worker.group_posts import WorkerFailure


@dataclass(frozen=True)
class GroupCommentsScanSummary:
    """保存 comments 單輪掃描摘要。"""

    target_id: str
    url: str
    item_count: int
    new_count: int
    matched_count: int
    scan_run_id: int
    round_stats: tuple[CommentExtractRoundStats, ...] = ()


class NotificationSender(NtfySender, Protocol):
    """定義 comments worker 可注入的通知發送函式介面。"""


def build_comments_scan_metadata(
    *,
    items_count: int,
    new_count: int,
    matched_count: int,
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

    return {
        "worker": "comments_scan",
        "collection_strategy": collection_meta.mode,
        "comment_count": items_count,
        "new_count": new_count,
        "matched_count": matched_count,
        "target_count": max_items_per_scan,
        "candidate_count": collection_meta.candidate_count,
        "round_count": len(round_stats),
        "requested_scroll_rounds": max(requested_scroll_rounds, 0),
        "scroll_rounds": max(scroll_rounds, 0),
        "scroll_wait_ms": max(scroll_wait_ms, 0),
        "auto_load_more": auto_load_more,
        "load_more_mode": collection_meta.load_more_mode,
        "comment_scroll_collection_enabled": auto_load_more and max(scroll_rounds, 0) > 0,
        "stop_reason": collection_meta.stop_reason,
        "comment_sort": sort_adjust_result.to_metadata(),
        "comment_extract_rounds": [
            {
                "round_index": stat.round_index,
                "raw_item_count": stat.raw_item_count,
                "unique_item_count": stat.unique_item_count,
                "candidate_count": stat.candidate_count,
                "parsed_count": stat.parsed_count,
                "accumulated_count": stat.accumulated_count,
                "filtered_empty_text_count": stat.filtered_empty_text_count,
                "filtered_non_post_count": stat.filtered_non_post_count,
                "comments_with_comment_id_count": stat.comments_with_comment_id_count,
                "scroll_moved": stat.scroll_moved,
                "scroll_target_label": stat.scroll_target_label,
                "scroll_before_top": stat.scroll_before_top,
                "scroll_after_top": stat.scroll_after_top,
                "scroll_moved_distance": stat.scroll_moved_distance,
                "scroll_step": stat.scroll_step,
                "load_more_mode": stat.load_more_mode,
                "added_count": stat.added_count,
                "stagnant_windows": stat.stagnant_windows,
            }
            for stat in round_stats
        ],
        "comments_meta": collection_meta.to_metadata(),
    }


def scan_comments_page(
    *,
    page: Any,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    scroll_rounds: int = 0,
    scroll_wait_ms: int = 0,
    notification_sender: NotificationSender = send_ntfy_notification,
    desktop_notification_sender: DesktopSender = send_desktop_notification,
    discord_notification_sender: DiscordSender = send_discord_notification,
) -> GroupCommentsScanSummary:
    """掃描目前頁面可見留言，並寫入 comments latest scan state。"""

    ensure_comments_target(target)
    body_text = page.locator("body").inner_text(timeout=10000)
    if "log into facebook" in body_text.lower() or "登入 facebook" in body_text.lower():
        raise WorkerFailure("login_required", "Facebook login is required.")

    sort_adjust_result = ensure_preferred_comment_sort(
        page,
        enabled=config.auto_adjust_sort,
    )
    effective_scroll_rounds = get_effective_scroll_rounds(
        target_count=config.max_items_per_scan,
        requested_scroll_rounds=scroll_rounds,
        auto_load_more=config.auto_load_more,
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
        raise WorkerFailure("extractor_empty", "No comment-like items were extracted.")
    return persist_comments_scan(
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
    )


async def scan_comments_page_async(
    *,
    page: Any,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    scroll_rounds: int = 0,
    scroll_wait_ms: int = 0,
    notification_sender: NotificationSender = send_ntfy_notification,
    desktop_notification_sender: DesktopSender = send_desktop_notification,
    discord_notification_sender: DiscordSender = send_discord_notification,
) -> GroupCommentsScanSummary:
    """async 版本：掃描目前頁面可見留言。"""

    ensure_comments_target(target)
    body_text = await page.locator("body").inner_text(timeout=10000)
    if "log into facebook" in body_text.lower() or "登入 facebook" in body_text.lower():
        raise WorkerFailure("login_required", "Facebook login is required.")

    sort_adjust_result = await ensure_preferred_comment_sort_async(
        page,
        enabled=config.auto_adjust_sort,
    )
    effective_scroll_rounds = get_effective_scroll_rounds(
        target_count=config.max_items_per_scan,
        requested_scroll_rounds=scroll_rounds,
        auto_load_more=config.auto_load_more,
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
        raise WorkerFailure("extractor_empty", "No comment-like items were extracted.")
    return persist_comments_scan(
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
    )


def ensure_comments_target(target: TargetDescriptor) -> None:
    """確認呼叫端傳入 comments target。"""

    if target.target_kind != TargetKind.COMMENTS:
        raise WorkerFailure("target_kind_unsupported", "Only comments targets are supported.")
    if not target.parent_post_id or not target.scope_id:
        raise WorkerFailure("target_invalid", "Comments target requires parent_post_id and scope_id.")


def persist_comments_scan(
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
    notification_sender: NotificationSender,
    desktop_notification_sender: DesktopSender,
    discord_notification_sender: DiscordSender,
) -> GroupCommentsScanSummary:
    """將 comments scan items 寫入 seen/history/latest scan/scan run。"""

    new_count = 0
    matched_count = 0
    latest_items: list[tuple[int, str, str]] = []
    for item in items:
        item_key = make_item_key(item)
        item_key_aliases = make_item_key_aliases(item)
        if not item_key or not item_key_aliases:
            continue
        is_new = app.repositories.seen_items.mark_seen_aliases(
            SeenItem(
                scope_id=target.scope_id,
                item_key=item_key,
                item_kind=ItemKind.COMMENT,
                parent_post_id=target.parent_post_id,
                comment_id=item.comment_id,
            ),
            item_key_aliases,
        )
        keyword_evaluation = evaluate_keyword_rules(
            item.text,
            include_keywords=config.include_keywords,
            exclude_keywords=config.exclude_keywords,
        )
        matched_keyword = keyword_evaluation.display_rule
        if keyword_evaluation.eligible:
            matched_count += 1
        latest_items.append((len(latest_items), item_key, matched_keyword))
        if is_new:
            new_count += 1
        if is_new and keyword_evaluation.eligible:
            app.repositories.match_history.add(
                MatchHistoryEntry(
                    target_id=target.id,
                    group_id=target.group_id,
                    group_name=target.group_name,
                    item_kind=ItemKind.COMMENT,
                    parent_post_id=target.parent_post_id,
                    comment_id=item.comment_id,
                    item_key=item_key,
                    author=item.author,
                    text=item.text,
                    permalink=item.permalink,
                    include_rule=keyword_evaluation.include_rule,
                )
            )
            notify_match_if_enabled(
                app=app,
                target=target,
                config=config,
                item_key=item_key,
                author=item.author,
                item_text=item.text,
                permalink=item.permalink,
                matched_keyword=matched_keyword,
                item_kind=ItemKind.COMMENT,
                ntfy_sender=notification_sender,
                desktop_sender=desktop_notification_sender,
                discord_sender=discord_notification_sender,
            )

    scan_run_id = app.services.scans.record_scan(
        RecordScanRequest(
            target_id=target.id,
            status=ScanStatus.SUCCESS,
            item_count=len(items),
            matched_count=matched_count,
            metadata=build_comments_scan_metadata(
                items_count=len(items),
                new_count=new_count,
                matched_count=matched_count,
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
    )
    app.repositories.latest_scan_items.replace_for_target(
        target.id,
        [
            LatestScanItem(
                target_id=target.id,
                scan_run_id=scan_run_id,
                item_kind=ItemKind.COMMENT,
                item_key=item_key,
                item_index=item_index,
                author=items[item_index].author,
                text=items[item_index].text,
                permalink=items[item_index].permalink,
                matched_keyword=matched_keyword,
                debug_metadata=items[item_index].debug_metadata or {},
            )
            for item_index, item_key, matched_keyword in latest_items
        ],
    )
    return GroupCommentsScanSummary(
        target_id=target.id,
        url=page_url,
        item_count=len(items),
        new_count=new_count,
        matched_count=matched_count,
        scan_run_id=scan_run_id,
        round_stats=tuple(round_stats),
    )
