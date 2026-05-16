"""更新交接檔測試。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.runtime.paths import resolve_runtime_paths
from facebook_monitor.updates.download import UpdateDownloadResult
from facebook_monitor.updates.handoff import load_pending_update
from facebook_monitor.updates.handoff import pending_update_path
from facebook_monitor.updates.handoff import write_pending_update
from facebook_monitor.updates.release_check import UpdateCheckResult


def update_check() -> UpdateCheckResult:
    """建立測試用更新檢查結果。"""

    return UpdateCheckResult(
        checked=True,
        status="available",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        current_version="0.1.0-rc1",
        latest_version="0.1.0",
        update_available=True,
        summary="有新版 0.1.0",
        detail="",
        release_url="https://github.com/OooPeople/facebook_monitor_py/releases/tag/v0.1.0",
        asset_name="facebook-monitor-0.1.0-windows-portable.zip",
        asset_download_url="https://downloads.example.test/app.zip",
        sha256_asset_name="facebook-monitor-0.1.0-windows-portable.zip.sha256",
        sha256_asset_download_url="https://downloads.example.test/app.zip.sha256",
        failure_reason="",
    )


def test_write_pending_update_contains_only_paths_and_hashes(tmp_path: Path) -> None:
    """pending update JSON 只保存 updater 所需的路徑與 hash。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    zip_path = paths.updates_dir / "0.1.0" / "facebook-monitor-0.1.0-windows-portable.zip"
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"zip")

    pending = write_pending_update(
        update_check=update_check(),
        download_result=UpdateDownloadResult(
            status="verified",
            downloaded=True,
            verified=True,
            file_path=zip_path,
            sha256_path=zip_path.with_suffix(zip_path.suffix + ".sha256"),
            expected_sha256="a" * 64,
            actual_sha256="a" * 64,
            failure_reason="",
        ),
        paths=paths,
    )
    loaded = load_pending_update(pending_update_path(paths.runtime_dir))

    assert loaded == pending
    assert loaded.zip_path == zip_path.resolve()
    assert loaded.app_base_dir == paths.app_base_dir
    assert loaded.data_dir == paths.data_dir
    assert loaded.db_path == paths.db_path
    assert loaded.profile_dir == paths.profile_dir
    assert loaded.logs_dir == paths.logs_dir
    assert loaded.runtime_dir == paths.runtime_dir


def test_write_pending_update_rejects_missing_verified_file(tmp_path: Path) -> None:
    """即使 download result 標示 verified，也不能寫出指向不存在 zip 的 handoff。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")

    try:
        write_pending_update(
            update_check=update_check(),
            download_result=UpdateDownloadResult(
                status="verified",
                downloaded=True,
                verified=True,
                file_path=paths.updates_dir / "missing.zip",
                sha256_path=None,
                expected_sha256="a" * 64,
                actual_sha256="a" * 64,
                failure_reason="",
            ),
            paths=paths,
        )
    except ValueError as exc:
        assert str(exc) == "download_result_file_missing"
    else:
        raise AssertionError("expected missing verified file to fail")


def test_load_pending_update_rejects_moved_handoff_file(tmp_path: Path) -> None:
    """pending JSON 不能被搬到 runtime dir 以外再交給 updater 使用。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    zip_path = paths.updates_dir / "0.1.0" / "facebook-monitor-0.1.0-windows-portable.zip"
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"zip")
    write_pending_update(
        update_check=update_check(),
        download_result=UpdateDownloadResult(
            status="verified",
            downloaded=True,
            verified=True,
            file_path=zip_path,
            sha256_path=zip_path.with_suffix(zip_path.suffix + ".sha256"),
            expected_sha256="a" * 64,
            actual_sha256="a" * 64,
            failure_reason="",
        ),
        paths=paths,
    )
    moved_path = tmp_path / "pending_update.json"
    moved_path.write_text(
        pending_update_path(paths.runtime_dir).read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    try:
        load_pending_update(moved_path)
    except ValueError as exc:
        assert str(exc) == "pending_update_path_mismatch"
    else:
        raise AssertionError("expected moved handoff file to fail")


def test_load_pending_update_accepts_utf8_bom(tmp_path: Path) -> None:
    """Windows 腳本若寫出 UTF-8 BOM，updater 仍可讀取 pending JSON。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    zip_path = paths.updates_dir / "0.1.0" / "facebook-monitor-0.1.0-windows-portable.zip"
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"zip")
    write_pending_update(
        update_check=update_check(),
        download_result=UpdateDownloadResult(
            status="verified",
            downloaded=True,
            verified=True,
            file_path=zip_path,
            sha256_path=zip_path.with_suffix(zip_path.suffix + ".sha256"),
            expected_sha256="a" * 64,
            actual_sha256="a" * 64,
            failure_reason="",
        ),
        paths=paths,
    )
    path = pending_update_path(paths.runtime_dir)
    text = path.read_text(encoding="utf-8")
    path.write_text(text, encoding="utf-8-sig")

    assert load_pending_update(path).zip_path == zip_path.resolve()
