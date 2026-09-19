"""Recovery probe 共用 absolute deadline / cleanup runtime tests。"""

from __future__ import annotations

import asyncio
from datetime import UTC
from datetime import datetime
from datetime import timedelta

import pytest

from facebook_monitor.core.scan_failures import CONTENT_UNAVAILABLE_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.worker.facebook_recovery_probe_runtime import (
    build_facebook_probe_deadline,
)
from facebook_monitor.worker.facebook_recovery_probe_runtime import (
    FacebookProbeCleanupResult,
)
from facebook_monitor.worker.facebook_recovery_probe_runtime import (
    close_probe_browser_resources,
)
from facebook_monitor.worker.facebook_recovery_probe_runtime import (
    normalize_probe_failure_reason,
)


_NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def test_probe_failure_reason_normalization_preserves_safe_code_only() -> None:
    """既有page-guard分類保留，未知或含秘密的reason只降級為unknown。"""

    assert (
        normalize_probe_failure_reason(CONTENT_UNAVAILABLE_REASON)
        == CONTENT_UNAVAILABLE_REASON
    )
    assert (
        normalize_probe_failure_reason("secret-token=do-not-emit") == UNKNOWN_REASON
    )


def test_probe_deadline_uses_shorter_durable_lease_budget() -> None:
    """Configured timeout不得延長已由DB claim取得的durable lease。"""

    monotonic_now = 100.0
    deadline = build_facebook_probe_deadline(
        configured_timeout_seconds=120,
        lease_expires_at=_NOW + timedelta(seconds=15),
        wall_now=_NOW,
        monotonic=lambda: monotonic_now,
    )

    assert deadline.expires_at == 115.0
    assert deadline.remaining_seconds() == 15.0


def test_expired_probe_lease_times_out_before_running_body() -> None:
    """已過期lease不可再取得新的async action budget。"""

    async def run() -> bool:
        body_entered = False
        deadline = build_facebook_probe_deadline(
            configured_timeout_seconds=120,
            lease_expires_at=_NOW - timedelta(seconds=1),
            wall_now=_NOW,
        )
        with pytest.raises(TimeoutError):
            async with deadline.enforce():
                body_entered = True
        return body_entered

    assert not asyncio.run(run())


def test_probe_cleanup_grace_bounds_hanging_context_close() -> None:
    """Context close卡住時仍會在grace內停止並嘗試收掉Playwright manager。"""

    class HangingContext:
        async def close(self) -> None:
            await asyncio.Event().wait()

    class Manager:
        exited = False

        async def __aexit__(self, *_exc: object) -> None:
            self.exited = True

    manager = Manager()
    result = asyncio.run(
        close_probe_browser_resources(
            browser_context=HangingContext(),
            playwright_manager=manager,
            playwright_started=True,
            timeout_seconds=0.1,
        )
    )

    assert not result.completed
    assert not result.context_closed
    assert manager.exited


def test_probe_cleanup_grace_is_hard_bound_when_both_steps_hang() -> None:
    """Context與manager都卡住時也不得讓wait_for取消後反向拖住caller。"""

    class HangingContext:
        async def close(self) -> None:
            await asyncio.Event().wait()

    class HangingManager:
        exit_started = False

        async def __aexit__(self, *_exc: object) -> None:
            self.exit_started = True
            await asyncio.Event().wait()

    manager = HangingManager()

    async def run() -> FacebookProbeCleanupResult:
        return await asyncio.wait_for(
            close_probe_browser_resources(
                browser_context=HangingContext(),
                playwright_manager=manager,
                playwright_started=True,
                timeout_seconds=0.1,
            ),
            timeout=0.5,
        )

    result = asyncio.run(run())

    assert not result.completed
    assert not result.context_closed
    assert manager.exit_started
