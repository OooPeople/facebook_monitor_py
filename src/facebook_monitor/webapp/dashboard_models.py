"""Dashboard view models。

職責：整理 target card、sidebar 與設定摘要所需資料。
"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.webapp.dashboard_presenters import SettingsSummary
from facebook_monitor.webapp.dashboard_presenters import TargetCardSummary
from facebook_monitor.webapp.dashboard_presenters import TargetCardSummaryPresenter
from facebook_monitor.webapp.dashboard_presenters import TargetIdentityPresenter
from facebook_monitor.webapp.dashboard_presenters import TargetSettingsPresenter
from facebook_monitor.webapp.dashboard_presenters import TargetStatusPresenter
from facebook_monitor.webapp.diagnostics_presenter import build_scan_diagnostics_view
from facebook_monitor.webapp.diagnostics_presenter import format_datetime_for_ui
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
    hit_count: int
    latest_error_summary: str = ""
    thumbnail_url: str = ""
    active: bool = False


@dataclass(frozen=True)
class TargetRow:
    """保存 target 清單顯示所需資料。"""

    target: TargetDescriptor
    config: TargetConfig
    runtime_state: TargetRuntimeState
    latest_scan_run: ScanRun | None = None
    latest_failed_scan_run: ScanRun | None = None
    latest_notification_event: NotificationEvent | None = None
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
    def kind_label(self) -> str:
        """回傳 target 類型顯示文字。"""

        return TargetIdentityPresenter(self.target).kind_label

    @property
    def target_type_label(self) -> str:
        """回傳主畫面使用的 target 類型文字。"""

        return TargetIdentityPresenter(self.target).target_type_label

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
            target_type_label=self.target_type_label,
            status_label=self.status_label,
            settings=self.settings_presenter,
            latest_scan_run=self.latest_scan_run,
            latest_failed_scan_run=self.latest_failed_scan_run,
            latest_notification_event=self.latest_notification_event,
            hit_record_total_count=self.hit_record_total_count,
        )

    @property
    def target_identity_label(self) -> str:
        """回傳 target 的 group/post/scope 診斷摘要。"""

        if self.target.target_kind == TargetKind.COMMENTS:
            return (
                f"group={self.target.group_id} · "
                f"parent_post={self.target.parent_post_id or '(none)'} · "
                f"scope={self.target.scope_id}"
            )
        return f"group={self.target.group_id} · scope={self.target.scope_id}"

    @property
    def header_summary_label(self) -> str:
        """回傳 target header 的低干擾摘要，避免主畫面顯示診斷 ID。"""

        parts = [self.target_type_label, f"最近掃描 {self.latest_scan_label}"]
        if self.latest_notification_event:
            parts.append(f"最近通知 {self.latest_notification_event.channel.value}")
        else:
            parts.append("尚無通知")
        if self.latest_failed_scan_run:
            parts.append("最近有錯誤")
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
        return self.runtime_state.last_error

    @property
    def runtime_skip_reason(self) -> str:
        """回傳最近一次 scan guard skip 原因。"""

        return self.runtime_state.last_skip_reason

    @property
    def latest_scan_label(self) -> str:
        """回傳最近掃描完成時間。"""

        if not self.latest_scan_run:
            return "尚無掃描"
        return format_datetime_for_ui(self.latest_scan_run.finished_at)

    @property
    def latest_scan_diagnostics_summary(self) -> str:
        """回傳最近成功掃描的診斷短摘要。"""

        return build_scan_diagnostics_view(
            target=self.target,
            config=self.config,
            runtime_state=self.runtime_state,
            latest_scan_run=self.latest_scan_run,
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
    def latest_notification_label(self) -> str:
        """回傳最近通知通道狀態。"""

        if not self.latest_notification_event:
            return "尚無通知"
        event = self.latest_notification_event
        return (
            f"{event.channel.value}: {event.status.value} · "
            f"{format_datetime_for_ui(event.created_at)}"
            + (f" · {event.message}" if event.message else "")
        )

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
        if self.hit_record_total_count:
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
            hit_count=self.hit_record_total_count,
            latest_error_summary=latest_error_summary,
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
        """回傳主操作按鈕文字，對齊 userscript 開始 / 暫停語義。"""

        return "開始" if self.monitoring_action == "start" else "停止"
