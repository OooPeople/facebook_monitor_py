"""Dashboard presenter helpers。

職責：把 target 狀態、設定摘要與收合卡片摘要從 `TargetRow` 拆出，
讓 dashboard row 維持資料聚合角色。
"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.keyword_groups import legacy_include_keyword_groups
from facebook_monitor.core.keyword_groups import normalize_include_keyword_groups
from facebook_monitor.core.notification_channels import NOTIFICATION_CHANNEL_DEFINITIONS
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
from facebook_monitor.core.scan_failures import CONTENT_UNAVAILABLE_REASON
from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.core.user_messages import split_coded_message
from facebook_monitor.facebook.route_detection import clean_facebook_page_title
from facebook_monitor.webapp.diagnostics_presenter import format_datetime_for_ui
from facebook_monitor.webapp.form_models import FIXED_REFRESH_MODE
from facebook_monitor.webapp.form_models import FLOATING_REFRESH_MODE
from facebook_monitor.webapp.notification_presenters import format_notification_channel_label

PENDING_TARGET_DISPLAY_NAME = "抓取社團名稱中，請稍後"
FAILED_TARGET_DISPLAY_NAME = "無法自動抓取名稱，請手動更改名稱"
EMPTY_INCLUDE_KEYWORDS_LABEL = "目前沒有關鍵字"
CONTENT_UNAVAILABLE_LABEL = "連結已失效"
CONTENT_UNAVAILABLE_TITLE = "Facebook 顯示目前無法查看此內容，可能已刪除或權限變更。"
CONTENT_UNAVAILABLE_ERROR_MESSAGE = (
    "連結已失效：Facebook 顯示目前無法查看此內容，可能已刪除或權限變更。"
)
CONTENT_UNAVAILABLE_HISTORY_MESSAGE = (
    "曾偵測到連結失效：Facebook 顯示目前無法查看此內容，可能已刪除或權限變更。"
)


def is_content_unavailable_scan(scan: ScanRun | None) -> bool:
    """判斷 failed scan 是否代表 Facebook 內容不可見。"""

    if scan is None:
        return False
    metadata = scan.metadata or {}
    return (
        metadata.get("reason") == CONTENT_UNAVAILABLE_REASON
        or scan.error_message.startswith(f"{CONTENT_UNAVAILABLE_REASON}:")
        or scan.error_message.startswith(f"{CONTENT_UNAVAILABLE_LABEL}：")
    )


def is_content_unavailable_runtime_error(value: str) -> bool:
    """判斷 runtime error 是否代表 Facebook 內容不可見。"""

    code, _detail = split_coded_message(value)
    return code == CONTENT_UNAVAILABLE_REASON or value.startswith(
        f"{CONTENT_UNAVAILABLE_LABEL}："
    )


def is_retrying_failure_scan(scan: ScanRun | None) -> bool:
    """判斷 failed scan 是否為未達上限、將於下輪重試的失敗。"""

    if scan is None:
        return False
    metadata = scan.metadata or {}
    return bool(metadata.get("retryable")) and metadata.get("runtime_action") == "will_retry"


def format_retrying_failure_title(scan: ScanRun) -> str:
    """格式化可重試 failed scan 的 hover 說明。"""

    metadata = scan.metadata or {}
    retry_streak = metadata.get("retry_streak")
    retry_limit = metadata.get("retry_limit")
    if retry_streak and retry_limit:
        prefix = f"本輪掃描失敗，將於下輪重試（{retry_streak}/{retry_limit}）"
    else:
        prefix = "本輪掃描失敗，將於下輪重試"
    detail = format_failure_message_text(scan.error_message)
    return f"{prefix}：{detail}" if detail else prefix


def format_latest_error_indicator_label(
    scan: ScanRun | None,
    *,
    content_unavailable_current: bool | None = None,
    retrying_current: bool = False,
) -> str:
    """回傳 target header 使用的最近錯誤短標籤。"""

    if scan is None:
        return ""
    if retrying_current:
        return "將重試"
    current = (
        is_content_unavailable_scan(scan)
        if content_unavailable_current is None
        else content_unavailable_current
    )
    if current:
        return CONTENT_UNAVAILABLE_LABEL
    return "最近有錯誤"


def format_latest_error_indicator_title(
    scan: ScanRun | None,
    *,
    content_unavailable_current: bool | None = None,
    retrying_current: bool = False,
) -> str:
    """回傳 target header 最近錯誤的 hover 說明。"""

    if scan is None:
        return ""
    if retrying_current:
        return format_retrying_failure_title(scan)
    current = (
        is_content_unavailable_scan(scan)
        if content_unavailable_current is None
        else content_unavailable_current
    )
    if current:
        return CONTENT_UNAVAILABLE_TITLE
    return format_failure_message_text(scan.error_message)


def format_runtime_error_message(value: str) -> str:
    """把 runtime error 轉成使用者可讀訊息。"""

    if is_content_unavailable_runtime_error(value):
        return CONTENT_UNAVAILABLE_ERROR_MESSAGE
    return format_failure_message_text(value)


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

    include_keywords_summary: str
    exclude_keywords_summary: str
    latest_scan_label: str
    hit_record_total_count: int
    refresh_label: str
    max_items_label: str
    latest_error_summary: str = ""

    @property
    def sections(self) -> tuple[TargetCardSummarySection, ...]:
        """回傳收合卡片使用的欄位式摘要。"""

        scan_lines = [self.latest_scan_label]
        if self.latest_error_summary:
            scan_lines.append(self.latest_error_summary)
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
                lines=tuple(scan_lines),
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

@dataclass(frozen=True)
class TargetSettingsPresenter:
    """整理 target 設定值的表單文字與摘要。"""

    config: TargetConfig

    @property
    def include_text(self) -> str:
        """回傳 include keywords 表單文字。"""

        return self.include_group_texts[0] if self.include_group_texts else ""

    @property
    def include_text_2(self) -> str:
        """回傳 include keyword 第 2 組表單文字。"""

        return self.include_group_texts[1] if len(self.include_group_texts) > 1 else ""

    @property
    def include_text_3(self) -> str:
        """回傳 include keyword 第 3 組表單文字。"""

        return self.include_group_texts[2] if len(self.include_group_texts) > 2 else ""

    @property
    def include_group_texts(self) -> tuple[str, ...]:
        """回傳固定 include group slots 的表單文字。"""

        groups = normalize_include_keyword_groups(
            self.config.include_keyword_groups,
            fill_empty_slots=True,
        )
        if not any(group.keywords for group in groups) and self.config.include_keywords:
            groups = legacy_include_keyword_groups(
                self.config.include_keywords,
                fill_empty_slots=True,
            )
        return tuple(";".join(group.keywords) for group in groups)

    @property
    def include_summary_label(self) -> str:
        """回傳收合摘要使用的 include keyword 短文字。"""

        non_empty_groups = [
            text for text in self.include_group_texts if text
        ]
        if not non_empty_groups:
            return EMPTY_INCLUDE_KEYWORDS_LABEL
        keyword_count = sum(
            len(tuple(keyword for keyword in text.split(";") if keyword.strip()))
            for text in non_empty_groups
        )
        if len(non_empty_groups) == 1:
            return non_empty_groups[0]
        return f"{len(non_empty_groups)} 組 / {keyword_count} 條"

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
            return FLOATING_REFRESH_MODE
        return FIXED_REFRESH_MODE

    @property
    def refresh_mode_label(self) -> str:
        """回傳 refresh mode 摘要。"""

        if self.refresh_mode == FLOATING_REFRESH_MODE:
            min_seconds = min(self.config.min_refresh_sec, self.config.max_refresh_sec)
            max_seconds = max(self.config.min_refresh_sec, self.config.max_refresh_sec)
            return f"浮動 {min_seconds}-{max_seconds} 秒"
        return f"固定 {self.fixed_refresh_value} 秒"

    @property
    def notification_summary_label(self) -> str:
        """回傳設定摘要用的通知通道列表。"""

        channels = [
            format_notification_channel_label(definition.channel)
            for definition in NOTIFICATION_CHANNEL_DEFINITIONS
            if getattr(self.config, definition.enabled_field)
        ]
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

    settings: TargetSettingsPresenter
    latest_scan_run: ScanRun | None
    latest_failed_scan_run: ScanRun | None
    hit_record_total_count: int
    content_unavailable_current: bool = False

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
        if (
            is_content_unavailable_scan(self.latest_failed_scan_run)
            and self.content_unavailable_current
        ):
            return CONTENT_UNAVAILABLE_LABEL
        if is_content_unavailable_scan(self.latest_failed_scan_run):
            return (
                f"{format_datetime_for_ui(self.latest_failed_scan_run.finished_at)} · "
                f"{CONTENT_UNAVAILABLE_HISTORY_MESSAGE}"
            )
        failed = self.latest_failed_scan_run
        return (
            f"{format_datetime_for_ui(failed.finished_at)} · "
            f"{format_failure_message_text(failed.error_message)}"
        )

    @property
    def summary(self) -> TargetCardSummary:
        """回傳收合卡片可共用的摘要 view model。"""

        return TargetCardSummary(
            include_keywords_summary=self.settings.include_summary_label,
            exclude_keywords_summary=self.settings.exclude_text or "未設定",
            latest_scan_label=self.latest_scan_label,
            hit_record_total_count=self.hit_record_total_count,
            refresh_label=self.settings.refresh_mode_label,
            max_items_label=self.settings.settings_summary.max_items_label,
            latest_error_summary=self.latest_failed_scan_summary,
        )
