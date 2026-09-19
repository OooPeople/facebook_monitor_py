"""Profile 級 Facebook automation work coordinator。

職責：在 Playwright context 外序列化 scan、metadata、cover 與 recovery probe，
並在相鄰 work 之間套用可測試的保守 quiet gap。這個 coordinator 只治理程式主動
Facebook work；persistent circuit admission 仍由 application service 決定。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import random
import time
from uuid import uuid4


AsyncSleep = Callable[[float], Awaitable[None]]
MonotonicClock = Callable[[], float]
JitterSource = Callable[[float, float], float]


class FacebookAutomationWaitCancelled(RuntimeError):
    """表示 runtime stop/restart 已取消尚未取得的 automation work。"""


class FacebookAutomationWorkKind(StrEnum):
    """共用 profile admission 的 Facebook work 類型。"""

    HALF_OPEN_PROBE = "half_open_probe"
    TARGET_SCAN = "target_scan"
    METADATA_REFRESH = "metadata_refresh"
    COVER_REFRESH = "cover_refresh"


@dataclass(frozen=True)
class FacebookAutomationCoordinatorSnapshot:
    """提供 runtime diagnostics 的 privacy-safe coordinator snapshot。"""

    active: bool = False
    active_work_kind: str = ""
    active_owner_alias: str = ""
    waiter_count: int = 0
    admitted_count: int = 0
    cancelled_count: int = 0
    max_observed_active_actions: int = 0
    quiet_gap_seconds: float = 0.0
    next_allowed_in_seconds: float = 0.0


class FacebookAutomationLease:
    """保存一次已取得的 coordinator lease，允許跨 producer/worker task 移交。"""

    def __init__(
        self,
        *,
        coordinator: FacebookAutomationCoordinator,
        operation_id: str,
        work_kind: FacebookAutomationWorkKind,
        owner_alias: str,
        cancellation_event: asyncio.Event,
    ) -> None:
        self._coordinator = coordinator
        self.operation_id = operation_id
        self.work_kind = work_kind
        self.owner_alias = owner_alias
        self._cancellation_event = cancellation_event
        self._released = False

    @property
    def released(self) -> bool:
        """回傳 lease 是否已完成釋放。"""

        return self._released

    async def release(self) -> None:
        """冪等釋放 lease；取消不得留下假釋放的 active owner。"""

        if self._released:
            return
        release_task = asyncio.create_task(self._coordinator._release(self))
        cancelled = False
        while not release_task.done():
            try:
                await asyncio.shield(release_task)
            except asyncio.CancelledError:
                cancelled = True
        release_task.result()
        self._released = True
        if cancelled:
            raise asyncio.CancelledError

    @property
    def cancellation_event(self) -> asyncio.Event:
        """回傳本次 admission generation 的取消訊號。"""

        return self._cancellation_event

    async def wait_or_cancel(self, awaitable: Awaitable[None]) -> None:
        """等待 lease 內部 pacing；runtime cancellation 先到時中止。"""

        await self._coordinator._sleep_or_cancel(awaitable, self._cancellation_event)

    async def __aenter__(self) -> FacebookAutomationLease:
        """支援一般同 task 的 async context manager 用法。"""

        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.release()


class FacebookAutomationCoordinator:
    """序列化同一 automation profile 的主動 Facebook work。"""

    def __init__(
        self,
        *,
        quiet_gap_min_seconds: float,
        quiet_gap_max_seconds: float,
        sleep_fn: AsyncSleep = asyncio.sleep,
        monotonic_fn: MonotonicClock = time.monotonic,
        jitter_source: JitterSource = random.uniform,
    ) -> None:
        self._quiet_gap_min_seconds = max(float(quiet_gap_min_seconds), 0.0)
        self._quiet_gap_max_seconds = max(
            float(quiet_gap_max_seconds),
            self._quiet_gap_min_seconds,
        )
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn
        self._jitter_source = jitter_source
        self._lease_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._waiter_count = 0
        self._active_operation_id = ""
        self._active_work_kind = ""
        self._active_owner_alias = ""
        self._active_action_count = 0
        self._admitted_count = 0
        self._cancelled_count = 0
        self._max_observed_active_actions = 0
        self._last_quiet_gap_seconds = 0.0
        self._next_allowed_monotonic = 0.0
        self._waiter_cancellation_event = asyncio.Event()

    def cancel_pending_waiters(self) -> None:
        """取消目前 generation 的 waiters；後續新 work 使用新的 generation。"""

        cancelled_event = self._waiter_cancellation_event
        self._waiter_cancellation_event = asyncio.Event()
        cancelled_event.set()

    async def _acquire_lock_or_cancel(self, cancellation_event: asyncio.Event) -> None:
        """等待 process lock；取消與 lock 同時完成時優先 fail closed 並釋放 lock。"""

        lock_task = asyncio.create_task(self._lease_lock.acquire())
        cancel_task = asyncio.create_task(cancellation_event.wait())
        lock_acquired = False
        try:
            await asyncio.wait({lock_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
            if lock_task.done():
                lock_acquired = await lock_task
            if cancellation_event.is_set():
                if not lock_task.done():
                    lock_task.cancel()
                    await asyncio.gather(lock_task, return_exceptions=True)
                raise FacebookAutomationWaitCancelled(
                    "Facebook automation coordinator wait was cancelled"
                )
            if not lock_acquired:
                lock_acquired = await lock_task
        except BaseException:
            if lock_acquired:
                self._lease_lock.release()
                lock_acquired = False
            elif not lock_task.done():
                lock_task.cancel()
                await asyncio.gather(lock_task, return_exceptions=True)
            elif not lock_acquired and not lock_task.cancelled():
                error = lock_task.exception()
                if error is None and lock_task.result():
                    self._lease_lock.release()
            raise
        finally:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)

    @staticmethod
    async def _sleep_or_cancel(
        sleep_awaitable: Awaitable[None],
        cancellation_event: asyncio.Event,
    ) -> None:
        """等待 quiet gap，runtime cancellation 先到時取消 sleep。"""

        sleep_task = asyncio.ensure_future(sleep_awaitable)
        cancel_task = asyncio.create_task(cancellation_event.wait())
        try:
            await asyncio.wait({sleep_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
            if cancellation_event.is_set():
                sleep_task.cancel()
                await asyncio.gather(sleep_task, return_exceptions=True)
                raise FacebookAutomationWaitCancelled(
                    "Facebook automation quiet-gap wait was cancelled"
                )
            await sleep_task
        except BaseException:
            if not sleep_task.done():
                sleep_task.cancel()
                await asyncio.gather(sleep_task, return_exceptions=True)
            raise
        finally:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)

    async def acquire(
        self,
        work_kind: FacebookAutomationWorkKind,
        *,
        owner_alias: str = "",
    ) -> FacebookAutomationLease:
        """等待 quiet gap 後取得唯一 work lease。"""

        async with self._state_lock:
            self._waiter_count += 1
        cancellation_event = self._waiter_cancellation_event
        acquired = False
        try:
            await self._acquire_lock_or_cancel(cancellation_event)
            acquired = True
            delay = max(self._next_allowed_monotonic - self._monotonic(), 0.0)
            if delay:
                await self._sleep_or_cancel(self._sleep(delay), cancellation_event)
            if cancellation_event.is_set():
                raise FacebookAutomationWaitCancelled(
                    "Facebook automation admission was cancelled before ownership transfer"
                )
            operation_id = f"facebook-work-{uuid4()}"
            async with self._state_lock:
                self._active_operation_id = operation_id
                self._active_work_kind = work_kind.value
                self._active_owner_alias = _safe_owner_alias(owner_alias)
                self._active_action_count = 1
                self._admitted_count += 1
                self._max_observed_active_actions = max(
                    self._max_observed_active_actions,
                    self._active_action_count,
                )
            return FacebookAutomationLease(
                coordinator=self,
                operation_id=operation_id,
                work_kind=work_kind,
                owner_alias=_safe_owner_alias(owner_alias),
                cancellation_event=cancellation_event,
            )
        except BaseException:
            async with self._state_lock:
                self._cancelled_count += 1
            if acquired:
                self._lease_lock.release()
            raise
        finally:
            async with self._state_lock:
                self._waiter_count = max(self._waiter_count - 1, 0)

    async def snapshot(self) -> FacebookAutomationCoordinatorSnapshot:
        """回傳不含 target/profile 原值的 diagnostics snapshot。"""

        async with self._state_lock:
            return FacebookAutomationCoordinatorSnapshot(
                active=bool(self._active_operation_id),
                active_work_kind=self._active_work_kind,
                active_owner_alias=self._active_owner_alias,
                waiter_count=self._waiter_count,
                admitted_count=self._admitted_count,
                cancelled_count=self._cancelled_count,
                max_observed_active_actions=self._max_observed_active_actions,
                quiet_gap_seconds=self._last_quiet_gap_seconds,
                next_allowed_in_seconds=max(
                    self._next_allowed_monotonic - self._monotonic(),
                    0.0,
                ),
            )

    async def _release(self, lease: FacebookAutomationLease) -> None:
        """只允許目前 operation owner 推進 quiet gap 與釋放 lock。"""

        async with self._state_lock:
            if self._active_operation_id != lease.operation_id:
                return
            quiet_gap = float(
                self._jitter_source(
                    self._quiet_gap_min_seconds,
                    self._quiet_gap_max_seconds,
                )
            )
            quiet_gap = min(
                max(quiet_gap, self._quiet_gap_min_seconds),
                self._quiet_gap_max_seconds,
            )
            self._last_quiet_gap_seconds = quiet_gap
            self._next_allowed_monotonic = self._monotonic() + quiet_gap
            self._active_operation_id = ""
            self._active_work_kind = ""
            self._active_owner_alias = ""
            self._active_action_count = 0
        self._lease_lock.release()


def _safe_owner_alias(value: str) -> str:
    """只保留呼叫端已建立的短 alias，拒絕 URL/path 類內容進 diagnostics。"""

    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if any(marker in normalized for marker in ("/", "\\", ":", "?", "=")):
        return "redacted"
    return normalized[:64]


__all__ = [
    "FacebookAutomationCoordinator",
    "FacebookAutomationCoordinatorSnapshot",
    "FacebookAutomationLease",
    "FacebookAutomationWorkKind",
    "FacebookAutomationWaitCancelled",
]
