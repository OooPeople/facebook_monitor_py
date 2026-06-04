"""更新交接檔測試。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from facebook_monitor.runtime.paths import resolve_runtime_paths
from facebook_monitor.updates.download import UpdateDownloadResult
from facebook_monitor.updates.handoff import load_pending_update
from facebook_monitor.updates.handoff import PendingUpdate
from facebook_monitor.updates.handoff import pending_update_path
from facebook_monitor.updates.handoff import validate_pending_update_paths
from facebook_monitor.updates.handoff import write_pending_update
from facebook_monitor.updates.manifest import release_manifest_asset_name
from facebook_monitor.updates.manifest import release_manifest_signature_asset_name
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
        manifest_asset_name=release_manifest_asset_name("0.1.0"),
        manifest_asset_download_url="https://downloads.example.test/manifest.json",
        manifest_signature_asset_name=release_manifest_signature_asset_name("0.1.0"),
        manifest_signature_asset_download_url="https://downloads.example.test/manifest.json.sig",
    )


def verified_download_result(
    zip_path: Path,
    *,
    expected_sha256: str = "a" * 64,
    actual_sha256: str = "a" * 64,
    manifest_sha256: str = "b" * 64,
    manifest_key_id: str = "test-key",
) -> UpdateDownloadResult:
    """建立含 signed manifest metadata 的 verified download result。"""

    manifest_path = zip_path.with_name(release_manifest_asset_name("0.1.0"))
    signature_path = zip_path.with_name(release_manifest_signature_asset_name("0.1.0"))
    manifest_path.write_text("manifest", encoding="utf-8")
    signature_path.write_text("sig", encoding="utf-8")
    return UpdateDownloadResult(
        status="verified",
        downloaded=True,
        verified=True,
        file_path=zip_path,
        sha256_path=zip_path.with_suffix(zip_path.suffix + ".sha256"),
        expected_sha256=expected_sha256,
        actual_sha256=actual_sha256,
        failure_reason="",
        manifest_path=manifest_path,
        manifest_signature_path=signature_path,
        manifest_sha256=manifest_sha256,
        manifest_key_id=manifest_key_id,
    )


def test_write_pending_update_contains_only_paths_and_hashes(tmp_path: Path) -> None:
    """pending update JSON 只保存 updater 所需的路徑與 hash。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    zip_path = paths.updates_dir / "0.1.0" / "facebook-monitor-0.1.0-windows-portable.zip"
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"zip")

    pending = write_pending_update(
        update_check=update_check(),
        download_result=verified_download_result(zip_path),
        paths=paths,
    )
    loaded = load_pending_update(pending_update_path(paths.runtime_dir))

    assert loaded == pending
    assert loaded.repository == "OooPeople/facebook_monitor_py"
    assert loaded.zip_path == zip_path.resolve()
    assert loaded.app_base_dir == paths.app_base_dir
    assert loaded.data_dir == paths.data_dir
    assert loaded.db_path == paths.db_path
    assert loaded.profile_dir == paths.profile_dir
    assert loaded.logs_dir == paths.logs_dir
    assert loaded.runtime_dir == paths.runtime_dir


def test_write_pending_update_preserves_signed_manifest_metadata(tmp_path: Path) -> None:
    """handoff 要保存 manifest digest，供 updater 套用前二次檢查。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    zip_path = paths.updates_dir / "0.1.0" / "facebook-monitor-0.1.0-windows-portable.zip"
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"zip")
    manifest_path = zip_path.with_name("facebook-monitor-0.1.0-manifest.json")
    signature_path = zip_path.with_name("facebook-monitor-0.1.0-manifest.json.sig")
    manifest_path.write_text("manifest", encoding="utf-8")
    signature_path.write_text("sig", encoding="utf-8")

    pending = write_pending_update(
        update_check=update_check(),
        download_result=UpdateDownloadResult(
            status="verified",
            downloaded=True,
            verified=True,
            file_path=zip_path,
            sha256_path=None,
            expected_sha256="a" * 64,
            actual_sha256="a" * 64,
            failure_reason="",
            manifest_path=manifest_path,
            manifest_signature_path=signature_path,
            manifest_sha256="b" * 64,
            manifest_key_id="test-key",
        ),
        paths=paths,
    )
    loaded = load_pending_update(pending_update_path(paths.runtime_dir))

    assert loaded == pending
    assert loaded.manifest_path == manifest_path.resolve()
    assert loaded.manifest_signature_path == signature_path.resolve()
    assert loaded.manifest_sha256 == "b" * 64
    assert loaded.manifest_key_id == "test-key"
    assert loaded.repository == "OooPeople/facebook_monitor_py"


def test_write_pending_update_rejects_verified_download_without_manifest(
    tmp_path: Path,
) -> None:
    """verified download 若缺 signed manifest handoff metadata 不可交給 updater。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    zip_path = paths.updates_dir / "0.1.0" / "facebook-monitor-0.1.0-windows-portable.zip"
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"zip")

    try:
        write_pending_update(
            update_check=update_check(),
            download_result=UpdateDownloadResult(
                status="verified",
                downloaded=True,
                verified=True,
                file_path=zip_path,
                sha256_path=None,
                expected_sha256="a" * 64,
                actual_sha256="a" * 64,
                failure_reason="",
            ),
            paths=paths,
        )
    except ValueError as exc:
        assert str(exc) == "download_result_manifest_missing"
    else:
        raise AssertionError("expected missing manifest metadata to fail")


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


def test_write_pending_update_rejects_existing_tmp_symlink(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """pending handoff 暫存檔若已是 symlink，不可 follow 後覆寫外部檔案。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    zip_path = paths.updates_dir / "0.1.0" / "facebook-monitor-0.1.0-windows-portable.zip"
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"zip")
    paths.runtime_dir.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    tmp_handoff = paths.runtime_dir / ".pending_update.json.fixed.tmp"
    try:
        tmp_handoff.symlink_to(outside)
    except (NotImplementedError, OSError):
        return
    monkeypatch.setattr(
        "facebook_monitor.updates.handoff.uuid.uuid4",
        lambda: SimpleNamespace(hex="fixed"),
    )

    try:
        write_pending_update(
            update_check=update_check(),
            download_result=verified_download_result(zip_path),
            paths=paths,
        )
    except ValueError as exc:
        assert str(exc) == "pending_update_tmp_unsafe"
    else:
        raise AssertionError("expected symlinked handoff temp file to fail")
    assert outside.read_text(encoding="utf-8") == "keep"


def test_load_pending_update_rejects_moved_handoff_file(tmp_path: Path) -> None:
    """pending JSON 不能被搬到 runtime dir 以外再交給 updater 使用。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    zip_path = paths.updates_dir / "0.1.0" / "facebook-monitor-0.1.0-windows-portable.zip"
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"zip")
    write_pending_update(
        update_check=update_check(),
        download_result=verified_download_result(zip_path),
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
        download_result=verified_download_result(zip_path),
        paths=paths,
    )
    path = pending_update_path(paths.runtime_dir)
    text = path.read_text(encoding="utf-8")
    path.write_text(text, encoding="utf-8-sig")

    assert load_pending_update(path).zip_path == zip_path.resolve()


def test_validate_pending_update_rejects_nested_data_dir_under_app(
    tmp_path: Path,
) -> None:
    """data dir 若在 app root 內，必須是直接的 app/data，避免替換時刪到父層。"""

    app_base_dir = tmp_path / "app"
    data_dir = app_base_dir / "nested" / "data"
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"zip")
    pending = PendingUpdate(
        schema_version=1,
        version="0.1.0",
        repository="OooPeople/facebook_monitor_py",
        asset_name=zip_path.name,
        zip_path=zip_path,
        expected_sha256="a" * 64,
        actual_sha256="a" * 64,
        app_base_dir=app_base_dir,
        data_dir=data_dir,
        db_path=data_dir / "app.db",
        profile_dir=data_dir / "profiles" / "automation_default",
        logs_dir=data_dir / "logs",
        runtime_dir=data_dir / "runtime",
        created_at="2026-05-17T00:00:00+00:00",
    )

    try:
        validate_pending_update_paths(pending)
    except ValueError as exc:
        assert str(exc) == "pending_update_data_dir_must_be_app_data"
    else:
        raise AssertionError("expected nested app data dir to fail")


def test_validate_pending_update_rejects_logs_dir_inside_app_outside_data(
    tmp_path: Path,
) -> None:
    """logs dir 不可落在 app root 內但 data dir 外，避免 updater 替換時刪除。"""

    app_base_dir = tmp_path / "app"
    data_dir = app_base_dir / "data"
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"zip")
    pending = PendingUpdate(
        schema_version=1,
        version="0.1.0",
        repository="OooPeople/facebook_monitor_py",
        asset_name=zip_path.name,
        zip_path=zip_path,
        expected_sha256="a" * 64,
        actual_sha256="a" * 64,
        app_base_dir=app_base_dir,
        data_dir=data_dir,
        db_path=data_dir / "app.db",
        profile_dir=data_dir / "profiles" / "automation_default",
        logs_dir=app_base_dir / "logs",
        runtime_dir=data_dir / "runtime",
        created_at="2026-05-17T00:00:00+00:00",
    )

    try:
        validate_pending_update_paths(pending)
    except ValueError as exc:
        assert str(exc) == "pending_update_logs_dir_unsafe"
    else:
        raise AssertionError("expected app-local logs dir outside data to fail")


def test_validate_pending_update_rejects_dotdot_version(
    tmp_path: Path,
) -> None:
    """pending version 不能用 `..` 讓 updater 工作目錄退回 runtime root。"""

    app_base_dir = tmp_path / "app"
    data_dir = app_base_dir / "data"
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"zip")
    pending = PendingUpdate(
        schema_version=1,
        version="..",
        repository="OooPeople/facebook_monitor_py",
        asset_name=zip_path.name,
        zip_path=zip_path,
        expected_sha256="a" * 64,
        actual_sha256="a" * 64,
        app_base_dir=app_base_dir,
        data_dir=data_dir,
        db_path=data_dir / "app.db",
        profile_dir=data_dir / "profiles" / "automation_default",
        logs_dir=data_dir / "logs",
        runtime_dir=data_dir / "runtime",
        created_at="2026-05-17T00:00:00+00:00",
    )

    try:
        validate_pending_update_paths(pending)
    except ValueError as exc:
        assert str(exc) == "invalid_asset_name"
    else:
        raise AssertionError("expected dotdot version to fail")


def test_validate_pending_update_allows_logs_dir_inside_data(
    tmp_path: Path,
) -> None:
    """portable app/data/logs 是 updater 可保留的安全 logs 位置。"""

    app_base_dir = tmp_path / "app"
    data_dir = app_base_dir / "data"
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"zip")
    pending = PendingUpdate(
        schema_version=1,
        version="0.1.0",
        repository="OooPeople/facebook_monitor_py",
        asset_name=zip_path.name,
        zip_path=zip_path,
        expected_sha256="a" * 64,
        actual_sha256="a" * 64,
        app_base_dir=app_base_dir,
        data_dir=data_dir,
        db_path=data_dir / "app.db",
        profile_dir=data_dir / "profiles" / "automation_default",
        logs_dir=data_dir / "logs",
        runtime_dir=data_dir / "runtime",
        created_at="2026-05-17T00:00:00+00:00",
    )

    validate_pending_update_paths(pending)
