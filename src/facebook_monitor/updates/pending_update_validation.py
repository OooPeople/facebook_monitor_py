"""Pending update trust-boundary validation。

職責：驗證 pending JSON 內的路徑、hash 與 atomic artifact set 邊界。
"""

from __future__ import annotations

from pathlib import Path
import re

from facebook_monitor.updates.artifacts import release_sha256_asset_name
from facebook_monitor.updates.artifacts import sanitize_release_asset_name
from facebook_monitor.updates.download import UpdateDownloadResult
from facebook_monitor.updates.download import VERIFIED_DOWNLOAD_SET_MARKER_NAME
from facebook_monitor.updates.download import validate_verified_download_set
from facebook_monitor.updates.pending_update_models import PendingUpdate
from facebook_monitor.updates.pending_update_models import pending_update_path
from facebook_monitor.updates.validation import is_dangerous_root
from facebook_monitor.updates.validation import is_reparse_or_symlink


def validate_pending_update_payload_integrity(pending: PendingUpdate) -> None:
    """驗證 pending update payload 的 hash 與必要檔案仍存在。"""

    if pending.expected_sha256 != pending.actual_sha256:
        raise ValueError("pending_update_sha256_mismatch")
    if not re.fullmatch(r"[0-9a-f]{64}", pending.expected_sha256.casefold()):
        raise ValueError("pending_update_sha256_invalid")
    if not pending.zip_path.is_file():
        raise ValueError("pending_update_zip_missing")
    if not pending.manifest_sha256:
        raise ValueError("pending_update_manifest_missing")
    if not re.fullmatch(r"[0-9a-f]{64}", pending.manifest_sha256.casefold()):
        raise ValueError("pending_update_manifest_sha256_invalid")
    if pending.manifest_path is None or not pending.manifest_path.is_file():
        raise ValueError("pending_update_manifest_missing")
    if pending.manifest_signature_path is None or not pending.manifest_signature_path.is_file():
        raise ValueError("pending_update_manifest_signature_missing")
    if not pending.manifest_key_id:
        raise ValueError("pending_update_manifest_key_missing")


def validate_pending_update_paths(
    pending: PendingUpdate,
    *,
    pending_path: Path | None = None,
) -> None:
    """驗證 pending update 的路徑仍落在 updater 可接受的安全邊界內。"""

    if not pending.repository.strip() or "/" not in pending.repository.strip():
        raise ValueError("pending_update_repository_invalid")
    sanitize_release_asset_name(pending.version)
    sanitize_release_asset_name(pending.asset_name)
    app_base_dir = pending.app_base_dir.resolve()
    data_dir = pending.data_dir.resolve()
    runtime_dir = pending.runtime_dir.resolve()
    zip_path = pending.zip_path.resolve()
    updates_dir = data_dir / "updates"
    profiles_dir = data_dir / "profiles"

    if is_dangerous_root(app_base_dir) or is_dangerous_root(data_dir):
        raise ValueError("pending_update_path_dangerous")
    if app_base_dir == data_dir:
        raise ValueError("pending_update_app_data_overlap")
    if data_dir.is_relative_to(app_base_dir) and data_dir != app_base_dir / "data":
        raise ValueError("pending_update_data_dir_must_be_app_data")
    if runtime_dir != data_dir / "runtime":
        raise ValueError("pending_update_runtime_dir_mismatch")
    if not zip_path.is_relative_to(updates_dir):
        raise ValueError("pending_update_zip_outside_updates_dir")
    if pending.manifest_path is not None:
        manifest_path = pending.manifest_path.resolve()
        if not manifest_path.is_relative_to(updates_dir):
            raise ValueError("pending_update_manifest_outside_updates_dir")
    if pending.manifest_signature_path is not None:
        manifest_signature_path = pending.manifest_signature_path.resolve()
        if not manifest_signature_path.is_relative_to(updates_dir):
            raise ValueError("pending_update_manifest_signature_outside_updates_dir")
    if not pending.db_path.resolve().is_relative_to(data_dir):
        raise ValueError("pending_update_db_outside_data_dir")
    if not pending.profile_dir.resolve().is_relative_to(profiles_dir):
        raise ValueError("pending_update_profile_outside_profiles_dir")
    logs_dir = pending.logs_dir.resolve()
    if logs_dir == app_base_dir:
        raise ValueError("pending_update_logs_dir_unsafe")
    if logs_dir.is_relative_to(app_base_dir) and not logs_dir.is_relative_to(data_dir):
        raise ValueError("pending_update_logs_dir_unsafe")
    if pending_path is not None:
        expected_pending_path = pending_update_path(runtime_dir).resolve()
        if pending_path.resolve() != expected_pending_path:
            raise ValueError("pending_update_path_mismatch")


def validate_pending_update_artifact_set(pending: PendingUpdate) -> None:
    """確認 pending update 指向完整 atomic artifact set。"""

    updates_version_dir = (
        pending.data_dir.resolve()
        / "updates"
        / sanitize_release_asset_name(pending.version)
    )
    set_dir = pending.zip_path.resolve().parent
    if set_dir == updates_version_dir or set_dir.parent != updates_version_dir:
        raise ValueError("pending_update_artifact_set_invalid")
    if not set_dir.name.startswith("attempt-"):
        raise ValueError("pending_update_artifact_set_invalid")
    if is_reparse_or_symlink(set_dir):
        raise ValueError("pending_update_artifact_set_unsafe")
    sha256_path = set_dir / release_sha256_asset_name(pending.zip_path.name)
    if not sha256_path.is_file():
        raise ValueError("pending_update_sha256_missing")
    if pending.manifest_path is None or pending.manifest_signature_path is None:
        raise ValueError("pending_update_manifest_missing")
    marker_path = set_dir / VERIFIED_DOWNLOAD_SET_MARKER_NAME
    if not marker_path.is_file():
        raise ValueError("pending_update_verified_set_missing")
    download_result = UpdateDownloadResult(
        status="verified",
        downloaded=True,
        verified=True,
        file_path=pending.zip_path,
        sha256_path=sha256_path,
        expected_sha256=pending.expected_sha256,
        actual_sha256=pending.actual_sha256,
        failure_reason="",
        manifest_path=pending.manifest_path,
        manifest_signature_path=pending.manifest_signature_path,
        manifest_sha256=pending.manifest_sha256,
        manifest_key_id=pending.manifest_key_id,
        verified_set_marker_path=marker_path,
    )
    try:
        validate_verified_download_set(download_result)
    except (OSError, ValueError) as exc:
        raise ValueError(str(exc)) from exc


__all__ = [
    "validate_pending_update_artifact_set",
    "validate_pending_update_paths",
    "validate_pending_update_payload_integrity",
]
