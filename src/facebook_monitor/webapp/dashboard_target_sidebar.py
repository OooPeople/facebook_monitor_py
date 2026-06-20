"""TargetRow sidebar item presenter helper。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


def sidebar_item(row: Any) -> SidebarTargetItem:
    """回傳 sidebar 使用的 target 摘要。"""

    base_status_summary = row.status_label
    if row.content_unavailable_current:
        status_detail = row.latest_error_indicator_label
    elif row.hit_record_total_count:
        status_detail = f"命中 {row.hit_record_total_count} 筆"
    elif not row.latest_scan_run:
        status_detail = "尚未掃描"
    else:
        status_detail = ""
    status_summary = (
        f"{base_status_summary} · {status_detail}"
        if status_detail
        else base_status_summary
    )
    latest_error_summary = row.latest_failed_scan_summary if row.latest_failed_scan_run else ""
    return SidebarTargetItem(
        target_id=row.target_id,
        display_name=row.display_name,
        anchor_id=row.anchor_id,
        base_status_summary=base_status_summary,
        status_class=row.status_class,
        status_detail=status_detail,
        status_summary=status_summary,
        mode_label="留言" if row.target.target_kind == TargetKind.COMMENTS else "貼文",
        mode_class=row.mode_class,
        hit_count=row.hit_record_total_count,
        latest_error_summary=latest_error_summary,
        thumbnail_url=row.thumbnail_url,
        active=row.target.enabled and not row.target.paused,
    )


__all__ = [
    "SidebarTargetItem",
    "sidebar_item",
]
