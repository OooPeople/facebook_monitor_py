"""Phase A 核心 domain models。

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
    PAUSED = "paused"


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


def is_generated_group_posts_name(name: str, group_id: str) -> bool:
    """判斷 target name 是否為系統產生的 group posts 預設名稱。"""

    return name == generated_group_posts_name(group_id)


def is_generated_group_comments_name(name: str, group_id: str, parent_post_id: str) -> bool:
    """判斷 target name 是否為系統產生的 group comments 預設名稱。"""

    return name == generated_group_comments_name(group_id, parent_post_id)


def build_group_comments_scope_id(group_id: str, parent_post_id: str) -> str:
    """建立 comments target 的 target-scoped seen/baseline scope id。"""

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
    parent_post_id: str = ""
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
    ) -> TargetDescriptor:
        """建立 group feed posts target descriptor。"""

        target_name = name or group_name or generated_group_posts_name(group_id)
        return cls(
            id=new_id(),
            name=target_name,
            target_kind=TargetKind.POSTS,
            group_id=group_id,
            group_name=group_name,
            scope_id=group_id,
            canonical_url=canonical_url,
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
    ) -> TargetDescriptor:
        """建立 group post comments target descriptor。"""

        scope_id = build_group_comments_scope_id(group_id, parent_post_id)
        target_name = name or group_name or generated_group_comments_name(
            group_id,
            parent_post_id,
        )
        return cls(
            id=new_id(),
            name=target_name,
            target_kind=TargetKind.COMMENTS,
            group_id=group_id,
            group_name=group_name,
            parent_post_id=parent_post_id,
            scope_id=scope_id,
            canonical_url=canonical_url,
            paused=True,
        )


@dataclass(frozen=True)
class TargetConfig:
    """保存社團層級監視設定。

    `target_id` 欄位目前保留既有命名，但正式語義是 config owner id：
    新路徑使用 group_id，seen / baseline / latest scan 仍由 target scope 分流。
    """

    target_id: str
    include_keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    min_refresh_sec: int = 300
    max_refresh_sec: int = 600
    jitter_enabled: bool = True
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
    """保存 Web UI 全域通知預設與測試通知設定。"""

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
    desired_state: TargetDesiredState = TargetDesiredState.ACTIVE
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
class MatchHistoryEntry:
    """保存一次 keyword match 的歷史紀錄。"""

    target_id: str
    group_id: str
    item_kind: ItemKind
    item_key: str
    author: str = ""
    text: str = ""
    permalink: str = ""
    group_name: str = ""
    parent_post_id: str = ""
    comment_id: str = ""
    include_rule: str = ""
    timestamp_text: str = ""
    notified_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)


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
    permalink: str = ""
    matched_keyword: str = ""
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
    created_at: datetime = field(default_factory=utc_now)
