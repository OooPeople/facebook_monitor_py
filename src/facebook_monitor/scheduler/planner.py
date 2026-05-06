"""Target-level independent schedule planner。

職責：在 scheduler / resident worker 內維護每個 target 自己的下一次到期時間，
避免整輪序列化時用「上一輪最後完成時間」扭曲單一 target 的掃描週期語義。
"""

from __future__ import annotations

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


RESIDENT_SCANNABLE_TARGET_KINDS = frozenset({TargetKind.POSTS, TargetKind.COMMENTS})


@dataclass(frozen=True)
class DueTarget:
    """保存一次 scheduler 判定已到期的 target。"""

    target_id: str
    interval_seconds: float
    due_at: datetime
    scan_requested: bool = False


class TargetSchedulePlanner:
    """維護 target-level next_due_at，供 bounded executor 逐步取用。"""

    def __init__(
        self,
        *,
        scannable_target_kinds: frozenset[TargetKind] = RESIDENT_SCANNABLE_TARGET_KINDS,
    ) -> None:
        self.scannable_target_kinds = scannable_target_kinds
        self._next_due_at_by_target: dict[str, datetime] = {}
        self._last_started_at_by_target: dict[str, datetime] = {}
        self._last_finished_at_by_target: dict[str, datetime] = {}

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
        with SqliteApplicationContext(db_path) as app:
            for target in app.repositories.targets.list_enabled():
                if target.target_kind not in self.scannable_target_kinds:
                    continue
                runtime_state = app.services.targets.ensure_runtime_state(target.id)
                if runtime_state.desired_state != TargetDesiredState.ACTIVE:
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
                if current_time >= due_at:
                    selected.append(
                        DueTarget(
                            target_id=target.id,
                            interval_seconds=interval_seconds,
                            due_at=due_at,
                        )
                    )

        self.prune_inactive(active_target_ids)
        sorted_targets = tuple(sorted(selected, key=lambda item: item.due_at))
        if max_count is None:
            return sorted_targets
        bounded_count = max(int(max_count), 1)
        return sorted_targets[:bounded_count]

    def mark_dispatched(self, due_target: DueTarget, *, now: datetime | None = None) -> None:
        """target 成功取得 scan lock 後，推進該 target 的 next_due_at。"""

        current_time = now or datetime.now(timezone.utc)
        self._last_started_at_by_target[due_target.target_id] = current_time
        self._next_due_at_by_target[due_target.target_id] = current_time + timedelta(
            seconds=max(due_target.interval_seconds, 1)
        )

    def mark_finished(self, target_id: str, *, now: datetime | None = None) -> None:
        """記錄 target 掃描完成時間，供 diagnostics 或後續策略使用。"""

        self._last_finished_at_by_target[target_id] = now or datetime.now(timezone.utc)

    def prune_inactive(self, active_target_ids: set[str]) -> None:
        """移除已停用或已刪除 target 的排程暫存狀態。"""

        for target_id in tuple(self._next_due_at_by_target):
            if target_id in active_target_ids:
                continue
            self._next_due_at_by_target.pop(target_id, None)
            self._last_started_at_by_target.pop(target_id, None)
            self._last_finished_at_by_target.pop(target_id, None)

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
