"""Dashboard collapsed target card summary presenters。"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.models import ScanRun
from facebook_monitor.webapp.dashboard_error_presenters import (
    format_latest_failed_scan_summary,
)
from facebook_monitor.webapp.dashboard_settings_presenters import TargetSettingsPresenter
from facebook_monitor.webapp.time_presenters import format_datetime_for_ui


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

        return format_latest_failed_scan_summary(
            self.latest_failed_scan_run,
            content_unavailable_current=self.content_unavailable_current,
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
