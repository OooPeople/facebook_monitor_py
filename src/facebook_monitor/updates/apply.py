"""獨立 updater 套用更新。

職責：在主程式已關閉後，讀取 pending update、重驗 zip SHA256、解壓到
staging、驗證 PyInstaller onedir 結構，並以保留 `data/` 的方式替換 app
files。此模組不讀寫 profile/DB/secrets 內容。
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from pathlib import Path
import shutil
import time
import zipfile

from facebook_monitor.runtime.instance_lock import AppInstanceLockError
from facebook_monitor.runtime.instance_lock import AppInstanceLock
from facebook_monitor.runtime.instance_lock import acquire_app_instance_lock
from facebook_monitor.updates.download import calculate_sha256
from facebook_monitor.updates.download import sanitize_release_asset_name
from facebook_monitor.updates.handoff import PendingUpdate
from facebook_monitor.updates.handoff import load_pending_update
from facebook_monitor.updates.handoff import validate_pending_update_paths


STAGING_DIR_NAME = "update_staging"
BACKUP_DIR_NAME = "update_backups"
APP_EXE_NAME = "facebook-monitor.exe"
UPDATER_EXE_NAME = "facebook-monitor-updater.exe"
MAX_ZIP_ENTRIES = 50_000
MAX_ZIP_SINGLE_FILE_BYTES = 1024 * 1024 * 1024
MAX_ZIP_UNCOMPRESSED_BYTES = 3 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class UpdaterApplyResult:
    """更新套用結果。"""

    status: str
    applied: bool
    message: str
    backup_dir: Path | None = None
    staging_dir: Path | None = None


def apply_pending_update_file(
    path: Path,
    *,
    wait_for_lock_seconds: float = 0,
    poll_seconds: float = 1,
    log_path: Path | None = None,
) -> UpdaterApplyResult:
    """從 pending update JSON 套用更新。"""

    try:
        pending = load_pending_update(path)
    except (OSError, ValueError) as exc:
        result = UpdaterApplyResult(
            status="failed",
            applied=False,
            message=str(exc),
        )
        _append_updater_log(log_path, result)
        return result
    return apply_loaded_pending_update_file(
        pending,
        path,
        wait_for_lock_seconds=wait_for_lock_seconds,
        poll_seconds=poll_seconds,
        log_path=log_path,
    )


def apply_loaded_pending_update_file(
    pending: PendingUpdate,
    path: Path,
    *,
    wait_for_lock_seconds: float = 0,
    poll_seconds: float = 1,
    log_path: Path | None = None,
) -> UpdaterApplyResult:
    """使用已讀取的 pending update 套用更新並處理 handoff cleanup。"""

    result = apply_pending_update(
        pending,
        wait_for_lock_seconds=wait_for_lock_seconds,
        poll_seconds=poll_seconds,
    )
    cleanup_warnings: tuple[str, ...] = ()
    if result.applied:
        cleanup_warnings = _cleanup_applied_update(path, pending)
    _append_updater_log(log_path, result)
    _append_cleanup_warning_log(log_path, cleanup_warnings)
    return result


def apply_pending_update(
    pending: PendingUpdate,
    *,
    wait_for_lock_seconds: float = 0,
    poll_seconds: float = 1,
) -> UpdaterApplyResult:
    """套用已驗證更新包；主程式仍執行時會拒絕替換。"""

    try:
        validate_pending_update_paths(pending)
        _validate_pending_hash(pending)
        with _wait_for_app_lock(
            pending.runtime_dir,
            wait_for_lock_seconds=wait_for_lock_seconds,
            poll_seconds=poll_seconds,
        ):
            staging_dir = _prepare_empty_dir(
                pending.runtime_dir / STAGING_DIR_NAME / sanitize_release_asset_name(
                    pending.version
                )
            )
            safe_extract_zip(pending.zip_path, staging_dir)
            staging_app_root = find_staging_app_root(staging_dir)
            validate_staging_app_root(staging_app_root)
            validate_current_app_root(pending.app_base_dir)
            backup_dir = _prepare_empty_dir(
                pending.runtime_dir
                / BACKUP_DIR_NAME
                / _backup_folder_name(pending.version)
            )
            backup_current_app_files(
                app_base_dir=pending.app_base_dir,
                backup_dir=backup_dir,
                data_dir=pending.data_dir,
            )
            try:
                replace_app_files(
                    staging_app_root=staging_app_root,
                    app_base_dir=pending.app_base_dir,
                    data_dir=pending.data_dir,
                )
            except Exception:
                restore_backup(
                    app_base_dir=pending.app_base_dir,
                    backup_dir=backup_dir,
                    data_dir=pending.data_dir,
                )
                raise
    except AppInstanceLockError as exc:
        return UpdaterApplyResult(
            status="app_running",
            applied=False,
            message=str(exc),
        )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return UpdaterApplyResult(
            status="failed",
            applied=False,
            message=str(exc),
        )
    return UpdaterApplyResult(
        status="applied",
        applied=True,
        message="updated",
        backup_dir=backup_dir,
        staging_dir=staging_dir,
    )


@contextmanager
def _wait_for_app_lock(
    runtime_dir: Path,
    *,
    wait_for_lock_seconds: float,
    poll_seconds: float,
) -> Iterator[AppInstanceLock]:
    """等待主程式釋放 app lock，避免 updater 啟動太早就失敗。"""

    deadline = time.monotonic() + max(0, wait_for_lock_seconds)
    last_error: AppInstanceLockError | None = None
    while True:
        try:
            with acquire_app_instance_lock(runtime_dir, "updater") as app_lock:
                yield app_lock
                return
        except AppInstanceLockError as exc:
            last_error = exc
            if time.monotonic() >= deadline:
                raise last_error
            time.sleep(max(0.1, poll_seconds))


def safe_extract_zip(
    zip_path: Path,
    destination: Path,
    *,
    max_entries: int = MAX_ZIP_ENTRIES,
    max_single_file_bytes: int = MAX_ZIP_SINGLE_FILE_BYTES,
    max_uncompressed_bytes: int = MAX_ZIP_UNCOMPRESSED_BYTES,
) -> None:
    """安全解壓 zip，拒絕 path traversal、絕對路徑與過大 archive。"""

    destination = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) > max_entries:
            raise ValueError("zip_too_many_entries")
        total_uncompressed = 0
        for member in members:
            member_path = Path(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError("zip_member_path_unsafe")
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination):
                raise ValueError("zip_member_path_unsafe")
            if member.is_dir():
                continue
            if member.file_size > max_single_file_bytes:
                raise ValueError("zip_member_too_large")
            total_uncompressed += member.file_size
            if total_uncompressed > max_uncompressed_bytes:
                raise ValueError("zip_uncompressed_too_large")
        archive.extractall(destination)


def find_staging_app_root(staging_dir: Path) -> Path:
    """尋找 update zip 內的 app root，支援 zip 包住單一 `facebook-monitor/` 目錄。"""

    if (staging_dir / APP_EXE_NAME).is_file():
        return staging_dir
    child_dirs = [path for path in staging_dir.iterdir() if path.is_dir()]
    for child in child_dirs:
        if (child / APP_EXE_NAME).is_file():
            return child
    raise ValueError("staging_app_root_missing")


def validate_staging_app_root(app_root: Path) -> None:
    """驗證 staging app root 至少包含目前 frozen onedir 必要檔案。"""

    required = (
        app_root / APP_EXE_NAME,
        app_root / UPDATER_EXE_NAME,
        app_root / "_internal" / "browser" / "chrome.exe",
        app_root / "_internal" / "assets" / "facebook-monitor.ico",
        app_root / "_internal" / "assets" / "facebook-monitor-tray.ico",
    )
    missing = [path for path in required if not path.exists()]
    if missing:
        raise ValueError("staging_required_file_missing:" + str(missing[0]))


def validate_current_app_root(app_root: Path) -> None:
    """驗證目前要被替換的 app root 看起來像本專案的 frozen onedir。"""

    if _is_dangerous_app_root(app_root):
        raise ValueError("app_base_dir_unsafe")
    required = (
        app_root / APP_EXE_NAME,
        app_root / UPDATER_EXE_NAME,
        app_root / "_internal",
    )
    missing = [path for path in required if not path.exists()]
    if missing:
        raise ValueError("app_required_file_missing:" + str(missing[0]))


def backup_current_app_files(
    *,
    app_base_dir: Path,
    backup_dir: Path,
    data_dir: Path,
) -> None:
    """備份目前 app files；portable `data/` 永遠不進備份或替換範圍。"""

    for child in app_base_dir.iterdir():
        if _is_protected_data_path(child, data_dir):
            continue
        _copy_path(child, backup_dir / child.name)


def replace_app_files(
    *,
    staging_app_root: Path,
    app_base_dir: Path,
    data_dir: Path,
) -> None:
    """用 staging app files 替換目前 app files，保留 data dir。"""

    for child in list(app_base_dir.iterdir()):
        if _is_protected_data_path(child, data_dir):
            continue
        _remove_path(child)
    for child in staging_app_root.iterdir():
        if child.name == "data":
            continue
        _copy_path(child, app_base_dir / child.name)


def restore_backup(
    *,
    app_base_dir: Path,
    backup_dir: Path,
    data_dir: Path,
) -> None:
    """替換失敗時嘗試從備份還原 app files。"""

    for child in list(app_base_dir.iterdir()):
        if _is_protected_data_path(child, data_dir):
            continue
        _remove_path(child)
    for child in backup_dir.iterdir():
        _copy_path(child, app_base_dir / child.name)


def _validate_pending_hash(pending: PendingUpdate) -> None:
    """套用前重算 zip SHA256，避免 handoff 後檔案被替換。"""

    actual = calculate_sha256(pending.zip_path)
    if actual != pending.expected_sha256:
        raise ValueError("pending_zip_sha256_mismatch")


def _prepare_empty_dir(path: Path) -> Path:
    """建立空目錄；既有內容會先刪除。"""

    resolved = path.resolve()
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _backup_folder_name(version: str) -> str:
    """建立可排序且檔名安全的 backup folder name。"""

    safe_version = sanitize_release_asset_name(version)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{safe_version}-{timestamp}"


def _is_protected_data_path(path: Path, data_dir: Path) -> bool:
    """判斷 path 是否是必須保留的 data dir。"""

    try:
        return path.resolve() == data_dir.resolve()
    except OSError:
        return False


def _copy_path(source: Path, destination: Path) -> None:
    """複製檔案或目錄。"""

    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _remove_path(path: Path) -> None:
    """移除檔案或目錄。"""

    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _is_dangerous_app_root(path: Path) -> bool:
    """拒絕磁碟根目錄與 home 這類不可作為 app root 的寬路徑。"""

    resolved = path.resolve()
    if resolved == resolved.parent:
        return True
    try:
        return resolved == Path.home().resolve()
    except RuntimeError:
        return False


def _append_updater_log(log_path: Path | None, result: UpdaterApplyResult) -> None:
    """寫入 updater 結果 log；log 失敗不應影響更新結果。"""

    if log_path is None:
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        line = (
            f"{datetime.now(timezone.utc).isoformat()} "
            f"status={result.status} applied={str(result.applied).lower()} "
            f"message={result.message}\n"
        )
        log_path.open("a", encoding="utf-8").write(line)
    except OSError:
        return


def _append_cleanup_warning_log(log_path: Path | None, warnings: tuple[str, ...]) -> None:
    """寫入 cleanup warning；清理失敗不應遮蔽套用結果。"""

    if log_path is None or not warnings:
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as file:
            timestamp = datetime.now(timezone.utc).isoformat()
            for warning in warnings:
                file.write(f"{timestamp} cleanup_warning={warning}\n")
    except OSError:
        return


def _cleanup_applied_update(
    pending_path: Path,
    pending: PendingUpdate,
) -> tuple[str, ...]:
    """成功套用後清除本次下載與 handoff 檔，失敗時保留供診斷。"""

    warnings: list[str] = []
    updates_dir = pending.data_dir / "updates"
    _cleanup_file(pending.zip_path, label="zip", warnings=warnings)
    _cleanup_file(
        pending.zip_path.with_name(pending.zip_path.name + ".sha256"),
        label="sha256",
        warnings=warnings,
    )
    parent = pending.zip_path.parent
    try:
        resolved_parent = parent.resolve()
        resolved_updates_dir = updates_dir.resolve()
        if (
            resolved_parent != resolved_updates_dir
            and resolved_parent.is_relative_to(resolved_updates_dir)
        ):
            parent.rmdir()
    except OSError as exc:
        warnings.append(_cleanup_warning("updates_parent", parent, exc))
    _cleanup_file(pending_path, label="pending", warnings=warnings)
    staging_dir = pending.runtime_dir / STAGING_DIR_NAME / sanitize_release_asset_name(
        pending.version
    )
    try:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
    except OSError as exc:
        warnings.append(_cleanup_warning("staging", staging_dir, exc))
    return tuple(warnings)


def _cleanup_file(path: Path, *, label: str, warnings: list[str]) -> None:
    """刪除 cleanup 檔案並收集失敗原因。"""

    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        warnings.append(_cleanup_warning(label, path, exc))


def _cleanup_warning(label: str, path: Path, exc: OSError) -> str:
    """整理 cleanup warning，避免 updater log 只剩模糊失敗。"""

    return (
        f"{label}:{path}:"
        f"{exc.__class__.__name__}:{exc}"
    )
