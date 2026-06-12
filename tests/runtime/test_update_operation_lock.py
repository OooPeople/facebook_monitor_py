"""Updater operation lock tests。"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from facebook_monitor.runtime.update_operation_lock import acquire_update_operation_lock
from facebook_monitor.runtime.update_operation_lock import ensure_update_operation_lock
from facebook_monitor.runtime.update_operation_lock import UPDATE_OPERATION_LOCK_FILE_NAME
from facebook_monitor.runtime.update_operation_lock import UpdateOperationLockError


def test_update_operation_lock_rejects_same_process_reentry(tmp_path) -> None:
    """同一 process 重複進入更新操作必須被擋下。"""

    runtime_dir = tmp_path / "runtime"

    with acquire_update_operation_lock(runtime_dir, "first") as lock:
        assert lock.lock_path.name == UPDATE_OPERATION_LOCK_FILE_NAME
        assert lock.lock_path.is_file()
        with pytest.raises(UpdateOperationLockError):
            with acquire_update_operation_lock(runtime_dir, "second"):
                pass


def test_update_operation_lock_releases_and_overwrites_owner_info(tmp_path) -> None:
    """釋放後可再次取得 lock，lock 檔只保留最新持有者資訊。"""

    runtime_dir = tmp_path / "runtime"

    with acquire_update_operation_lock(runtime_dir, "first"):
        pass
    with acquire_update_operation_lock(runtime_dir, "second") as lock:
        lock_path = lock.lock_path
    payload = json.loads(lock_path.read_text(encoding="utf-8"))

    assert payload["owner"] == "second"
    assert payload["pid"] == os.getpid()
    assert payload["started_at"]


def test_update_operation_context_reuses_settings_owner(tmp_path) -> None:
    """settings flow 持有 lock 時，底層 updater API 可重用同一個 context。"""

    runtime_dir = tmp_path / "runtime"

    with acquire_update_operation_lock(runtime_dir, "settings-download") as outer:
        with ensure_update_operation_lock(runtime_dir, "download-and-verify-update") as inner:
            assert inner is outer


def test_update_operation_context_does_not_reuse_external_owner(tmp_path) -> None:
    """測試或其他入口持有 external lock 時，底層 updater API 仍應視為 busy。"""

    runtime_dir = tmp_path / "runtime"

    with acquire_update_operation_lock(runtime_dir, "external"):
        with pytest.raises(UpdateOperationLockError):
            with ensure_update_operation_lock(runtime_dir, "download-and-verify-update"):
                pass


def test_update_operation_context_does_not_escape_to_child_task_after_release(
    tmp_path,
) -> None:
    """async child task 複製到舊 context 後，不可在 lock 釋放後重用過期 token。"""

    runtime_dir = tmp_path / "runtime"
    ready = asyncio.Event()
    proceed = asyncio.Event()

    async def child() -> bool:
        ready.set()
        await proceed.wait()
        with ensure_update_operation_lock(runtime_dir, "download-and-verify-update") as lock:
            return lock.owner == "download-and-verify-update"

    async def run_probe() -> bool:
        with acquire_update_operation_lock(runtime_dir, "settings-download"):
            task = asyncio.create_task(child())
            await ready.wait()
        proceed.set()
        return await task

    assert asyncio.run(run_probe())


def test_update_operation_context_does_not_reuse_stale_child_context_after_reacquire(
    tmp_path,
) -> None:
    """舊 child context 不可在同路徑 lock 被別人重取後重用過期 settings lock。"""

    runtime_dir = tmp_path / "runtime"
    ready = asyncio.Event()
    proceed = asyncio.Event()

    async def child() -> None:
        ready.set()
        await proceed.wait()
        with pytest.raises(UpdateOperationLockError):
            with ensure_update_operation_lock(runtime_dir, "download-and-verify-update"):
                pass

    async def run_probe() -> None:
        with acquire_update_operation_lock(runtime_dir, "settings-download"):
            task = asyncio.create_task(child())
            await ready.wait()
        with acquire_update_operation_lock(runtime_dir, "external"):
            proceed.set()
            await task

    asyncio.run(run_probe())
