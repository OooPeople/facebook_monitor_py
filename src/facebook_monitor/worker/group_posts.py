"""Group posts worker scan workflow。

職責：使用已保存的 target/config 執行單輪 Facebook group feed 掃描，
並寫入 seen item、match history、latest scan items 與 scan run。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Protocol

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.services import RecordScanRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.keyword_rules import evaluate_keyword_rules
from facebook_monitor.facebook.collection_policy import (
    CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT,
)
from facebook_monitor.facebook.collection_policy import get_dynamic_max_windows
from facebook_monitor.facebook.collection_policy import get_effective_scroll_rounds
from facebook_monitor.facebook.feed_extractor import ExtractCollectionMeta
from facebook_monitor.facebook.feed_extractor import ExtractRoundStats
from facebook_monitor.facebook.feed_extractor import collect_items_with_diagnostics_async
from facebook_monitor.facebook.feed_extractor import collect_items_with_diagnostics
from facebook_monitor.facebook.feed_extractor import make_item_key
from facebook_monitor.facebook.feed_extractor import make_item_key_aliases
from facebook_monitor.facebook.sort_controls import SortAdjustResult
from facebook_monitor.facebook.sort_controls import ensure_preferred_feed_sort_async
from facebook_monitor.facebook.sort_controls import ensure_preferred_feed_sort
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.dispatcher import DesktopSender
from facebook_monitor.notifications.dispatcher import DiscordSender
from facebook_monitor.notifications.dispatcher import NtfySender
from facebook_monitor.notifications.dispatcher import notify_match_if_enabled


@dataclass(frozen=True)
class GroupPostsScanSummary:
    """保存正式 worker 單輪 group posts 掃描摘要。"""

    target_id: str
    url: str
    item_count: int
    new_count: int
    matched_count: int
    scan_run_id: int
    round_stats: tuple[ExtractRoundStats, ...]


class WorkerFailure(RuntimeError):
    """保存正式 worker 的失敗分類。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class NotificationSender(NtfySender, Protocol):
    """定義 worker 可注入的通知發送函式介面。"""


def build_scan_metadata(
    *,
    items_count: int,
    new_count: int,
    matched_count: int,
    max_items_per_scan: int,
    scroll_rounds: int,
    requested_scroll_rounds: int,
    scroll_wait_ms: int,
    auto_load_more: bool,
    sort_adjust_result: SortAdjustResult,
    round_stats: list[ExtractRoundStats],
    collection_meta: ExtractCollectionMeta,
) -> dict[str, Any]:
    """整理單輪掃描診斷資料，對齊 userscript latestScan 摘要語義。"""

    normalized_rounds = []
    for stat in round_stats:
        round_metadata: dict[str, Any] = {
            "round_index": stat.round_index,
            "raw_item_count": stat.raw_item_count,
            "unique_item_count": stat.unique_item_count,
            "scroll_y": stat.scroll_y,
            "scroll_height": stat.scroll_height,
        }
        if max(scroll_rounds, 0) > 0 and stat.scroll_target_label:
            round_metadata["scroll_target_label"] = stat.scroll_target_label
            round_metadata["scroll_target_top"] = stat.scroll_target_top
            round_metadata["added_count"] = stat.added_count
            round_metadata["stagnant_windows"] = stat.stagnant_windows
        if stat.scroll_moved is not None:
            round_metadata["scroll_moved"] = stat.scroll_moved
            round_metadata["scroll_before_top"] = stat.scroll_before_top
            round_metadata["scroll_after_top"] = stat.scroll_after_top
            round_metadata["scroll_moved_distance"] = stat.scroll_moved_distance
            round_metadata["scroll_step"] = stat.scroll_step
            round_metadata["load_more_mode"] = stat.load_more_mode
        normalized_rounds.append(round_metadata)
    raw_counts = [stat.raw_item_count for stat in round_stats]
    candidate_count = max(raw_counts) if raw_counts else items_count
    stop_reason = infer_scan_stop_reason(
        items_count=items_count,
        max_items_per_scan=max_items_per_scan,
        scroll_rounds=scroll_rounds,
        round_stats=round_stats,
    )
    return {
        "worker": "phase_b_group_posts_once",
        "collection_strategy": "feed_scroll_rounds"
        if auto_load_more and max(scroll_rounds, 0) > 0
        else "feed_visible_window",
        "auto_load_more": auto_load_more,
        "scroll_collection_enabled": auto_load_more and max(scroll_rounds, 0) > 0,
        "new_count": new_count,
        "matched_count": matched_count,
        "target_count": max_items_per_scan,
        "scanned_count": items_count,
        "candidate_count": candidate_count,
        "round_count": len(round_stats),
        "max_window_count": get_dynamic_max_windows(max_items_per_scan)
        if auto_load_more
        else 1,
        "requested_scroll_rounds": max(requested_scroll_rounds, 0),
        "scroll_rounds": max(scroll_rounds, 0),
        "scroll_wait_ms": max(scroll_wait_ms, 0),
        "load_more_mode": collection_meta.load_more_mode,
        "stop_reason": stop_reason,
        "collected_meta": collection_meta.to_metadata() | {"stopReason": stop_reason},
        "sort_adjust": sort_adjust_result.to_metadata(),
        "rounds": normalized_rounds,
    }


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


def scan_group_posts_page(
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
) -> GroupPostsScanSummary:
    """掃描目前 page，並把結果寫入 application context。"""

    body_text = page.locator("body").inner_text(timeout=10000)
    if "log into facebook" in body_text.lower() or "登入 facebook" in body_text.lower():
        raise WorkerFailure("login_required", "Facebook login is required.")

    sort_adjust_result = ensure_preferred_feed_sort(
        page,
        enabled=config.auto_adjust_sort,
    )
    effective_scroll_rounds = get_effective_scroll_rounds(
        target_count=config.max_items_per_scan,
        requested_scroll_rounds=scroll_rounds,
        auto_load_more=config.auto_load_more,
    )
    items, round_stats, collection_meta = collect_items_with_diagnostics(
        page=page,
        max_items=config.max_items_per_scan,
        scroll_rounds=effective_scroll_rounds,
        scroll_wait_ms=scroll_wait_ms,
    )
    if not items:
        raise WorkerFailure("extractor_empty", "No post-like items were extracted.")

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
                item_kind=ItemKind.POST,
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
                    item_kind=ItemKind.POST,
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
            metadata=build_scan_metadata(
                items_count=len(items),
                new_count=new_count,
                matched_count=matched_count,
                max_items_per_scan=config.max_items_per_scan,
                scroll_rounds=effective_scroll_rounds,
                requested_scroll_rounds=scroll_rounds,
                scroll_wait_ms=scroll_wait_ms,
                auto_load_more=config.auto_load_more,
                sort_adjust_result=sort_adjust_result,
                round_stats=round_stats,
                collection_meta=collection_meta,
            ),
        )
    )
    app.repositories.latest_scan_items.replace_for_target(
        target.id,
        [
            LatestScanItem(
                target_id=target.id,
                scan_run_id=scan_run_id,
                item_kind=ItemKind.POST,
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

    return GroupPostsScanSummary(
        target_id=target.id,
        url=page.url,
        item_count=len(items),
        new_count=new_count,
        matched_count=matched_count,
        scan_run_id=scan_run_id,
        round_stats=tuple(round_stats),
    )


async def scan_group_posts_page_async(
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
) -> GroupPostsScanSummary:
    """async resident worker 掃描目前 page，並寫入 application context。"""

    body_text = await page.locator("body").inner_text(timeout=10000)
    if "log into facebook" in body_text.lower() or "登入 facebook" in body_text.lower():
        raise WorkerFailure("login_required", "Facebook login is required.")

    sort_adjust_result = await ensure_preferred_feed_sort_async(
        page,
        enabled=config.auto_adjust_sort,
    )
    effective_scroll_rounds = get_effective_scroll_rounds(
        target_count=config.max_items_per_scan,
        requested_scroll_rounds=scroll_rounds,
        auto_load_more=config.auto_load_more,
    )
    items, round_stats, collection_meta = await collect_items_with_diagnostics_async(
        page=page,
        max_items=config.max_items_per_scan,
        scroll_rounds=effective_scroll_rounds,
        scroll_wait_ms=scroll_wait_ms,
    )
    if not items:
        raise WorkerFailure("extractor_empty", "No post-like items were extracted.")

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
                item_kind=ItemKind.POST,
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
                    item_kind=ItemKind.POST,
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
            metadata=build_scan_metadata(
                items_count=len(items),
                new_count=new_count,
                matched_count=matched_count,
                max_items_per_scan=config.max_items_per_scan,
                scroll_rounds=effective_scroll_rounds,
                requested_scroll_rounds=scroll_rounds,
                scroll_wait_ms=scroll_wait_ms,
                auto_load_more=config.auto_load_more,
                sort_adjust_result=sort_adjust_result,
                round_stats=round_stats,
                collection_meta=collection_meta,
            ),
        )
    )
    app.repositories.latest_scan_items.replace_for_target(
        target.id,
        [
            LatestScanItem(
                target_id=target.id,
                scan_run_id=scan_run_id,
                item_kind=ItemKind.POST,
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

    return GroupPostsScanSummary(
        target_id=target.id,
        url=page.url,
        item_count=len(items),
        new_count=new_count,
        matched_count=matched_count,
        scan_run_id=scan_run_id,
        round_stats=tuple(round_stats),
    )
