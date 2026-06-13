"""核心 domain models。

職責：定義 target、config、seen item、scan result 與通知事件等資料結構。
這些 model 不依賴 Playwright 或 SQLite，方便後續被 worker、repository 與測試共用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS


class TargetKind(StrEnum):
    """監視 target 類型。"""

    POSTS = "posts"
    COMMENTS = "comments"


class WorkerMode(StrEnum):
    """worker 執行模式。"""

    HEADLESS = "headless"
    HEADED_COMPAT = "headed_compat"


class ItemKind(StrEnum):
    """抽取項目類型。"""

    POST = "post"
    COMMENT = "comment"


class ScanStatus(StrEnum):
    """單輪掃描狀態。"""

    SUCCESS = "success"
    FAILED = "failed"


class NotificationChannel(StrEnum):
    """通知通道。"""

    DESKTOP = "desktop"
    NTFY = "ntfy"
    DISCORD = "discord"


class NotificationStatus(StrEnum):
    """通知發送狀態。"""

    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class NotificationEventKind(StrEnum):
    """通知事件來源語義。"""

    MATCH = "match"
    RUNTIME_FAILURE = "runtime_failure"


class NotificationOutboxStatus(StrEnum):
    """通知 outbox 事件狀態。"""

    PENDING = "pending"
    PROCESSING_PENDING = "processing_pending"
    SENT = "sent"
    FAILED = "failed"
    PROCESSING_FAILED = "processing_failed"
    SKIPPED = "skipped"


class NotificationDedupeStatus(StrEnum):
    """通知 dedupe ledger 狀態。"""

    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class TargetDesiredState(StrEnum):
    """target 在 scheduler 中期望維持的狀態。"""

    ACTIVE = "active"
    STOPPED = "stopped"


class TargetRuntimeStatus(StrEnum):
    """target worker 目前實際執行狀態。"""

    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    ERROR = "error"


class TargetMetadataStatus(StrEnum):
    """target metadata 補齊狀態。"""

    RESOLVED = "resolved"
    PENDING = "pending"
    FAILED = "failed"


class TargetCoverImageRefreshStatus(StrEnum):
    """target cover image URL 背景刷新狀態。"""

    IDLE = "idle"
    PENDING = "pending"
    FAILED = "failed"


class CoverImageRefreshRequestStatus(StrEnum):
    """UI 壞圖上報轉成 cover refresh 排程的結果狀態。"""

    QUEUED = "queued"
    PENDING = "pending"
    THROTTLED = "throttled"
    NOT_FOUND = "not_found"
    INVALID_URL = "invalid_url"
    IGNORED_STALE_URL = "ignored_stale_url"


class TargetCoverImageRefreshResult(StrEnum):
    """target cover image refresh worker 最近一次處理結果。"""

    NONE = ""
    QUEUED = "queued"
    ATTEMPTED = "attempted"
    SUCCEEDED_CHANGED = "succeeded_changed"
    SUCCEEDED_UNCHANGED = "succeeded_unchanged"
    STALE_SKIPPED = "stale_skipped"
    FAILED = "failed"


def utc_now() -> datetime:
    """取得 timezone-aware UTC 時間。"""

    return datetime.now(timezone.utc)


def new_id() -> str:
    """產生 repository 使用的 UUID 字串。"""

    return str(uuid4())


def generated_group_posts_name(group_id: str) -> str:
    """回傳 group posts target 的系統預設名稱。"""

    return f"group:{group_id}:posts"


def generated_group_comments_name(group_id: str, parent_post_id: str) -> str:
    """回傳 group comments target 的系統預設名稱。"""

    return f"group:{group_id}:post:{parent_post_id}:comments"


def generated_group_comments_display_name(group_name: str, parent_post_id: str) -> str:
    """回傳保留 post scope 的 comments target 顯示名稱。"""

    normalized_group_name = str(group_name or "").strip()
    normalized_parent_post_id = str(parent_post_id or "").strip()
    if not normalized_group_name:
        return ""
    if not normalized_parent_post_id:
        return normalized_group_name
    return f"{normalized_group_name} / post:{normalized_parent_post_id}"


def is_generated_group_posts_name(name: str, group_id: str) -> bool:
    """判斷 target name 是否為系統產生的 group posts 預設名稱。"""

    return name == generated_group_posts_name(group_id)


def is_generated_group_comments_name(name: str, group_id: str, parent_post_id: str) -> bool:
    """判斷 target name 是否為系統產生的 group comments 預設名稱。"""

    return name == generated_group_comments_name(group_id, parent_post_id)


def build_group_comments_scope_id(group_id: str, parent_post_id: str) -> str:
    """建立 comments target 的 target-scoped scan state scope id。"""

    normalized_group_id = str(group_id or "").strip()
    normalized_parent_post_id = str(parent_post_id or "").strip()
    if not normalized_group_id or not normalized_parent_post_id:
        return ""
    return f"{normalized_group_id}:post:{normalized_parent_post_id}:comments"


@dataclass(frozen=True)
class TargetDescriptor:
    """描述一個可監視 Facebook target。"""

    id: str
    name: str
    target_kind: TargetKind
    group_id: str
    scope_id: str
    canonical_url: str
    group_name: str = ""
    group_cover_image_url: str = ""
    parent_post_id: str = ""
    metadata_status: TargetMetadataStatus = TargetMetadataStatus.RESOLVED
    metadata_error: str = ""
    enabled: bool = True
    paused: bool = False
    worker_mode: WorkerMode = WorkerMode.HEADLESS
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @classmethod
    def for_group_posts(
        cls,
        *,
        group_id: str,
        canonical_url: str,
        name: str = "",
        group_name: str = "",
        group_cover_image_url: str = "",
    ) -> TargetDescriptor:
        """建立 group feed posts target descriptor。"""

        target_name = name or group_name or generated_group_posts_name(group_id)
        return cls(
            id=new_id(),
            name=target_name,
            target_kind=TargetKind.POSTS,
            group_id=group_id,
            group_name=group_name,
            group_cover_image_url=group_cover_image_url,
            scope_id=group_id,
            canonical_url=canonical_url,
            paused=True,
        )

    @classmethod
    def for_comments(
        cls,
        *,
        group_id: str,
        parent_post_id: str,
        canonical_url: str,
        name: str = "",
        group_name: str = "",
        group_cover_image_url: str = "",
    ) -> TargetDescriptor:
        """建立 group post comments target descriptor。"""

        scope_id = build_group_comments_scope_id(group_id, parent_post_id)
        target_name = name or generated_group_comments_display_name(
            group_name,
            parent_post_id,
        ) or generated_group_comments_name(
            group_id,
            parent_post_id,
        )
        return cls(
            id=new_id(),
            name=target_name,
            target_kind=TargetKind.COMMENTS,
            group_id=group_id,
            group_name=group_name,
            group_cover_image_url=group_cover_image_url,
            parent_post_id=parent_post_id,
            scope_id=scope_id,
            canonical_url=canonical_url,
            paused=True,
        )


@dataclass(frozen=True)
class TargetCoverImageRefreshState:
    """保存單一 target cover image URL 刷新排程狀態。"""

    target_id: str
    status: TargetCoverImageRefreshStatus
    requested_at: datetime | None = None
    last_attempted_at: datetime | None = None
    last_succeeded_at: datetime | None = None
    last_failed_at: datetime | None = None
    last_reported_url: str = ""
    last_resolved_url: str = ""
    last_result: TargetCoverImageRefreshResult = TargetCoverImageRefreshResult.NONE
    changed: bool = False
    error: str = ""
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class IncludeKeywordGroup:
    """保存 include keyword 的單一分組設定。

    分組內仍沿用既有 keyword 規則；分組之間由 matcher 套用 AND 語義。
    """

    group_id: str
    label: str
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class KeywordGroupMatch:
    """保存一次 keyword 命中的分組快照。"""

    group_id: str
    group_label: str
    rule: str


@dataclass(frozen=True)
class TargetConfig:
    """保存單一 target 的監視設定。

    設定 owner 是 target_id；同一 Facebook 社團下的 posts target 與各 comments
    target 不能共用關鍵字、掃描或通知設定。
    """

    target_id: str
    include_keywords: tuple[str, ...] = ()
    include_keyword_groups: tuple[IncludeKeywordGroup, ...] = ()
    exclude_keywords: tuple[str, ...] = PYTHON_TARGET_CONFIG_DEFAULTS.exclude_keywords
    exclude_ignore_phrases: tuple[str, ...] = PYTHON_TARGET_CONFIG_DEFAULTS.exclude_ignore_phrases
    min_refresh_sec: int = PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec
    max_refresh_sec: int = PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec
    jitter_enabled: bool = PYTHON_TARGET_CONFIG_DEFAULTS.jitter_enabled
    fixed_refresh_sec: int | None = PYTHON_TARGET_CONFIG_DEFAULTS.fixed_refresh_sec
    max_items_per_scan: int = PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan
    auto_load_more: bool = PYTHON_TARGET_CONFIG_DEFAULTS.auto_load_more
    auto_adjust_sort: bool = PYTHON_TARGET_CONFIG_DEFAULTS.auto_adjust_sort
    enable_desktop_notification: bool = PYTHON_TARGET_CONFIG_DEFAULTS.enable_desktop_notification
    enable_ntfy: bool = PYTHON_TARGET_CONFIG_DEFAULTS.enable_ntfy
    ntfy_topic: str = PYTHON_TARGET_CONFIG_DEFAULTS.ntfy_topic
    enable_discord_notification: bool = PYTHON_TARGET_CONFIG_DEFAULTS.enable_discord_notification
    discord_webhook: str = PYTHON_TARGET_CONFIG_DEFAULTS.discord_webhook


@dataclass(frozen=True)
class GlobalNotificationSettings:
    """保存舊版全域通知設定資料，保留給 migration / secret storage 相容性。"""

    enable_desktop_notification: bool = PYTHON_TARGET_CONFIG_DEFAULTS.enable_desktop_notification
    enable_ntfy: bool = PYTHON_TARGET_CONFIG_DEFAULTS.enable_ntfy
    ntfy_topic: str = PYTHON_TARGET_CONFIG_DEFAULTS.ntfy_topic
    enable_discord_notification: bool = PYTHON_TARGET_CONFIG_DEFAULTS.enable_discord_notification
    discord_webhook: str = PYTHON_TARGET_CONFIG_DEFAULTS.discord_webhook
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class TargetRuntimeState:
    """保存 scheduler 對單一 target 的期望狀態與實際執行狀態。"""

    target_id: str
    desired_state: TargetDesiredState = TargetDesiredState.STOPPED
    runtime_status: TargetRuntimeStatus = TargetRuntimeStatus.IDLE
    scan_requested_at: datetime | None = None
    last_enqueued_at: datetime | None = None
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    last_error: str = ""
    last_skip_reason: str = ""
    enqueue_reason: str = ""
    active_worker_id: str = ""
    active_page_id: str = ""
    last_page_reloaded_at: datetime | None = None
    scan_guard_count: int = 0
    display_next_due_at: datetime | None = None
    consecutive_failure_reason: str = ""
    consecutive_failure_count: int = 0
    consecutive_scan_skip_reason: str = ""
    consecutive_scan_skip_count: int = 0
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def queued(self) -> bool:
        """回傳 target 是否已在 executor queue 等候執行。"""

        return self.runtime_status == TargetRuntimeStatus.QUEUED

    @property
    def running(self) -> bool:
        """回傳 target 是否正在被 worker 執行。"""

        return self.runtime_status == TargetRuntimeStatus.RUNNING


@dataclass(frozen=True)
class SeenItem:
    """保存已看過 item 的去重資訊。"""

    scope_id: str
    item_key: str
    item_kind: ItemKind
    parent_post_id: str = ""
    comment_id: str = ""
    first_seen_at: datetime = field(default_factory=utc_now)
    last_seen_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class SeenAliasMarkResult:
    """保存 logical item alias 去重寫入結果。"""

    is_new: bool
    logical_item_id: int
    canonical_item_key: str
    alias_keys: tuple[str, ...]


@dataclass(frozen=True)
class MatchHistoryEntry:
    """保存一次 keyword match 的歷史紀錄。

    `notified_at` 是既有 DB 欄位名；目前實際語義是 match 被記錄進
    history 的時間，不保證外部通知已成功送達。
    """

    target_id: str
    group_id: str
    item_kind: ItemKind
    item_key: str
    author: str = ""
    text: str = ""
    display_text: str = ""
    permalink: str = ""
    group_name: str = ""
    parent_post_id: str = ""
    comment_id: str = ""
    include_rule: str = ""
    timestamp_text: str = ""
    notified_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    include_rules: tuple[str, ...] = ()
    include_group_matches: tuple[KeywordGroupMatch, ...] = ()


@dataclass(frozen=True)
class LatestScanItem:
    """保存單一 target 最近一輪掃描到的貼文候選。"""

    target_id: str
    scan_run_id: int
    item_kind: ItemKind
    item_key: str
    item_index: int
    author: str = ""
    text: str = ""
    display_text: str = ""
    permalink: str = ""
    matched_keyword: str = ""
    matched_keywords: tuple[str, ...] = ()
    matched_keyword_groups: tuple[KeywordGroupMatch, ...] = ()
    debug_metadata: dict[str, Any] = field(default_factory=dict)
    scanned_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class ScanRun:
    """保存單輪掃描結果摘要。"""

    target_id: str
    status: ScanStatus
    started_at: datetime
    finished_at: datetime
    item_count: int = 0
    matched_count: int = 0
    error_message: str = ""
    worker_mode: WorkerMode = WorkerMode.HEADLESS
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NotificationEvent:
    """保存通知發送結果。"""

    target_id: str
    item_key: str
    channel: NotificationChannel
    status: NotificationStatus
    message: str = ""
    event_kind: NotificationEventKind = NotificationEventKind.MATCH
    source_scan_run_id: int | None = None
    failure_reason: str = ""
    failure_count: int = 0
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class NotificationOutboxEntry:
    """保存 commit 後才可送出的通知 outbox 事件。"""

    idempotency_key: str
    target_id: str
    item_key: str
    item_kind: ItemKind
    channel: NotificationChannel
    title: str
    message: str
    endpoint: str = ""
    permalink: str = ""
    event_kind: NotificationEventKind = NotificationEventKind.MATCH
    source_scan_run_id: int | None = None
    failure_reason: str = ""
    failure_count: int = 0
    status: NotificationOutboxStatus = NotificationOutboxStatus.PENDING
    attempts: int = 0
    last_error: str = ""
    notification_event_id: int | None = None
    dedupe_id: int | None = None
    id: int | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class NotificationOutboxSummary:
    """保存 target-scoped outbox backlog 診斷摘要。"""

    target_id: str
    pending_count: int = 0
    processing_count: int = 0
    failed_count: int = 0
    terminal_count: int = 0
    oldest_pending_updated_at: datetime | None = None
    max_attempts: int = 0
