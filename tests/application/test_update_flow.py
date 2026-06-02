"""Settings updater use case tests。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from facebook_monitor.application.update_flow import download_and_launch_verified_update
from facebook_monitor.application.update_flow import download_verified_update
from facebook_monitor.runtime.paths import resolve_runtime_paths
from facebook_monitor.runtime.update_operation_lock import acquire_update_operation_lock
from facebook_monitor.updates.capability import UpdateCapability
from facebook_monitor.updates.download import UpdateDownloadResult
from facebook_monitor.updates.launcher import UpdaterLaunchResult
from facebook_monitor.updates.release_check import UpdateCheckResult


def test_download_verified_update_returns_busy_when_operation_lock_is_active(
    tmp_path: Path,
) -> None:
    """已有更新流程時，下載流程應回 busy outcome 且不進入 release check。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    called = False

    async def fake_check_updates(**kwargs) -> UpdateCheckResult:
        nonlocal called
        called = True
        raise AssertionError("check_updates should not run while operation lock is busy")

    async def fake_download_update(**kwargs) -> UpdateDownloadResult:
        raise AssertionError("download_update should not run while operation lock is busy")

    with acquire_update_operation_lock(paths.runtime_dir, "external"):
        outcome = asyncio.run(
            download_verified_update(
                current_version="0.1.0",
                paths=paths,
                update_capability=UpdateCapability(
                    download_supported=True,
                    apply_supported=True,
                    unsupported_reason="",
                ),
                allow_env_repository_override=False,
                check_updates=fake_check_updates,
                download_update=fake_download_update,
                reveal_file_manager=lambda path: False,
                write_pending_update=lambda **kwargs: None,
                reveal_download=False,
            )
        )

    assert not outcome.ok
    assert outcome.stage == "operation_lock"
    assert "更新流程正在執行中" in outcome.message
    assert called is False


def test_download_and_launch_uses_one_operation_lock_without_self_deadlock(
    tmp_path: Path,
) -> None:
    """download-and-apply 內部不得呼叫 public wrapper 造成自己重入 lock。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    update_check = UpdateCheckResult(
        checked=True,
        status="available",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        current_version="0.1.0",
        latest_version="0.1.1",
        update_available=True,
        summary="available",
        detail="",
        release_url="https://example.test/release",
        asset_name="facebook-monitor-0.1.1-windows-portable.zip",
        asset_download_url="https://downloads.example.test/app.zip",
        sha256_asset_name="facebook-monitor-0.1.1-windows-portable.zip.sha256",
        sha256_asset_download_url="https://downloads.example.test/app.zip.sha256",
        failure_reason="",
    )
    checked = False
    downloaded = False
    handed_off = False
    launched = False

    async def fake_check_updates(**kwargs) -> UpdateCheckResult:
        nonlocal checked
        checked = True
        return update_check

    async def fake_download_update(**kwargs) -> UpdateDownloadResult:
        nonlocal downloaded
        downloaded = True
        file_path = paths.updates_dir / "0.1.1" / update_check.asset_name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"zip")
        return UpdateDownloadResult(
            status="verified",
            downloaded=True,
            verified=True,
            file_path=file_path,
            sha256_path=file_path.with_name(file_path.name + ".sha256"),
            expected_sha256="a" * 64,
            actual_sha256="a" * 64,
            failure_reason="",
            manifest_path=file_path.with_name("manifest.json"),
            manifest_signature_path=file_path.with_name("manifest.json.sig"),
            manifest_sha256="b" * 64,
            manifest_key_id="test-key",
        )

    def fake_write_pending_update(**kwargs) -> None:
        nonlocal handed_off
        handed_off = True

    def fake_launch_updater(**kwargs) -> UpdaterLaunchResult:
        nonlocal launched
        launched = True
        return UpdaterLaunchResult(
            launched=True,
            status="launched",
            message="launched",
            pid=123,
        )

    outcome = asyncio.run(
        download_and_launch_verified_update(
            current_version="0.1.0",
            paths=paths,
            update_capability=UpdateCapability(
                download_supported=True,
                apply_supported=True,
                unsupported_reason="",
            ),
            allow_env_repository_override=False,
            check_updates=fake_check_updates,
            download_update=fake_download_update,
            write_pending_update=fake_write_pending_update,
            launch_updater=fake_launch_updater,
            request_shutdown=lambda: True,
        )
    )

    assert outcome.ok
    assert outcome.stage == "launched"
    assert outcome.latest_version == "0.1.1"
    assert checked
    assert downloaded
    assert handed_off
    assert launched
