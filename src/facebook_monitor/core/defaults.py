"""Python 版監視設定預設值。

職責：集中保存正式產品預設值，避免 Web UI、service 與 domain model
各自硬寫一份而造成行為漂移。
"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.scan_limits import DEFAULT_TARGET_POSTS


DEFAULT_REFRESH_SECONDS = 60


@dataclass(frozen=True)
class TargetConfigDefaults:
    """保存 target config 的 Python 版預設值。"""

    include_keyword_group_count: int = 3
    fixed_refresh_sec: int | None = None
    default_fixed_refresh_sec: int = DEFAULT_REFRESH_SECONDS
    min_refresh_sec: int = 50
    max_refresh_sec: int = 70
    jitter_enabled: bool = True
    exclude_keywords: tuple[str, ...] = ("徵", "收", "已售", "#換")
    exclude_ignore_phrases: tuple[str, ...] = ("全收", "回收", "另收", "另徵")
    max_items_per_scan: int = DEFAULT_TARGET_POSTS
    auto_load_more: bool = True
    auto_adjust_sort: bool = True
    enable_desktop_notification: bool = False
    enable_ntfy: bool = False
    ntfy_topic: str = ""
    enable_discord_notification: bool = False
    discord_webhook: str = ""


@dataclass(frozen=True)
class SchedulerRuntimeDefaults:
    """保存 scheduler / worker runtime 的 Python 版預設值。"""

    resident_interval_seconds: float = DEFAULT_REFRESH_SECONDS
    one_shot_interval_seconds: float = 300
    scheduler_tick_seconds: float = 2
    max_concurrent_scans: int = 4
    scroll_rounds: int = 3
    scroll_wait_ms: int = 2500
    scan_timeout_seconds: float = 120
    min_browser_scan_timeout_seconds: float = 10
    min_scan_task_timeout_seconds: float = 0.01
    stale_running_after_seconds: float = 180
    heartbeat_interval_seconds: float = 30
    metadata_refresh_target_limit_per_tick: int = 1
    cover_image_refresh_target_limit_per_tick: int = 1
    cover_image_load_failure_min_interval_seconds: int = 21600
    page_load_timeout_failure_limit: int = 3
    stale_running_failure_limit: int = 3
    scheduler_runtime_failure_limit: int = 3
    sort_adjust_unconfirmed_skip_limit: int = 3
    sort_adjust_unconfirmed_failure_limit: int = 3
    recoverable_failure_limit: int = 3


@dataclass(frozen=True)
class BrowserRuntimeDefaults:
    """保存 Playwright browser runtime 的 Python 版預設值。"""

    viewport_width: int = 1366
    viewport_height: int = 900
    timeout_seconds: float = 120.0
    group_metadata_wait_ms: int = 3000


@dataclass(frozen=True)
class WebUiRuntimeDefaults:
    """保存本機 Web UI 啟動相關的 Python 版預設值。"""

    host: str = "127.0.0.1"
    port: int = 4818
    graceful_shutdown_timeout_seconds: int = 5
    sse_poll_interval_seconds: float = 1.0
    sse_keepalive_seconds: float = 20.0
    sse_retry_milliseconds: int = 2500
    # Deprecated: 短 SSE 連線欄位，長 SSE 正式路徑不再讀取。
    sse_max_connection_seconds: float = 1.2
    hit_record_preview_limit: int = 5
    hit_record_preview_max_limit: int = 20
    hit_record_full_limit: int = 50
    hit_record_full_max_limit: int = 200


@dataclass(frozen=True)
class ProfileLoginDefaults:
    """保存 Facebook profile 引導登入流程的 Python 版預設值。"""

    poll_interval_seconds: float = 1.0
    min_poll_interval_seconds: float = 0.2


@dataclass(frozen=True)
class UpdaterRuntimeDefaults:
    """保存更新下載與 updater smoke 的 Python 版預設值。"""

    timeout_seconds: float = 120.0


@dataclass(frozen=True)
class DiagnosticsRuntimeDefaults:
    """保存診斷產物 retention 預設值。"""

    support_bundle_retention_days: int = 14
    support_bundle_max_files: int = 10


@dataclass(frozen=True)
class NotificationRuntimeDefaults:
    """保存 notification sender、outbox 與 event retention 預設值。"""

    stale_processing_seconds: int = 300
    dispatch_batch_limit: int = 10
    events_per_target_limit: int = 500
    ntfy_server: str = "https://ntfy.sh"
    ntfy_timeout_seconds: float = 15.0
    ntfy_ascii_title_fallback: str = "Facebook keyword match"
    discord_username: str = "facebook_monitor_py"
    discord_content_limit: int = 1900
    discord_timeout_seconds: float = 15.0
    discord_rate_limit_retry_limit: int = 1
    discord_rate_limit_retry_after_cap_seconds: float = 5.0
    desktop_balloon_tip_milliseconds: int = 5000
    desktop_cleanup_sleep_milliseconds: int = 1000
    desktop_command_timeout_seconds: float = 10.0


@dataclass(frozen=True)
class PersistenceQueryDefaults:
    """保存 repository read/query fallback 的 Python 版預設值。"""

    list_limit: int = 50
    list_limit_per_target: int = 50


@dataclass(frozen=True)
class PersistenceRetentionDefaults:
    """保存本機 DB bounded retention 的 Python 版預設值。"""

    logical_dedupe_horizon_days: int = 60
    terminal_outbox_retention_days: int = 7
    failed_outbox_retention_days: int = 14
    maintenance_interval_seconds: int = 3600
    maintenance_retry_interval_seconds: int = 60


PYTHON_TARGET_CONFIG_DEFAULTS = TargetConfigDefaults()
PYTHON_SCHEDULER_RUNTIME_DEFAULTS = SchedulerRuntimeDefaults()
PYTHON_BROWSER_RUNTIME_DEFAULTS = BrowserRuntimeDefaults()
PYTHON_WEBUI_RUNTIME_DEFAULTS = WebUiRuntimeDefaults()
PYTHON_PROFILE_LOGIN_DEFAULTS = ProfileLoginDefaults()
PYTHON_UPDATER_RUNTIME_DEFAULTS = UpdaterRuntimeDefaults()
PYTHON_DIAGNOSTICS_RUNTIME_DEFAULTS = DiagnosticsRuntimeDefaults()
PYTHON_NOTIFICATION_RUNTIME_DEFAULTS = NotificationRuntimeDefaults()
PYTHON_PERSISTENCE_QUERY_DEFAULTS = PersistenceQueryDefaults()
PYTHON_PERSISTENCE_RETENTION_DEFAULTS = PersistenceRetentionDefaults()
