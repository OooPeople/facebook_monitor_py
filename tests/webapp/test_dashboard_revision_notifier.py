"""Dashboard revision notifier tests。"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from threading import Event
from threading import Lock

from facebook_monitor.webapp.dashboard_read_models import DashboardRevision
from facebook_monitor.webapp.dashboard_read_models import DashboardRevisionUnavailable
from facebook_monitor.webapp.dashboard_revision_notifier import DashboardRevisionNotifier


def test_notifier_reads_initial_revision(tmp_path: Path) -> None:
    """subscriber 建立後會收到目前真實 dashboard revision。"""

    async def run_test() -> None:
        notifier = DashboardRevisionNotifier(
            db_path=tmp_path / "app.db",
            get_dashboard_revision=lambda _path: DashboardRevision(
                revision="rev-1",
                last_changed_at="2026-06-18T00:00:00",
            ),
            poll_interval_seconds=1.0,
        )
        await notifier.start()
        stream = notifier.subscribe()
        try:
            revision = await asyncio.wait_for(anext(stream), timeout=0.5)
        finally:
            await stream.aclose()
            await notifier.stop()

        assert revision == DashboardRevision(
            revision="rev-1",
            last_changed_at="2026-06-18T00:00:00",
        )
        assert notifier.subscriber_count == 0

    asyncio.run(run_test())


def test_initial_read_failure_does_not_emit_fake_revision(tmp_path: Path) -> None:
    """initial read 失敗時不送 revision=0，等下一輪成功才送真 revision。"""

    state = {"locked": True}

    def load_revision(_path: Path) -> DashboardRevision:
        if state["locked"]:
            raise DashboardRevisionUnavailable("database is locked")
        return DashboardRevision(revision="rev-2", last_changed_at="2026-06-18T00:00:01")

    async def run_test() -> None:
        notifier = DashboardRevisionNotifier(
            db_path=tmp_path / "app.db",
            get_dashboard_revision=load_revision,
            poll_interval_seconds=1.0,
        )
        await notifier.start()
        stream = notifier.subscribe()
        next_revision = asyncio.create_task(anext(stream))
        try:
            done, _pending = await asyncio.wait({next_revision}, timeout=0.05)
            assert not done

            state["locked"] = False
            notifier.wake()
            revision = await asyncio.wait_for(next_revision, timeout=0.5)
        finally:
            await stream.aclose()
            await notifier.stop()

        assert revision.revision == "rev-2"

    asyncio.run(run_test())


def test_revision_change_broadcasts_to_multiple_subscribers(tmp_path: Path) -> None:
    """多個 subscribers 共享同一 watcher，revision 變更會廣播給每個 tab。"""

    state = {"revision": "rev-1"}

    def load_revision(_path: Path) -> DashboardRevision:
        return DashboardRevision(
            revision=state["revision"],
            last_changed_at="2026-06-18T00:00:00",
        )

    async def run_test() -> None:
        notifier = DashboardRevisionNotifier(
            db_path=tmp_path / "app.db",
            get_dashboard_revision=load_revision,
            poll_interval_seconds=1.0,
        )
        await notifier.start()
        first = notifier.subscribe()
        second = notifier.subscribe()
        try:
            assert (await asyncio.wait_for(anext(first), timeout=0.5)).revision == "rev-1"
            assert (await asyncio.wait_for(anext(second), timeout=0.5)).revision == "rev-1"

            state["revision"] = "rev-2"
            notifier.wake()

            first_next = await asyncio.wait_for(anext(first), timeout=0.5)
            second_next = await asyncio.wait_for(anext(second), timeout=0.5)
        finally:
            await first.aclose()
            await second.aclose()
            await notifier.stop()

        assert first_next.revision == "rev-2"
        assert second_next.revision == "rev-2"

    asyncio.run(run_test())


def test_subscribers_reuse_latest_revision_without_per_client_db_read(
    tmp_path: Path,
) -> None:
    """latest 已初始化後，新 subscriber 不應各自重讀 DB revision。"""

    calls = 0

    def load_revision(_path: Path) -> DashboardRevision:
        nonlocal calls
        calls += 1
        return DashboardRevision(revision="rev-1")

    async def run_test() -> None:
        notifier = DashboardRevisionNotifier(
            db_path=tmp_path / "app.db",
            get_dashboard_revision=load_revision,
            poll_interval_seconds=1.0,
        )
        first = notifier.subscribe()
        second = notifier.subscribe()
        try:
            assert (await asyncio.wait_for(anext(first), timeout=0.5)).revision == "rev-1"
            assert (await asyncio.wait_for(anext(second), timeout=0.5)).revision == "rev-1"
        finally:
            await first.aclose()
            await second.aclose()
            await notifier.stop()

        assert calls == 1

    asyncio.run(run_test())


def test_concurrent_cold_subscribers_share_single_initial_db_read(
    tmp_path: Path,
) -> None:
    """多個冷啟動 subscribers 併發接入時，只允許第一個 initial read 打 DB。"""

    calls = 0
    calls_lock = Lock()
    release_loader = Event()

    def load_revision(_path: Path) -> DashboardRevision:
        nonlocal calls
        with calls_lock:
            calls += 1
        release_loader.wait(timeout=1)
        return DashboardRevision(revision="rev-1")

    async def run_test() -> None:
        notifier = DashboardRevisionNotifier(
            db_path=tmp_path / "app.db",
            get_dashboard_revision=load_revision,
            poll_interval_seconds=1.0,
        )
        streams = [notifier.subscribe() for _index in range(5)]
        tasks = [asyncio.create_task(anext(stream)) for stream in streams]
        try:
            while True:
                with calls_lock:
                    current_calls = calls
                if current_calls:
                    break
                await asyncio.sleep(0)
            await asyncio.sleep(0.05)
            with calls_lock:
                assert calls == 1
            release_loader.set()
            revisions = await asyncio.wait_for(asyncio.gather(*tasks), timeout=0.5)
        finally:
            release_loader.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            for stream in streams:
                await stream.aclose()
            await notifier.stop()

        assert [revision.revision for revision in revisions] == ["rev-1"] * 5
        assert notifier.subscriber_count == 0

    asyncio.run(run_test())


def test_slow_subscriber_receives_latest_revision_only(tmp_path: Path) -> None:
    """subscriber delivery 採 last-value-only，不累積每個中間 revision。"""

    state = {"revision": "rev-1"}

    def load_revision(_path: Path) -> DashboardRevision:
        return DashboardRevision(
            revision=state["revision"],
            last_changed_at="2026-06-18T00:00:00",
        )

    async def run_test() -> None:
        notifier = DashboardRevisionNotifier(
            db_path=tmp_path / "app.db",
            get_dashboard_revision=load_revision,
            poll_interval_seconds=1.0,
        )
        stream = notifier.subscribe()
        try:
            assert (await asyncio.wait_for(anext(stream), timeout=0.5)).revision == "rev-1"
            state["revision"] = "rev-2"
            await notifier._read_and_publish_once()
            state["revision"] = "rev-3"
            await notifier._read_and_publish_once()

            revision = await asyncio.wait_for(anext(stream), timeout=0.5)
        finally:
            await stream.aclose()
            await notifier.stop()

        assert revision.revision == "rev-3"

    asyncio.run(run_test())


def test_wake_without_revision_change_does_not_emit_event(tmp_path: Path) -> None:
    """wake 只觸發重讀；revision 未變時不送 dashboard_revision event。"""

    def load_revision(_path: Path) -> DashboardRevision:
        return DashboardRevision(revision="rev-1")

    async def run_test() -> None:
        notifier = DashboardRevisionNotifier(
            db_path=tmp_path / "app.db",
            get_dashboard_revision=load_revision,
            poll_interval_seconds=1.0,
        )
        await notifier.start()
        stream = notifier.subscribe()
        next_revision: asyncio.Task[DashboardRevision] | None = None
        try:
            assert (await asyncio.wait_for(anext(stream), timeout=0.5)).revision == "rev-1"
            notifier.wake()
            next_revision = asyncio.create_task(anext(stream))
            done, _pending = await asyncio.wait({next_revision}, timeout=0.05)
            assert not done
        finally:
            if next_revision is not None:
                next_revision.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await next_revision
            await stream.aclose()
            await notifier.stop()

    asyncio.run(run_test())


def test_stop_wakes_subscribers_and_is_idempotent(tmp_path: Path) -> None:
    """stop 會喚醒 subscribers、清 registry，重複呼叫與 stop 後 wake 都不掛住。"""

    async def run_test() -> None:
        notifier = DashboardRevisionNotifier(
            db_path=tmp_path / "app.db",
            get_dashboard_revision=lambda _path: DashboardRevision(revision="rev-1"),
            poll_interval_seconds=1.0,
        )
        await notifier.start()
        stream = notifier.subscribe()
        await asyncio.wait_for(anext(stream), timeout=0.5)
        next_revision = asyncio.create_task(anext(stream))

        await asyncio.wait_for(notifier.stop(), timeout=0.5)
        await asyncio.wait_for(notifier.stop(), timeout=0.5)
        notifier.wake()

        try:
            await asyncio.wait_for(next_revision, timeout=0.5)
        except StopAsyncIteration:
            pass
        else:
            raise AssertionError("subscriber should finish after notifier stop")

        stopped_stream = notifier.subscribe()
        try:
            try:
                await asyncio.wait_for(anext(stopped_stream), timeout=0.1)
            except StopAsyncIteration:
                pass
            else:
                raise AssertionError("subscribe after stop should finish immediately")
        finally:
            await stopped_stream.aclose()

        assert notifier.subscriber_count == 0
        assert not notifier.running

    asyncio.run(run_test())


def test_cancelled_stop_still_clears_task_and_subscribers(tmp_path: Path) -> None:
    """stop coroutine 被取消時仍要清 watcher task 與 subscriber registry。"""

    loader_started = Event()
    release_loader = Event()

    def load_revision(_path: Path) -> DashboardRevision:
        loader_started.set()
        release_loader.wait(timeout=1)
        return DashboardRevision(revision="rev-1")

    async def run_test() -> None:
        notifier = DashboardRevisionNotifier(
            db_path=tmp_path / "app.db",
            get_dashboard_revision=load_revision,
            poll_interval_seconds=1.0,
            stop_timeout_seconds=5.0,
        )
        await notifier.start()
        while not loader_started.is_set():
            await asyncio.sleep(0)

        stream = notifier.subscribe()
        next_revision = asyncio.create_task(anext(stream))
        while notifier.subscriber_count == 0:
            await asyncio.sleep(0)

        stop_task = asyncio.create_task(notifier.stop())
        await asyncio.sleep(0)
        stop_task.cancel()
        release_loader.set()
        try:
            await asyncio.wait_for(stop_task, timeout=0.5)
        except asyncio.CancelledError:
            pass

        next_revision.cancel()
        with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
            await next_revision
        await stream.aclose()

        assert notifier.subscriber_count == 0
        assert not notifier.running

    asyncio.run(run_test())
