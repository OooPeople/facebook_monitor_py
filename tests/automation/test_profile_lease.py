"""Automation profile lease tests。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

import facebook_monitor.automation.profile_lease as profile_lease
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.automation.profile_lease import LOCK_FILE_NAME
from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.one_shot_dispatch import OneShotScanOptions
from facebook_monitor.worker.one_shot_dispatch import run_one_shot_scan


def test_profile_lease_blocks_same_process_reentry(tmp_path: Path) -> None:
    """同一個 Python process 內不能重複持有同一個 profile。"""

    profile_dir = tmp_path / "profile"

    with acquire_profile_lease(profile_dir, "first"):
        with pytest.raises(ProfileLeaseError):
            with acquire_profile_lease(profile_dir, "second"):
                pass


def test_profile_lease_blocks_other_process(tmp_path: Path) -> None:
    """跨 process 持有 automation profile lease 時，本 process 必須回報 busy。"""

    profile_dir = tmp_path / "profile"
    ready_path = tmp_path / "child-ready.txt"
    child = _spawn_profile_lease_holder(profile_dir, ready_path)
    try:
        _wait_for_child_ready(child, ready_path)

        with pytest.raises(ProfileLeaseError):
            with acquire_profile_lease(profile_dir, "parent"):
                pass
    finally:
        _release_child_lease(child)


def test_profile_lease_releases_after_context_exit(tmp_path: Path) -> None:
    """lease 離開 context 後，同一個 profile 可再次被取得。"""

    profile_dir = tmp_path / "profile"

    with acquire_profile_lease(profile_dir, "first"):
        assert profile_dir.exists()

    with acquire_profile_lease(profile_dir, "second") as lease:
        assert lease.profile_dir == profile_dir.resolve()
        assert lease.lock_path.exists()


def test_profile_lease_releases_after_body_exception(tmp_path: Path) -> None:
    """context body 丟例外時，profile claim 與 OS lock 仍需釋放。"""

    profile_dir = tmp_path / "profile"

    with pytest.raises(RuntimeError, match="boom"):
        with acquire_profile_lease(profile_dir, "first"):
            raise RuntimeError("boom")
    with acquire_profile_lease(profile_dir, "second") as lease:
        assert lease.owner == "second"


def test_profile_lease_releases_claim_when_lock_setup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跨 process lock setup 失敗時，不可留下本 process profile claim。"""

    profile_dir = tmp_path / "profile"

    def fail_lock_file(*_args, **_kwargs) -> None:
        raise ProfileLeaseError("busy")

    with monkeypatch.context() as patch:
        patch.setattr(profile_lease, "_lock_file", fail_lock_file)
        with pytest.raises(ProfileLeaseError):
            with acquire_profile_lease(profile_dir, "first"):
                pass

    with acquire_profile_lease(profile_dir, "second") as lease:
        assert lease.owner == "second"


def test_profile_lease_canonicalizes_equivalent_profile_paths(tmp_path: Path) -> None:
    """等價 profile path 應被視為同一個 automation profile。"""

    profile_dir = tmp_path / "profile"
    equivalent = profile_dir / "." / "child" / ".."

    with acquire_profile_lease(profile_dir, "first"):
        with pytest.raises(ProfileLeaseError):
            with acquire_profile_lease(equivalent, "second"):
                pass


def test_profile_lease_overwrites_stale_owner_file(tmp_path: Path) -> None:
    """沒有 OS lock 的舊 owner file 不可阻擋新 lease，且應被覆寫。"""

    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    lock_path = profile_dir / LOCK_FILE_NAME
    lock_path.write_text("owner=stale\npid=0\n", encoding="utf-8")

    with acquire_profile_lease(profile_dir, "fresh") as lease:
        assert lease.lock_path == lock_path

    lock_text = lock_path.read_text(encoding="utf-8")
    assert "owner=fresh" in lock_text
    assert "owner=stale" not in lock_text


def test_profile_lease_allows_different_profiles(tmp_path: Path) -> None:
    """不同 automation profile 可以同時持有，不互相阻塞。"""

    first_profile = tmp_path / "first"
    second_profile = tmp_path / "second"

    with acquire_profile_lease(first_profile, "first"):
        with acquire_profile_lease(second_profile, "second") as lease:
            assert lease.profile_dir == second_profile.resolve()


def test_one_shot_worker_reports_profile_locked_before_playwright(
    tmp_path: Path,
) -> None:
    """one-shot worker 遇到 profile lease 衝突時會記錄 profile_locked。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )

    with acquire_profile_lease(profile_dir, "test holder"):
        with pytest.raises(WorkerFailure) as exc_info:
            run_one_shot_scan(
                OneShotScanOptions(
                    db_path=db_path,
                    profile_dir=profile_dir,
                    target_id=target.id,
                )
            )

    assert exc_info.value.reason == "profile_locked"
    with SqliteApplicationContext(db_path) as app:
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.FAILED
    assert latest_scan.error_message.startswith("瀏覽器設定檔使用中：")


def _spawn_profile_lease_holder(
    profile_dir: Path,
    ready_path: Path,
) -> subprocess.Popen[str]:
    """啟動子 process 持有 profile lease，直到 stdin 收到換行。"""

    code = (
        "from pathlib import Path\n"
        "from facebook_monitor.automation.profile_lease import acquire_profile_lease\n"
        f"profile_dir = Path({str(profile_dir)!r})\n"
        f"ready_path = Path({str(ready_path)!r})\n"
        "with acquire_profile_lease(profile_dir, 'child'):\n"
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
                f"child profile lease exited before ready: {stdout=} {stderr=}"
            )
        time.sleep(0.05)
    child.kill()
    stdout, stderr = child.communicate(timeout=5)
    raise AssertionError(f"child profile lease did not become ready: {stdout=} {stderr=}")


def _release_child_lease(child: subprocess.Popen[str]) -> None:
    """釋放測試用子 process profile lease 並確認子 process 正常結束。"""

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
        raise AssertionError(f"child profile lease timed out: {stdout=} {stderr=}")
    assert child.returncode == 0, stderr
