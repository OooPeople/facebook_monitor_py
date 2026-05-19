"""獨立 updater 套用流程測試。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

from facebook_monitor.runtime.instance_lock import acquire_app_instance_lock
from facebook_monitor.updates.apply import apply_loaded_pending_update_file
from facebook_monitor.updates.apply import apply_pending_update_file
from facebook_monitor.updates.apply import apply_pending_update
from facebook_monitor.updates.apply import _backup_folder_name
from facebook_monitor.updates.apply import _cleanup_old_backup_dirs
from facebook_monitor.updates.apply import safe_extract_zip
from facebook_monitor.updates.apply import UpdaterApplyResult
from facebook_monitor.updates.handoff import PendingUpdate


def make_app_root(root: Path, *, exe_text: str) -> None:
    """建立最小 PyInstaller onedir 目錄。"""

    (root / "_internal" / "browser").mkdir(parents=True)
    (root / "_internal" / "assets").mkdir(parents=True)
    (root / "_internal" / "browser" / "chrome.exe").write_text("chrome", encoding="utf-8")
    (root / "_internal" / "assets" / "facebook-monitor.ico").write_text(
        "icon",
        encoding="utf-8",
    )
    (root / "_internal" / "assets" / "facebook-monitor-tray.ico").write_text(
        "tray",
        encoding="utf-8",
    )
    (root / "facebook-monitor.exe").write_text(exe_text, encoding="utf-8")
    (root / "facebook-monitor-updater.exe").write_text("updater", encoding="utf-8")


def make_macos_app_root(root: Path, *, app_text: str) -> None:
    """建立最小 macOS arm64 onedir 目錄。"""

    browser = root / "browser" / "Chromium.app" / "Contents" / "MacOS"
    browser.mkdir(parents=True)
    browser_exe = browser / "Chromium"
    browser_exe.write_text("chromium", encoding="utf-8")
    browser_exe.chmod(0o755)
    (root / "_internal").mkdir(parents=True)
    (root / "_internal" / "python").write_text("runtime", encoding="utf-8")
    app_entry = root / "facebook-monitor"
    updater_entry = root / "facebook-monitor-updater"
    app_entry.write_text(app_text, encoding="utf-8")
    updater_entry.write_text("updater", encoding="utf-8")
    app_entry.chmod(0o755)
    updater_entry.chmod(0o755)


def make_macos_chrome_for_testing_app_root(root: Path, *, app_text: str) -> None:
    """建立 Playwright Apple Silicon 目前常見的 macOS onedir fixture。"""

    browser = root / "browser" / "Google Chrome for Testing.app" / "Contents" / "MacOS"
    browser.mkdir(parents=True)
    browser_exe = browser / "Google Chrome for Testing"
    browser_exe.write_text("chromium", encoding="utf-8")
    browser_exe.chmod(0o755)
    (root / "_internal").mkdir(parents=True)
    (root / "_internal" / "python").write_text("runtime", encoding="utf-8")
    app_entry = root / "facebook-monitor"
    updater_entry = root / "facebook-monitor-updater"
    app_entry.write_text(app_text, encoding="utf-8")
    updater_entry.write_text("updater", encoding="utf-8")
    app_entry.chmod(0o755)
    updater_entry.chmod(0o755)


def make_update_zip(zip_path: Path, *, exe_text: str) -> str:
    """建立含單層 facebook-monitor 目錄的 update zip，回傳 SHA256。"""

    source_root = zip_path.parent / "new" / "facebook-monitor"
    make_app_root(source_root, exe_text=exe_text)
    with zipfile.ZipFile(zip_path, "w") as archive:
        for file_path in source_root.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(source_root.parent))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    return digest


def make_macos_update_zip(zip_path: Path, *, app_text: str) -> str:
    """建立含單層 facebook-monitor 目錄的 macOS update zip。"""

    source_root = zip_path.parent / "new" / "facebook-monitor"
    make_macos_app_root(source_root, app_text=app_text)
    with zipfile.ZipFile(zip_path, "w") as archive:
        for file_path in source_root.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(source_root.parent))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    return digest


def make_macos_chrome_for_testing_update_zip(zip_path: Path, *, app_text: str) -> str:
    """建立含 Google Chrome for Testing.app 的 macOS update zip。"""

    source_root = zip_path.parent / "new" / "facebook-monitor"
    make_macos_chrome_for_testing_app_root(source_root, app_text=app_text)
    with zipfile.ZipFile(zip_path, "w") as archive:
        for file_path in source_root.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(source_root.parent))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    return digest


def pending_update(tmp_path: Path, *, zip_path: Path, digest: str) -> PendingUpdate:
    """建立測試用 pending update。"""

    return PendingUpdate(
        schema_version=1,
        version="0.1.0",
        asset_name=zip_path.name,
        zip_path=zip_path,
        expected_sha256=digest,
        actual_sha256=digest,
        app_base_dir=tmp_path / "app",
        data_dir=tmp_path / "app" / "data",
        db_path=tmp_path / "app" / "data" / "app.db",
        profile_dir=tmp_path / "app" / "data" / "profiles" / "automation_default",
        logs_dir=tmp_path / "app" / "data" / "logs",
        runtime_dir=tmp_path / "app" / "data" / "runtime",
        created_at="2026-05-17T00:00:00+00:00",
    )


def test_apply_pending_update_replaces_app_files_but_preserves_data(tmp_path: Path) -> None:
    """updater 會替換 app files，並保留 portable data dir。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    (data_dir / "app.db").write_text("user data", encoding="utf-8")
    zip_path = tmp_path / "app" / "data" / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "applied"
    assert result.applied
    assert (app_root / "facebook-monitor.exe").read_text(encoding="utf-8") == "new"
    assert (data_dir / "app.db").read_text(encoding="utf-8") == "user data"
    assert result.backup_dir is not None
    assert (result.backup_dir / "facebook-monitor.exe").read_text(encoding="utf-8") == "old"


def test_apply_pending_update_supports_macos_arm64_onedir_layout(
    tmp_path: Path,
) -> None:
    """platform layout policy 允許 macOS onedir 替換 app files 並保留 data。"""

    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    (data_dir / "app.db").write_text("user data", encoding="utf-8")
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_macos_update_zip(zip_path, app_text="new")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "applied"
    assert result.applied
    assert (app_root / "facebook-monitor").read_text(encoding="utf-8") == "new"
    assert (app_root / "facebook-monitor").stat().st_mode & 0o111
    assert (app_root / "facebook-monitor-updater").stat().st_mode & 0o111
    assert (data_dir / "app.db").read_text(encoding="utf-8") == "user data"
    assert result.backup_dir is not None
    assert (result.backup_dir / "facebook-monitor").read_text(encoding="utf-8") == "old"


def test_apply_pending_update_supports_macos_chrome_for_testing_layout(
    tmp_path: Path,
) -> None:
    """macOS updater layout 接受 Playwright 的 Google Chrome for Testing.app。"""

    app_root = tmp_path / "app"
    make_macos_chrome_for_testing_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    (data_dir / "app.db").write_text("user data", encoding="utf-8")
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_macos_chrome_for_testing_update_zip(zip_path, app_text="new")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "applied"
    assert result.applied
    assert (app_root / "facebook-monitor").read_text(encoding="utf-8") == "new"
    assert (app_root / "facebook-monitor").stat().st_mode & 0o111
    assert (app_root / "facebook-monitor-updater").stat().st_mode & 0o111
    assert (data_dir / "app.db").read_text(encoding="utf-8") == "user data"
    browser_exe = (
        app_root
        / "browser"
        / "Google Chrome for Testing.app"
        / "Contents"
        / "MacOS"
        / "Google Chrome for Testing"
    )
    assert browser_exe.read_text(encoding="utf-8") == "chromium"
    assert browser_exe.stat().st_mode & 0o111


def test_apply_pending_update_refuses_when_app_lock_is_held(tmp_path: Path) -> None:
    """主程式仍持有 app lock 時，updater 不替換檔案。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    zip_path = tmp_path / "app" / "data" / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    pending = pending_update(tmp_path, zip_path=zip_path, digest=digest)

    with acquire_app_instance_lock(pending.runtime_dir, "test"):
        result = apply_pending_update(pending)

    assert result.status == "app_running"
    assert not result.applied
    assert (app_root / "facebook-monitor.exe").read_text(encoding="utf-8") == "old"


def test_apply_pending_update_file_writes_result_log(tmp_path: Path) -> None:
    """updater CLI path 會把套用結果寫進 updater log。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    data_dir = app_root / "data"
    runtime_dir = data_dir / "runtime"
    runtime_dir.mkdir(parents=True)
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    pending_path = runtime_dir / "pending_update.json"
    pending_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "0.1.0",
                "asset_name": zip_path.name,
                "zip_path": str(zip_path),
                "expected_sha256": digest,
                "actual_sha256": digest,
                "app_base_dir": str(app_root),
                "data_dir": str(data_dir),
                "db_path": str(data_dir / "app.db"),
                "profile_dir": str(data_dir / "profiles" / "automation_default"),
                "logs_dir": str(data_dir / "logs"),
                "runtime_dir": str(runtime_dir),
                "created_at": "2026-05-17T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    log_path = data_dir / "logs" / "updater.log"

    result = apply_pending_update_file(pending_path, log_path=log_path)

    assert result.status == "applied"
    assert "status=applied applied=true message=updated" in log_path.read_text(
        encoding="utf-8"
    )
    assert not pending_path.exists()
    assert not zip_path.exists()
    assert not zip_path.with_name(zip_path.name + ".sha256").exists()


def test_apply_pending_update_file_removes_verified_sha256_asset(tmp_path: Path) -> None:
    """成功套用後會移除本次下載的 zip 與 `.sha256`，避免更新檔長期殘留。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    data_dir = app_root / "data"
    runtime_dir = data_dir / "runtime"
    runtime_dir.mkdir(parents=True)
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}\n",
        encoding="utf-8",
    )
    pending_path = runtime_dir / "pending_update.json"
    pending_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "0.1.0",
                "asset_name": zip_path.name,
                "zip_path": str(zip_path),
                "expected_sha256": digest,
                "actual_sha256": digest,
                "app_base_dir": str(app_root),
                "data_dir": str(data_dir),
                "db_path": str(data_dir / "app.db"),
                "profile_dir": str(data_dir / "profiles" / "automation_default"),
                "logs_dir": str(data_dir / "logs"),
                "runtime_dir": str(runtime_dir),
                "created_at": "2026-05-17T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    result = apply_pending_update_file(pending_path)

    assert result.applied
    assert not zip_path.exists()
    assert not zip_path.with_name(zip_path.name + ".sha256").exists()
    assert not pending_path.exists()


def test_apply_pending_update_file_prunes_old_backups(tmp_path: Path) -> None:
    """成功套用後只保留最近 backup，避免舊備份無限制累積。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    data_dir = app_root / "data"
    runtime_dir = data_dir / "runtime"
    backup_root = runtime_dir / "update_backups"
    runtime_dir.mkdir(parents=True)
    for index in range(5):
        old_backup = backup_root / (
            f"0.1.0-20260517T00000{index}000000Z-deadbee{index}"
        )
        old_backup.mkdir(parents=True)
        (old_backup / "marker.txt").write_text(str(index), encoding="utf-8")
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    pending_path = runtime_dir / "pending_update.json"
    pending_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "0.1.0",
                "asset_name": zip_path.name,
                "zip_path": str(zip_path),
                "expected_sha256": digest,
                "actual_sha256": digest,
                "app_base_dir": str(app_root),
                "data_dir": str(data_dir),
                "db_path": str(data_dir / "app.db"),
                "profile_dir": str(data_dir / "profiles" / "automation_default"),
                "logs_dir": str(data_dir / "logs"),
                "runtime_dir": str(runtime_dir),
                "created_at": "2026-05-17T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    result = apply_pending_update_file(pending_path)

    assert result.applied
    assert result.backup_dir is not None
    retained = {path.name for path in backup_root.iterdir() if path.is_dir()}
    assert len(retained) == 3
    assert result.backup_dir.name in retained
    assert "0.1.0-20260517T000004000000Z-deadbee4" in retained
    assert "0.1.0-20260517T000003000000Z-deadbee3" in retained


def test_backup_folder_name_uses_unique_suffix() -> None:
    """同一秒同版本建立 backup 時，資料夾名稱仍不應碰撞。"""

    first = _backup_folder_name("0.1.0")
    second = _backup_folder_name("0.1.0")

    assert first != second


def test_cleanup_old_backup_dirs_skips_unknown_backup_names(tmp_path: Path) -> None:
    """不符合 updater 命名格式的資料夾不可被自動刪除。"""

    backup_root = tmp_path / "runtime" / "update_backups"
    backup_root.mkdir(parents=True)
    unknown = backup_root / "manual-backup"
    unknown.mkdir()
    old = backup_root / "0.1.0-20260517T000001000000Z-deadbee1"
    new = backup_root / "0.1.0-20260517T000002000000Z-deadbee2"
    old.mkdir()
    new.mkdir()

    warnings = _cleanup_old_backup_dirs(backup_root, keep_count=1, preserve=None)

    assert unknown.exists()
    assert new.exists()
    assert not old.exists()
    assert any("backup_unknown" in warning for warning in warnings)


def test_cleanup_old_backup_dirs_rejects_root_that_escapes_runtime(
    tmp_path: Path,
) -> None:
    """backup root 若被 symlink/junction 導到 runtime 外，不可清理外部資料。"""

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("external data", encoding="utf-8")
    backup_root = runtime_dir / "update_backups"
    try:
        backup_root.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        return

    warnings = _cleanup_old_backup_dirs(backup_root, keep_count=1, preserve=None)

    assert warnings
    assert (outside / "keep.txt").exists()


def test_apply_loaded_pending_update_file_logs_cleanup_warnings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """cleanup 失敗會寫入 updater log，但不改變成功套用結果。"""

    zip_path = tmp_path / "app" / "data" / "updates" / "0.1.0" / "update.zip"
    pending = pending_update(tmp_path, zip_path=zip_path, digest="a" * 64)
    pending_path = tmp_path / "app" / "data" / "runtime" / "pending_update.json"
    log_path = tmp_path / "app" / "data" / "logs" / "updater.log"

    def fake_apply_pending_update(*args, **kwargs) -> UpdaterApplyResult:
        return UpdaterApplyResult(status="applied", applied=True, message="updated")

    def fake_cleanup_applied_update(*args, **kwargs) -> tuple[str, ...]:
        return ("pending:EACCES",)

    monkeypatch.setattr(
        "facebook_monitor.updates.apply.apply_pending_update",
        fake_apply_pending_update,
    )
    monkeypatch.setattr(
        "facebook_monitor.updates.apply._cleanup_applied_update",
        fake_cleanup_applied_update,
    )

    result = apply_loaded_pending_update_file(pending, pending_path, log_path=log_path)

    assert result.applied
    log_text = log_path.read_text(encoding="utf-8")
    assert "status=applied applied=true message=updated" in log_text
    assert "cleanup_warning=pending:EACCES" in log_text


def test_apply_pending_update_rejects_hash_changed_after_handoff(tmp_path: Path) -> None:
    """handoff 後 zip 被替換時，updater 會重算 SHA256 並拒絕套用。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    zip_path = tmp_path / "app" / "data" / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    zip_path.write_bytes(b"changed")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message == "pending_zip_sha256_mismatch"
    assert (app_root / "facebook-monitor.exe").read_text(encoding="utf-8") == "old"


def test_safe_extract_zip_rejects_path_traversal(tmp_path: Path) -> None:
    """zip 不能含有會逃出 staging dir 的 member path。"""

    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("../evil.txt", "bad")

    try:
        safe_extract_zip(zip_path, tmp_path / "staging")
    except ValueError as exc:
        assert str(exc) == "zip_member_path_unsafe"
    else:
        raise AssertionError("expected unsafe zip member to fail")


def test_safe_extract_zip_preserves_executable_bit(tmp_path: Path) -> None:
    """macOS updater 解壓 staging 時必須保留 executable bit。"""

    source = tmp_path / "source" / "facebook-monitor"
    source.parent.mkdir()
    source.write_text("app", encoding="utf-8")
    source.chmod(0o755)
    zip_path = tmp_path / "app.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.write(source, "facebook-monitor/facebook-monitor")

    safe_extract_zip(zip_path, tmp_path / "staging")

    extracted = tmp_path / "staging" / "facebook-monitor" / "facebook-monitor"
    assert extracted.read_text(encoding="utf-8") == "app"
    assert extracted.stat().st_mode & 0o111


def test_apply_pending_update_rejects_zip_outside_updates_dir(tmp_path: Path) -> None:
    """pending update 不能指向 data updates 目錄外的 zip。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    zip_path = tmp_path / "update.zip"
    digest = make_update_zip(zip_path, exe_text="new")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message == "pending_update_zip_outside_updates_dir"
    assert (app_root / "facebook-monitor.exe").read_text(encoding="utf-8") == "old"


def test_safe_extract_zip_rejects_oversized_archive(tmp_path: Path) -> None:
    """解壓前會檢查展開後大小，避免異常 zip 耗盡磁碟。"""

    zip_path = tmp_path / "big.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("large.bin", "12345")

    try:
        safe_extract_zip(
            zip_path,
            tmp_path / "staging",
            max_uncompressed_bytes=4,
        )
    except ValueError as exc:
        assert str(exc) == "zip_uncompressed_too_large"
    else:
        raise AssertionError("expected oversized zip to fail")
