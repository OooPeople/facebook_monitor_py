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
    updated_count: int = 0


def restart_target_monitoring_action(db_path: Path, target_id: str) -> TargetActionOutcome:
    """開始 target，保留 seen/outbox 並要求下一輪掃描。"""

    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.restart_target_monitoring(target_id)
    return TargetActionOutcome(
        ok=True,
        message="target 已開始",
        feedback="target_started",
        wake_scheduler=True,
    )


def reset_target_notification_state_action(
    db_path: Path,
    target_id: str,
) -> TargetActionOutcome:
    """重置單一 target 的通知與 seen 去重狀態，不喚醒 scheduler。"""

    with SqliteApplicationContext(db_path) as app_context:
        result = app_context.services.targets.reset_target_notification_state(target_id)
    return TargetActionOutcome(
        ok=True,
        message=(
            f"已重置通知狀態：清除通知紀錄 {result.notification_outbox_rows} 筆、"
            f"已看紀錄 {result.seen_items} 筆"
        ),
        feedback="notification_state_reset",
        updated_count=result.total_rows,
    )


def clear_target_hit_records_action(db_path: Path, target_id: str) -> TargetActionOutcome:
    """清空單一 target 的命中紀錄，讓寫入語義留在 application layer。"""

    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.get(target_id)
        if target is None:
            return TargetActionOutcome(ok=False, message="target not found")
        deleted_count = app_context.repositories.match_history.clear_by_target(target.id)
    return TargetActionOutcome(
        ok=True,
        message="hit records cleared",
        feedback="hit_records_cleared",
        updated_count=deleted_count,
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


def restart_sidebar_group_monitoring_action(
    db_path: Path,
    group_id: str,
) -> TargetActionOutcome:
    """開始 sidebar group 內所有 targets，沿用單 target 開始語義。"""

    with SqliteApplicationContext(db_path) as app_context:
        if app_context.repositories.sidebar_layout.get_group(group_id) is None:
            raise ValueError("找不到指定的 sidebar 群組")
        target_ids = app_context.repositories.sidebar_layout.list_target_ids_for_group(group_id)
        for target_id in target_ids:
            app_context.services.targets.restart_target_monitoring(target_id)
    count = len(target_ids)
    return TargetActionOutcome(
        ok=True,
        message=f"已開始群組內 {count} 個 target" if count else "群組內沒有 target",
        feedback="sidebar_group_started",
        wake_scheduler=count > 0,
        updated_count=count,
    )


def pause_sidebar_group_monitoring_action(
    db_path: Path,
    group_id: str,
) -> TargetActionOutcome:
    """停止 sidebar group 內所有 targets，保留各 target seen/history。"""

    with SqliteApplicationContext(db_path) as app_context:
        if app_context.repositories.sidebar_layout.get_group(group_id) is None:
            raise ValueError("找不到指定的 sidebar 群組")
        target_ids = app_context.repositories.sidebar_layout.list_target_ids_for_group(group_id)
        for target_id in target_ids:
            app_context.services.targets.pause_target_monitoring(target_id)
    count = len(target_ids)
    return TargetActionOutcome(
        ok=True,
        message=f"已停止群組內 {count} 個 target" if count else "群組內沒有 target",
        feedback="sidebar_group_stopped",
        wake_scheduler=count > 0,
        updated_count=count,
    )
