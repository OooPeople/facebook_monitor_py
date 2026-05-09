"""Dashboard view models。

職責：整理 target card、sidebar 與設定摘要所需資料。
"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import is_generated_group_comments_name
from facebook_monitor.core.models import is_generated_group_posts_name
from facebook_monitor.facebook.route_detection import clean_facebook_page_title
from facebook_monitor.webapp.diagnostics_presenter import build_scan_diagnostics_view
from facebook_monitor.webapp.diagnostics_presenter import format_datetime_for_ui
from facebook_monitor.webapp.preview_models import HitRecordPreviewRow
from facebook_monitor.webapp.preview_models import LatestScanItemRow
from facebook_monitor.webapp.preview_models import TargetPreviewRow


@dataclass(frozen=True)
class SettingsSummary:
    """保存 target card 設定摘要。"""

    refresh_label: str
    max_items_label: str
    auto_load_more_label: str
    auto_sort_label: str
    notification_label: str

    @property
    def lines(self) -> tuple[str, ...]:
        """回傳可直接顯示的摘要列。"""

        return (
            f"刷新：{self.refresh_label}",
            f"目標掃描：{self.max_items_label}",
            f"載入更多：{self.auto_load_more_label}",
            f"排序：{self.auto_sort_label}",
            f"通知：{self.notification_label}",
        )


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
class TargetCardSummarySection:
    """保存收合卡片摘要單一欄位的標題與內容。"""

    label: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class TargetCardSummary:
    """保存 Phase 9 收合卡片摘要需要的穩定欄位。"""

    target_type_label: str
    status_label: str
    include_keywords_summary: str
    exclude_keywords_summary: str
    latest_scan_label: str
    hit_record_total_count: int
    refresh_label: str
    max_items_label: str
    notification_summary: str
    latest_error_summary: str = ""

    @property
    def sections(self) -> tuple[TargetCardSummarySection, ...]:
        """回傳收合卡片使用的欄位式摘要。"""

        return (
            TargetCardSummarySection(
                label="包含關鍵字",
                lines=(self.include_keywords_summary,),
            ),
            TargetCardSummarySection(
                label="排除關鍵字",
                lines=(self.exclude_keywords_summary,),
            ),
            TargetCardSummarySection(
                label="設定摘要",
                lines=(f"刷新 {self.refresh_label}", f"目標掃描 {self.max_items_label}"),
            ),
            TargetCardSummarySection(
                label="最近掃描",
                lines=(self.latest_scan_label,),
            ),
            TargetCardSummarySection(
                label="命中紀錄",
                lines=(f"{self.hit_record_total_count} 筆",),
            ),
        )

    @property
    def lines(self) -> tuple[str, ...]:
        """回傳舊版 partial update 相容用短句。"""

        return tuple(
            f"{section.label}：{' / '.join(section.lines)}"
            for section in self.sections
        )


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

        if self.target.target_kind == TargetKind.COMMENTS:
            if self.target.name and not is_generated_group_comments_name(
                self.target.name,
                self.target.group_id,
                self.target.parent_post_id,
            ):
                return clean_facebook_page_title(self.target.name)
            base_name = self.target.group_name or self.target.name
            return clean_facebook_page_title(base_name)
        if self.target.name and not is_generated_group_posts_name(
            self.target.name,
            self.target.group_id,
        ):
            return clean_facebook_page_title(self.target.name)
        return clean_facebook_page_title(self.target.group_name or self.target.name)

    @property
    def kind_label(self) -> str:
        """回傳 target 類型顯示文字。"""

        return "comments" if self.target.target_kind == TargetKind.COMMENTS else "posts"

    @property
    def target_type_label(self) -> str:
        """回傳主畫面使用的 target 類型文字。"""

        return "社團留言" if self.target.target_kind == TargetKind.COMMENTS else "社團貼文"

    @property
    def scanning_supported(self) -> bool:
        """回傳目前 target 是否已接上 worker 掃描流程。"""

        return self.target.target_kind in {TargetKind.POSTS, TargetKind.COMMENTS}

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

        if not self.target.enabled:
            return "停用"
        if self.target.paused:
            return "已停止"
        if not self.scanning_supported:
            return "尚未接上掃描"
        labels = {
            TargetRuntimeStatus.IDLE: "閒置",
            TargetRuntimeStatus.QUEUED: "排隊中",
            TargetRuntimeStatus.RUNNING: "執行中",
            TargetRuntimeStatus.ERROR: "錯誤",
        }
        return labels.get(self.runtime_state.runtime_status, "已啟用")

    @property
    def status_class(self) -> str:
        """回傳 target 狀態對應 CSS class。"""

        if not self.target.enabled:
            return "muted"
        if self.target.paused:
            return "stopped"
        if self.runtime_state.runtime_status == TargetRuntimeStatus.QUEUED:
            return "queued"
        if self.runtime_state.runtime_status == TargetRuntimeStatus.RUNNING:
            return "running"
        if self.runtime_state.runtime_status == TargetRuntimeStatus.ERROR:
            return "error"
        return "enabled"

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

        if not self.latest_failed_scan_run:
            return ""
        failed = self.latest_failed_scan_run
        return f"{format_datetime_for_ui(failed.finished_at)} · {failed.error_message}"

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

        channels: list[str] = []
        if self.config.enable_desktop_notification:
            channels.append("桌面")
        if self.config.enable_ntfy:
            channels.append("ntfy")
        if self.config.enable_discord_notification:
            channels.append("Discord")
        return " / ".join(channels) if channels else "關閉"

    @property
    def include_text(self) -> str:
        """回傳 include keywords 表單文字。"""

        return ", ".join(self.config.include_keywords)

    @property
    def exclude_text(self) -> str:
        """回傳 exclude keywords 表單文字。"""

        return ", ".join(self.config.exclude_keywords)

    @property
    def fixed_refresh_value(self) -> int:
        """回傳表單使用的固定掃描間隔秒數。"""

        return self.config.fixed_refresh_sec or PYTHON_TARGET_CONFIG_DEFAULTS.fixed_refresh_sec

    @property
    def refresh_mode(self) -> str:
        """回傳目前 refresh mode。"""

        if self.config.fixed_refresh_sec is None and self.config.jitter_enabled:
            return "floating"
        return "fixed"

    @property
    def refresh_mode_label(self) -> str:
        """回傳 refresh mode 摘要。"""

        if self.refresh_mode == "floating":
            min_seconds = min(self.config.min_refresh_sec, self.config.max_refresh_sec)
            max_seconds = max(self.config.min_refresh_sec, self.config.max_refresh_sec)
            return f"浮動 {min_seconds}-{max_seconds} 秒"
        return f"固定 {self.fixed_refresh_value} 秒"

    @property
    def settings_summary(self) -> SettingsSummary:
        """回傳 target card 設定摘要 view model。"""

        return SettingsSummary(
            refresh_label=self.refresh_mode_label,
            max_items_label=f"{self.config.max_items_per_scan} 筆",
            auto_load_more_label="開啟" if self.config.auto_load_more else "關閉",
            auto_sort_label="開啟" if self.config.auto_adjust_sort else "關閉",
            notification_label=self.notification_summary_label,
        )

    @property
    def card_summary(self) -> TargetCardSummary:
        """回傳 Phase 9 收合卡片可共用的摘要 view model。"""

        return TargetCardSummary(
            target_type_label=self.target_type_label,
            status_label=self.status_label,
            include_keywords_summary=self.include_text or "未設定",
            exclude_keywords_summary=self.exclude_text or "未設定",
            latest_scan_label=self.latest_scan_label,
            hit_record_total_count=self.hit_record_total_count,
            refresh_label=self.refresh_mode_label,
            max_items_label=self.settings_summary.max_items_label,
            notification_summary=self.notification_summary_label,
            latest_error_summary=self.latest_failed_scan_summary,
        )

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

        return self.config.min_refresh_sec or PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec

    @property
    def max_refresh_value(self) -> int:
        """回傳表單使用的浮動最大掃描間隔秒數。"""

        return self.config.max_refresh_sec or PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec

    @property
    def monitoring_action(self) -> str:
        """回傳主操作按鈕應提交的 monitoring action。"""

        return "start" if self.target.paused or not self.target.enabled else "stop"

    @property
    def monitoring_button_label(self) -> str:
        """回傳主操作按鈕文字，對齊 userscript 開始 / 暫停語義。"""

        return "開始" if self.monitoring_action == "start" else "停止"
