"""Automation profile lease tests。"""

from __future__ import annotations

from pathlib import Path

import pytest

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
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


def test_profile_lease_releases_after_context_exit(tmp_path: Path) -> None:
    """lease 離開 context 後，同一個 profile 可再次被取得。"""

    profile_dir = tmp_path / "profile"

    with acquire_profile_lease(profile_dir, "first"):
        assert profile_dir.exists()

    with acquire_profile_lease(profile_dir, "second") as lease:
        assert lease.profile_dir == profile_dir.resolve()
        assert lease.lock_path.exists()


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
