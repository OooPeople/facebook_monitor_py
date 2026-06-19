"""Updater download path planning and filesystem guards."""

from __future__ import annotations

from pathlib import Path
import shutil
import uuid

from facebook_monitor.updates.artifacts import sanitize_release_asset_name
from facebook_monitor.updates.download_models import UpdateDownloadPlan
from facebook_monitor.updates.download_models import VERIFIED_DOWNLOAD_SET_MARKER_NAME
from facebook_monitor.updates.download_url_policy import validate_initial_release_download_url
from facebook_monitor.updates.release_check import UpdateCheckResult
from facebook_monitor.updates.validation import has_unsafe_existing_path_component
from facebook_monitor.updates.validation import is_reparse_or_symlink


def ensure_child_path(parent: Path, child: Path) -> None:
    """確認 child 路徑位於 parent 底下。"""

    if not child.is_relative_to(parent):
        raise ValueError("download_path_outside_updates_dir")


def ensure_safe_download_path(path: Path, *, updates_root: Path) -> None:
    """確認 download path 不會經由 symlink/junction 寫到 updates dir 外。"""

    absolute_path = path.absolute()
    absolute_updates_root = updates_root.absolute()
    ensure_child_path(absolute_updates_root, absolute_path)
    if has_unsafe_existing_path_component(
        absolute_path,
        root=absolute_updates_root.parent,
    ):
        raise ValueError("download_path_unsafe")


def build_download_plan(
    *,
    update_check: UpdateCheckResult,
    updates_dir: Path,
) -> UpdateDownloadPlan:
    """驗證 release metadata 並建立下載路徑 plan；尚不建立檔案。"""

    asset_name = sanitize_release_asset_name(update_check.asset_name)
    sha256_name = sanitize_release_asset_name(update_check.sha256_asset_name)
    manifest_name = sanitize_release_asset_name(update_check.manifest_asset_name)
    manifest_signature_name = sanitize_release_asset_name(
        update_check.manifest_signature_asset_name
    )
    version_dir_name = sanitize_release_asset_name(update_check.latest_version)
    updates_root = Path(updates_dir).expanduser().absolute()
    destination_dir = updates_root / version_dir_name
    attempt_id = uuid.uuid4().hex
    verified_set_dir = destination_dir / f"attempt-{attempt_id}"
    staged_set_dir = destination_dir / f".attempt-{attempt_id}"
    file_path = verified_set_dir / asset_name
    sha256_path = verified_set_dir / sha256_name
    manifest_path = verified_set_dir / manifest_name
    manifest_signature_path = verified_set_dir / manifest_signature_name
    validate_initial_release_download_url(
        update_check.asset_download_url,
        expected_asset_name=asset_name,
        repository=update_check.repository,
    )
    validate_initial_release_download_url(
        update_check.manifest_asset_download_url,
        expected_asset_name=manifest_name,
        repository=update_check.repository,
    )
    validate_initial_release_download_url(
        update_check.manifest_signature_asset_download_url,
        expected_asset_name=manifest_signature_name,
        repository=update_check.repository,
    )
    validate_initial_release_download_url(
        update_check.sha256_asset_download_url,
        expected_asset_name=sha256_name,
        repository=update_check.repository,
    )
    ensure_child_path(updates_root, destination_dir)
    ensure_safe_download_path(destination_dir, updates_root=updates_root)
    return UpdateDownloadPlan(
        asset_name=asset_name,
        sha256_name=sha256_name,
        manifest_name=manifest_name,
        manifest_signature_name=manifest_signature_name,
        updates_root=updates_root,
        destination_dir=destination_dir,
        verified_set_dir=verified_set_dir,
        staged_set_dir=staged_set_dir,
        file_path=file_path,
        sha256_path=sha256_path,
        manifest_path=manifest_path,
        manifest_signature_path=manifest_signature_path,
        verified_set_marker_path=verified_set_dir / VERIFIED_DOWNLOAD_SET_MARKER_NAME,
        staged_file_path=staged_set_dir / asset_name,
        staged_sha256_path=staged_set_dir / sha256_name,
        staged_manifest_path=staged_set_dir / manifest_name,
        staged_manifest_signature_path=staged_set_dir / manifest_signature_name,
    )


def prepare_download_plan(plan: UpdateDownloadPlan) -> None:
    """建立下載目錄並檢查正式 / staging 目的地安全性。"""

    prepare_destination_dir(plan.destination_dir, updates_root=plan.updates_root)
    prepare_attempt_set_dir(plan.staged_set_dir, updates_root=plan.updates_root)
    ensure_download_set_destination_available(
        plan.verified_set_dir,
        updates_root=plan.updates_root,
    )
    destinations = [
        plan.file_path,
        plan.staged_file_path,
        plan.manifest_path,
        plan.manifest_signature_path,
        plan.staged_manifest_path,
        plan.staged_manifest_signature_path,
        plan.verified_set_marker_path,
    ]
    if plan.sha256_path is not None and plan.staged_sha256_path is not None:
        destinations.extend([plan.sha256_path, plan.staged_sha256_path])
    prepare_download_destinations(
        *destinations,
        updates_root=plan.updates_root,
    )


def prepare_destination_dir(path: Path, *, updates_root: Path) -> None:
    """建立安全的下載版本目錄；既有非目錄或連結一律拒絕。"""

    ensure_safe_download_path(path, updates_root=updates_root)
    if is_reparse_or_symlink(path):
        raise ValueError("download_path_unsafe")
    if path.exists():
        if not path.is_dir():
            raise ValueError("download_path_unsafe")
        return
    path.mkdir(parents=True, exist_ok=True)
    ensure_safe_download_path(path, updates_root=updates_root)


def prepare_attempt_set_dir(path: Path, *, updates_root: Path) -> None:
    """建立單次下載 staging set 目錄；既有路徑一律視為不安全。"""

    ensure_download_set_destination_available(path, updates_root=updates_root)
    path.mkdir()
    ensure_safe_download_path(path, updates_root=updates_root)
    if is_reparse_or_symlink(path):
        raise ValueError("download_path_unsafe")


def ensure_download_set_destination_available(
    path: Path,
    *,
    updates_root: Path,
) -> None:
    """確認 artifact set 目錄目的地可安全建立。"""

    ensure_safe_download_path(path.parent, updates_root=updates_root)
    ensure_safe_download_path(path, updates_root=updates_root)
    if is_reparse_or_symlink(path):
        raise ValueError("download_path_unsafe")
    if path.exists():
        raise ValueError("download_path_unsafe")


def prepare_download_destinations(
    *destinations: Path,
    updates_root: Path,
) -> None:
    """下載前準備所有正式與 staging 路徑，並移除安全範圍內 stale `.tmp`。"""

    for destination in destinations:
        prepare_download_tmp(
            destination,
            destination.with_name(destination.name + ".tmp"),
            updates_root=updates_root,
        )


def prepare_download_tmp(
    destination: Path,
    tmp_destination: Path,
    *,
    updates_root: Path,
) -> None:
    """準備下載暫存檔；不得 follow 既有 symlink/junction。"""

    ensure_download_destination_available(destination, updates_root=updates_root)
    ensure_download_destination_available(tmp_destination, updates_root=updates_root)
    if tmp_destination.exists():
        try:
            tmp_destination.unlink()
        except OSError as exc:
            raise ValueError("download_path_unsafe") from exc


def ensure_download_destination_available(
    destination: Path,
    *,
    updates_root: Path,
) -> None:
    """確認下載目的地可安全建立或覆寫。"""

    ensure_safe_download_path(destination.parent, updates_root=updates_root)
    ensure_safe_download_path(destination, updates_root=updates_root)
    if is_reparse_or_symlink(destination):
        raise ValueError("download_path_unsafe")
    if destination.exists() and not destination.is_file():
        raise ValueError("download_path_unsafe")


def cleanup_staged_download(plan: UpdateDownloadPlan) -> None:
    """清除 staged manifest、signature、sidecar 與 zip。"""

    cleanup_download_dir(plan.staged_set_dir, updates_root=plan.updates_root)


def cleanup_download_dir(path: Path, *, updates_root: Path) -> None:
    """安全清除單次下載 artifact set 目錄。"""

    try:
        ensure_safe_download_path(path, updates_root=updates_root)
    except ValueError:
        return
    try:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
            return
        if path.exists():
            if is_reparse_or_symlink(path):
                return
            shutil.rmtree(path)
    except OSError:
        pass


def operation_runtime_dir_for_updates_dir(updates_dir: Path) -> Path:
    """依 RuntimePaths 慣例從 updates dir 推導 updater operation lock 目錄。"""

    return Path(updates_dir).expanduser().resolve().parent / "runtime"


__all__ = [
    "build_download_plan",
    "cleanup_download_dir",
    "cleanup_staged_download",
    "ensure_child_path",
    "ensure_download_destination_available",
    "ensure_download_set_destination_available",
    "ensure_safe_download_path",
    "operation_runtime_dir_for_updates_dir",
    "prepare_attempt_set_dir",
    "prepare_destination_dir",
    "prepare_download_destinations",
    "prepare_download_plan",
    "prepare_download_tmp",
]
