"""Target-level independent schedule planner。

職責：在 scheduler / resident main worker 內維護每個 target 自己的下一次到期時間，
避免整輪序列化時用「上一輪最後完成時間」扭曲單一 target 的掃描週期語義。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.refresh_policy import resolve_refresh_interval_seconds
from facebook_monitor.core.scan_failure_policy import failure_retry_delay_seconds


RESIDENT_SCANNABLE_TARGET_KINDS = frozenset({TargetKind.POSTS, TargetKind.COMMENTS})


@dataclass(frozen=True)
class DueTarget:
    """保存一次 scheduler 判定已到期的 target。"""

    target_id: str
    interval_seconds: float
    due_at: datetime
    scan_requested: bool = False
    scan_requested_at: datetime | None = None


class TargetSchedulePlanner:
    """維護 target-level next_due_at，供 bounded executor 逐步取用。"""

    def __init__(
        self,
        *,
        scannable_target_kinds: frozenset[TargetKind] = RESIDENT_SCANNABLE_TARGET_KINDS,
        on_display_next_due_changed: Callable[[str, datetime | None], None] | None = None,
    ) -> None:
        self.scannable_target_kinds = scannable_target_kinds
        self.on_display_next_due_changed = on_display_next_due_changed
        self._next_due_at_by_target: dict[str, datetime] = {}

    def list_due_targets(
        self,
        db_path: Path,
        *,
        default_interval_seconds: float,
        max_count: int | None = None,
        now: datetime | None = None,
    ) -> tuple[DueTarget, ...]:
        """列出已到期且可提交給 executor 的 target。"""

        current_time = now or datetime.now(timezone.utc)
        selected: list[DueTarget] = []
        active_target_ids: set[str] = set()
        initialized_due_times: list[tuple[str, datetime]] = []
        with SqliteApplicationContext(db_path) as app:
            for target in app.repositories.targets.list_enabled():
                if target.target_kind not in self.scannable_target_kinds:
                    continue
                runtime_state = app.services.targets.ensure_runtime_state(target.id)
                if runtime_state.desired_state != TargetDesiredState.ACTIVE:
                    continue
                if runtime_state.runtime_status == TargetRuntimeStatus.ERROR:
                    continue
                active_target_ids.add(target.id)
                if runtime_state.runtime_status in {
                    TargetRuntimeStatus.QUEUED,
                    TargetRuntimeStatus.RUNNING,
                }:
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
                if runtime_state.scan_requested_at is not None:
                    selected.append(
                        DueTarget(
                            target_id=target.id,
                            interval_seconds=interval_seconds,
                            due_at=current_time,
                            scan_requested=True,
                            scan_requested_at=runtime_state.scan_requested_at,
                        )
                    )
                    continue

                delayed_retry_due_at = self._delayed_failure_retry_due_at(
                    failure_reason=runtime_state.consecutive_failure_reason,
                    failure_count=runtime_state.consecutive_failure_count,
                    latest_finished_at=latest_finished_at,
                )
                if delayed_retry_due_at is not None:
                    if self._next_due_at_by_target.get(target.id) != delayed_retry_due_at:
                        self._next_due_at_by_target[target.id] = delayed_retry_due_at
                        initialized_due_times.append((target.id, delayed_retry_due_at))
                    if current_time >= delayed_retry_due_at:
                        selected.append(
                            DueTarget(
                                target_id=target.id,
                                interval_seconds=interval_seconds,
                                due_at=delayed_retry_due_at,
                            )
                        )
                    continue

                due_at = self._next_due_at_by_target.get(target.id)
                if due_at is None:
                    due_at = self._initial_due_at(
                        latest_finished_at=latest_finished_at,
                        interval_seconds=interval_seconds,
                        now=current_time,
                    )
                    self._next_due_at_by_target[target.id] = due_at
                    initialized_due_times.append((target.id, due_at))
                if current_time >= due_at:
                    selected.append(
                        DueTarget(
                            target_id=target.id,
                            interval_seconds=interval_seconds,
                            due_at=due_at,
                        )
                    )

        for target_id, due_at in initialized_due_times:
            self._publish_display_next_due_at(target_id, due_at)
        self.prune_inactive(active_target_ids)
        sorted_targets = tuple(sorted(selected, key=lambda item: item.due_at))
        if max_count is None:
            return sorted_targets
        bounded_count = max(int(max_count), 1)
        return sorted_targets[:bounded_count]

    def mark_dispatched(self, due_target: DueTarget, *, now: datetime | None = None) -> None:
        """target 成功取得 scan lock 後，依 start-to-start cadence 推進 next_due_at。"""

        current_time = now or datetime.now(timezone.utc)
        next_due_at = current_time + timedelta(
            seconds=max(due_target.interval_seconds, 1)
        )
        self._next_due_at_by_target[due_target.target_id] = next_due_at
        self._publish_display_next_due_at(due_target.target_id, next_due_at)

    def prune_inactive(self, active_target_ids: set[str]) -> None:
        """移除已停用或已刪除 target 的排程暫存狀態。"""

        for target_id in tuple(self._next_due_at_by_target):
            if target_id in active_target_ids:
                continue
            self._next_due_at_by_target.pop(target_id, None)
            self._publish_display_next_due_at(target_id, None)

    def _publish_display_next_due_at(
        self,
        target_id: str,
        due_at: datetime | None,
    ) -> None:
        """發布 UI 顯示用 due time；排程判斷仍只讀 planner 記憶體。"""

        if self.on_display_next_due_changed is None:
            return
        self.on_display_next_due_changed(target_id, due_at)

    @staticmethod
    def _initial_due_at(
        *,
        latest_finished_at: datetime | None,
        interval_seconds: float,
        now: datetime,
    ) -> datetime:
        """依既有 scan history 初始化下一次到期時間。"""

        if latest_finished_at is None:
            return now
        return latest_finished_at + timedelta(seconds=max(interval_seconds, 1))

    @staticmethod
    def _delayed_failure_retry_due_at(
        *,
        failure_reason: str,
        failure_count: int,
        latest_finished_at: datetime | None,
    ) -> datetime | None:
        """依持久化 failure streak 計算延遲補掃時間。"""

        if failure_count <= 0 or latest_finished_at is None:
            return None
        delay_seconds = failure_retry_delay_seconds(failure_reason)
        if delay_seconds <= 0:
            return None
        return latest_finished_at + timedelta(seconds=delay_seconds)
