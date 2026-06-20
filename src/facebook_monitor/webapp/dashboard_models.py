"""Dashboard view models。

職責：整理 target card、sidebar 與設定摘要所需資料。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.external_url_policy import sanitize_facebook_group_cover_image_url
from facebook_monitor.core.models import NotificationOutboxSummary
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.sidebar_models import SidebarGroupConfigTemplate
from facebook_monitor.webapp.dashboard_presenters import SettingsSummary
from facebook_monitor.webapp.dashboard_presenters import TargetCardSummary
from facebook_monitor.webapp.dashboard_presenters import TargetCardSummaryPresenter
from facebook_monitor.webapp.dashboard_presenters import TargetIdentityPresenter
from facebook_monitor.webapp.dashboard_presenters import TargetSettingsPresenter
from facebook_monitor.webapp.dashboard_presenters import TargetStatusPresenter
import facebook_monitor.webapp.dashboard_target_diagnostics as target_diagnostics
import facebook_monitor.webapp.dashboard_target_errors as target_errors
from facebook_monitor.webapp.dashboard_target_refresh import NextRefreshDisplay
import facebook_monitor.webapp.dashboard_target_refresh as target_refresh
from facebook_monitor.webapp.dashboard_target_sidebar import SidebarTargetItem
import facebook_monitor.webapp.dashboard_target_sidebar as target_sidebar
import facebook_monitor.webapp.dashboard_target_status as target_status
from facebook_monitor.webapp.form_refresh import FLOATING_REFRESH_MODE
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
    def include_text_2(self) -> str:
        """回傳 template include keyword 第 2 組表單文字。"""

        presenter = self.template_presenter
        return presenter.include_text_2 if presenter else ""

    @property
    def include_text_3(self) -> str:
        """回傳 template include keyword 第 3 組表單文字。"""

        presenter = self.template_presenter
        return presenter.include_text_3 if presenter else ""

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

        result = sanitize_facebook_group_cover_image_url(
            self.target.group_cover_image_url
        )
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

        return target_status.scanning_supported(self)

    @property
    def status_presenter(self) -> TargetStatusPresenter:
        """回傳 target 狀態 presenter。"""

        return target_status.status_presenter(self)

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

        return target_status.status_label(self)

    @property
    def status_class(self) -> str:
        """回傳 target 狀態對應 CSS class。"""

        return target_status.status_class(self)

    @property
    def runtime_error(self) -> str:
        """回傳 runtime error 顯示文字。"""

        return target_errors.runtime_error(self)

    @property
    def runtime_skip_reason(self) -> str:
        """回傳最近一次 scan guard skip 原因。"""

        return target_errors.runtime_skip_reason(self)

    @property
    def latest_scan_header_time_label(self) -> str:
        """回傳 target header 使用的最近掃描短時間。"""

        if not self.latest_scan_run:
            return "尚無掃描"
        return self.latest_scan_run.finished_at.astimezone().strftime("%H:%M:%S")

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

        return target_diagnostics.scan_cycle_result_label(self)

    @property
    def latest_scan_diagnostics_summary(self) -> str:
        """回傳最近成功掃描的診斷短摘要。"""

        return target_diagnostics.latest_scan_diagnostics_summary(self)

    @property
    def latest_scan_diagnostics_text(self) -> str:
        """回傳可複製的 scan-level diagnostics。"""

        return target_diagnostics.latest_scan_diagnostics_text(self)

    @property
    def latest_error_label(self) -> str:
        """回傳最近錯誤時間。"""

        return target_errors.latest_error_label(self)

    @property
    def latest_failed_scan_summary(self) -> str:
        """回傳最近失敗掃描摘要。"""

        return target_errors.latest_failed_scan_summary(self)

    @property
    def latest_error_indicator_label(self) -> str:
        """回傳 target header 的最近錯誤短標籤。"""

        return target_errors.latest_error_indicator_label(self)

    @property
    def latest_error_indicator_title(self) -> str:
        """回傳 target header 最近錯誤說明。"""

        return target_errors.latest_error_indicator_title(self)

    @property
    def latest_error_indicator_kind(self) -> str:
        """回傳最近錯誤 UI 類型。"""

        return target_errors.latest_error_indicator_kind(self)

    @property
    def retrying_failure_current(self) -> bool:
        """回傳最近 failed scan 是否仍代表等待下輪重試的目前狀態。"""

        return target_errors.retrying_failure_current(self)

    @property
    def content_unavailable_current(self) -> bool:
        """回傳連結失效是否仍代表目前狀態。"""

        return target_errors.content_unavailable_current(self)

    @property
    def notification_summary_label(self) -> str:
        """回傳設定摘要用的通知通道列表。"""

        return self.settings_presenter.notification_summary_label

    @property
    def include_text(self) -> str:
        """回傳 include keywords 表單文字。"""

        return self.settings_presenter.include_text

    @property
    def include_text_2(self) -> str:
        """回傳 include keyword 第 2 組表單文字。"""

        return self.settings_presenter.include_text_2

    @property
    def include_text_3(self) -> str:
        """回傳 include keyword 第 3 組表單文字。"""

        return self.settings_presenter.include_text_3

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
        """回傳收合卡片可共用的摘要 view model。"""

        return self.card_summary_presenter.summary

    @property
    def sidebar_item(self) -> SidebarTargetItem:
        """回傳 sidebar 使用的 target 摘要。"""

        return target_sidebar.sidebar_item(self)

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

        return target_status.monitoring_action(self)

    @property
    def monitoring_button_label(self) -> str:
        """回傳主操作按鈕文字，維持開始 / 暫停語義。"""

        return target_status.monitoring_button_label(self)
