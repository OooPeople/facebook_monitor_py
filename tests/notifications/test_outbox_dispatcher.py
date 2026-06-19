"""Notification outbox background dispatcher tests。"""

from __future__ import annotations

from pathlib import Path
from threading import Event
from threading import Lock

from facebook_monitor.notifications.outbox_dispatcher import NotificationOutboxDispatcher
from facebook_monitor.notifications.outbox_dispatcher import (
    register_notification_outbox_dispatcher,
)
from facebook_monitor.notifications.outbox_dispatcher import (
    unregister_notification_outbox_dispatcher,
)
from facebook_monitor.notifications.outbox_dispatcher import (
    wake_notification_outbox_dispatcher_for_db,
)


def test_dispatcher_start_drains_pending_backlog(tmp_path: Path) -> None:
    """dispatcher 啟動時會 wake 一次，處理已存在的 pending backlog。"""

    calls: list[Path] = []
    called = Event()

    def fake_dispatch(**kwargs: object) -> int:
        db_path = kwargs["db_path"]
        assert isinstance(db_path, Path)
        calls.append(db_path)
        called.set()
        return 3

    dispatcher = NotificationOutboxDispatcher(
        db_path=tmp_path / "app.db",
        dispatch_pending=fake_dispatch,
    )
    dispatcher.start()
    try:
        assert called.wait(2)
        assert calls == [tmp_path / "app.db"]
        assert dispatcher.last_dispatch_count == 3
    finally:
        assert dispatcher.stop(timeout_seconds=2)


def test_dispatcher_wake_does_not_run_dispatch_concurrently(tmp_path: Path) -> None:
    """多次 wake 只由同一背景 thread 串行 dispatch，不並行呼叫 sender path。"""

    entered = Event()
    release = Event()
    done = Event()
    lock = Lock()
    active = 0
    max_active = 0

    def fake_dispatch(**_kwargs: object) -> int:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        entered.set()
        release.wait(2)
        with lock:
            active -= 1
        done.set()
        return 1

    dispatcher = NotificationOutboxDispatcher(
        db_path=tmp_path / "app.db",
        dispatch_pending=fake_dispatch,
    )
    dispatcher.start(wake_on_start=False)
    try:
        assert dispatcher.wake() is True
        assert entered.wait(2)
        assert dispatcher.wake() is True
        assert dispatcher.wake() is True
        release.set()
        assert done.wait(2)
        assert max_active == 1
    finally:
        assert dispatcher.stop(timeout_seconds=2)


def test_dispatcher_survives_dispatch_exception(tmp_path: Path) -> None:
    """dispatch 例外只記錄錯誤，不會讓背景 dispatcher 永久死亡。"""

    calls = 0
    first_call = Event()
    second_call = Event()

    def flaky_dispatch(**_kwargs: object) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_call.set()
            raise RuntimeError("temporary outage")
        second_call.set()
        return 1

    dispatcher = NotificationOutboxDispatcher(
        db_path=tmp_path / "app.db",
        dispatch_pending=flaky_dispatch,
    )
    dispatcher.start(wake_on_start=False)
    try:
        assert dispatcher.wake() is True
        assert first_call.wait(2)
        assert calls == 1
        assert dispatcher.last_error.startswith("RuntimeError:")
        assert dispatcher.wake() is True
        assert second_call.wait(2)
        assert calls == 2
        assert dispatcher.last_error == ""
    finally:
        assert dispatcher.stop(timeout_seconds=2)


def test_dispatcher_stop_is_bounded_when_dispatch_blocks(tmp_path: Path) -> None:
    """sender path 卡住時 stop 不可無限等待；釋放後可完成收尾。"""

    entered = Event()
    release = Event()

    def blocking_dispatch(**_kwargs: object) -> int:
        entered.set()
        release.wait(2)
        return 1

    dispatcher = NotificationOutboxDispatcher(
        db_path=tmp_path / "app.db",
        dispatch_pending=blocking_dispatch,
        stop_timeout_seconds=0.01,
    )
    dispatcher.start(wake_on_start=False)
    assert dispatcher.wake() is True
    assert entered.wait(2)
    try:
        assert dispatcher.stop() is False
    finally:
        release.set()
    assert dispatcher.stop(timeout_seconds=2) is True


def test_registry_wakes_only_registered_dispatcher(tmp_path: Path) -> None:
    """after-commit registry 只喚醒已註冊且尚未解除註冊的 dispatcher。"""

    called = Event()

    def fake_dispatch(**_kwargs: object) -> int:
        called.set()
        return 1

    db_path = tmp_path / "app.db"
    dispatcher = NotificationOutboxDispatcher(
        db_path=db_path,
        dispatch_pending=fake_dispatch,
    )
    dispatcher.start(wake_on_start=False)
    register_notification_outbox_dispatcher(db_path, dispatcher)
    try:
        assert wake_notification_outbox_dispatcher_for_db(db_path) is True
        assert called.wait(2)
    finally:
        unregister_notification_outbox_dispatcher(db_path, dispatcher)
        assert dispatcher.stop(timeout_seconds=2)

    assert wake_notification_outbox_dispatcher_for_db(db_path) is False
