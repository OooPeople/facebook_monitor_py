"""TargetRow 狀態與主操作 presenter helper。"""

from __future__ import annotations

from typing import Any

from facebook_monitor.core.models import TargetKind
from facebook_monitor.webapp.dashboard_presenters import TargetStatusPresenter


def scanning_supported(row: Any) -> bool:
    """回傳目前 target 是否已接上 worker 掃描流程。"""

    return row.target.target_kind in {TargetKind.POSTS, TargetKind.COMMENTS}


def status_presenter(row: Any) -> TargetStatusPresenter:
    """建立 target 狀態 presenter。"""

    return TargetStatusPresenter(
        target=row.target,
        runtime_state=row.runtime_state,
        scanning_supported=scanning_supported(row),
    )


def status_label(row: Any) -> str:
    """回傳 target 啟停狀態文字。"""

    return row.status_presenter.label


def status_class(row: Any) -> str:
    """回傳 target 狀態對應 CSS class。"""

    return row.status_presenter.css_class


def monitoring_action(row: Any) -> str:
    """回傳主操作按鈕應提交的 monitoring action。"""

    return "start" if row.target.paused or not row.target.enabled else "stop"


def monitoring_button_label(row: Any) -> str:
    """回傳主操作按鈕文字，維持開始 / 暫停語義。"""

    return "開始" if monitoring_action(row) == "start" else "停止"


__all__ = [
    "monitoring_action",
    "monitoring_button_label",
    "scanning_supported",
    "status_class",
    "status_label",
    "status_presenter",
]
