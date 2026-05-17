"""One-shot fallback scheduler loop。

職責：供 fallback/debug mode 依 target-level schedule 觸發 one-shot posts scan。
正式產品主路徑由 resident main queue/executor 負責。
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
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.refresh_policy import resolve_refresh_interval_seconds
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.scheduler.runtime_recovery import RETRYABLE_IDLE_FAILURE_REASONS
from facebook_monitor.scheduler.runtime_recovery import recover_stale_runtime_targets
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.one_shot_dispatch import OneShotScanOptions
from facebook_monitor.worker.one_shot_dispatch import run_one_shot_scan
from facebook_monitor.worker.scan_failure_finalize import record_scan_failure


ScanCallable = Callable[[OneShotScanOptions], PostsScanSummary]
SleepCallable = Callable[[float], None]


@dataclass(frozen=True)
class SchedulerOptions:
    """保存 one-shot fallback scheduler 執行選項。"""

    db_path: Path
    profile_dir: Path
    interval_seconds: float = PYTHON_SCHEDULER_RUNTIME_DEFAULTS.one_shot_interval_seconds
    scheduler_tick_seconds: float = PYTHON_SCHEDULER_RUNTIME_DEFAULTS.scheduler_tick_seconds
    max_concurrent_scans: int = PYTHON_SCHEDULER_RUNTIME_DEFAULTS.max_concurrent_scans
    scroll_rounds: int = PYTHON_SCHEDULER_RUNTIME_DEFAULTS.scroll_rounds
    scroll_wait_ms: int = PYTHON_SCHEDULER_RUNTIME_DEFAULTS.scroll_wait_ms
    scan_timeout_seconds: float = PYTHON_SCHEDULER_RUNTIME_DEFAULTS.scan_timeout_seconds
    stale_running_after_seconds: float = (
        PYTHON_SCHEDULER_RUNTIME_DEFAULTS.stale_running_after_seconds
    )
    max_cycles: int | None = None


@dataclass(frozen=True)
class SchedulerCycleSummary:
    """保存單輪 one-shot fallback scheduler 掃描摘要。"""

    cycle_index: int
    selected_count: int
    success_count: int
    failure_count: int
    skipped_count: int = 0


def list_schedulable_target_ids(
    db_path: Path,
    *,
    default_interval_seconds: float = (
        PYTHON_SCHEDULER_RUNTIME_DEFAULTS.one_shot_interval_seconds
    ),
    now: datetime | None = None,
) -> tuple[str, ...]:
    """列出目前 one-shot fallback scheduler 應該掃描的 target ids。"""

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


def run_one_shot_scheduler_loop(
    options: SchedulerOptions,
    *,
    scan_once: ScanCallable = run_one_shot_scan,
    sleep_fn: SleepCallable = sleep,
) -> list[SchedulerCycleSummary]:
    """執行 one-shot fallback scheduler loop；max_cycles 為 None 時會持續執行。"""

    summaries: list[SchedulerCycleSummary] = []
    cycle_index = 0
    schedule_planner = TargetSchedulePlanner(scannable_target_kinds=frozenset({TargetKind.POSTS}))
    while options.max_cycles is None or cycle_index < options.max_cycles:
        cycle_index += 1
        recover_stale_runtime_targets(options.db_path, options.stale_running_after_seconds)
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
                    OneShotScanOptions(
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
                    target = app.repositories.targets.get(target_id)
                    if target is not None:
                        record_scan_failure(
                            app=app,
                            target=target,
                            reason=exc.reason,
                            message=str(exc),
                            worker_path="one_shot_scheduler",
                            exception_class=exc.__class__.__name__,
                            retryable=exc.reason in RETRYABLE_IDLE_FAILURE_REASONS,
                        )
                    if exc.reason in RETRYABLE_IDLE_FAILURE_REASONS:
                        app.services.targets.mark_target_idle(target_id)
                    else:
                        app.services.targets.mark_target_error(target_id, f"{exc.reason}: {exc}")
            except Exception as exc:
                failure_count += 1
                with SqliteApplicationContext(options.db_path) as app:
                    target = app.repositories.targets.get(target_id)
                    if target is not None:
                        record_scan_failure(
                            app=app,
                            target=target,
                            reason="unknown",
                            message=str(exc),
                            worker_path="one_shot_scheduler",
                            exception_class=exc.__class__.__name__,
                        )
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
