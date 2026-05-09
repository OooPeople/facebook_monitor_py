"""共用 scan finalize layer。

職責：接收 posts/comments pipeline 已正規化的掃描項目，集中處理
seen 去重、keyword 分類、history、notification、latest scan 與 scan run 寫入。
Extractor、sort 與 load-more 仍由 target-kind-specific pipeline 負責。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.scan_recording_service import RecordScanRequest
from facebook_monitor.core.keyword_rules import KeywordEvaluation
from facebook_monitor.core.keyword_rules import evaluate_keyword_rules
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import utc_now
from facebook_monitor.facebook.extracted_item import ExtractedItem
from facebook_monitor.facebook.extracted_item import make_item_key
from facebook_monitor.facebook.extracted_item import make_item_key_aliases
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.channel_dispatch import DesktopSender
from facebook_monitor.notifications.channel_dispatch import DiscordSender
from facebook_monitor.notifications.channel_dispatch import NtfySender
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.notifications.outbox_service import queue_match_notifications_after_commit


@dataclass(frozen=True)
class NormalizedScanItem:
    """posts/comments extractor 輸出的共用中間表示。"""

    item_kind: ItemKind
    item_key: str
    alias_keys: tuple[str, ...]
    group_id: str
    parent_post_id: str = ""
    comment_id: str = ""
    author: str = ""
    text: str = ""
    permalink: str = ""
    timestamp_text: str = ""
    raw_target_kind: str = ""
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ScanMatchResult:
    """保存單一 normalized item 經過 seen 與 keyword 後的分類結果。"""

    item: NormalizedScanItem
    is_new: bool
    is_matched: bool
    include_rule: str
    exclude_rule: str
    eligible_for_notify: bool
    matched_keyword: str


@dataclass(frozen=True)
class MatchNotificationPayload:
    """保存 shared finalize 層準備送出的 match 通知資料。"""

    item_key: str
    item_kind: ItemKind
    author: str
    text: str
    permalink: str
    matched_keyword: str


@dataclass(frozen=True)
class ScanFinalizeResult:
    """保存 shared finalize 層的總輸出。"""

    scan_run_id: int
    new_items: tuple[NormalizedScanItem, ...]
    matched_items: tuple[NormalizedScanItem, ...]
    match_results: tuple[ScanMatchResult, ...]
    history_entries: tuple[MatchHistoryEntry, ...]
    notification_payloads: tuple[MatchNotificationPayload, ...]
    latest_items: tuple[LatestScanItem, ...]
    scan_summary: dict[str, Any]

    @property
    def new_count(self) -> int:
        """回傳本輪首次看見的項目數。"""

        return len(self.new_items)

    @property
    def matched_count(self) -> int:
        """回傳本輪符合 keyword 規則的項目數。"""

        return len(self.matched_items)


def normalize_extracted_scan_items(
    *,
    items: list[ExtractedItem],
    item_kind: ItemKind,
    target: TargetDescriptor,
) -> list[NormalizedScanItem]:
    """將 target-specific extractor item 轉成 shared finalize 使用的格式。"""

    normalized_items: list[NormalizedScanItem] = []
    for item in items:
        item_key = make_item_key(item)
        alias_keys = make_item_key_aliases(item)
        if not item_key or not alias_keys:
            continue
        normalized_items.append(
            NormalizedScanItem(
                item_kind=item_kind,
                item_key=item_key,
                alias_keys=alias_keys,
                group_id=target.group_id,
                parent_post_id=target.parent_post_id if item_kind == ItemKind.COMMENT else "",
                comment_id=item.comment_id if item_kind == ItemKind.COMMENT else "",
                author=item.author,
                text=item.text,
                permalink=item.permalink,
                raw_target_kind=target.target_kind.value,
                metadata=item.debug_metadata or {},
            )
        )
    return normalized_items


def finalize_scan_items(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    items: list[NormalizedScanItem],
    item_count: int,
    metadata: dict[str, Any],
    notification_sender: NtfySender = send_ntfy_notification,
    desktop_notification_sender: DesktopSender = send_desktop_notification,
    discord_notification_sender: DiscordSender = send_discord_notification,
) -> ScanFinalizeResult:
    """完成 target-kind-independent 的 scan 後處理與持久化。"""

    match_results: list[ScanMatchResult] = []
    new_items: list[NormalizedScanItem] = []
    matched_items: list[NormalizedScanItem] = []
    history_entries: list[MatchHistoryEntry] = []
    notification_payloads: list[MatchNotificationPayload] = []

    for item in items:
        is_new = app.repositories.seen_items.mark_seen_aliases(
            SeenItem(
                scope_id=target.scope_id,
                item_key=item.item_key,
                item_kind=item.item_kind,
                parent_post_id=item.parent_post_id,
                comment_id=item.comment_id,
            ),
            item.alias_keys,
        )
        keyword_evaluation = evaluate_keyword_rules(
            item.text,
            include_keywords=config.include_keywords,
            exclude_keywords=config.exclude_keywords,
        )
        result = build_scan_match_result(
            item=item,
            is_new=is_new,
            keyword_evaluation=keyword_evaluation,
        )
        match_results.append(result)
        if result.is_new:
            new_items.append(item)
        if result.is_matched:
            matched_items.append(item)
        if not result.eligible_for_notify:
            continue

        notified_at = utc_now()
        history_entry = MatchHistoryEntry(
            target_id=target.id,
            group_id=target.group_id,
            group_name=target.group_name,
            item_kind=item.item_kind,
            parent_post_id=item.parent_post_id,
            comment_id=item.comment_id,
            item_key=item.item_key,
            author=item.author,
            text=item.text,
            permalink=item.permalink,
            include_rule=result.include_rule,
            timestamp_text=item.timestamp_text,
            notified_at=notified_at,
            created_at=notified_at,
        )
        app.repositories.match_history.add(history_entry)
        history_entries.append(history_entry)

        notification_payload = MatchNotificationPayload(
            item_key=item.item_key,
            item_kind=item.item_kind,
            author=item.author,
            text=item.text,
            permalink=item.permalink,
            matched_keyword=result.matched_keyword,
        )
        queue_match_notifications_after_commit(
            app=app,
            target=target,
            config=config,
            item_key=notification_payload.item_key,
            author=notification_payload.author,
            item_text=notification_payload.text,
            permalink=notification_payload.permalink,
            matched_keyword=notification_payload.matched_keyword,
            item_kind=notification_payload.item_kind,
            ntfy_sender=notification_sender,
            desktop_sender=desktop_notification_sender,
            discord_sender=discord_notification_sender,
        )
        notification_payloads.append(notification_payload)

    scan_metadata = dict(metadata)
    scan_metadata["new_count"] = len(new_items)
    scan_metadata["matched_count"] = len(matched_items)
    scan_run_id = app.services.scans.record_scan(
        RecordScanRequest(
            target_id=target.id,
            status=ScanStatus.SUCCESS,
            item_count=item_count,
            matched_count=len(matched_items),
            metadata=scan_metadata,
        )
    )
    latest_items = build_latest_scan_items(
        target=target,
        scan_run_id=scan_run_id,
        match_results=match_results,
    )
    app.repositories.latest_scan_items.replace_for_target(target.id, latest_items)

    return ScanFinalizeResult(
        scan_run_id=scan_run_id,
        new_items=tuple(new_items),
        matched_items=tuple(matched_items),
        match_results=tuple(match_results),
        history_entries=tuple(history_entries),
        notification_payloads=tuple(notification_payloads),
        latest_items=tuple(latest_items),
        scan_summary=scan_metadata,
    )


def build_scan_match_result(
    *,
    item: NormalizedScanItem,
    is_new: bool,
    keyword_evaluation: KeywordEvaluation,
) -> ScanMatchResult:
    """建立單一 item 的 shared classification 結果。"""

    return ScanMatchResult(
        item=item,
        is_new=is_new,
        is_matched=keyword_evaluation.eligible,
        include_rule=keyword_evaluation.include_rule,
        exclude_rule=keyword_evaluation.exclude_rule,
        eligible_for_notify=is_new and keyword_evaluation.eligible,
        matched_keyword=keyword_evaluation.display_rule,
    )


def build_latest_scan_items(
    *,
    target: TargetDescriptor,
    scan_run_id: int,
    match_results: list[ScanMatchResult],
) -> list[LatestScanItem]:
    """將 shared classification 結果轉成 latest scan snapshot。"""

    return [
        LatestScanItem(
            target_id=target.id,
            scan_run_id=scan_run_id,
            item_kind=result.item.item_kind,
            item_key=result.item.item_key,
            item_index=item_index,
            author=result.item.author,
            text=result.item.text,
            permalink=result.item.permalink,
            matched_keyword=result.matched_keyword,
            debug_metadata=result.item.metadata or {},
        )
        for item_index, result in enumerate(match_results)
    ]
