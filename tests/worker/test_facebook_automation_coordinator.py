from __future__ import annotations

import asyncio

from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationCoordinator,
)
from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationWorkKind,
)
from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationWaitCancelled,
)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_coordinator_serializes_many_contenders() -> None:
    async def scenario() -> None:
        active = 0
        max_active = 0
        coordinator = FacebookAutomationCoordinator(
            quiet_gap_min_seconds=0,
            quiet_gap_max_seconds=0,
        )

        async def run_one(index: int) -> None:
            nonlocal active, max_active
            lease = await coordinator.acquire(
                FacebookAutomationWorkKind.TARGET_SCAN,
                owner_alias=f"target-{index}",
            )
            try:
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0)
                active -= 1
            finally:
                await lease.release()

        await asyncio.gather(*(run_one(index) for index in range(100)))

        snapshot = await coordinator.snapshot()
        assert max_active == 1
        assert snapshot.max_observed_active_actions == 1
        assert snapshot.admitted_count == 100
        assert not snapshot.active

    asyncio.run(scenario())


def test_coordinator_applies_quiet_gap_between_leases() -> None:
    async def scenario() -> None:
        clock = _FakeClock()
        coordinator = FacebookAutomationCoordinator(
            quiet_gap_min_seconds=15,
            quiet_gap_max_seconds=30,
            sleep_fn=clock.sleep,
            monotonic_fn=clock.monotonic,
            jitter_source=lambda _low, _high: 20,
        )

        first = await coordinator.acquire(FacebookAutomationWorkKind.METADATA_REFRESH)
        await first.release()
        second = await coordinator.acquire(FacebookAutomationWorkKind.COVER_REFRESH)
        await second.release()

        assert clock.sleeps == [20]
        snapshot = await coordinator.snapshot()
        assert snapshot.quiet_gap_seconds == 20
        assert snapshot.next_allowed_in_seconds == 20

    asyncio.run(scenario())


def test_cancelled_waiter_does_not_lock_coordinator() -> None:
    async def scenario() -> None:
        coordinator = FacebookAutomationCoordinator(
            quiet_gap_min_seconds=0,
            quiet_gap_max_seconds=0,
        )
        first = await coordinator.acquire(FacebookAutomationWorkKind.TARGET_SCAN)
        waiter = asyncio.create_task(
            coordinator.acquire(FacebookAutomationWorkKind.METADATA_REFRESH)
        )
        await asyncio.sleep(0)
        waiter.cancel()
        try:
            await waiter
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("cancelled waiter should raise CancelledError")
        await first.release()

        next_lease = await coordinator.acquire(FacebookAutomationWorkKind.COVER_REFRESH)
        await next_lease.release()
        snapshot = await coordinator.snapshot()
        assert snapshot.cancelled_count == 1
        assert not snapshot.active

    asyncio.run(scenario())


def test_cancel_during_release_finishes_owner_before_propagating() -> None:
    """Release 等 state lock 時被 cancel 仍要先解除 active owner。"""

    async def scenario() -> None:
        coordinator = FacebookAutomationCoordinator(
            quiet_gap_min_seconds=0,
            quiet_gap_max_seconds=0,
        )
        lease = await coordinator.acquire(FacebookAutomationWorkKind.TARGET_SCAN)
        await coordinator._state_lock.acquire()
        release_task = asyncio.create_task(lease.release())
        await asyncio.sleep(0)
        release_task.cancel()
        coordinator._state_lock.release()

        try:
            await asyncio.wait_for(release_task, timeout=1)
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("release must propagate cancellation after cleanup")

        assert lease.released
        await lease.release()
        snapshot = await coordinator.snapshot()
        assert not snapshot.active and snapshot.waiter_count == 0
        next_lease = await coordinator.acquire(
            FacebookAutomationWorkKind.METADATA_REFRESH
        )
        await next_lease.release()

    asyncio.run(scenario())


def test_owner_alias_redacts_path_like_values() -> None:
    async def scenario() -> None:
        coordinator = FacebookAutomationCoordinator(
            quiet_gap_min_seconds=0,
            quiet_gap_max_seconds=0,
        )
        lease = await coordinator.acquire(
            FacebookAutomationWorkKind.HALF_OPEN_PROBE,
            owner_alias="C:\\private\\profile",
        )
        snapshot = await coordinator.snapshot()
        assert snapshot.active_owner_alias == "redacted"
        await lease.release()

    asyncio.run(scenario())


def test_runtime_cancellation_removes_lock_waiter_without_poisoning_next_generation() -> None:
    """restart 只取消既有 waiters，後續 runtime generation 仍可取得 lease。"""

    async def scenario() -> None:
        coordinator = FacebookAutomationCoordinator(
            quiet_gap_min_seconds=0,
            quiet_gap_max_seconds=0,
        )
        first = await coordinator.acquire(FacebookAutomationWorkKind.TARGET_SCAN)
        waiter = asyncio.create_task(
            coordinator.acquire(FacebookAutomationWorkKind.METADATA_REFRESH)
        )
        await asyncio.sleep(0)

        coordinator.cancel_pending_waiters()
        try:
            await waiter
        except FacebookAutomationWaitCancelled:
            pass
        else:
            raise AssertionError("runtime cancellation must stop an existing waiter")
        await first.release()

        next_lease = await coordinator.acquire(FacebookAutomationWorkKind.COVER_REFRESH)
        await next_lease.release()
        snapshot = await coordinator.snapshot()
        assert snapshot.waiter_count == 0
        assert snapshot.cancelled_count == 1

    asyncio.run(scenario())


def test_runtime_cancellation_interrupts_quiet_gap_and_releases_process_lock() -> None:
    """quiet gap wait 被取消後不可遺留 process lock。"""

    async def scenario() -> None:
        sleep_started = asyncio.Event()
        allow_sleep = asyncio.Event()

        async def blocking_sleep(_seconds: float) -> None:
            sleep_started.set()
            await allow_sleep.wait()

        coordinator = FacebookAutomationCoordinator(
            quiet_gap_min_seconds=10,
            quiet_gap_max_seconds=10,
            sleep_fn=blocking_sleep,
            jitter_source=lambda _low, _high: 10,
        )
        first = await coordinator.acquire(FacebookAutomationWorkKind.TARGET_SCAN)
        await first.release()
        waiter = asyncio.create_task(
            coordinator.acquire(FacebookAutomationWorkKind.METADATA_REFRESH)
        )
        await sleep_started.wait()

        coordinator.cancel_pending_waiters()
        try:
            await waiter
        except FacebookAutomationWaitCancelled:
            pass
        else:
            raise AssertionError("quiet-gap waiter must observe runtime cancellation")

        allow_sleep.set()
        next_lease = await coordinator.acquire(FacebookAutomationWorkKind.COVER_REFRESH)
        await next_lease.release()

    asyncio.run(scenario())
