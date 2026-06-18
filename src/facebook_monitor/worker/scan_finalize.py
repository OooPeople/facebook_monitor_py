"""共用 scan finalize layer。

職責：接收 posts/comments pipeline 已正規化的掃描項目，集中處理
seen 去重、keyword 分類、history、notification、latest scan 與 scan run 寫入。
Extractor、sort 與 load-more 仍由 target-kind-specific pipeline 負責。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from typing import Any

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.scan_recording_service import RecordScanRequest
from facebook_monitor.application.target_display import format_target_display_name
from facebook_monitor.core.defaults import PYTHON_PERSISTENCE_RETENTION_DEFAULTS
from facebook_monitor.core.keyword_rules import KeywordEvaluation
from facebook_monitor.core.keyword_rules import KeywordGroupMatchResult
from facebook_monitor.core.keyword_rules import compile_keyword_matcher
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import KeywordGroupMatch
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.core.scan_failures import TARGET_STOPPED_REASON
from facebook_monitor.facebook.extracted_item import ExtractedItem
from facebook_monitor.facebook.extracted_item import make_item_key
from facebook_monitor.facebook.extracted_item import make_item_key_aliases
from facebook_monitor.facebook.group_metadata_validation import is_invalid_facebook_group_name
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.notifications.outbox_service import queue_match_notifications_after_commit
from facebook_monitor.notifications.senders import DesktopSender
from facebook_monitor.notifications.senders import DiscordSender
from facebook_monitor.notifications.senders import NtfySender
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.scan_latest_snapshot import build_latest_scan_items
from facebook_monitor.worker.scan_latest_snapshot import should_carry_over_previous_latest_items


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
    display_text: str = ""
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
    baseline_mode: bool = False
    matched_keywords: tuple[str, ...] = ()
    matched_keyword_groups: tuple[KeywordGroupMatch, ...] = ()
    include_group_results: tuple[KeywordGroupMatchResult, ...] = ()
    logical_item_id: int | None = None


@dataclass(frozen=True)
class MatchNotificationPayload:
    """保存 shared finalize 層準備送出的 match 通知資料。"""

    item_key: str
    logical_item_id: int | None
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

    @property
    def baseline_mode(self) -> bool:
        """回傳本輪是否只建立 scope baseline、不送通知。"""

        return bool(self.scan_summary.get("baseline_mode"))


@dataclass(frozen=True)
class _ClassifiedScanItem:
    """保存單筆 item 的分類結果與原始 keyword evaluation。"""

    result: ScanMatchResult
    keyword_evaluation: KeywordEvaluation


@dataclass(frozen=True)
class _ScanFinalizeAccumulator:
    """保存 finalize transaction 內累積出的寫入結果。"""

    match_results: list[ScanMatchResult]
    new_items: list[NormalizedScanItem]
    matched_items: list[NormalizedScanItem]
    history_entries: list[MatchHistoryEntry]
    notification_payloads: list[MatchNotificationPayload]


@dataclass(frozen=True)
class ScanCommitGuard:
    """保存本輪 scan admission identity，避免 stop/start 後舊掃描寫回。"""

    worker_id: str
    started_at: datetime
    page_id: str = ""


UNGUARDED_SCAN_COMMIT: ScanCommitGuard | None = None
"""明確標示 debug / one-shot 入口允許不綁定 runtime admission identity。"""

SORT_ADJUST_UNCONFIRMED_STOP_REASON = "sort_adjust_unconfirmed_skip"
SORT_ADJUST_UNCONFIRMED_SKIP_REASON = SORT_ADJUST_UNCONFIRMED_REASON


def scan_commit_guard_from_runtime_state(
    state: TargetRuntimeState,
) -> ScanCommitGuard:
    """由 running runtime state 建立本輪 scan commit guard。"""

    if state.last_started_at is None:
        raise ValueError("scan commit guard requires last_started_at")
    return ScanCommitGuard(
        worker_id=state.active_worker_id,
        page_id=state.active_page_id,
        started_at=state.last_started_at,
    )


def record_guarded_skipped_scan(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    metadata: dict[str, Any],
    commit_guard: ScanCommitGuard,
) -> ScanFinalizeResult:
    """記錄正式 runtime guarded protective skipped scan。"""

    return _record_skipped_scan(
        app=app,
        target=target,
        metadata=metadata,
        commit_guard=commit_guard,
    )


def record_unguarded_skipped_scan_for_one_shot(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    metadata: dict[str, Any],
) -> ScanFinalizeResult:
    """記錄 debug / one-shot 入口允許的 unguarded protective skipped scan。"""

    return _record_skipped_scan(
        app=app,
        target=target,
        metadata=metadata,
        commit_guard=UNGUARDED_SCAN_COMMIT,
    )


def _record_skipped_scan(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    metadata: dict[str, Any],
    commit_guard: ScanCommitGuard | None,
) -> ScanFinalizeResult:
    """記錄保護性 skipped scan；達門檻時升級為 worker failure。"""

    begin_scan_commit_transaction(app)
    ensure_target_allows_scan_commit(app=app, target=target, commit_guard=commit_guard)
    skip_reason = str(
        metadata.get("skip_reason") or SORT_ADJUST_UNCONFIRMED_SKIP_REASON
    )
    skip_decision = app.services.targets.decide_scan_skip(
        target.id,
        skip_reason,
        skip_limit=PYTHON_SCHEDULER_RUNTIME_DEFAULTS.sort_adjust_unconfirmed_skip_limit,
    )
    if skip_decision.escalate:
        raise WorkerFailure(
            skip_decision.reason,
            (
                "protective scan skip reached "
                f"{skip_decision.skip_streak}/{skip_decision.skip_limit}"
            ),
        )
    scan_metadata = dict(metadata)
    scan_metadata.setdefault("scan_skipped", True)
    scan_metadata.setdefault("skip_reason", skip_decision.reason)
    scan_metadata.setdefault("stop_reason", SORT_ADJUST_UNCONFIRMED_STOP_REASON)
    scan_metadata.setdefault("new_count", 0)
    scan_metadata.setdefault("matched_count", 0)
    scan_metadata.setdefault("skip_streak", skip_decision.skip_streak)
    scan_metadata.setdefault("skip_limit", skip_decision.skip_limit)
    scan_run_id = app.services.scans.record_scan(
        RecordScanRequest(
            target_id=target.id,
            status=ScanStatus.SUCCESS,
            item_count=0,
            matched_count=0,
            metadata=scan_metadata,
        )
    )
    app.repositories.latest_scan_items.replace_for_target(target.id, [])
    if commit_guard is None:
        app.services.targets.force_apply_scan_skip_decision(target.id, skip_decision)
    else:
        updated_state = app.services.targets.guarded_apply_scan_skip_decision(
            target.id,
            skip_decision,
            worker_id=commit_guard.worker_id,
            started_at=commit_guard.started_at,
            page_id=commit_guard.page_id,
        )
        if updated_state is None:
            raise WorkerFailure(
                TARGET_STOPPED_REASON,
                "target stopped before skipped scan finalize",
            )
    return ScanFinalizeResult(
        scan_run_id=scan_run_id,
        new_items=(),
        matched_items=(),
        match_results=(),
        history_entries=(),
        notification_payloads=(),
        latest_items=(),
        scan_summary=scan_metadata,
    )


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
                display_text=item.display_text,
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
    commit_guard: ScanCommitGuard | None,
) -> ScanFinalizeResult:
    """完成 target-kind-independent 的 scan 後處理與持久化。"""

    begin_scan_commit_transaction(app)
    ensure_target_allows_scan_commit(app=app, target=target, commit_guard=commit_guard)
    app.repositories.app_settings.mark_profile_ok(source="scan_success")
    baseline_mode = not app.repositories.scan_scope_state.is_initialized(target.scope_id)
    accumulator = _process_scan_items_for_finalize(
        app=app,
        target=target,
        config=config,
        items=items,
        baseline_mode=baseline_mode,
        notification_sender=notification_sender,
        desktop_notification_sender=desktop_notification_sender,
        discord_notification_sender=discord_notification_sender,
    )
    scan_run_id, latest_items, scan_metadata = _record_success_scan_snapshot(
        app=app,
        target=target,
        config=config,
        item_count=item_count,
        metadata=metadata,
        baseline_mode=baseline_mode,
        accumulator=accumulator,
    )
    return _build_scan_finalize_result(
        scan_run_id=scan_run_id,
        latest_items=latest_items,
        scan_metadata=scan_metadata,
        accumulator=accumulator,
    )


def _process_scan_items_for_finalize(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    items: list[NormalizedScanItem],
    baseline_mode: bool,
    notification_sender: NtfySender,
    desktop_notification_sender: DesktopSender,
    discord_notification_sender: DiscordSender,
) -> _ScanFinalizeAccumulator:
    """在既有 scan commit transaction 內處理 seen、keyword、history 與 outbox。"""

    accumulator = _ScanFinalizeAccumulator(
        match_results=[],
        new_items=[],
        matched_items=[],
        history_entries=[],
        notification_payloads=[],
    )
    keyword_matcher = compile_keyword_matcher(
        include_keywords=config.include_keywords,
        include_keyword_groups=config.include_keyword_groups,
        exclude_keywords=config.exclude_keywords,
        exclude_ignore_phrases=config.exclude_ignore_phrases,
    )
    for item in items:
        classified = _mark_seen_and_classify_item(
            app=app,
            target=target,
            item=item,
            keyword_matcher=keyword_matcher,
            baseline_mode=baseline_mode,
        )
        result = classified.result
        accumulator.match_results.append(result)
        if result.is_new:
            accumulator.new_items.append(item)
        if result.is_matched:
            accumulator.matched_items.append(item)
        if not result.eligible_for_notify:
            continue
        history_entry, notification_payload = _record_match_notification_side_effects(
            app=app,
            target=target,
            config=config,
            classified=classified,
            notification_sender=notification_sender,
            desktop_notification_sender=desktop_notification_sender,
            discord_notification_sender=discord_notification_sender,
        )
        accumulator.history_entries.append(history_entry)
        accumulator.notification_payloads.append(notification_payload)
    return accumulator


def _mark_seen_and_classify_item(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    item: NormalizedScanItem,
    keyword_matcher: Any,
    baseline_mode: bool,
) -> _ClassifiedScanItem:
    """標記 seen aliases 並產生單筆 scan match result。"""

    seen_item = SeenItem(
        scope_id=target.scope_id,
        item_key=item.item_key,
        item_kind=item.item_kind,
        parent_post_id=item.parent_post_id,
        comment_id=item.comment_id,
    )
    logical_seen = app.repositories.logical_items.mark_seen_aliases(
        target_id=target.id,
        item=seen_item,
        item_keys=item.alias_keys,
    )
    legacy_seen_within_horizon = app.repositories.seen_items.has_seen_any_since(
        target.scope_id,
        item.alias_keys,
        utc_now()
        - timedelta(
            days=PYTHON_PERSISTENCE_RETENTION_DEFAULTS.logical_dedupe_horizon_days
        ),
    )
    app.repositories.seen_items.mark_seen_aliases(
        seen_item,
        item.alias_keys,
    )
    keyword_evaluation = keyword_matcher.evaluate(item.text)
    return _ClassifiedScanItem(
        result=build_scan_match_result(
            item=item,
            is_new=logical_seen.is_new and not legacy_seen_within_horizon,
            keyword_evaluation=keyword_evaluation,
            baseline_mode=baseline_mode,
            logical_item_id=logical_seen.logical_item_id,
        ),
        keyword_evaluation=keyword_evaluation,
    )


def _record_match_notification_side_effects(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    classified: _ClassifiedScanItem,
    notification_sender: NtfySender,
    desktop_notification_sender: DesktopSender,
    discord_notification_sender: DiscordSender,
) -> tuple[MatchHistoryEntry, MatchNotificationPayload]:
    """寫入 match history 並註冊 notification outbox after-commit dispatch。"""

    result = classified.result
    item = result.item
    notified_at = utc_now()
    history_entry = MatchHistoryEntry(
        target_id=target.id,
        group_id=target.group_id,
        group_name=_match_history_group_name(target),
        item_kind=item.item_kind,
        parent_post_id=item.parent_post_id,
        comment_id=item.comment_id,
        item_key=item.item_key,
        author=item.author,
        text=item.text,
        display_text=item.display_text or item.text,
        permalink=item.permalink,
        include_rule=result.include_rule,
        include_rules=classified.keyword_evaluation.include_rules,
        include_group_matches=classified.keyword_evaluation.include_group_matches,
        timestamp_text=item.timestamp_text,
        notified_at=notified_at,
        created_at=notified_at,
    )
    app.repositories.match_history.add(history_entry)

    notification_payload = MatchNotificationPayload(
        item_key=item.item_key,
        logical_item_id=result.logical_item_id,
        item_kind=item.item_kind,
        author=item.author,
        text=item.display_text or item.text,
        permalink=item.permalink,
        matched_keyword=result.matched_keyword,
    )
    queue_match_notifications_after_commit(
        app=app,
        target=target,
        config=config,
        item_key=notification_payload.item_key,
        logical_item_id=notification_payload.logical_item_id,
        author=notification_payload.author,
        item_text=notification_payload.text,
        permalink=notification_payload.permalink,
        matched_keyword=notification_payload.matched_keyword,
        item_kind=notification_payload.item_kind,
        ntfy_sender=notification_sender,
        desktop_sender=desktop_notification_sender,
        discord_sender=discord_notification_sender,
    )
    return history_entry, notification_payload


def _match_history_group_name(target: TargetDescriptor) -> str:
    """回傳 match history 用 group metadata，污染名稱才退回 target display fallback。"""

    group_name = str(target.group_name or "").strip()
    if group_name and not is_invalid_facebook_group_name(group_name):
        return group_name
    return format_target_display_name(target)


def _record_success_scan_snapshot(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    item_count: int,
    metadata: dict[str, Any],
    baseline_mode: bool,
    accumulator: _ScanFinalizeAccumulator,
) -> tuple[int, tuple[LatestScanItem, ...], dict[str, Any]]:
    """寫入 scan run 與 latest snapshot；仍由 caller 持有同一個 transaction。"""

    scan_metadata = _build_success_scan_metadata(
        metadata=metadata,
        target=target,
        baseline_mode=baseline_mode,
        new_count=len(accumulator.new_items),
        matched_count=len(accumulator.matched_items),
    )
    scan_run_id = app.services.scans.record_scan(
        RecordScanRequest(
            target_id=target.id,
            status=ScanStatus.SUCCESS,
            item_count=item_count,
            matched_count=len(accumulator.matched_items),
            metadata=scan_metadata,
        )
    )
    previous_latest_items = app.repositories.latest_scan_items.list_by_target(
        target.id,
        limit=config.max_items_per_scan,
    )
    latest_items = tuple(
        build_latest_scan_items(
            target=target,
            scan_run_id=scan_run_id,
            match_results=accumulator.match_results,
            previous_latest_items=previous_latest_items,
            target_count=config.max_items_per_scan,
            carry_over_previous_items=should_carry_over_previous_latest_items(
                scan_metadata
            ),
        )
    )
    app.repositories.latest_scan_items.replace_for_target(target.id, latest_items)
    if not baseline_mode or accumulator.match_results:
        app.repositories.scan_scope_state.mark_initialized(target.scope_id)
    return scan_run_id, latest_items, scan_metadata


def _build_success_scan_metadata(
    *,
    metadata: dict[str, Any],
    target: TargetDescriptor,
    baseline_mode: bool,
    new_count: int,
    matched_count: int,
) -> dict[str, Any]:
    """建立 success scan metadata，保留 caller metadata 並補 finalize counters。"""

    scan_metadata = dict(metadata)
    scan_metadata["baseline_mode"] = baseline_mode
    scan_metadata["scope_id"] = target.scope_id
    scan_metadata["new_count"] = new_count
    scan_metadata["matched_count"] = matched_count
    return scan_metadata


def _build_scan_finalize_result(
    *,
    scan_run_id: int,
    latest_items: tuple[LatestScanItem, ...],
    scan_metadata: dict[str, Any],
    accumulator: _ScanFinalizeAccumulator,
) -> ScanFinalizeResult:
    """將 finalize accumulator 轉成 public result model。"""

    return ScanFinalizeResult(
        scan_run_id=scan_run_id,
        new_items=tuple(accumulator.new_items),
        matched_items=tuple(accumulator.matched_items),
        match_results=tuple(accumulator.match_results),
        history_entries=tuple(accumulator.history_entries),
        notification_payloads=tuple(accumulator.notification_payloads),
        latest_items=latest_items,
        scan_summary=scan_metadata,
    )


def target_matches_scan_commit_guard(
    *,
    app: ApplicationContext,
    target_id: str,
    commit_guard: ScanCommitGuard | None,
) -> bool:
    """確認 runtime state 仍是本輪 scan admission。"""

    if commit_guard is None:
        return True
    runtime_state = app.services.targets.ensure_runtime_state(target_id)
    return _runtime_state_matches_commit_guard(runtime_state, commit_guard)


def begin_scan_commit_transaction(app: ApplicationContext) -> None:
    """開始 guarded scan write transaction，避免 guard check 後被 stop/start 穿插。"""

    connection = app.repositories.runtime_states.connection
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")


def mark_target_idle_for_scan_commit(
    *,
    app: ApplicationContext,
    target_id: str,
    commit_guard: ScanCommitGuard | None,
) -> bool:
    """在同一個 write transaction 內確認 guard 後才將 target 標回 idle。"""

    begin_scan_commit_transaction(app)
    target = app.repositories.targets.get(target_id)
    if target is None:
        return False
    if not _target_allows_scan_commit(
        app=app,
        target=target,
        commit_guard=commit_guard,
    ):
        return False
    if commit_guard is None:
        app.services.targets.force_mark_target_idle(target_id)
        return True
    updated_state = app.services.targets.guarded_mark_target_idle(
        target_id,
        worker_id=commit_guard.worker_id,
        started_at=commit_guard.started_at,
        page_id=commit_guard.page_id,
    )
    if updated_state is None:
        return False
    return True


def ensure_target_allows_scan_commit(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    commit_guard: ScanCommitGuard | None,
) -> None:
    """確認 target 仍可接受本輪 scan commit，否則丟出停止錯誤。"""

    if not _target_allows_scan_commit(
        app=app,
        target=target,
        commit_guard=commit_guard,
    ):
        raise WorkerFailure(TARGET_STOPPED_REASON, "target stopped before scan finalize")


def _target_allows_scan_commit(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    commit_guard: ScanCommitGuard | None,
) -> bool:
    """確認 target 仍是 active 且未換成另一輪 scan attempt。"""

    current_target = app.repositories.targets.get(target.id)
    if current_target is None or not current_target.enabled or current_target.paused:
        return False
    runtime_state = app.services.targets.ensure_runtime_state(target.id)
    if runtime_state.desired_state != TargetDesiredState.ACTIVE:
        return False
    return _runtime_state_matches_commit_guard(runtime_state, commit_guard)


def _runtime_state_matches_commit_guard(
    runtime_state: TargetRuntimeState,
    commit_guard: ScanCommitGuard | None,
) -> bool:
    """比對 runtime 是否仍是同一個 running attempt。"""

    if commit_guard is None:
        return True
    if runtime_state.runtime_status != TargetRuntimeStatus.RUNNING:
        return False
    if runtime_state.active_worker_id != commit_guard.worker_id:
        return False
    if runtime_state.last_started_at != commit_guard.started_at:
        return False
    return not commit_guard.page_id or runtime_state.active_page_id == commit_guard.page_id


def build_scan_match_result(
    *,
    item: NormalizedScanItem,
    is_new: bool,
    keyword_evaluation: KeywordEvaluation,
    baseline_mode: bool = False,
    logical_item_id: int | None = None,
) -> ScanMatchResult:
    """建立單一 item 的 shared classification 結果。"""

    return ScanMatchResult(
        item=item,
        is_new=is_new,
        is_matched=keyword_evaluation.eligible,
        include_rule=keyword_evaluation.include_rule,
        exclude_rule=keyword_evaluation.exclude_rule,
        eligible_for_notify=(not baseline_mode) and is_new and keyword_evaluation.eligible,
        matched_keyword=keyword_evaluation.display_rule,
        baseline_mode=baseline_mode,
        matched_keywords=keyword_evaluation.include_rules if keyword_evaluation.eligible else (),
        matched_keyword_groups=(
            keyword_evaluation.include_group_matches if keyword_evaluation.eligible else ()
        ),
        include_group_results=keyword_evaluation.include_group_results,
        logical_item_id=logical_item_id,
    )
