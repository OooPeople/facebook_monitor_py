"""獨立 updater 套用流程測試。"""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from facebook_monitor.updates.apply import apply_loaded_pending_update_file
from facebook_monitor.updates.apply import _backup_folder_name
from facebook_monitor.updates.apply import _cleanup_old_backup_dirs
from facebook_monitor.updates.apply import _prepare_empty_dir
from facebook_monitor.updates.apply import UpdaterApplyResult


from tests.updates.apply_test_helpers import pending_update

TEST_KEY_ID = "test-key"
TEST_PRIVATE_KEY = Ed25519PrivateKey.generate()
TEST_REPOSITORY = "OooPeople/facebook_monitor_py"
TEST_VERSION = "0.1.0"


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


def test_cleanup_old_backup_dirs_preserves_current_backup_over_newer_backups(
    tmp_path: Path,
) -> None:
    """保留數量為一時，仍以本次套用產生的 backup 作為 rollback anchor。"""

    backup_root = tmp_path / "runtime" / "update_backups"
    backup_root.mkdir(parents=True)
    current = backup_root / "0.1.0-20260517T000001000000Z-deadbee1"
    newer = backup_root / "0.1.0-20260517T000002000000Z-deadbee2"
    current.mkdir()
    newer.mkdir()

    warnings = _cleanup_old_backup_dirs(backup_root, keep_count=1, preserve=current)

    assert warnings == ()
    assert current.exists()
    assert not newer.exists()


def test_cleanup_old_backup_dirs_skips_symlinked_managed_backup(
    tmp_path: Path,
) -> None:
    """managed backup 若是 symlink/junction，不可 follow 後刪除外部目錄。"""

    backup_root = tmp_path / "runtime" / "update_backups"
    backup_root.mkdir(parents=True)
    current = backup_root / "0.1.0-20260517T000001000000Z-deadbee1"
    current.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("external data", encoding="utf-8")
    linked = backup_root / "0.1.0-20260517T000002000000Z-deadbee2"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    warnings = _cleanup_old_backup_dirs(backup_root, keep_count=1, preserve=current)

    assert current.exists()
    assert linked.is_symlink()
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "external data"
    assert any("backup_unsafe" in warning for warning in warnings)


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


def test_prepare_empty_dir_rejects_path_that_resolves_to_work_root(
    tmp_path: Path,
) -> None:
    """update staging/backup 目錄不可用 `..` 解析成 runtime root 後被清空。"""

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    marker = runtime_dir / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    try:
        _prepare_empty_dir(runtime_dir / "update_staging" / "..", work_root=runtime_dir)
    except ValueError as exc:
        assert str(exc) == "update_work_dir_unsafe"
    else:
        raise AssertionError("expected work root path to fail")
    assert marker.read_text(encoding="utf-8") == "keep"


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
