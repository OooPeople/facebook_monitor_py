"""Resident maintenance runtime error helper tests。"""

from __future__ import annotations

from playwright.async_api import Error as AsyncPlaywrightError

from facebook_monitor.facebook.group_metadata import GroupMetadataError
from facebook_monitor.worker.resident_maintenance_errors import (
    is_scheduler_runtime_refresh_failure,
)
from facebook_monitor.worker.resident_maintenance_errors import runtime_refresh_failure_detail
from facebook_monitor.worker.resident_maintenance_errors import (
    should_skip_refresh_failure_for_shutdown,
)


def test_should_skip_refresh_failure_for_shutdown_handles_runtime_closed_only_when_stopping() -> None:
    """maintenance shutdown 只在 stop requested 時略過 Playwright runtime closed 診斷。"""

    exc = AsyncPlaywrightError("Target page, context or browser has been closed")
    wrapped = _wrapped_runtime_closed_exception()

    assert should_skip_refresh_failure_for_shutdown(exc, lambda: True)
    assert should_skip_refresh_failure_for_shutdown(wrapped, lambda: True)
    assert not should_skip_refresh_failure_for_shutdown(exc, lambda: False)
    assert not should_skip_refresh_failure_for_shutdown(RuntimeError("boom"), lambda: True)


def test_runtime_closed_refresh_failure_still_requests_scheduler_runtime_handling() -> None:
    """非停止期間的 Playwright runtime closed 仍是 scheduler runtime refresh failure。"""

    exc = AsyncPlaywrightError("Target page, context or browser has been closed")
    wrapped = _wrapped_runtime_closed_exception()

    assert is_scheduler_runtime_refresh_failure(exc)
    assert is_scheduler_runtime_refresh_failure(wrapped)


def test_runtime_refresh_failure_detail_uses_nearest_runtime_closed_exception() -> None:
    """wrapped maintenance exception 應回報最接近 Playwright runtime closed 的 detail。"""

    exception_class, message = runtime_refresh_failure_detail(_wrapped_runtime_closed_exception())

    assert exception_class == "Error"
    assert "Target page, context or browser has been closed" in message


def _wrapped_runtime_closed_exception() -> GroupMetadataError:
    """建立模擬 group metadata helper 包住 Playwright runtime closed 的例外。"""

    try:
        try:
            raise AsyncPlaywrightError("Target page, context or browser has been closed")
        except AsyncPlaywrightError as exc:
            raise GroupMetadataError("metadata refresh failed") from exc
    except GroupMetadataError as exc:
        return exc
