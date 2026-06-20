"""TargetRow sidebar item presenter helper。"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind


@dataclass(frozen=True)
class SidebarTargetItem:
    """保存 sidebar 需要的 target 摘要資料。"""

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
class TargetSidebarPresenter:
    """整理 sidebar 使用的 target 摘要。"""

    target: TargetDescriptor
    target_id: str
    display_name: str
    anchor_id: str
    status_label: str
    status_class: str
    mode_class: str
    hit_record_total_count: int
    latest_scan_run: ScanRun | None = None
    latest_failed_scan_run: ScanRun | None = None
    latest_failed_scan_summary: str = ""
    latest_error_indicator_label: str = ""
    content_unavailable_current: bool = False
    thumbnail_url: str = ""

    @property
    def status_detail(self) -> str:
        """回傳 sidebar 第二層狀態細節。"""

        if self.content_unavailable_current:
            return self.latest_error_indicator_label
        if self.hit_record_total_count:
            return f"命中 {self.hit_record_total_count} 筆"
        if not self.latest_scan_run:
            return "尚未掃描"
        return ""

    @property
    def status_summary(self) -> str:
        """回傳 sidebar 完整狀態摘要。"""

        return (
            f"{self.status_label} · {self.status_detail}"
            if self.status_detail
            else self.status_label
        )

    @property
    def latest_error_summary(self) -> str:
        """回傳 sidebar 最近錯誤摘要。"""

        return self.latest_failed_scan_summary if self.latest_failed_scan_run else ""

    @property
    def item(self) -> SidebarTargetItem:
        """回傳 sidebar 使用的 target 摘要。"""

        return SidebarTargetItem(
            target_id=self.target_id,
            display_name=self.display_name,
            anchor_id=self.anchor_id,
            base_status_summary=self.status_label,
            status_class=self.status_class,
            status_detail=self.status_detail,
            status_summary=self.status_summary,
            mode_label="留言" if self.target.target_kind == TargetKind.COMMENTS else "貼文",
            mode_class=self.mode_class,
            hit_count=self.hit_record_total_count,
            latest_error_summary=self.latest_error_summary,
            thumbnail_url=self.thumbnail_url,
            active=self.target.enabled and not self.target.paused,
        )


__all__ = [
    "SidebarTargetItem",
    "TargetSidebarPresenter",
]
