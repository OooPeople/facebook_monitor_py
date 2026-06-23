"""Resident main worker tests。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import Error as AsyncPlaywrightError

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.worker.playwright_runtime_errors import (
    is_playwright_runtime_closed_exception,
    is_playwright_runtime_closed_message,
)
from facebook_monitor.worker.resident_main import _install_playwright_shutdown_exception_handler
from facebook_monitor.worker.resident_runtime_errors import is_playwright_shutdown_noise_context
from facebook_monitor.worker.resident_shared import list_active_resident_target_ids


def test_list_active_resident_target_ids_excludes_error_runtime(tmp_path: Path) -> None:
    """resident page pool 不應保留已進入 error 的 active target page。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        active = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="active",
                canonical_url="https://www.facebook.com/groups/active",
            )
        )
        errored = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="errored",
                canonical_url="https://www.facebook.com/groups/errored",
            )
        )
        app.services.targets.restart_target_monitoring(active.id)
        app.services.targets.restart_target_monitoring(errored.id)
        app.services.targets.mark_target_error(errored.id, "terminal error")

    assert list_active_resident_target_ids(db_path) == {active.id}


def test_playwright_runtime_closed_exception_is_classified() -> None:
    """Playwright runtime/page/context/browser 關閉訊息應共用低階分類。"""

    assert is_playwright_runtime_closed_exception(
        Exception("Connection closed while reading from the driver")
    )
    assert is_playwright_runtime_closed_exception(
        AsyncPlaywrightError("Target page, context or browser has been closed")
    )
    assert is_playwright_runtime_closed_message("TARGET PAGE, CONTEXT OR BROWSER HAS BEEN CLOSED")
    assert not is_playwright_runtime_closed_exception(Exception("other error"))


def test_playwright_shutdown_noise_context_requires_unretrieved_future_message() -> None:
    """shutdown noise filter 必須同時符合 asyncio context 與 Playwright runtime closed。"""

    exc = AsyncPlaywrightError("Target page, context or browser has been closed")

    assert is_playwright_shutdown_noise_context(
        {"message": "Future exception was never retrieved", "exception": exc}
    )
    assert not is_playwright_shutdown_noise_context(
        {"message": "Task exception was never retrieved", "exception": exc}
    )
    assert not is_playwright_shutdown_noise_context(
        {"message": "Future exception was never retrieved", "exception": RuntimeError("boom")}
    )
    assert not is_playwright_shutdown_noise_context(
        {"message": "Future exception was never retrieved", "exception": RuntimeError("target closed")}
    )


def test_playwright_shutdown_noise_context_accepts_high_confidence_closed_sources() -> None:
    """shutdown noise 可消化 Playwright target closed 或完整 driver closed 訊息。"""

    assert is_playwright_shutdown_noise_context(
        {
            "message": "Future exception was never retrieved",
            "exception": AsyncPlaywrightError("Target closed"),
        }
    )
    assert is_playwright_shutdown_noise_context(
        {
            "message": "Future exception was never retrieved",
            "exception": RuntimeError("Connection closed while reading from the driver"),
        }
    )
    assert is_playwright_shutdown_noise_context(
        {
            "message": "Future exception was never retrieved",
            "exception": _wrapped_playwright_target_closed_exception(),
        }
    )


def test_playwright_shutdown_handler_suppresses_unretrieved_runtime_closed_future() -> None:
    """event loop handler 只消化 shutdown 期間未取回的 Playwright runtime closed Future。"""

    previous_calls: list[dict[str, object]] = []

    async def run_test() -> None:
        loop = asyncio.get_running_loop()
        original_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: previous_calls.append(context))
        restore_handler = _install_playwright_shutdown_exception_handler()
        try:
            for exception in (
                AsyncPlaywrightError("Target page, context or browser has been closed"),
                AsyncPlaywrightError("Target closed"),
                RuntimeError("Connection closed while reading from the driver"),
                _wrapped_playwright_target_closed_exception(),
            ):
                loop.call_exception_handler(
                    {
                        "message": "Future exception was never retrieved",
                        "exception": exception,
                    }
                )
        finally:
            restore_handler()
            loop.set_exception_handler(original_handler)

    asyncio.run(run_test())

    assert previous_calls == []


def test_playwright_shutdown_handler_delegates_unrelated_context() -> None:
    """非 shutdown noise 的 event loop exception 仍必須交給原 handler。"""

    previous_calls: list[dict[str, object]] = []

    async def run_test() -> None:
        loop = asyncio.get_running_loop()
        original_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: previous_calls.append(context))
        restore_handler = _install_playwright_shutdown_exception_handler()
        try:
            context = {
                "message": "Task exception was never retrieved",
                "exception": AsyncPlaywrightError(
                    "Target page, context or browser has been closed"
                ),
            }
            loop.call_exception_handler(context)
        finally:
            restore_handler()
            loop.set_exception_handler(original_handler)

    asyncio.run(run_test())

    assert len(previous_calls) == 1
    assert previous_calls[0]["message"] == "Task exception was never retrieved"


def test_playwright_shutdown_handler_delegates_unrelated_future_exception() -> None:
    """Future context 若不是 Playwright runtime closed，也不可被 shutdown handler 吞掉。"""

    previous_calls: list[dict[str, object]] = []

    async def run_test() -> None:
        loop = asyncio.get_running_loop()
        original_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: previous_calls.append(context))
        restore_handler = _install_playwright_shutdown_exception_handler()
        try:
            context = {
                "message": "Future exception was never retrieved",
                "exception": RuntimeError("boom"),
            }
            loop.call_exception_handler(context)
        finally:
            restore_handler()
            loop.set_exception_handler(original_handler)

    asyncio.run(run_test())

    assert len(previous_calls) == 1
    assert isinstance(previous_calls[0]["exception"], RuntimeError)


def test_playwright_shutdown_handler_delegates_generic_target_closed_future() -> None:
    """generic target closed 訊息不足以讓 shutdown handler 消音。"""

    previous_calls: list[dict[str, object]] = []

    async def run_test() -> None:
        loop = asyncio.get_running_loop()
        original_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: previous_calls.append(context))
        restore_handler = _install_playwright_shutdown_exception_handler()
        try:
            context = {
                "message": "Future exception was never retrieved",
                "exception": RuntimeError("target closed"),
            }
            loop.call_exception_handler(context)
        finally:
            restore_handler()
            loop.set_exception_handler(original_handler)

    asyncio.run(run_test())

    assert len(previous_calls) == 1
    assert str(previous_calls[0]["exception"]) == "target closed"


def test_playwright_shutdown_handler_delegates_task_target_closed_context() -> None:
    """Task context 即使帶 Playwright target closed，也不可被 shutdown handler 消音。"""

    previous_calls: list[dict[str, object]] = []

    async def run_test() -> None:
        loop = asyncio.get_running_loop()
        original_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: previous_calls.append(context))
        restore_handler = _install_playwright_shutdown_exception_handler()
        try:
            context = {
                "message": "Task exception was never retrieved",
                "exception": AsyncPlaywrightError("Target closed"),
            }
            loop.call_exception_handler(context)
        finally:
            restore_handler()
            loop.set_exception_handler(original_handler)

    asyncio.run(run_test())

    assert len(previous_calls) == 1
    assert previous_calls[0]["message"] == "Task exception was never retrieved"


def test_playwright_shutdown_handler_restores_previous_handler() -> None:
    """restore handler 後 event loop 應回到安裝前的 exception handler。"""

    previous_calls: list[dict[str, object]] = []

    async def run_test() -> None:
        loop = asyncio.get_running_loop()
        original_handler = loop.get_exception_handler()

        def previous_handler(
            _loop: asyncio.AbstractEventLoop,
            context: dict[str, object],
        ) -> None:
            """記錄 restore 後是否回到原 handler。"""

            previous_calls.append(context)

        loop.set_exception_handler(previous_handler)
        restore_handler = _install_playwright_shutdown_exception_handler()
        restore_handler()
        try:
            loop.call_exception_handler(
                {
                    "message": "Future exception was never retrieved",
                    "exception": RuntimeError("boom"),
                }
            )
        finally:
            loop.set_exception_handler(original_handler)

    asyncio.run(run_test())

    assert len(previous_calls) == 1


def _wrapped_playwright_target_closed_exception() -> RuntimeError:
    """建立模擬 background Future 包住 Playwright target closed 的例外。"""

    try:
        try:
            raise AsyncPlaywrightError("Target closed")
        except AsyncPlaywrightError as exc:
            raise RuntimeError("background task failed") from exc
    except RuntimeError as exc:
        return exc
