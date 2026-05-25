"""Dashboard view models。

職責：整理 target card、sidebar 與設定摘要所需資料。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from functools import cached_property
from math import ceil

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.external_url_policy import sanitize_facebook_image_url
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import NotificationOutboxSummary
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.refresh_policy import resolve_refresh_interval_seconds
from facebook_monitor.core.sidebar_models import SidebarGroupConfigTemplate
from facebook_monitor.core.user_messages import format_runtime_skip_message
from facebook_monitor.webapp.dashboard_presenters import SettingsSummary
from facebook_monitor.webapp.dashboard_presenters import TargetCardSummary
from facebook_monitor.webapp.dashboard_presenters import TargetCardSummaryPresenter
from facebook_monitor.webapp.dashboard_presenters import TargetIdentityPresenter
from facebook_monitor.webapp.dashboard_presenters import TargetSettingsPresenter
from facebook_monitor.webapp.dashboard_presenters import TargetStatusPresenter
from facebook_monitor.webapp.dashboard_presenters import format_latest_error_indicator_label
from facebook_monitor.webapp.dashboard_presenters import format_latest_error_indicator_title
from facebook_monitor.webapp.dashboard_presenters import format_runtime_error_message
from facebook_monitor.webapp.dashboard_presenters import is_content_unavailable_runtime_error
from facebook_monitor.webapp.dashboard_presenters import is_content_unavailable_scan
from facebook_monitor.webapp.dashboard_presenters import is_retrying_failure_scan
from facebook_monitor.webapp.diagnostics_presenter import build_scan_diagnostics_view
from facebook_monitor.webapp.diagnostics_presenter import format_scan_cycle_result_reason
from facebook_monitor.webapp.diagnostics_presenter import format_datetime_for_ui
from facebook_monitor.webapp.form_models import FLOATING_REFRESH_MODE
from facebook_monitor.webapp.preview_models import HitRecordPreviewRow
from facebook_monitor.webapp.preview_models import LatestScanItemRow
from facebook_monitor.webapp.preview_models import TargetPreviewRow


@dataclass(frozen=True)
class SidebarTargetItem:
    """保存 Phase 5 sidebar 需要的 target 摘要資料。"""

    target_id: str
    display_name: str
    anchor_id: str
    base_status_summary: str
    status_class: str
    status_detail: str
    status_summary: str
    mode_label: str
    mode_class: str
    hit_count: int
    latest_error_summary: str = ""
    thumbnail_url: str = ""
    active: bool = False


@dataclass(frozen=True)
class SidebarGroupSection:
    """保存 sidebar group 區塊與其 target 摘要。"""

    group_id: str | None
    name: str
    items: tuple[SidebarTargetItem, ...]
    collapsed: bool = False
    is_system: bool = False
    template: SidebarGroupConfigTemplate | None = None

    @property
    def target_count(self) -> int:
        """回傳 group 內 target 數量。"""

        return len(self.items)

    @property
    def has_active_target(self) -> bool:
        """回傳 group 內是否至少一個 target 正在啟用。"""

        return any(item.active for item in self.items)

    @property
    def monitoring_action(self) -> str:
        """回傳 group 開始/停止按鈕提交的 action。"""

        return "stop" if self.has_active_target else "start"

    @property
    def monitoring_button_label(self) -> str:
        """回傳 group 開始/停止按鈕的可讀標籤。"""

        return "停止群組" if self.monitoring_action == "stop" else "開始群組"

    @property
    def monitoring_disabled(self) -> bool:
        """回傳 group 是否沒有可操作 target。"""

        return not self.items

    @property
    def dom_group_id(self) -> str:
        """回傳前端 data attribute 使用的 group id。"""

        return self.group_id or ""

    @property
    def template_presenter(self) -> TargetSettingsPresenter | None:
        """回傳 group template 設定 presenter。"""

        if self.template is None:
            return None
        return TargetSettingsPresenter(config=self.template.to_target_config(target_id=""))

    @property
    def include_text(self) -> str:
        """回傳 template include keywords 表單文字。"""

        presenter = self.template_presenter
        return presenter.include_text if presenter else ""

    @property
    def exclude_text(self) -> str:
        """回傳 template exclude keywords 表單文字。"""

        presenter = self.template_presenter
        return presenter.exclude_text if presenter else ""

    @property
    def exclude_ignore_phrases_text(self) -> str:
        """回傳 template 排除字忽略片語表單文字。"""

        presenter = self.template_presenter
        return presenter.exclude_ignore_phrases_text if presenter else ""

    @property
    def refresh_mode(self) -> str:
        """回傳 template refresh mode。"""

        presenter = self.template_presenter
        return presenter.refresh_mode if presenter else FLOATING_REFRESH_MODE

    @property
    def fixed_refresh_value(self) -> int:
        """回傳 template 固定刷新秒數。"""

        presenter = self.template_presenter
        return (
            presenter.fixed_refresh_value
            if presenter
            else PYTHON_TARGET_CONFIG_DEFAULTS.default_fixed_refresh_sec
        )

    @property
    def min_refresh_value(self) -> int:
        """回傳 template 浮動刷新最小秒數。"""

        presenter = self.template_presenter
        return (
            presenter.min_refresh_value
            if presenter
            else PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec
        )

    @property
    def max_refresh_value(self) -> int:
        """回傳 template 浮動刷新最大秒數。"""

        presenter = self.template_presenter
        return (
            presenter.max_refresh_value
            if presenter
            else PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec
        )


@dataclass(frozen=True)
class NextRefreshDisplay:
    """保存下一次刷新在 UI 顯示與前端倒數校準所需資料。"""

    label: str
    seconds: int | None = None


@dataclass(frozen=True)
class TargetRow:
    """保存 target 清單顯示所需資料。"""

    target: TargetDescriptor
    config: TargetConfig
    runtime_state: TargetRuntimeState
    latest_scan_run: ScanRun | None = None
    latest_failed_scan_run: ScanRun | None = None
    notification_outbox_summary: NotificationOutboxSummary | None = None
    latest_scan_items: tuple[LatestScanItemRow, ...] = ()
    hit_record_preview_items: tuple[HitRecordPreviewRow, ...] = ()
    hit_record_total_count: int = 0

    @property
    def target_id(self) -> str:
        """回傳 target id。"""

        return self.target.id

    @property
    def anchor_id(self) -> str:
        """回傳 target card anchor id。"""

        return f"target-{self.target_id}"

    @property
    def latest_items_heading(self) -> str:
        """回傳右側最近掃描項目的標題。"""

        label = "留言" if self.target.target_kind == TargetKind.COMMENTS else "貼文"
        return f"最近掃描{label}（{len(self.latest_scan_items)}）"

    @property
    def latest_item_link_label(self) -> str:
        """回傳右側項目 permalink 的連結文字。"""

        return "開啟連結"

    @property
    def latest_scan_preview_rows(self) -> tuple[TargetPreviewRow, ...]:
        """回傳最近掃描 preview rows。"""

        return tuple(
            item.to_preview_row(link_label=self.latest_item_link_label)
            for item in self.latest_scan_items
        )

    @property
    def hit_record_preview_rows(self) -> tuple[TargetPreviewRow, ...]:
        """回傳命中紀錄 preview rows。"""

        return tuple(item.to_preview_row() for item in self.hit_record_preview_items)

    @property
    def hit_records_heading(self) -> str:
        """回傳命中紀錄 preview 標題。"""

        return f"命中紀錄（{self.hit_record_total_count}）"

    @property
    def display_name(self) -> str:
        """回傳 UI 顯示名稱。"""

        return TargetIdentityPresenter(self.target).display_name

    @property
    def rename_display_name(self) -> str:
        """回傳更名 modal 的預填名稱。"""

        return TargetIdentityPresenter(self.target).rename_value

    @property
    def thumbnail_url(self) -> str:
        """回傳 target header / sidebar 使用的社團縮圖 URL。"""

        result = sanitize_facebook_image_url(self.target.group_cover_image_url)
        return result.url if result.ok else ""

    @property
    def mode_label(self) -> str:
        """回傳 target card header 使用的掃描模式文字。"""

        return "留言模式" if self.target.target_kind == TargetKind.COMMENTS else "貼文模式"

    @property
    def mode_class(self) -> str:
        """回傳掃描模式 chip 對應 CSS class。"""

        return "comments" if self.target.target_kind == TargetKind.COMMENTS else "posts"

    @property
    def scanning_supported(self) -> bool:
        """回傳目前 target 是否已接上 worker 掃描流程。"""

        return self.target.target_kind in {TargetKind.POSTS, TargetKind.COMMENTS}

    @property
    def status_presenter(self) -> TargetStatusPresenter:
        """回傳 target 狀態 presenter。"""

        return TargetStatusPresenter(
            target=self.target,
            runtime_state=self.runtime_state,
            scanning_supported=self.scanning_supported,
        )

    @property
    def settings_presenter(self) -> TargetSettingsPresenter:
        """回傳 target 設定 presenter。"""

        return TargetSettingsPresenter(config=self.config)

    @property
    def card_summary_presenter(self) -> TargetCardSummaryPresenter:
        """回傳收合卡片摘要 presenter。"""

        return TargetCardSummaryPresenter(
            settings=self.settings_presenter,
            latest_scan_run=self.latest_scan_run,
            latest_failed_scan_run=self.latest_failed_scan_run,
            hit_record_total_count=self.hit_record_total_count,
            content_unavailable_current=self.content_unavailable_current,
        )

    @property
    def header_summary_label(self) -> str:
        """回傳 target header 的低干擾摘要，避免主畫面顯示診斷 ID。"""

        parts = [
            self.mode_label,
            f"最近掃描 {self.latest_scan_header_time_label}",
            f"下次刷新：{self.next_refresh_label}",
        ]
        if self.latest_failed_scan_run:
            parts.append(self.latest_error_indicator_label)
        return " · ".join(parts)

    @property
    def status_label(self) -> str:
        """回傳 target 啟停狀態文字。"""

        return self.status_presenter.label

    @property
    def status_class(self) -> str:
        """回傳 target 狀態對應 CSS class。"""

        return self.status_presenter.css_class

    @property
    def runtime_error(self) -> str:
        """回傳 runtime error 顯示文字。"""

        if self.runtime_state.runtime_status != TargetRuntimeStatus.ERROR:
            return ""
        return format_runtime_error_message(self.runtime_state.last_error)

    @property
    def runtime_skip_reason(self) -> str:
        """回傳最近一次 scan guard skip 原因。"""

        return format_runtime_skip_message(self.runtime_state.last_skip_reason)

    @property
    def latest_scan_header_time_label(self) -> str:
        """回傳 target header 使用的最近掃描短時間。"""

        if not self.latest_scan_run:
            return "尚無掃描"
        return self.latest_scan_run.finished_at.astimezone().strftime("%H:%M:%S")

    @property
    def next_refresh_label(self) -> str:
        """回傳 target header 使用的下一次刷新狀態。"""

        return self.next_refresh_display.label

    @property
    def next_refresh_seconds(self) -> int | None:
        """回傳前端本地倒數用的剩餘秒數；不可倒數時回傳 None。"""

        return self.next_refresh_display.seconds

    @cached_property
    def next_refresh_display(self) -> NextRefreshDisplay:
        """一次產生下一次刷新顯示值，避免同一 row 重複計算倒數。"""

        if (
            not self.target.enabled
            or self.target.paused
            or self.runtime_state.desired_state != TargetDesiredState.ACTIVE
        ):
            return NextRefreshDisplay(label="未排程")
        if self.runtime_state.runtime_status == TargetRuntimeStatus.ERROR:
            return NextRefreshDisplay(label="未排程")
        if self.runtime_state.runtime_status == TargetRuntimeStatus.QUEUED:
            return NextRefreshDisplay(label="排隊中")
        if self.runtime_state.runtime_status == TargetRuntimeStatus.RUNNING:
            return NextRefreshDisplay(label="掃描中")
        if self.runtime_state.scan_requested_at is not None:
            return NextRefreshDisplay(label="即將刷新")
        remaining_seconds = self._next_refresh_remaining_seconds()
        if remaining_seconds is None or remaining_seconds <= 0:
            return NextRefreshDisplay(label="即將刷新")
        return NextRefreshDisplay(
            label=_format_countdown_seconds(remaining_seconds),
            seconds=remaining_seconds,
        )

    def _next_refresh_remaining_seconds(self) -> int | None:
        """依後端目前排程狀態計算下一次刷新剩餘秒數。"""

        if self.runtime_state.display_next_due_at is not None:
            remaining_seconds = ceil(
                (self.runtime_state.display_next_due_at - utc_now()).total_seconds()
            )
            return max(remaining_seconds, 0)

        last_reference_at = self.runtime_state.last_started_at
        if last_reference_at is None and self.latest_scan_run:
            last_reference_at = self.latest_scan_run.finished_at
        if last_reference_at is None:
            return None
        interval_seconds = resolve_refresh_interval_seconds(
            config=self.config,
            default_interval_seconds=self.settings_presenter.fixed_refresh_value,
            target_id=self.target_id,
            latest_finished_at=self.latest_scan_run.finished_at
            if self.latest_scan_run
            else None,
        )
        due_at = last_reference_at + timedelta(seconds=max(interval_seconds, 1))
        remaining_seconds = ceil((due_at - utc_now()).total_seconds())
        if remaining_seconds <= 0:
            return 0
        return remaining_seconds

    @property
    def scan_cycle_result_label(self) -> str:
        """回傳右側結果 panel 使用的最近一輪結束原因。"""

        if not self.latest_scan_run:
            return ""
        metadata = self.latest_scan_run.metadata or {}
        reason = str(metadata.get("stop_reason") or "")
        if not reason:
            return ""
        return f"本輪：{format_scan_cycle_result_reason(reason)}"

    @property
    def latest_scan_diagnostics_summary(self) -> str:
        """回傳最近成功掃描的診斷短摘要。"""

        return build_scan_diagnostics_view(
            target=self.target,
            config=self.config,
            runtime_state=self.runtime_state,
            latest_scan_run=self.latest_scan_run,
            notification_outbox_summary=self.notification_outbox_summary,
            latest_failed_scan_run=self.latest_failed_scan_run,
        ).summary

    @property
    def latest_scan_diagnostics_text(self) -> str:
        """回傳可複製的 scan-level diagnostics。"""

        return build_scan_diagnostics_view(
            target=self.target,
            config=self.config,
            runtime_state=self.runtime_state,
            latest_scan_run=self.latest_scan_run,
            latest_scan_items=tuple(row.item for row in self.latest_scan_items),
            notification_outbox_summary=self.notification_outbox_summary,
            latest_failed_scan_run=self.latest_failed_scan_run,
        ).text

    @property
    def latest_error_label(self) -> str:
        """回傳最近錯誤時間。"""

        if not self.latest_failed_scan_run:
            return ""
        return format_datetime_for_ui(self.latest_failed_scan_run.finished_at)

    @property
    def latest_failed_scan_summary(self) -> str:
        """回傳最近失敗掃描摘要。"""

        return self.card_summary_presenter.latest_failed_scan_summary

    @property
    def latest_error_indicator_label(self) -> str:
        """回傳 target header 的最近錯誤短標籤。"""

        return format_latest_error_indicator_label(
            self.latest_failed_scan_run,
            content_unavailable_current=self.content_unavailable_current,
            retrying_current=self.retrying_failure_current,
        )

    @property
    def latest_error_indicator_title(self) -> str:
        """回傳 target header 最近錯誤說明。"""

        return format_latest_error_indicator_title(
            self.latest_failed_scan_run,
            content_unavailable_current=self.content_unavailable_current,
            retrying_current=self.retrying_failure_current,
        )

    @property
    def latest_error_indicator_kind(self) -> str:
        """回傳最近錯誤 UI 類型。"""

        if self.content_unavailable_current:
            return "content-unavailable"
        if self.retrying_failure_current:
            return "retrying"
        return "error" if self.latest_failed_scan_run else ""

    @property
    def retrying_failure_current(self) -> bool:
        """回傳最近 failed scan 是否仍代表等待下輪重試的目前狀態。"""

        failed_scan = self.latest_failed_scan_run
        if not is_retrying_failure_scan(failed_scan):
            return False
        latest_scan = self.latest_scan_run
        if latest_scan is None:
            return True
        if failed_scan is None:
            return False
        return failed_scan.finished_at >= latest_scan.finished_at

    @property
    def content_unavailable_current(self) -> bool:
        """回傳連結失效是否仍代表目前狀態。"""

        failed_scan = self.latest_failed_scan_run
        if not is_content_unavailable_scan(failed_scan):
            return False
        if (
            self.runtime_state.runtime_status == TargetRuntimeStatus.ERROR
            and is_content_unavailable_runtime_error(self.runtime_state.last_error)
        ):
            return True
        latest_scan = self.latest_scan_run
        if latest_scan is None:
            return True
        if failed_scan is None:
            return False
        return failed_scan.finished_at >= latest_scan.finished_at

    @property
    def notification_summary_label(self) -> str:
        """回傳設定摘要用的通知通道列表。"""

        return self.settings_presenter.notification_summary_label

    @property
    def include_text(self) -> str:
        """回傳 include keywords 表單文字。"""

        return self.settings_presenter.include_text

    @property
    def exclude_text(self) -> str:
        """回傳 exclude keywords 表單文字。"""

        return self.settings_presenter.exclude_text

    @property
    def exclude_ignore_phrases_text(self) -> str:
        """回傳排除字忽略片語表單文字。"""

        return self.settings_presenter.exclude_ignore_phrases_text

    @property
    def fixed_refresh_value(self) -> int:
        """回傳表單使用的固定掃描間隔秒數。"""

        return self.settings_presenter.fixed_refresh_value

    @property
    def refresh_mode(self) -> str:
        """回傳目前 refresh mode。"""

        return self.settings_presenter.refresh_mode

    @property
    def refresh_mode_label(self) -> str:
        """回傳 refresh mode 摘要。"""

        return self.settings_presenter.refresh_mode_label

    @property
    def settings_summary(self) -> SettingsSummary:
        """回傳 target card 設定摘要 view model。"""

        return self.settings_presenter.settings_summary

    @property
    def card_summary(self) -> TargetCardSummary:
        """回傳 Phase 9 收合卡片可共用的摘要 view model。"""

        return self.card_summary_presenter.summary

    @property
    def sidebar_item(self) -> SidebarTargetItem:
        """回傳 Phase 5 sidebar 使用的 target 摘要。"""

        base_status_summary = self.status_label
        if self.content_unavailable_current:
            status_detail = self.latest_error_indicator_label
        elif self.hit_record_total_count:
            status_detail = f"命中 {self.hit_record_total_count} 筆"
        elif not self.latest_scan_run:
            status_detail = "尚未掃描"
        else:
            status_detail = ""
        status_summary = (
            f"{base_status_summary} · {status_detail}"
            if status_detail
            else base_status_summary
        )
        latest_error_summary = self.latest_failed_scan_summary if self.latest_failed_scan_run else ""
        return SidebarTargetItem(
            target_id=self.target_id,
            display_name=self.display_name,
            anchor_id=self.anchor_id,
            base_status_summary=base_status_summary,
            status_class=self.status_class,
            status_detail=status_detail,
            status_summary=status_summary,
            mode_label="留言" if self.target.target_kind == TargetKind.COMMENTS else "貼文",
            mode_class=self.mode_class,
            hit_count=self.hit_record_total_count,
            latest_error_summary=latest_error_summary,
            thumbnail_url=self.thumbnail_url,
            active=self.target.enabled and not self.target.paused,
        )

    @property
    def min_refresh_value(self) -> int:
        """回傳表單使用的浮動最小掃描間隔秒數。"""

        return self.settings_presenter.min_refresh_value

    @property
    def max_refresh_value(self) -> int:
        """回傳表單使用的浮動最大掃描間隔秒數。"""

        return self.settings_presenter.max_refresh_value

    @property
    def monitoring_action(self) -> str:
        """回傳主操作按鈕應提交的 monitoring action。"""

        return "start" if self.target.paused or not self.target.enabled else "stop"

    @property
    def monitoring_button_label(self) -> str:
        """回傳主操作按鈕文字，維持開始 / 暫停語義。"""

        return "開始" if self.monitoring_action == "start" else "停止"


def _format_countdown_seconds(seconds: int) -> str:
    """格式化 header 的下一次刷新倒數。"""

    bounded_seconds = max(int(seconds), 0)
    if bounded_seconds < 60:
        return f"{bounded_seconds}s"
    minutes, remainder = divmod(bounded_seconds, 60)
    if minutes < 60:
        return f"{minutes}m {remainder}s" if remainder else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"
