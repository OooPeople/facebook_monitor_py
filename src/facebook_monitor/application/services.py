"""Phase A application services。

職責：集中正式 workflow 的 repository 協調邏輯，避免 worker 或 CLI 直接散落操作多個 repository。
此層不碰 Playwright，也不直接撰寫 SQL。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from datetime import timedelta
from typing import Any
from typing import TypeVar

from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import GlobalNotificationSettings
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import WorkerMode
from facebook_monitor.core.models import is_generated_group_posts_name
from facebook_monitor.core.models import is_generated_group_comments_name
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.facebook.collection_policy import clamp_target_post_count
from facebook_monitor.facebook.route_detection import clean_facebook_page_title
from facebook_monitor.persistence.sqlite import ScanRunRepository
from facebook_monitor.persistence.sqlite import SeenItemRepository
from facebook_monitor.persistence.sqlite import TargetConfigRepository
from facebook_monitor.persistence.sqlite import TargetRepository
from facebook_monitor.persistence.sqlite import TargetRuntimeStateRepository


DEFAULT_WEBUI_FIXED_REFRESH_SECONDS = PYTHON_TARGET_CONFIG_DEFAULTS.fixed_refresh_sec


class UnsetConfigValue:
    """標記 upsert request 未提供某個 config 欄位。"""


UNSET_CONFIG_VALUE = UnsetConfigValue()
ConfigFieldValue = TypeVar("ConfigFieldValue")


def _provided_or_default(
    value: ConfigFieldValue | UnsetConfigValue,
    default: ConfigFieldValue,
) -> ConfigFieldValue:
    """合併 upsert request：未提供欄位時使用新 target 預設值。"""

    if isinstance(value, UnsetConfigValue):
        return default
    return value


def _provided_or_existing(
    value: ConfigFieldValue | UnsetConfigValue,
    existing: ConfigFieldValue,
) -> ConfigFieldValue:
    """合併 upsert request：未提供欄位時保留既有 config。"""

    if isinstance(value, UnsetConfigValue):
        return existing
    return value


@dataclass(frozen=True)
class CreateGroupPostsTargetRequest:
    """建立 group posts target 所需輸入。"""

    group_id: str
    canonical_url: str
    group_name: str = ""
    name: str = ""
    include_keywords: tuple[str, ...] | UnsetConfigValue = UNSET_CONFIG_VALUE
    exclude_keywords: tuple[str, ...] | UnsetConfigValue = UNSET_CONFIG_VALUE
    fixed_refresh_sec: int | None | UnsetConfigValue = UNSET_CONFIG_VALUE
    max_items_per_scan: int | UnsetConfigValue = UNSET_CONFIG_VALUE
    auto_load_more: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    auto_adjust_sort: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    enable_desktop_notification: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    enable_ntfy: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    ntfy_topic: str | UnsetConfigValue = UNSET_CONFIG_VALUE
    enable_discord_notification: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    discord_webhook: str | UnsetConfigValue = UNSET_CONFIG_VALUE


@dataclass(frozen=True)
class CreateCommentsTargetRequest:
    """建立 group post comments target 所需輸入。"""

    group_id: str
    parent_post_id: str
    canonical_url: str
    group_name: str = ""
    name: str = ""
    include_keywords: tuple[str, ...] | UnsetConfigValue = UNSET_CONFIG_VALUE
    exclude_keywords: tuple[str, ...] | UnsetConfigValue = UNSET_CONFIG_VALUE
    fixed_refresh_sec: int | None | UnsetConfigValue = UNSET_CONFIG_VALUE
    max_items_per_scan: int | UnsetConfigValue = UNSET_CONFIG_VALUE
    auto_load_more: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    auto_adjust_sort: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    enable_desktop_notification: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    enable_ntfy: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    ntfy_topic: str | UnsetConfigValue = UNSET_CONFIG_VALUE
    enable_discord_notification: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    discord_webhook: str | UnsetConfigValue = UNSET_CONFIG_VALUE


TargetConfigRequest = CreateGroupPostsTargetRequest | CreateCommentsTargetRequest


def _target_config_from_request(
    target_id: str,
    request: TargetConfigRequest,
) -> TargetConfig:
    """將 target 建立 request 轉成 group-scoped config。"""

    return TargetConfig(
        target_id=target_id,
        include_keywords=_provided_or_default(request.include_keywords, ()),
        exclude_keywords=_provided_or_default(request.exclude_keywords, ()),
        fixed_refresh_sec=_provided_or_default(
            request.fixed_refresh_sec,
            PYTHON_TARGET_CONFIG_DEFAULTS.fixed_refresh_sec,
        ),
        max_items_per_scan=clamp_target_post_count(
            _provided_or_default(
                request.max_items_per_scan,
                PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan,
            )
        ),
        auto_load_more=_provided_or_default(
            request.auto_load_more,
            PYTHON_TARGET_CONFIG_DEFAULTS.auto_load_more,
        ),
        auto_adjust_sort=_provided_or_default(
            request.auto_adjust_sort,
            PYTHON_TARGET_CONFIG_DEFAULTS.auto_adjust_sort,
        ),
        enable_ntfy=_provided_or_default(
            request.enable_ntfy,
            PYTHON_TARGET_CONFIG_DEFAULTS.enable_ntfy,
        ),
        ntfy_topic=_provided_or_default(
            request.ntfy_topic,
            PYTHON_TARGET_CONFIG_DEFAULTS.ntfy_topic,
        ),
        enable_desktop_notification=_provided_or_default(
            request.enable_desktop_notification,
            PYTHON_TARGET_CONFIG_DEFAULTS.enable_desktop_notification,
        ),
        enable_discord_notification=_provided_or_default(
            request.enable_discord_notification,
            PYTHON_TARGET_CONFIG_DEFAULTS.enable_discord_notification,
        ),
        discord_webhook=_provided_or_default(
            request.discord_webhook,
            PYTHON_TARGET_CONFIG_DEFAULTS.discord_webhook,
        ),
    )


def _merge_target_config_request(
    existing_config: TargetConfig,
    request: TargetConfigRequest,
) -> TargetConfig:
    """將 upsert request 合併到既有 group-scoped config。"""

    return replace(
        existing_config,
        include_keywords=_provided_or_existing(
            request.include_keywords,
            existing_config.include_keywords,
        ),
        exclude_keywords=_provided_or_existing(
            request.exclude_keywords,
            existing_config.exclude_keywords,
        ),
        fixed_refresh_sec=_provided_or_existing(
            request.fixed_refresh_sec,
            existing_config.fixed_refresh_sec,
        ),
        max_items_per_scan=clamp_target_post_count(
            _provided_or_existing(
                request.max_items_per_scan,
                existing_config.max_items_per_scan,
            )
        ),
        auto_load_more=_provided_or_existing(
            request.auto_load_more,
            existing_config.auto_load_more,
        ),
        auto_adjust_sort=_provided_or_existing(
            request.auto_adjust_sort,
            existing_config.auto_adjust_sort,
        ),
        enable_desktop_notification=_provided_or_existing(
            request.enable_desktop_notification,
            existing_config.enable_desktop_notification,
        ),
        enable_ntfy=_provided_or_existing(
            request.enable_ntfy,
            existing_config.enable_ntfy,
        ),
        ntfy_topic=_provided_or_existing(
            request.ntfy_topic,
            existing_config.ntfy_topic,
        ),
        enable_discord_notification=_provided_or_existing(
            request.enable_discord_notification,
            existing_config.enable_discord_notification,
        ),
        discord_webhook=_provided_or_existing(
            request.discord_webhook,
            existing_config.discord_webhook,
        ),
    )


@dataclass(frozen=True)
class RecordScanRequest:
    """記錄 scan run 所需輸入。"""

    target_id: str
    status: ScanStatus
    item_count: int = 0
    matched_count: int = 0
    error_message: str = ""
    worker_mode: WorkerMode = WorkerMode.HEADLESS
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UpdateTargetConfigRequest:
    """更新 target 所屬 group config 所需輸入。"""

    target_id: str
    include_keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    fixed_refresh_sec: int | None
    max_items_per_scan: int
    auto_load_more: bool
    auto_adjust_sort: bool
    enable_ntfy: bool = False
    ntfy_topic: str = ""
    enable_desktop_notification: bool | None = None
    enable_discord_notification: bool | None = None
    discord_webhook: str | None = None


@dataclass(frozen=True)
class UpdateTargetStatusRequest:
    """更新 target 啟停狀態所需輸入。"""

    target_id: str
    enabled: bool
    paused: bool


class TargetApplicationService:
    """協調 target 與 config repository。"""

    def __init__(
        self,
        targets: TargetRepository,
        configs: TargetConfigRepository,
        runtime_states: TargetRuntimeStateRepository,
        seen_items: SeenItemRepository,
    ) -> None:
        self.targets = targets
        self.configs = configs
        self.runtime_states = runtime_states
        self.seen_items = seen_items

    def _create_group_posts_target(
        self,
        request: CreateGroupPostsTargetRequest,
    ) -> TargetDescriptor:
        """建立 group posts target 並同步寫入 group-scoped config。

        主要互動入口應使用 `upsert_group_posts_target()`；此方法保留給測試與
        內部工具建立全新 target 時使用，避免 create/upsert 語義分家。
        """

        group_name = clean_facebook_group_name(request.group_name)
        name = clean_facebook_group_name(request.name)
        target = TargetDescriptor.for_group_posts(
            group_id=request.group_id,
            canonical_url=request.canonical_url,
            name=name,
            group_name=group_name,
        )
        config = _target_config_from_request(target.group_id, request)
        self.targets.save(target)
        self.configs.save_for_target(target, config)
        self.ensure_runtime_state(target.id)
        return target

    def _create_comments_target(
        self,
        request: CreateCommentsTargetRequest,
    ) -> TargetDescriptor:
        """建立 comments target 並同步寫入 group-scoped config。

        主要互動入口應使用 `upsert_comments_target()`；此方法保留給測試與
        內部工具建立全新 target 時使用，避免 create/upsert 語義分家。
        """

        group_name = clean_facebook_group_name(request.group_name)
        name = clean_facebook_group_name(request.name)
        target = TargetDescriptor.for_comments(
            group_id=request.group_id,
            parent_post_id=request.parent_post_id,
            canonical_url=request.canonical_url,
            name=name,
            group_name=group_name,
        )
        config = _target_config_from_request(target.group_id, request)
        self.targets.save(target)
        self.configs.save_for_target(target, config)
        self.ensure_runtime_state(target.id)
        return target

    def normalize_target_names(self, target: TargetDescriptor) -> TargetDescriptor:
        """清理已保存 target 名稱並寫回，避免通知數前綴散到各輸出面。"""

        normalized_name = clean_facebook_group_name(target.name)
        normalized_group_name = clean_facebook_group_name(target.group_name)
        if normalized_name == target.name and normalized_group_name == target.group_name:
            return target
        updated_target = replace(
            target,
            name=normalized_name,
            group_name=normalized_group_name,
            updated_at=utc_now(),
        )
        self.targets.save(updated_target)
        return updated_target

    def ensure_runtime_state(self, target_id: str) -> TargetRuntimeState:
        """確保 target 已有 runtime state，供 scheduler/UI 查詢。"""

        existing_state = self.runtime_states.get(target_id)
        if existing_state:
            return existing_state
        state = TargetRuntimeState(target_id=target_id)
        self.runtime_states.save(state)
        return state

    def get_config_for_target(self, target: TargetDescriptor) -> TargetConfig:
        """讀取 target 所屬社團的 group-scoped config。"""

        return self.configs.get_for_target(target) or TargetConfig(target_id=target.group_id)

    def save_config_for_target(
        self,
        target: TargetDescriptor,
        config: TargetConfig,
    ) -> TargetConfig:
        """保存 target 所屬社團的 group-scoped config。"""

        return self.configs.save_for_target(target, config)

    def apply_global_notification_settings(
        self,
        settings: GlobalNotificationSettings,
    ) -> int:
        """將通知預設值套用到所有既有 group config。"""

        count = 0
        applied_group_ids: set[str] = set()
        for target in self.targets.list_all():
            if target.group_id in applied_group_ids:
                continue
            current = self.get_config_for_target(target)
            self.save_config_for_target(
                target,
                replace(
                    current,
                    enable_desktop_notification=settings.enable_desktop_notification,
                    enable_ntfy=settings.enable_ntfy,
                    ntfy_topic=settings.ntfy_topic,
                    enable_discord_notification=settings.enable_discord_notification,
                    discord_webhook=settings.discord_webhook,
                )
            )
            applied_group_ids.add(target.group_id)
            count += 1
        return count

    def mark_target_queued(self, target_id: str, reason: str) -> TargetRuntimeState:
        """標記單一 target 已進入 executor queue，等待 worker slot。"""

        target = self.targets.get(target_id)
        if target is None:
            raise ValueError(f"Target not found: {target_id}")
        existing_state = self.ensure_runtime_state(target_id)
        state = replace(
            existing_state,
            runtime_status=TargetRuntimeStatus.QUEUED,
            last_enqueued_at=utc_now(),
            last_error="",
            last_skip_reason="",
            enqueue_reason=reason,
            active_worker_id="",
            active_page_id="",
            updated_at=utc_now(),
        )
        self.runtime_states.save(state)
        return state

    def mark_target_running(
        self,
        target_id: str,
        worker_id: str,
        *,
        page_id: str = "",
    ) -> TargetRuntimeState:
        """標記單一 target 正由 scheduler/worker 掃描中。"""

        target = self.targets.get(target_id)
        if target is None:
            raise ValueError(f"Target not found: {target_id}")
        existing_state = self.ensure_runtime_state(target_id)
        state = replace(
            existing_state,
            runtime_status=TargetRuntimeStatus.RUNNING,
            last_started_at=utc_now(),
            last_heartbeat_at=utc_now(),
            last_error="",
            last_skip_reason="",
            active_worker_id=worker_id,
            active_page_id=page_id,
            updated_at=utc_now(),
        )
        self.runtime_states.save(state)
        return state

    def try_mark_target_running(
        self,
        target_id: str,
        worker_id: str,
        *,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """嘗試取得單一 target scan lock；已 running 時記錄 skip reason。"""

        target = self.targets.get(target_id)
        if target is None:
            raise ValueError(f"Target not found: {target_id}")
        existing_state = self.ensure_runtime_state(target_id)
        if existing_state.runtime_status == TargetRuntimeStatus.RUNNING:
            skipped_state = replace(
                existing_state,
                last_skip_reason=(
                    "scan_guard_skipped: target_already_running "
                    f"active_worker_id={existing_state.active_worker_id}"
                ),
                scan_guard_count=existing_state.scan_guard_count + 1,
                updated_at=utc_now(),
            )
            self.runtime_states.save(skipped_state)
            return None
        return self.mark_target_running(target_id, worker_id, page_id=page_id)

    def mark_target_page_reloaded(
        self,
        target_id: str,
        *,
        page_id: str = "",
        reloaded_at: datetime | None = None,
    ) -> TargetRuntimeState:
        """記錄 resident page 已完成 reload/goto，供 UI 診斷 page ownership。"""

        if self.targets.get(target_id) is None:
            raise ValueError(f"Target not found: {target_id}")
        existing_state = self.ensure_runtime_state(target_id)
        now = utc_now()
        state = replace(
            existing_state,
            active_page_id=page_id or existing_state.active_page_id,
            last_page_reloaded_at=reloaded_at or now,
            last_heartbeat_at=now,
            updated_at=now,
        )
        self.runtime_states.save(state)
        return state

    def record_scan_guard_skip(self, target_id: str, reason: str) -> TargetRuntimeState:
        """記錄 target 被 queue/executor guard 擋下的原因。"""

        if self.targets.get(target_id) is None:
            raise ValueError(f"Target not found: {target_id}")
        existing_state = self.ensure_runtime_state(target_id)
        state = replace(
            existing_state,
            last_skip_reason=reason,
            scan_guard_count=existing_state.scan_guard_count + 1,
            updated_at=utc_now(),
        )
        self.runtime_states.save(state)
        return state

    def mark_target_idle(self, target_id: str) -> TargetRuntimeState:
        """標記單一 target 已完成本輪掃描並回到 idle。"""

        target = self.targets.get(target_id)
        if target is None:
            raise ValueError(f"Target not found: {target_id}")
        existing_state = self.ensure_runtime_state(target_id)
        state = replace(
            existing_state,
            runtime_status=TargetRuntimeStatus.IDLE,
            scan_requested_at=None,
            last_finished_at=utc_now(),
            last_heartbeat_at=utc_now(),
            last_error="",
            last_skip_reason="",
            enqueue_reason="",
            active_worker_id="",
            active_page_id="",
            updated_at=utc_now(),
        )
        self.runtime_states.save(state)
        return state

    def mark_target_error(self, target_id: str, error: str) -> TargetRuntimeState:
        """標記單一 target 本輪掃描發生錯誤。"""

        target = self.targets.get(target_id)
        if target is None:
            raise ValueError(f"Target not found: {target_id}")
        existing_state = self.ensure_runtime_state(target_id)
        state = replace(
            existing_state,
            runtime_status=TargetRuntimeStatus.ERROR,
            scan_requested_at=None,
            last_finished_at=utc_now(),
            last_heartbeat_at=utc_now(),
            last_error=error,
            last_skip_reason="",
            enqueue_reason="",
            active_worker_id="",
            active_page_id="",
            updated_at=utc_now(),
        )
        self.runtime_states.save(state)
        return state

    def recover_stale_running_targets(
        self,
        *,
        stale_after_seconds: float,
        now: datetime | None = None,
    ) -> tuple[TargetRuntimeState, ...]:
        """將 heartbeat 過舊的 running target 標成 error，避免永久卡住。"""

        current_time = now or utc_now()
        stale_after = max(stale_after_seconds, 1)
        recovered: list[TargetRuntimeState] = []
        for state in self.runtime_states.list_all():
            if state.runtime_status != TargetRuntimeStatus.RUNNING:
                continue
            heartbeat_at = state.last_heartbeat_at or state.updated_at
            if current_time - heartbeat_at <= timedelta(seconds=stale_after):
                continue
            recovered_state = replace(
                state,
                runtime_status=TargetRuntimeStatus.ERROR,
                scan_requested_at=None,
                last_finished_at=current_time,
                last_error=(
                    "stale_running: worker heartbeat expired "
                    f"after {int(stale_after)} seconds"
                ),
                last_skip_reason="",
                enqueue_reason="",
                active_worker_id="",
                active_page_id="",
                updated_at=current_time,
            )
            self.runtime_states.save(recovered_state)
            recovered.append(recovered_state)
        return tuple(recovered)

    def update_target_config(self, request: UpdateTargetConfigRequest) -> TargetConfig:
        """更新 target 所屬社團監視設定，供互動式設定入口或未來 UI 共用。"""

        target = self.targets.get(request.target_id)
        if target is None:
            raise ValueError(f"Target not found: {request.target_id}")

        existing_config = self.get_config_for_target(target)
        config = replace(
            existing_config,
            include_keywords=request.include_keywords,
            exclude_keywords=request.exclude_keywords,
            fixed_refresh_sec=request.fixed_refresh_sec,
            max_items_per_scan=clamp_target_post_count(request.max_items_per_scan),
            auto_load_more=request.auto_load_more,
            auto_adjust_sort=request.auto_adjust_sort,
            enable_ntfy=request.enable_ntfy,
            ntfy_topic=request.ntfy_topic,
            enable_desktop_notification=(
                existing_config.enable_desktop_notification
                if request.enable_desktop_notification is None
                else request.enable_desktop_notification
            ),
            enable_discord_notification=(
                existing_config.enable_discord_notification
                if request.enable_discord_notification is None
                else request.enable_discord_notification
            ),
            discord_webhook=(
                existing_config.discord_webhook
                if request.discord_webhook is None
                else request.discord_webhook
            ),
        )
        self.save_config_for_target(target, config)
        return config

    def update_target_status(self, request: UpdateTargetStatusRequest) -> TargetDescriptor:
        """更新 target 啟停狀態，供 UI/console 與未來 scheduler 共用。"""

        target = self.targets.get(request.target_id)
        if target is None:
            raise ValueError(f"Target not found: {request.target_id}")

        updated_target = replace(
            target,
            enabled=request.enabled,
            paused=request.paused,
            updated_at=utc_now(),
        )
        self.targets.save(updated_target)
        self.runtime_states.save(
            TargetRuntimeState(
                target_id=target.id,
                desired_state=(
                    TargetDesiredState.ACTIVE
                    if request.enabled and not request.paused
                    else TargetDesiredState.STOPPED
                ),
                runtime_status=(
                    TargetRuntimeStatus.IDLE
                    if request.enabled and not request.paused
                    else TargetRuntimeStatus.PAUSED
                ),
            )
        )
        return updated_target

    def start_target(self, target_id: str) -> TargetDescriptor:
        """相容舊呼叫端：重新開始單一 target 監視。"""

        return self.restart_target_monitoring(target_id)

    def restart_target_monitoring(self, target_id: str) -> TargetDescriptor:
        """對齊 userscript「開始」：清 seen scope、啟用並要求立即掃描。"""

        target = self.targets.get(target_id)
        if target is None:
            raise ValueError(f"Target not found: {target_id}")
        if target.target_kind not in {TargetKind.POSTS, TargetKind.COMMENTS}:
            raise ValueError(f"Unsupported target kind: {target.target_kind.value}")
        target = self.normalize_target_names(target)

        self.seen_items.clear_scope(target.scope_id)
        existing_config = self.get_config_for_target(target)
        if existing_config.fixed_refresh_sec is None:
            self.save_config_for_target(
                target,
                replace(
                    existing_config,
                    fixed_refresh_sec=DEFAULT_WEBUI_FIXED_REFRESH_SECONDS,
                )
            )
        updated_target = replace(
            target,
            enabled=True,
            paused=False,
            updated_at=utc_now(),
        )
        self.targets.save(updated_target)
        existing_state = self.ensure_runtime_state(target_id)
        self.runtime_states.save(
            replace(
                existing_state,
                desired_state=TargetDesiredState.ACTIVE,
                runtime_status=TargetRuntimeStatus.IDLE,
                scan_requested_at=utc_now(),
                last_enqueued_at=None,
                last_started_at=None,
                last_finished_at=None,
                last_heartbeat_at=None,
                last_error="",
                last_skip_reason="",
                enqueue_reason="",
                active_worker_id="",
                active_page_id="",
                updated_at=utc_now(),
            )
        )
        return updated_target

    def request_target_scan(self, target_id: str) -> TargetRuntimeState:
        """要求 scheduler 下一輪立即掃描 target，不修改 seen 狀態。"""

        if self.targets.get(target_id) is None:
            raise ValueError(f"Target not found: {target_id}")
        existing_state = self.ensure_runtime_state(target_id)
        state = replace(
            existing_state,
            scan_requested_at=utc_now(),
            updated_at=utc_now(),
        )
        self.runtime_states.save(state)
        return state

    def clear_target_scan_request(self, target_id: str) -> TargetRuntimeState:
        """清除已被 scheduler 消化的立即掃描要求。"""

        if self.targets.get(target_id) is None:
            raise ValueError(f"Target not found: {target_id}")
        existing_state = self.ensure_runtime_state(target_id)
        state = replace(
            existing_state,
            scan_requested_at=None,
            updated_at=utc_now(),
        )
        self.runtime_states.save(state)
        return state

    def stop_target(self, target_id: str) -> TargetDescriptor:
        """相容舊呼叫端：暫停單一 target 監視但保留 seen 基準。"""

        return self.pause_target_monitoring(target_id)

    def pause_target_monitoring(self, target_id: str) -> TargetDescriptor:
        """對齊 userscript「停止」：停止排程但保留 seen/history。"""

        return self.update_target_status(
            UpdateTargetStatusRequest(
                target_id=target_id,
                enabled=True,
                paused=True,
            )
        )

    def pause_all_targets_for_webui_startup(
        self,
        *,
        default_fixed_refresh_sec: int = DEFAULT_WEBUI_FIXED_REFRESH_SECONDS,
    ) -> None:
        """Web UI 啟動時停止所有 target，並補齊固定掃描間隔設定。"""

        fixed_refresh_sec = max(int(default_fixed_refresh_sec), 1)
        for target in self.targets.list_all():
            target = self.normalize_target_names(target)
            self.pause_target_monitoring(target.id)
            config = self.get_config_for_target(target)
            if config.fixed_refresh_sec is None:
                self.save_config_for_target(
                    target,
                    replace(
                        config,
                        fixed_refresh_sec=fixed_refresh_sec,
                    )
                )

    def delete_target(self, target_id: str) -> None:
        """刪除單一 target 與其關聯設定，不影響其他 target。"""

        deleted = self.targets.delete(target_id)
        if not deleted:
            raise ValueError(f"Target not found: {target_id}")

    def upsert_group_posts_target(
        self,
        request: CreateGroupPostsTargetRequest,
    ) -> TargetDescriptor:
        """建立或更新 group posts target，供 capture script 可重複執行。"""

        existing = self.targets.find_by_kind_scope(TargetKind.POSTS, request.group_id)
        request_name = clean_facebook_group_name(request.name)
        request_group_name = clean_facebook_group_name(request.group_name)
        if existing:
            existing = self.normalize_target_names(existing)
            existing_name = clean_facebook_group_name(existing.name)
            existing_group_name = clean_facebook_group_name(existing.group_name)
            next_name = request_name or existing_name
            if (
                not request_name
                and request_group_name
                and is_generated_group_posts_name(existing.name, existing.group_id)
            ):
                next_name = request_group_name
            target = replace(
                existing,
                name=next_name,
                group_name=request_group_name or existing_group_name,
                canonical_url=request.canonical_url,
                updated_at=utc_now(),
            )
        else:
            target = TargetDescriptor.for_group_posts(
                group_id=request.group_id,
                canonical_url=request.canonical_url,
                name=request_name,
                group_name=request_group_name,
            )

        existing_config = self.configs.get_for_target(target)
        if existing_config:
            config = _merge_target_config_request(existing_config, request)
        else:
            config = _target_config_from_request(target.group_id, request)

        self.targets.save(target)
        self.save_config_for_target(target, config)
        self.ensure_runtime_state(target.id)
        return target

    def upsert_comments_target(
        self,
        request: CreateCommentsTargetRequest,
    ) -> TargetDescriptor:
        """建立或更新 comments target，打通 group_id / parent_post_id / scope_id。"""

        target_probe = TargetDescriptor.for_comments(
            group_id=request.group_id,
            parent_post_id=request.parent_post_id,
            canonical_url=request.canonical_url,
        )
        existing = self.targets.find_by_kind_scope(TargetKind.COMMENTS, target_probe.scope_id)
        request_name = clean_facebook_group_name(request.name)
        request_group_name = clean_facebook_group_name(request.group_name)
        if existing:
            existing = self.normalize_target_names(existing)
            existing_name = clean_facebook_group_name(existing.name)
            existing_group_name = clean_facebook_group_name(existing.group_name)
            next_name = request_name or existing_name
            if (
                not request_name
                and request_group_name
                and is_generated_group_comments_name(
                    existing.name,
                    existing.group_id,
                    existing.parent_post_id,
                )
            ):
                next_name = request_group_name
            target = replace(
                existing,
                name=next_name,
                group_name=request_group_name or existing_group_name,
                canonical_url=request.canonical_url,
                updated_at=utc_now(),
            )
        else:
            target = TargetDescriptor.for_comments(
                group_id=request.group_id,
                parent_post_id=request.parent_post_id,
                canonical_url=request.canonical_url,
                name=request_name,
                group_name=request_group_name,
            )

        existing_config = self.configs.get_for_target(target)
        if existing_config:
            config = _merge_target_config_request(existing_config, request)
        else:
            config = _target_config_from_request(target.group_id, request)

        self.targets.save(target)
        self.save_config_for_target(target, config)
        self.ensure_runtime_state(target.id)
        return target


class ScanApplicationService:
    """協調 scan run repository。"""

    def __init__(self, scan_runs: ScanRunRepository) -> None:
        self.scan_runs = scan_runs

    def record_scan(self, request: RecordScanRequest) -> int:
        """記錄一輪 scan 結果並回傳 row id。"""

        now = utc_now()
        return self.scan_runs.add(
            ScanRun(
                target_id=request.target_id,
                status=request.status,
                started_at=now,
                finished_at=now,
                item_count=request.item_count,
                matched_count=request.matched_count,
                error_message=request.error_message,
                worker_mode=request.worker_mode,
                metadata=request.metadata,
            )
        )


def clean_facebook_group_name(value: str) -> str:
    """清理準備保存的 Facebook 社團名稱，對齊 userscript 取得名稱階段。"""

    return clean_facebook_page_title(value)
