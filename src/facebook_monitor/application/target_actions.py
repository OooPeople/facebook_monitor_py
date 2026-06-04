"""Target 操作 use case。

職責：集中 Web UI 對單一 target 的開始、停止、刪除與立即掃描命令，
讓 route 只負責轉換 HTTP 表單、redirect 與 scheduler 喚醒。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext


@dataclass(frozen=True)
class TargetActionOutcome:
    """保存 target 操作結果與後續 scheduler side effect。"""

    ok: bool
    message: str
    feedback: str = ""
    wake_scheduler: bool = False
    start_scheduler: bool = False


def restart_target_monitoring_action(db_path: Path, target_id: str) -> TargetActionOutcome:
    """重新開始 target，清 seen/outbox 去重並要求下一輪掃描。"""

    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.restart_target_monitoring(target_id)
    return TargetActionOutcome(
        ok=True,
        message="target 已開始",
        feedback="target_started",
        wake_scheduler=True,
    )


def pause_target_monitoring_action(db_path: Path, target_id: str) -> TargetActionOutcome:
    """停止 target，保留 seen baseline 與歷史紀錄。"""

    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.pause_target_monitoring(target_id)
    return TargetActionOutcome(
        ok=True,
        message="target 已停止",
        feedback="target_stopped",
        wake_scheduler=True,
    )


def delete_target_action(db_path: Path, target_id: str) -> TargetActionOutcome:
    """刪除 target 與其 target-scoped 資料。"""

    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.delete_target(target_id)
    return TargetActionOutcome(
        ok=True,
        message="target 已刪除",
        feedback="target_deleted",
    )


def request_target_scan_once_action(db_path: Path, target_id: str) -> TargetActionOutcome:
    """對已開始的 target 排入一次 resident scan request。"""

    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.get(target_id)
        if target is None:
            return TargetActionOutcome(ok=False, message="掃描失敗：target 不存在")
        if not target.enabled or target.paused:
            return TargetActionOutcome(ok=False, message="掃描失敗：請先開始 target")
        app_context.services.targets.request_target_scan(target_id)
    return TargetActionOutcome(
        ok=True,
        message="已排入掃描",
        feedback="scan_requested",
        start_scheduler=True,
    )
