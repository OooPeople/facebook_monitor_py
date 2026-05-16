"""Dashboard presenter helpers。

職責：把 target 狀態、設定摘要與收合卡片摘要從 `TargetRow` 拆出，
讓 dashboard row 維持資料聚合角色。
"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import generated_group_comments_display_name
from facebook_monitor.core.models import is_generated_group_comments_name
from facebook_monitor.core.models import is_generated_group_posts_name
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.facebook.route_detection import clean_facebook_page_title
from facebook_monitor.webapp.diagnostics_presenter import format_datetime_for_ui
from facebook_monitor.webapp.notification_presenters import format_notification_channel_label

PENDING_TARGET_DISPLAY_NAME = "抓取社團名稱中，請稍後"
FAILED_TARGET_DISPLAY_NAME = "無法自動抓取名稱，請手動更改名稱"
EMPTY_INCLUDE_KEYWORDS_LABEL = "目前沒有關鍵字"


@dataclass(frozen=True)
class SettingsSummaryLine:
    """保存設定摘要單列結構，避免 template 用字串切割推論欄位。"""

    icon_key: str
    label: str
    value: str


@dataclass(frozen=True)
class SettingsSummary:
    """保存 target card 設定摘要。"""

    lines: tuple[SettingsSummaryLine, ...]

    @property
    def max_items_label(self) -> str:
        """回傳收合摘要沿用的目標掃描數。"""

        for line in self.lines:
            if line.icon_key == "target":
                return line.value
        return ""


@dataclass(frozen=True)
class TargetCardSummarySection:
    """保存收合卡片摘要單一欄位的標題與內容。"""

    icon_key: str
    label: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class TargetCardSummary:
    """保存收合卡片摘要需要的穩定欄位。"""

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
                icon_key="keyword",
                label="關鍵字",
                lines=(
                    f"包含: {self.include_keywords_summary}",
                    f"排除: {self.exclude_keywords_summary}",
                ),
            ),
            TargetCardSummarySection(
                icon_key="settings",
                label="設定摘要",
                lines=(f"{self.refresh_label} · {self.max_items_label}",),
            ),
            TargetCardSummarySection(
                icon_key="scan",
                label="最近掃描",
                lines=(self.latest_scan_label,),
            ),
            TargetCardSummarySection(
                icon_key="hit",
                label="命中紀錄",
                lines=(f"命中 {self.hit_record_total_count} 筆",),
            ),
        )

    @property
    def lines(self) -> tuple[str, ...]:
        """回傳摘要純文字，供非 HTML 顯示面使用。"""

        return tuple(
            f"{section.label}：{' / '.join(section.lines)}"
            for section in self.sections
        )


@dataclass(frozen=True)
class TargetStatusPresenter:
    """整理 target 啟停與 runtime 狀態顯示。"""

    target: TargetDescriptor
    runtime_state: TargetRuntimeState
    scanning_supported: bool

    @property
    def label(self) -> str:
        """回傳 target 啟停狀態文字。"""

        if not self.target.enabled:
            return "停用"
        if self.target.paused:
            return "已停止"
        if not self.scanning_supported:
            return "尚未接上掃描"
        labels = {
            TargetRuntimeStatus.IDLE: "已啟用",
            TargetRuntimeStatus.QUEUED: "排隊中",
            TargetRuntimeStatus.RUNNING: "掃描中",
            TargetRuntimeStatus.ERROR: "錯誤",
        }
        return labels.get(self.runtime_state.runtime_status, "已啟用")

    @property
    def css_class(self) -> str:
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


@dataclass(frozen=True)
class TargetIdentityPresenter:
    """整理 target 顯示名稱與類型 label。"""

    target: TargetDescriptor

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
            if self.target.group_name:
                return clean_facebook_page_title(
                    generated_group_comments_display_name(
                        self.target.group_name,
                        self.target.parent_post_id,
                    )
                )
            return self._metadata_fallback_display_name()
        if self.target.name and not is_generated_group_posts_name(
            self.target.name,
            self.target.group_id,
        ):
            return clean_facebook_page_title(self.target.name)
        if self.target.group_name:
            return clean_facebook_page_title(self.target.group_name)
        return self._metadata_fallback_display_name()

    @property
    def rename_value(self) -> str:
        """回傳更名 modal 預填值；metadata 未完成時不回填系統 fallback。"""

        display_name = self.display_name
        if display_name in {PENDING_TARGET_DISPLAY_NAME, FAILED_TARGET_DISPLAY_NAME}:
            return ""
        return display_name

    def _metadata_fallback_display_name(self) -> str:
        """依 target metadata 狀態顯示 fallback 名稱文案。"""

        if self.target.metadata_status == TargetMetadataStatus.FAILED:
            return FAILED_TARGET_DISPLAY_NAME
        return PENDING_TARGET_DISPLAY_NAME

    @property
    def kind_label(self) -> str:
        """回傳 target 類型診斷文字。"""

        return "comments" if self.target.target_kind == TargetKind.COMMENTS else "posts"

    @property
    def target_type_label(self) -> str:
        """回傳主畫面使用的 target 類型文字。"""

        return "社團留言" if self.target.target_kind == TargetKind.COMMENTS else "社團貼文"


@dataclass(frozen=True)
class TargetSettingsPresenter:
    """整理 target 設定值的表單文字與摘要。"""

    config: TargetConfig

    @property
    def include_text(self) -> str:
        """回傳 include keywords 表單文字。"""

        return ";".join(self.config.include_keywords)

    @property
    def exclude_text(self) -> str:
        """回傳 exclude keywords 表單文字。"""

        return ";".join(self.config.exclude_keywords)

    @property
    def exclude_ignore_phrases_text(self) -> str:
        """回傳排除字忽略片語表單文字。"""

        return ";".join(self.config.exclude_ignore_phrases)

    @property
    def fixed_refresh_value(self) -> int:
        """回傳表單使用的固定掃描間隔秒數。"""

        return (
            self.config.fixed_refresh_sec
            or PYTHON_TARGET_CONFIG_DEFAULTS.default_fixed_refresh_sec
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
    def notification_summary_label(self) -> str:
        """回傳設定摘要用的通知通道列表。"""

        channels: list[str] = []
        if self.config.enable_desktop_notification:
            channels.append(format_notification_channel_label(NotificationChannel.DESKTOP))
        if self.config.enable_ntfy:
            channels.append(format_notification_channel_label(NotificationChannel.NTFY))
        if self.config.enable_discord_notification:
            channels.append(format_notification_channel_label(NotificationChannel.DISCORD))
        return " / ".join(channels) if channels else "關閉"

    @property
    def settings_summary(self) -> SettingsSummary:
        """回傳 target card 設定摘要 view model。"""

        return SettingsSummary(
            lines=(
                SettingsSummaryLine("refresh", "刷新", self.refresh_mode_label),
                SettingsSummaryLine("target", "目標掃描", f"{self.config.max_items_per_scan} 筆"),
                SettingsSummaryLine(
                    "load_more",
                    "載入更多",
                    "開啟" if self.config.auto_load_more else "關閉",
                ),
                SettingsSummaryLine(
                    "sort",
                    "最新排序",
                    "開啟" if self.config.auto_adjust_sort else "關閉",
                ),
                SettingsSummaryLine("notification", "通知", self.notification_summary_label),
            ),
        )


@dataclass(frozen=True)
class TargetCardSummaryPresenter:
    """整理收合卡片摘要。"""

    target_type_label: str
    status_label: str
    settings: TargetSettingsPresenter
    latest_scan_run: ScanRun | None
    latest_failed_scan_run: ScanRun | None
    hit_record_total_count: int
    latest_notification_event: NotificationEvent | None = None

    @property
    def latest_scan_label(self) -> str:
        """回傳最近掃描完成時間。"""

        if not self.latest_scan_run:
            return "尚無掃描"
        return format_datetime_for_ui(self.latest_scan_run.finished_at)

    @property
    def latest_failed_scan_summary(self) -> str:
        """回傳最近失敗掃描摘要。"""

        if not self.latest_failed_scan_run:
            return ""
        failed = self.latest_failed_scan_run
        return f"{format_datetime_for_ui(failed.finished_at)} · {failed.error_message}"

    @property
    def summary(self) -> TargetCardSummary:
        """回傳收合卡片可共用的摘要 view model。"""

        return TargetCardSummary(
            target_type_label=self.target_type_label,
            status_label=self.status_label,
            include_keywords_summary=self.settings.include_text or EMPTY_INCLUDE_KEYWORDS_LABEL,
            exclude_keywords_summary=self.settings.exclude_text or "未設定",
            latest_scan_label=self.latest_scan_label,
            hit_record_total_count=self.hit_record_total_count,
            refresh_label=self.settings.refresh_mode_label,
            max_items_label=self.settings.settings_summary.max_items_label,
            notification_summary=self.settings.notification_summary_label,
            latest_error_summary=self.latest_failed_scan_summary,
        )
