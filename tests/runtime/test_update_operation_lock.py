"""Updater operation lock tests。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

import facebook_monitor.runtime.update_operation_lock as update_operation_lock
from facebook_monitor.runtime.update_operation_lock import acquire_update_operation_lock
from facebook_monitor.runtime.update_operation_lock import ensure_update_operation_lock
from facebook_monitor.runtime.update_operation_lock import UPDATE_OPERATION_LOCK_FILE_NAME
from facebook_monitor.runtime.update_operation_lock import UPDATE_OPERATION_LOCK_BUSY_MESSAGE
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


def test_update_operation_lock_blocks_other_process(tmp_path: Path) -> None:
    """跨 process 持有 lock 時，本 process 不可同時進入更新操作。"""

    runtime_dir = tmp_path / "runtime"
    ready_path = tmp_path / "child-ready.txt"
    child = _spawn_update_lock_holder(runtime_dir, ready_path)
    try:
        _wait_for_child_ready(child, ready_path)

        with pytest.raises(UpdateOperationLockError):
            with acquire_update_operation_lock(runtime_dir, "parent"):
                pass
    finally:
        _release_child_lock(child)


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


def test_update_operation_lock_releases_after_body_exception(tmp_path: Path) -> None:
    """context body 丟例外時，finally 仍需釋放 active registry 與 OS lock。"""

    runtime_dir = tmp_path / "runtime"

    with pytest.raises(RuntimeError, match="boom"):
        with acquire_update_operation_lock(runtime_dir, "first"):
            raise RuntimeError("boom")
    with acquire_update_operation_lock(runtime_dir, "second") as lock:
        assert lock.owner == "second"


def test_update_operation_lock_cleans_active_registry_when_setup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OS lock setup 失敗時，不可留下同 process active token。"""

    runtime_dir = tmp_path / "runtime"

    def fail_lock_file(*_args, **_kwargs) -> None:
        raise UpdateOperationLockError(
            UPDATE_OPERATION_LOCK_BUSY_MESSAGE,
            lock_path=runtime_dir / UPDATE_OPERATION_LOCK_FILE_NAME,
        )

    with monkeypatch.context() as patch:
        patch.setattr(update_operation_lock, "_lock_file", fail_lock_file)
        with pytest.raises(UpdateOperationLockError):
            with acquire_update_operation_lock(runtime_dir, "first"):
                pass

    with acquire_update_operation_lock(runtime_dir, "second") as lock:
        assert lock.owner == "second"


def test_update_operation_lock_canonicalizes_equivalent_runtime_paths(
    tmp_path: Path,
) -> None:
    """等價 runtime path 應被視為同一把 operation lock。"""

    runtime_dir = tmp_path / "runtime"
    equivalent = runtime_dir / "." / "child" / ".."

    with acquire_update_operation_lock(runtime_dir, "first"):
        with pytest.raises(UpdateOperationLockError):
            with acquire_update_operation_lock(equivalent, "second"):
                pass


def test_update_operation_lock_overwrites_stale_owner_file(tmp_path: Path) -> None:
    """沒有 OS lock 的舊 lock 檔只作診斷資訊，不可阻擋新操作。"""

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    lock_path = runtime_dir / UPDATE_OPERATION_LOCK_FILE_NAME
    lock_path.write_text('{"owner": "stale"}', encoding="utf-8")

    with acquire_update_operation_lock(runtime_dir, "fresh") as lock:
        assert lock.lock_path == lock_path

    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["owner"] == "fresh"


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


def _spawn_update_lock_holder(
    runtime_dir: Path,
    ready_path: Path,
) -> subprocess.Popen[str]:
    """啟動子 process 持有 update operation lock，直到 stdin 收到換行。"""

    code = (
        "from pathlib import Path\n"
        "from facebook_monitor.runtime.update_operation_lock import "
        "acquire_update_operation_lock\n"
        f"runtime_dir = Path({str(runtime_dir)!r})\n"
        f"ready_path = Path({str(ready_path)!r})\n"
        "with acquire_update_operation_lock(runtime_dir, 'child'):\n"
        "    ready_path.write_text('ready', encoding='utf-8')\n"
        "    input()\n"
    )
    env = os.environ.copy()
    root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=root,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for_child_ready(
    child: subprocess.Popen[str],
    ready_path: Path,
    *,
    timeout_seconds: float = 5,
) -> None:
    """等待 child 寫入 ready sentinel；失敗時帶出 stdout/stderr。"""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if ready_path.exists():
            return
        if child.poll() is not None:
            stdout, stderr = child.communicate(timeout=1)
            raise AssertionError(
                f"child lock holder exited before ready: {stdout=} {stderr=}"
            )
        time.sleep(0.05)
    child.kill()
    stdout, stderr = child.communicate(timeout=5)
    raise AssertionError(f"child lock holder did not become ready: {stdout=} {stderr=}")


def _release_child_lock(child: subprocess.Popen[str]) -> None:
    """釋放測試用子 process lock 並確認子 process 正常結束。"""

    if child.poll() is not None:
        return
    if child.stdin is not None:
        try:
            child.stdin.write("\n")
            child.stdin.flush()
        except OSError:
            pass
    try:
        stdout, stderr = child.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        stdout, stderr = child.communicate(timeout=5)
        raise AssertionError(f"child lock holder timed out: {stdout=} {stderr=}")
    assert child.returncode == 0, stderr
