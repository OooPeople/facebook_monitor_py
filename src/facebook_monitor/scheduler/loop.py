"""Minimal target scheduler loop。

職責：依 target-level schedule 判斷到期 target，並以 bounded executor 逐步觸發
group posts worker 掃描，避免整輪序列化扭曲單一 target 的掃描週期語義。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from time import sleep
from uuid import uuid4

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.refresh_policy import resolve_refresh_interval_seconds
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.group_posts import GroupPostsScanSummary
from facebook_monitor.worker.group_posts import WorkerFailure
from facebook_monitor.worker.runner import WorkerOnceOptions
from facebook_monitor.worker.runner import run_worker_once


ScanCallable = Callable[[WorkerOnceOptions], GroupPostsScanSummary]
SleepCallable = Callable[[float], None]
RETRYABLE_IDLE_FAILURE_REASONS = frozenset({"extractor_empty"})


@dataclass(frozen=True)
class SchedulerOptions:
    """保存最小 scheduler loop 執行選項。"""

    db_path: Path
    profile_dir: Path
    interval_seconds: float = 300
    scheduler_tick_seconds: float = 2
    max_concurrent_scans: int = 2
    scroll_rounds: int = 3
    scroll_wait_ms: int = 2500
    scan_timeout_seconds: float = 120
    stale_running_after_seconds: float = 180
    max_cycles: int | None = None


@dataclass(frozen=True)
class SchedulerCycleSummary:
    """保存單輪 scheduler 掃描摘要。"""

    cycle_index: int
    selected_count: int
    success_count: int
    failure_count: int
    skipped_count: int = 0


def list_schedulable_target_ids(
    db_path: Path,
    *,
    default_interval_seconds: float = 300,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """列出目前 scheduler 應該掃描的 target ids。"""

    current_time = now or datetime.now(timezone.utc)
    with SqliteApplicationContext(db_path) as app:
        target_ids: list[str] = []
        for target in app.repositories.targets.list_enabled():
            if target.target_kind != TargetKind.POSTS:
                continue
            runtime_state = app.services.targets.ensure_runtime_state(target.id)
            if runtime_state.desired_state != TargetDesiredState.ACTIVE:
                continue
            if runtime_state.runtime_status in {
                TargetRuntimeStatus.QUEUED,
                TargetRuntimeStatus.RUNNING,
            }:
                continue
            if runtime_state.scan_requested_at is not None:
                target_ids.append(target.id)
                continue
            config = app.services.targets.get_config_for_target(target)
            latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
            latest_finished_at = latest_scan.finished_at if latest_scan else None
            interval_seconds = resolve_refresh_interval_seconds(
                config=config,
                default_interval_seconds=default_interval_seconds,
                target_id=target.id,
                latest_finished_at=latest_finished_at,
            )
            if is_scan_due(
                latest_finished_at=latest_finished_at,
                interval_seconds=interval_seconds,
                now=current_time,
            ):
                target_ids.append(target.id)
        return tuple(target_ids)


def is_scan_due(
    *,
    latest_finished_at: datetime | None,
    interval_seconds: float,
    now: datetime,
) -> bool:
    """判斷 target 是否已到下一次掃描時間。"""

    if latest_finished_at is None:
        return True
    return (now - latest_finished_at).total_seconds() >= max(interval_seconds, 1)


def recover_stale_running_targets(db_path: Path, stale_after_seconds: float) -> int:
    """修復過舊的 running runtime state，回傳修復筆數。"""

    with SqliteApplicationContext(db_path) as app:
        return len(
            app.services.targets.recover_stale_running_targets(
                stale_after_seconds=stale_after_seconds,
            )
        )


def run_scheduler_loop(
    options: SchedulerOptions,
    *,
    scan_once: ScanCallable = run_worker_once,
    sleep_fn: SleepCallable = sleep,
) -> list[SchedulerCycleSummary]:
    """執行最小 scheduler loop；max_cycles 為 None 時會持續執行。"""

    summaries: list[SchedulerCycleSummary] = []
    cycle_index = 0
    schedule_planner = TargetSchedulePlanner(scannable_target_kinds=frozenset({TargetKind.POSTS}))
    while options.max_cycles is None or cycle_index < options.max_cycles:
        cycle_index += 1
        recover_stale_running_targets(options.db_path, options.stale_running_after_seconds)
        due_targets = schedule_planner.list_due_targets(
            options.db_path,
            default_interval_seconds=options.interval_seconds,
            max_count=options.max_concurrent_scans,
        )
        success_count = 0
        failure_count = 0
        skipped_count = 0
        worker_id = f"scheduler-{uuid4()}"

        for due_target in due_targets:
            target_id = due_target.target_id
            with SqliteApplicationContext(options.db_path) as app:
                locked_state = app.services.targets.try_mark_target_running(target_id, worker_id)
            if locked_state is None:
                skipped_count += 1
                continue
            schedule_planner.mark_dispatched(due_target)
            try:
                scan_once(
                    WorkerOnceOptions(
                        profile_dir=options.profile_dir,
                        db_path=options.db_path,
                        target_id=target_id,
                        scroll_rounds=options.scroll_rounds,
                        scroll_wait_ms=options.scroll_wait_ms,
                        scan_timeout_seconds=options.scan_timeout_seconds,
                    )
                )
            except WorkerFailure as exc:
                failure_count += 1
                with SqliteApplicationContext(options.db_path) as app:
                    if exc.reason in RETRYABLE_IDLE_FAILURE_REASONS:
                        app.services.targets.mark_target_idle(target_id)
                    else:
                        app.services.targets.mark_target_error(target_id, f"{exc.reason}: {exc}")
            except Exception as exc:
                failure_count += 1
                with SqliteApplicationContext(options.db_path) as app:
                    app.services.targets.mark_target_error(target_id, f"unknown: {exc}")
            else:
                success_count += 1
                with SqliteApplicationContext(options.db_path) as app:
                    app.services.targets.mark_target_idle(target_id)
            finally:
                schedule_planner.mark_finished(target_id)

        summaries.append(
            SchedulerCycleSummary(
                cycle_index=cycle_index,
                selected_count=len(due_targets),
                success_count=success_count,
                failure_count=failure_count,
                skipped_count=skipped_count,
            )
        )

        if options.max_cycles is not None and cycle_index >= options.max_cycles:
            break
        sleep_fn(max(options.scheduler_tick_seconds, 0))

    return summaries
