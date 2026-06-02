"""Updater work directory, handoff cleanup and backup retention helpers."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from pathlib import Path
import re
import shutil
import uuid

from facebook_monitor.updates.artifacts import release_sha256_asset_name
from facebook_monitor.updates.artifacts import sanitize_release_asset_name
from facebook_monitor.updates.handoff import PendingUpdate
from facebook_monitor.updates.validation import has_unsafe_existing_path_component
from facebook_monitor.updates.validation import is_reparse_or_symlink


STAGING_DIR_NAME = "update_staging"
BACKUP_DIR_NAME = "update_backups"
BACKUP_RETENTION_COUNT = 1


def _prepare_empty_dir(path: Path, *, work_root: Path) -> Path:
    """建立空目錄；既有內容會先刪除。"""

    resolved_work_root = work_root.resolve()
    resolved_path = path.resolve(strict=False)
    if resolved_path == resolved_work_root or not resolved_path.is_relative_to(resolved_work_root):
        raise ValueError("update_work_dir_unsafe")
    if has_unsafe_existing_path_component(path, root=work_root):
        raise ValueError("update_work_dir_unsafe")
    if path.exists() or path.is_symlink():
        if is_reparse_or_symlink(path):
            raise ValueError("update_work_dir_unsafe")
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _backup_folder_name(version: str) -> str:
    """建立可排序且檔名安全的 backup folder name。"""

    safe_version = sanitize_release_asset_name(version)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{safe_version}-{timestamp}-{uuid.uuid4().hex[:8]}"


def _cleanup_applied_update(
    pending_path: Path,
    pending: PendingUpdate,
) -> tuple[str, ...]:
    """成功套用後清除本次下載與 handoff 檔，失敗時保留供診斷。"""

    warnings: list[str] = []
    updates_dir = pending.data_dir / "updates"
    _cleanup_file(pending.zip_path, label="zip", warnings=warnings)
    _cleanup_file(
        pending.zip_path.with_name(release_sha256_asset_name(pending.zip_path.name)),
        label="sha256",
        warnings=warnings,
    )
    if pending.manifest_path is not None:
        _cleanup_file(pending.manifest_path, label="manifest", warnings=warnings)
    if pending.manifest_signature_path is not None:
        _cleanup_file(
            pending.manifest_signature_path,
            label="manifest_signature",
            warnings=warnings,
        )
    parent = pending.zip_path.parent
    try:
        resolved_parent = parent.resolve()
        resolved_updates_dir = updates_dir.resolve()
        if resolved_parent != resolved_updates_dir and resolved_parent.is_relative_to(
            resolved_updates_dir
        ):
            parent.rmdir()
    except OSError as exc:
        warnings.append(_cleanup_warning("updates_parent", parent, exc))
    _cleanup_file(pending_path, label="pending", warnings=warnings)
    staging_dir = (
        pending.runtime_dir / STAGING_DIR_NAME / sanitize_release_asset_name(pending.version)
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


def _cleanup_old_backup_dirs(
    backup_root: Path,
    *,
    keep_count: int,
    preserve: Path | None,
) -> tuple[str, ...]:
    """保留指定 backup 與必要數量的 managed backup；失敗只回傳 warning。"""

    warnings: list[str] = []
    if keep_count < 1:
        keep_count = 1
    if not backup_root.exists():
        return ()
    try:
        resolved_root = backup_root.resolve()
        resolved_parent = backup_root.parent.resolve()
    except OSError as exc:
        return (_cleanup_warning("backup_root_resolve", backup_root, exc),)
    if is_reparse_or_symlink(backup_root):
        return (
            _cleanup_warning(
                "backup_root_unsafe",
                backup_root,
                OSError("backup root is symlink or junction"),
            ),
        )
    if resolved_root == resolved_parent or not resolved_root.is_relative_to(resolved_parent):
        return (
            _cleanup_warning(
                "backup_root_unsafe",
                backup_root,
                OSError("backup root escaped runtime directory"),
            ),
        )
    try:
        backup_dirs = [path for path in backup_root.iterdir() if path.is_dir()]
    except OSError as exc:
        return (_cleanup_warning("backup_list", backup_root, exc),)

    sortable_dirs: list[tuple[float, str, Path]] = []
    for backup_dir in backup_dirs:
        timestamp = _backup_timestamp(backup_dir)
        if timestamp is None:
            warnings.append(
                _cleanup_warning(
                    "backup_unknown",
                    backup_dir,
                    OSError("backup directory name is not managed by updater"),
                )
            )
            continue
        sortable_dirs.append((timestamp, backup_dir.name, backup_dir))

    keep: set[Path] = set()
    if preserve is not None:
        keep.add(preserve.resolve())
    for _, _, backup_dir in sorted(sortable_dirs, reverse=True):
        if len(keep) >= keep_count:
            break
        resolved = _resolve_safe_backup_dir(
            backup_dir,
            resolved_root=resolved_root,
            warnings=warnings,
        )
        if resolved is None:
            continue
        keep.add(resolved)
    for _, _, backup_dir in sortable_dirs:
        resolved = _resolve_safe_backup_dir(
            backup_dir,
            resolved_root=resolved_root,
            warnings=warnings,
        )
        if resolved is None:
            continue
        if resolved in keep:
            continue
        if (
            _resolve_safe_backup_dir(
                backup_dir,
                resolved_root=resolved_root,
                warnings=warnings,
            )
            is None
        ):
            continue
        try:
            shutil.rmtree(backup_dir)
        except OSError as exc:
            warnings.append(_cleanup_warning("backup", backup_dir, exc))
    return tuple(warnings)


def _resolve_safe_backup_dir(
    backup_dir: Path,
    *,
    resolved_root: Path,
    warnings: list[str],
) -> Path | None:
    """確認 backup dir 仍留在 backup root 內，安全時回傳 resolved path。"""

    if is_reparse_or_symlink(backup_dir):
        warnings.append(
            _cleanup_warning(
                "backup_unsafe",
                backup_dir,
                OSError("backup directory is symlink or junction"),
            )
        )
        return None
    try:
        resolved = backup_dir.resolve()
    except OSError as exc:
        warnings.append(_cleanup_warning("backup_resolve", backup_dir, exc))
        return None
    if not resolved.is_relative_to(resolved_root):
        warnings.append(
            _cleanup_warning(
                "backup_unsafe",
                backup_dir,
                OSError("backup directory escaped backup root"),
            )
        )
        return None
    return resolved


def _backup_timestamp(path: Path) -> float | None:
    """解析 updater backup folder timestamp；未知格式不自動刪除。"""

    match = re.fullmatch(
        r".+-(\d{8}T\d{6}(?:\d{6})?Z)(?:-[0-9a-f]{8})?",
        path.name,
    )
    if match is None:
        return None
    value = match.group(1)
    fmt = "%Y%m%dT%H%M%S%fZ" if len(value) == 22 else "%Y%m%dT%H%M%SZ"
    try:
        return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _cleanup_warning(label: str, path: Path, exc: OSError) -> str:
    """整理 cleanup warning，避免 updater log 只剩模糊失敗。"""

    return f"{label}:{path}:{exc.__class__.__name__}:{exc}"
