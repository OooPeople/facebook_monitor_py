"""Facebook automation process-local trip/cancel runtime。

職責：在單一 browser runtime 內提供 one-way trip signal、追蹤正在執行的
Facebook work，並確保 temporary-block incident writer 先建立且完成後才讓
觸發者離開。此模組不保存跨程序狀態，也不提供 admission、pacing 或 recovery。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from facebook_monitor.core.facebook_temporary_block import TemporaryBlockFinding
from facebook_monitor.core.models import WorkerMode
from facebook_monitor.worker.facebook_access_incident import (
    FacebookAccessIncidentOutcome,
)
from facebook_monitor.worker.facebook_access_incident import (
    record_facebook_access_incident_for_db,
)
from facebook_monitor.worker.facebook_access_incident import (
    record_facebook_access_incident_for_db_async,
)
from facebook_monitor.worker.failure_diagnostics import WorkerFailureDiagnostics
from facebook_monitor.worker.scan_commit_guard import ScanCommitGuard


class FacebookAutomationRuntimeTripped(RuntimeError):
    """表示本 browser runtime 已觸發全域停止，不得開始新的 Facebook I/O。"""


class FacebookTemporaryBlockIncidentRecorded(RuntimeError):
    """表示 temporary-block incident 已完整持久化，caller 應停止目前 runtime。"""

    def __init__(self, outcome: FacebookAccessIncidentOutcome) -> None:
        super().__init__("facebook temporary-block incident recorded")
        self.outcome = outcome


class FacebookTemporaryBlockIncidentError(RuntimeError):
    """表示 incident writer 未能確認 transaction commit。"""


class FacebookAutomationTripSignal:
    """每個 browser runtime 一個、不可 reset 的 process-local trip signal。"""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def try_trip(self) -> bool:
        """首次 trip 回傳 True；已 trip 時回傳 False。"""

        if self._event.is_set():
            return False
        self._event.set()
        return True

    def is_tripped(self) -> bool:
        """回傳目前 runtime 是否已停止接受 Facebook I/O。"""

        return self._event.is_set()

    async def wait(self) -> None:
        """等待 runtime 被 trip。"""

        await self._event.wait()


@dataclass(frozen=True)
class _IncidentRequest:
    """保存 incident writer 所需的 bounded persistence 輸入。"""

    db_path: Path
    finding: TemporaryBlockFinding
    scan_commit_guard: ScanCommitGuard | None
    diagnostics: WorkerFailureDiagnostics | None
    worker_mode: WorkerMode


class FacebookAutomationRuntime:
    """協調單一 browser runtime 的 work registry 與 incident linearization。"""

    def __init__(self, signal: FacebookAutomationTripSignal | None = None) -> None:
        self.signal = signal or FacebookAutomationTripSignal()
        self._work_tasks: set[asyncio.Task[Any]] = set()
        self._incident_task: asyncio.Task[FacebookAccessIncidentOutcome] | None = None

    @contextmanager
    def facebook_work(self) -> Iterator[None]:
        """登記目前 task；runtime 已 trip 時在任何 I/O 前拒絕進入。"""

        if self.signal.is_tripped():
            raise FacebookAutomationRuntimeTripped(
                "facebook automation runtime is already tripped"
            )
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("facebook automation work requires an asyncio task")
        self._work_tasks.add(task)
        try:
            yield
        finally:
            self._work_tasks.discard(task)

    def ensure_io_allowed(self) -> None:
        """在 browser action 前確認 one-way signal 尚未觸發。"""

        if self.signal.is_tripped():
            raise FacebookAutomationRuntimeTripped(
                "facebook automation runtime is already tripped"
            )

    async def record_temporary_block(
        self,
        *,
        db_path: Path,
        finding: TemporaryBlockFinding,
        scan_commit_guard: ScanCommitGuard | None = None,
        diagnostics: WorkerFailureDiagnostics | None = None,
        worker_mode: WorkerMode = WorkerMode.HEADLESS,
    ) -> FacebookAccessIncidentOutcome:
        """Trip、建立 writer、取消 peers，再 shield writer 到 terminal。"""

        request = _IncidentRequest(
            db_path=db_path,
            finding=finding,
            scan_commit_guard=scan_commit_guard,
            diagnostics=diagnostics,
            worker_mode=worker_mode,
        )
        owner = asyncio.current_task()
        if owner is None:
            raise RuntimeError("temporary-block incident requires an asyncio task")

        writer: asyncio.Task[FacebookAccessIncidentOutcome]
        leader = self.signal.try_trip()
        if leader:
            writer = asyncio.create_task(
                self._write_incident(request),
                name="facebook-temporary-block-incident-writer",
            )
            self._incident_task = writer
            for task in tuple(self._work_tasks):
                if task is not owner and task is not writer and not task.done():
                    task.cancel()
        else:
            existing_writer = self._incident_task
            if existing_writer is None:
                raise FacebookTemporaryBlockIncidentError(
                    "facebook runtime tripped without an incident writer"
                )
            writer = existing_writer

        outcome = await _await_task_terminal_despite_cancellation(writer)
        if not outcome.committed:
            raise FacebookTemporaryBlockIncidentError(
                "facebook temporary-block incident was rejected: "
                f"{outcome.reason or outcome.kind.value}"
            )
        return outcome

    async def _write_incident(
        self,
        request: _IncidentRequest,
    ) -> FacebookAccessIncidentOutcome:
        """執行唯一 durable writer；caller 已先建立 task。"""

        return await record_facebook_access_incident_for_db_async(
            db_path=request.db_path,
            finding=request.finding,
            scan_commit_guard=request.scan_commit_guard,
            diagnostics=request.diagnostics,
            worker_mode=request.worker_mode,
        )


async def _await_task_terminal_despite_cancellation(
    task: asyncio.Task[FacebookAccessIncidentOutcome],
) -> FacebookAccessIncidentOutcome:
    """延後外部取消，直到 incident writer 已 terminal，再如實重拋。"""

    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    outcome = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return outcome


def record_temporary_block_sync(
    *,
    signal: FacebookAutomationTripSignal,
    db_path: Path,
    finding: TemporaryBlockFinding,
    scan_commit_guard: ScanCommitGuard | None = None,
    diagnostics: WorkerFailureDiagnostics | None = None,
    worker_mode: WorkerMode = WorkerMode.HEADLESS,
) -> FacebookAccessIncidentOutcome:
    """同步 fallback 使用的 one-way trip + durable incident writer。"""

    if not signal.try_trip():
        raise FacebookAutomationRuntimeTripped(
            "facebook automation runtime is already tripped"
        )
    outcome = record_facebook_access_incident_for_db(
        db_path=db_path,
        finding=finding,
        scan_commit_guard=scan_commit_guard,
        diagnostics=diagnostics,
        worker_mode=worker_mode,
    )
    if not outcome.committed:
        raise FacebookTemporaryBlockIncidentError(
            "facebook temporary-block incident was rejected: "
            f"{outcome.reason or outcome.kind.value}"
        )
    return outcome


__all__ = [
    "FacebookAutomationRuntime",
    "FacebookAutomationRuntimeTripped",
    "FacebookAutomationTripSignal",
    "FacebookTemporaryBlockIncidentError",
    "FacebookTemporaryBlockIncidentRecorded",
    "record_temporary_block_sync",
]
