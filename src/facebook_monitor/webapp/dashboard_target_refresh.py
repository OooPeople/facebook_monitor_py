"""TargetRow 下一次刷新倒數 presenter helper。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from math import ceil
from typing import Any

from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.refresh_policy import resolve_refresh_interval_seconds


@dataclass(frozen=True)
class NextRefreshDisplay:
    """保存下一次刷新在 UI 顯示與前端倒數校準所需資料。"""

    label: str
    seconds: int | None = None


def next_refresh_label(row: Any) -> str:
    """回傳 target header 使用的下一次刷新狀態。"""

    return row.next_refresh_display.label


def next_refresh_seconds(row: Any) -> int | None:
    """回傳前端本地倒數用的剩餘秒數；不可倒數時回傳 None。"""

    return row.next_refresh_display.seconds


def next_refresh_display(row: Any) -> NextRefreshDisplay:
    """一次產生下一次刷新顯示值，避免同一 row 重複計算倒數。"""

    if (
        not row.target.enabled
        or row.target.paused
        or row.runtime_state.desired_state != TargetDesiredState.ACTIVE
    ):
        return NextRefreshDisplay(label="未排程")
    if row.runtime_state.runtime_status == TargetRuntimeStatus.ERROR:
        return NextRefreshDisplay(label="未排程")
    if row.runtime_state.runtime_status == TargetRuntimeStatus.QUEUED:
        return NextRefreshDisplay(label="排隊中")
    if row.runtime_state.runtime_status == TargetRuntimeStatus.RUNNING:
        return NextRefreshDisplay(label="掃描中")
    if row.runtime_state.scan_requested_at is not None:
        return NextRefreshDisplay(label="即將刷新")
    remaining_seconds = next_refresh_remaining_seconds(row)
    if remaining_seconds is None or remaining_seconds <= 0:
        return NextRefreshDisplay(label="即將刷新")
    return NextRefreshDisplay(
        label=format_countdown_seconds(remaining_seconds),
        seconds=remaining_seconds,
    )


def next_refresh_remaining_seconds(row: Any) -> int | None:
    """依後端目前排程狀態計算下一次刷新剩餘秒數。"""

    if row.runtime_state.display_next_due_at is not None:
        remaining_seconds = ceil(
            (row.runtime_state.display_next_due_at - utc_now()).total_seconds()
        )
        return max(remaining_seconds, 0)

    last_reference_at = row.runtime_state.last_started_at
    if last_reference_at is None and row.latest_scan_run:
        last_reference_at = row.latest_scan_run.finished_at
    if last_reference_at is None:
        return None
    interval_seconds = resolve_refresh_interval_seconds(
        config=row.config,
        default_interval_seconds=row.settings_presenter.fixed_refresh_value,
        target_id=row.target_id,
        latest_finished_at=row.latest_scan_run.finished_at if row.latest_scan_run else None,
    )
    due_at = last_reference_at + timedelta(seconds=max(interval_seconds, 1))
    remaining_seconds = ceil((due_at - utc_now()).total_seconds())
    if remaining_seconds <= 0:
        return 0
    return remaining_seconds


def format_countdown_seconds(seconds: int) -> str:
    """格式化 header 的下一次刷新倒數。"""

    bounded_seconds = max(int(seconds), 0)
    if bounded_seconds < 60:
        return f"{bounded_seconds}s"
    minutes, remainder = divmod(bounded_seconds, 60)
    if minutes < 60:
        return f"{minutes}m {remainder}s" if remainder else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


__all__ = [
    "NextRefreshDisplay",
    "format_countdown_seconds",
    "next_refresh_display",
    "next_refresh_label",
    "next_refresh_remaining_seconds",
    "next_refresh_seconds",
]
