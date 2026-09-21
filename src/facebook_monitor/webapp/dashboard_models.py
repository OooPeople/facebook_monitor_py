"""Dashboard view models。

職責：整理 target card、sidebar 與設定摘要所需資料。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from facebook_monitor.core.external_url_policy import sanitize_facebook_group_cover_image_url
from facebook_monitor.core.models import NotificationOutboxSummary
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.sidebar_models import SidebarGroupConfigTemplate
from facebook_monitor.webapp.dashboard_card_summary_presenters import (
    TargetCardSummaryPresenter,
)
from facebook_monitor.webapp.dashboard_identity_presenters import TargetIdentityPresenter
from facebook_monitor.webapp.dashboard_settings_presenters import TargetSettingsPresenter
from facebook_monitor.webapp.dashboard_status_presenters import TargetStatusPresenter
from facebook_monitor.webapp.dashboard_target_diagnostics import TargetDiagnosticsPresenter
from facebook_monitor.webapp.dashboard_target_errors import TargetErrorPresenter
from facebook_monitor.webapp.dashboard_target_header import TargetHeaderPresenter
from facebook_monitor.webapp.dashboard_target_preview import TargetPreviewPresenter
from facebook_monitor.webapp.dashboard_target_sidebar import SidebarTargetItem
from facebook_monitor.webapp.dashboard_target_sidebar import TargetSidebarPresenter
from facebook_monitor.webapp.dashboard_target_status import TargetMonitoringPresenter
from facebook_monitor.webapp.dashboard_target_refresh import NextRefreshDisplay
import facebook_monitor.webapp.dashboard_target_refresh as target_refresh
from facebook_monitor.webapp.preview_models import HitRecordPreviewRow
from facebook_monitor.webapp.preview_models import LatestScanItemRow
from facebook_monitor.webapp.preview_models import TargetPreviewRow


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
    def template_presenter(self) -> TargetSettingsPresenter:
        """回傳 group template 設定 presenter。"""

        if self.template is None:
            return TargetSettingsPresenter(config=TargetConfig(target_id=""))
        return TargetSettingsPresenter(config=self.template.to_target_config(target_id=""))

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

    @cached_property
    def identity_presenter(self) -> TargetIdentityPresenter:
        """回傳 target identity presenter。"""

        return TargetIdentityPresenter(self.target)

    @cached_property
    def preview_presenter(self) -> TargetPreviewPresenter:
        """回傳右側 preview panel presenter。"""

        return TargetPreviewPresenter(
            target_kind=self.target.target_kind,
            latest_scan_items=self.latest_scan_items,
            hit_record_preview_items=self.hit_record_preview_items,
            hit_record_total_count=self.hit_record_total_count,
        )

    @cached_property
    def monitoring_presenter(self) -> TargetMonitoringPresenter:
        """回傳 target 啟停狀態與主操作 presenter。"""

        return TargetMonitoringPresenter(
            target=self.target,
            runtime_state=self.runtime_state,
        )

    @cached_property
    def error_presenter(self) -> TargetErrorPresenter:
        """回傳 runtime error 與最近 failed scan presenter。"""

        return TargetErrorPresenter(
            runtime_state=self.runtime_state,
            latest_scan_run=self.latest_scan_run,
            latest_failed_scan_run=self.latest_failed_scan_run,
        )

    @cached_property
    def diagnostics_presenter(self) -> TargetDiagnosticsPresenter:
        """回傳 scan diagnostics presenter。"""

        return TargetDiagnosticsPresenter(
            target=self.target,
            config=self.config,
            runtime_state=self.runtime_state,
            latest_scan_run=self.latest_scan_run,
            latest_scan_items=tuple(item.item for item in self.latest_scan_items),
            notification_outbox_summary=self.notification_outbox_summary,
            latest_failed_scan_run=self.latest_failed_scan_run,
        )

    @cached_property
    def header_presenter(self) -> TargetHeaderPresenter:
        """回傳 target card header presenter。"""

        return TargetHeaderPresenter(
            target_kind=self.target.target_kind,
            latest_scan_run=self.latest_scan_run,
            next_refresh_label=self.next_refresh_label,
            latest_failed_scan_run=self.latest_failed_scan_run,
            latest_error_indicator_label=self.latest_error_indicator_label,
        )

    @cached_property
    def sidebar_presenter(self) -> TargetSidebarPresenter:
        """回傳 sidebar target presenter。"""

        return TargetSidebarPresenter(
            target=self.target,
            target_id=self.target_id,
            display_name=self.display_name,
            anchor_id=self.anchor_id,
            status_label=self.status_label,
            status_class=self.status_class,
            mode_class=self.mode_class,
            hit_record_total_count=self.hit_record_total_count,
            latest_scan_run=self.latest_scan_run,
            latest_failed_scan_run=self.latest_failed_scan_run,
            latest_failed_scan_summary=self.latest_failed_scan_summary,
            latest_error_indicator_label=self.latest_error_indicator_label,
            content_unavailable_current=self.content_unavailable_current,
            thumbnail_url=self.thumbnail_url,
        )

    @property
    def latest_items_heading(self) -> str:
        """回傳右側最近掃描項目的標題。"""

        return self.preview_presenter.latest_items_heading

    @property
    def latest_item_link_label(self) -> str:
        """回傳右側項目 permalink 的連結文字。"""

        return self.preview_presenter.latest_item_link_label

    @property
    def latest_scan_preview_rows(self) -> tuple[TargetPreviewRow, ...]:
        """回傳最近掃描 preview rows。"""

        return self.preview_presenter.latest_scan_preview_rows

    @property
    def hit_record_preview_rows(self) -> tuple[TargetPreviewRow, ...]:
        """回傳命中紀錄 preview rows。"""

        return self.preview_presenter.hit_record_preview_rows

    @property
    def hit_records_heading(self) -> str:
        """回傳命中紀錄 preview 標題。"""

        return self.preview_presenter.hit_records_heading

    @property
    def display_name(self) -> str:
        """回傳 UI 顯示名稱。"""

        return self.identity_presenter.display_name

    @property
    def rename_display_name(self) -> str:
        """回傳更名 modal 的預填名稱。"""

        return self.identity_presenter.rename_value

    @property
    def thumbnail_url(self) -> str:
        """回傳 target header / sidebar 使用的社團縮圖 URL。"""

        result = sanitize_facebook_group_cover_image_url(
            self.target.group_cover_image_url
        )
        return result.url if result.ok else ""

    @property
    def mode_label(self) -> str:
        """回傳 target card header 使用的掃描模式文字。"""

        return self.header_presenter.mode_label

    @property
    def mode_class(self) -> str:
        """回傳掃描模式 chip 對應 CSS class。"""

        return self.header_presenter.mode_class

    @property
    def scanning_supported(self) -> bool:
        """回傳目前 target 是否已接上 worker 掃描流程。"""

        return self.monitoring_presenter.scanning_supported

    @property
    def status_presenter(self) -> TargetStatusPresenter:
        """回傳 target 狀態 presenter。"""

        return self.monitoring_presenter.status_presenter

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

        return self.header_presenter.header_summary_label

    @property
    def status_label(self) -> str:
        """回傳 target 啟停狀態文字。"""

        return self.monitoring_presenter.status_label

    @property
    def status_class(self) -> str:
        """回傳 target 狀態對應 CSS class。"""

        return self.monitoring_presenter.status_class

    @property
    def runtime_error(self) -> str:
        """回傳 runtime error 顯示文字。"""

        return self.error_presenter.runtime_error

    @property
    def runtime_skip_reason(self) -> str:
        """回傳最近一次 scan guard skip 原因。"""

        return self.error_presenter.runtime_skip_reason

    @property
    def latest_scan_header_time_label(self) -> str:
        """回傳 target header 使用的最近掃描短時間。"""

        return self.header_presenter.latest_scan_header_time_label

    @property
    def next_refresh_label(self) -> str:
        """回傳 target header 使用的下一次刷新狀態。"""

        return target_refresh.next_refresh_label(self)

    @property
    def next_refresh_seconds(self) -> int | None:
        """回傳前端本地倒數用的剩餘秒數；不可倒數時回傳 None。"""

        return target_refresh.next_refresh_seconds(self)

    @cached_property
    def next_refresh_display(self) -> NextRefreshDisplay:
        """一次產生下一次刷新顯示值，避免同一 row 重複計算倒數。"""

        return target_refresh.next_refresh_display(self)

    @property
    def scan_cycle_result_label(self) -> str:
        """回傳右側結果 panel 使用的最近一輪結束原因。"""

        return self.diagnostics_presenter.scan_cycle_result_label

    @property
    def latest_scan_diagnostics_summary(self) -> str:
        """回傳最近成功掃描的診斷短摘要。"""

        return self.diagnostics_presenter.latest_scan_diagnostics_summary

    @property
    def latest_scan_diagnostics_text(self) -> str:
        """回傳可複製的 scan-level diagnostics。"""

        return self.diagnostics_presenter.latest_scan_diagnostics_text

    @property
    def latest_error_label(self) -> str:
        """回傳最近錯誤時間。"""

        return self.error_presenter.latest_error_label

    @property
    def latest_failed_scan_summary(self) -> str:
        """回傳最近失敗掃描摘要。"""

        return self.error_presenter.latest_failed_scan_summary

    @property
    def latest_error_indicator_label(self) -> str:
        """回傳 target header 的最近錯誤短標籤。"""

        return self.error_presenter.latest_error_indicator_label

    @property
    def latest_error_indicator_title(self) -> str:
        """回傳 target header 最近錯誤說明。"""

        return self.error_presenter.latest_error_indicator_title

    @property
    def latest_error_indicator_kind(self) -> str:
        """回傳最近錯誤 UI 類型。"""

        return self.error_presenter.latest_error_indicator_kind

    @property
    def retrying_failure_current(self) -> bool:
        """回傳最近 failed scan 是否仍代表等待下輪重試的目前狀態。"""

        return self.error_presenter.retrying_failure_current

    @property
    def content_unavailable_current(self) -> bool:
        """回傳連結失效是否仍代表目前狀態。"""

        return self.error_presenter.content_unavailable_current

    @property
    def sidebar_item(self) -> SidebarTargetItem:
        """回傳 sidebar 使用的 target 摘要。"""

        return self.sidebar_presenter.item

    @property
    def monitoring_action(self) -> str:
        """回傳主操作按鈕應提交的 monitoring action。"""

        return self.monitoring_presenter.monitoring_action

    @property
    def monitoring_button_label(self) -> str:
        """回傳主操作按鈕文字，維持開始 / 暫停語義。"""

        return self.monitoring_presenter.monitoring_button_label
