"""更新交接檔測試。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from facebook_monitor.runtime.paths import RuntimePaths
from facebook_monitor.runtime.paths import resolve_runtime_paths
from facebook_monitor.updates.download import UpdateDownloadResult
from facebook_monitor.updates.download import VERIFIED_DOWNLOAD_SET_MARKER_NAME
from facebook_monitor.updates.download import VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION
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
    manifest_key_id: str = "test-key",
) -> UpdateDownloadResult:
    """建立含 signed manifest metadata 的 verified download result。"""

    manifest_path = zip_path.with_name(release_manifest_asset_name("0.1.0"))
    signature_path = zip_path.with_name(release_manifest_signature_asset_name("0.1.0"))
    manifest_path.write_text("manifest", encoding="utf-8")
    signature_path.write_text("sig", encoding="utf-8")
    expected_sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    sha256_path = zip_path.with_suffix(zip_path.suffix + ".sha256")
    sha256_path.write_text(f"{expected_sha256}  {zip_path.name}\n", encoding="utf-8")
    marker_path = zip_path.parent / VERIFIED_DOWNLOAD_SET_MARKER_NAME
    marker_path.write_text(
        json.dumps(
            {
                "schema_version": VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION,
                "asset_name": zip_path.name,
                "asset_sha256": expected_sha256,
                "asset_size": zip_path.stat().st_size,
                "sha256_name": sha256_path.name,
                "sha256_sha256": hashlib.sha256(sha256_path.read_bytes()).hexdigest(),
                "manifest_name": manifest_path.name,
                "manifest_sha256": manifest_sha256,
                "manifest_key_id": manifest_key_id,
                "manifest_signature_name": signature_path.name,
                "manifest_signature_sha256": hashlib.sha256(
                    signature_path.read_bytes()
                ).hexdigest(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return UpdateDownloadResult(
        status="verified",
        downloaded=True,
        verified=True,
        file_path=zip_path,
        sha256_path=sha256_path,
        expected_sha256=expected_sha256,
        actual_sha256=expected_sha256,
        failure_reason="",
        manifest_path=manifest_path,
        manifest_signature_path=signature_path,
        manifest_sha256=manifest_sha256,
        manifest_key_id=manifest_key_id,
        verified_set_marker_path=marker_path,
    )


def artifact_zip_path(paths: RuntimePaths) -> Path:
    """回傳測試用 atomic artifact set zip path。"""

    return (
        paths.updates_dir
        / "0.1.0"
        / "attempt-test"
        / "facebook-monitor-0.1.0-windows-portable.zip"
    )


def write_pending_payload(path: Path, pending: PendingUpdate) -> None:
    """寫出測試用 pending JSON payload。"""

    payload = dict(pending.__dict__)
    for key, value in list(payload.items()):
        if isinstance(value, Path):
            payload[key] = str(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_write_pending_update_contains_only_paths_and_hashes(tmp_path: Path) -> None:
    """pending update JSON 只保存 updater 所需的路徑與 hash。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    zip_path = artifact_zip_path(paths)
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
    zip_path = artifact_zip_path(paths)
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"zip")
    download_result = verified_download_result(zip_path)

    pending = write_pending_update(
        update_check=update_check(),
        download_result=download_result,
        paths=paths,
    )
    loaded = load_pending_update(pending_update_path(paths.runtime_dir))

    assert loaded == pending
    assert download_result.manifest_path is not None
    assert download_result.manifest_signature_path is not None
    assert loaded.manifest_path == download_result.manifest_path.resolve()
    assert loaded.manifest_signature_path == download_result.manifest_signature_path.resolve()
    assert loaded.manifest_sha256 == download_result.manifest_sha256
    assert loaded.manifest_key_id == "test-key"
    assert loaded.repository == "OooPeople/facebook_monitor_py"


def test_write_pending_update_rejects_verified_download_without_set_marker(
    tmp_path: Path,
) -> None:
    """verified download 沒有完整 set marker 時不可寫出 updater handoff。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    zip_path = artifact_zip_path(paths)
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"zip")
    download_result = verified_download_result(zip_path)
    assert download_result.verified_set_marker_path is not None
    download_result.verified_set_marker_path.unlink()

    try:
        write_pending_update(
            update_check=update_check(),
            download_result=download_result,
            paths=paths,
        )
    except ValueError as exc:
        assert str(exc) == "download_result_verified_set_missing"
    else:
        raise AssertionError("expected missing verified set marker to fail")


def test_write_pending_update_rejects_tampered_manifest_signature_after_marker(
    tmp_path: Path,
) -> None:
    """verified set marker 建立後 `.sig` 被替換時不可寫出 updater handoff。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    zip_path = artifact_zip_path(paths)
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"zip")
    download_result = verified_download_result(zip_path)
    assert download_result.manifest_signature_path is not None
    download_result.manifest_signature_path.write_text("changed", encoding="utf-8")

    try:
        write_pending_update(
            update_check=update_check(),
            download_result=download_result,
            paths=paths,
        )
    except ValueError as exc:
        assert str(exc) == "download_result_verified_set_mismatch"
    else:
        raise AssertionError("expected tampered signature to fail")


def test_write_pending_update_rejects_verified_download_without_manifest(
    tmp_path: Path,
) -> None:
    """verified download 若缺 signed manifest handoff metadata 不可交給 updater。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    zip_path = artifact_zip_path(paths)
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
    zip_path = artifact_zip_path(paths)
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
    zip_path = artifact_zip_path(paths)
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
    zip_path = artifact_zip_path(paths)
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


def test_load_pending_update_rejects_loose_artifact_set(tmp_path: Path) -> None:
    """pending JSON 指向 version dir loose zip 時，updater load path 必須拒絕。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    zip_path = paths.updates_dir / "0.1.0" / "facebook-monitor-0.1.0-windows-portable.zip"
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"zip")
    download_result = verified_download_result(zip_path)
    assert download_result.manifest_path is not None
    assert download_result.manifest_signature_path is not None
    pending = PendingUpdate(
        schema_version=1,
        version="0.1.0",
        repository="OooPeople/facebook_monitor_py",
        asset_name=zip_path.name,
        zip_path=zip_path.resolve(),
        expected_sha256=download_result.expected_sha256,
        actual_sha256=download_result.actual_sha256,
        app_base_dir=paths.app_base_dir.resolve(),
        data_dir=paths.data_dir.resolve(),
        db_path=paths.db_path.resolve(),
        profile_dir=paths.profile_dir.resolve(),
        logs_dir=paths.logs_dir.resolve(),
        runtime_dir=paths.runtime_dir.resolve(),
        created_at="2026-05-17T00:00:00+00:00",
        manifest_path=download_result.manifest_path.resolve(),
        manifest_signature_path=download_result.manifest_signature_path.resolve(),
        manifest_sha256=download_result.manifest_sha256,
        manifest_key_id=download_result.manifest_key_id,
    )
    path = pending_update_path(paths.runtime_dir)
    write_pending_payload(path, pending)

    try:
        load_pending_update(path)
    except ValueError as exc:
        assert str(exc) == "pending_update_artifact_set_invalid"
    else:
        raise AssertionError("expected loose artifact set to fail")


def test_validate_pending_update_rejects_nested_data_dir_under_app(
    tmp_path: Path,
) -> None:
    """data dir 若在 app root 內，必須是直接的 app/data，避免替換時刪到父層。"""

    app_base_dir = tmp_path / "app"
    data_dir = app_base_dir / "nested" / "data"
    zip_path = data_dir / "updates" / "0.1.0" / "attempt-test" / "update.zip"
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
    zip_path = data_dir / "updates" / "0.1.0" / "attempt-test" / "update.zip"
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
    zip_path = data_dir / "updates" / "0.1.0" / "attempt-test" / "update.zip"
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
    zip_path = data_dir / "updates" / "0.1.0" / "attempt-test" / "update.zip"
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
