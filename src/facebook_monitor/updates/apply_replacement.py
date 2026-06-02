"""Updater app replacement, backup and rollback helpers."""

from __future__ import annotations

from pathlib import Path
import shutil

from facebook_monitor.updates.apply_app_tree import _is_protected_data_path
from facebook_monitor.updates.validation import is_junction
from facebook_monitor.updates.validation import validate_tree_links_stay_within_root


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
        _copy_path(child, backup_dir / child.name, source_root=app_base_dir)


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
        _copy_path(child, app_base_dir / child.name, source_root=staging_app_root)


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
        _copy_path(child, app_base_dir / child.name, source_root=backup_dir)


def _copy_path(source: Path, destination: Path, *, source_root: Path) -> None:
    """複製檔案或目錄。"""

    validate_tree_links_stay_within_root(
        source,
        root=source_root,
        reason="app_path_unsafe",
    )
    if source.is_symlink():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(source.readlink())
        return
    if is_junction(source):
        raise ValueError("app_path_unsafe")
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _remove_path(path: Path) -> None:
    """移除檔案或目錄。"""

    if is_junction(path):
        raise ValueError("app_path_unsafe")
    if path.is_symlink():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
