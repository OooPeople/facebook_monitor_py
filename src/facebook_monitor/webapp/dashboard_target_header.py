"""Target card header presenter。"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import TargetKind


@dataclass(frozen=True)
class TargetHeaderPresenter:
    """整理 target card header 的模式、時間與摘要標籤。"""

    target_kind: TargetKind
    latest_scan_run: ScanRun | None
    next_refresh_label: str
    latest_failed_scan_run: ScanRun | None = None
    latest_error_indicator_label: str = ""

    @property
    def mode_label(self) -> str:
        """回傳 target card header 使用的掃描模式文字。"""

        return "留言模式" if self.target_kind == TargetKind.COMMENTS else "貼文模式"

    @property
    def mode_class(self) -> str:
        """回傳掃描模式 chip 對應 CSS class。"""

        return "comments" if self.target_kind == TargetKind.COMMENTS else "posts"

    @property
    def latest_scan_header_time_label(self) -> str:
        """回傳 target header 使用的最近掃描短時間。"""

        if not self.latest_scan_run:
            return "尚無掃描"
        return self.latest_scan_run.finished_at.astimezone().strftime("%H:%M:%S")

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
