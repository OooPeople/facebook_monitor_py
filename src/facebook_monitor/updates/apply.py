"""獨立 updater 套用更新。

職責：在主程式已關閉後，讀取 pending update、重驗 zip SHA256、解壓到
staging、驗證 PyInstaller onedir 結構，並以保留 `data/` 的方式替換 app
files。此模組不讀寫 profile/DB/secrets 內容。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
import json
from pathlib import Path
import time
import uuid
import zipfile

from facebook_monitor.runtime.instance_lock import AppInstanceLockError
from facebook_monitor.runtime.instance_lock import AppInstanceLock
from facebook_monitor.runtime.instance_lock import acquire_app_instance_lock
from facebook_monitor.runtime.update_operation_lock import ensure_update_operation_lock
from facebook_monitor.runtime.update_operation_lock import UpdateOperationLock
from facebook_monitor.runtime.update_operation_lock import UpdateOperationLockError
from facebook_monitor.updates.apply_app_tree import find_staging_app_root
from facebook_monitor.updates.apply_app_tree import validate_current_app_root
from facebook_monitor.updates.apply_app_tree import validate_staging_app_root
from facebook_monitor.updates.apply_cleanup import BACKUP_DIR_NAME
from facebook_monitor.updates.apply_cleanup import BACKUP_RETENTION_COUNT
from facebook_monitor.updates.apply_cleanup import STAGING_DIR_NAME
from facebook_monitor.updates.apply_cleanup import _backup_folder_name
from facebook_monitor.updates.apply_cleanup import _cleanup_applied_update
from facebook_monitor.updates.apply_cleanup import _cleanup_old_backup_dirs
from facebook_monitor.updates.apply_cleanup import _prepare_empty_dir
from facebook_monitor.updates.apply_replacement import backup_current_app_files
from facebook_monitor.updates.apply_replacement import replace_app_files
from facebook_monitor.updates.apply_replacement import restore_backup
from facebook_monitor.updates.apply_zip import safe_extract_zip
from facebook_monitor.updates.apply_zip import validate_macos_zip_executable_bits
from facebook_monitor.updates.artifacts import UpdateArtifactPolicy
from facebook_monitor.updates.artifacts import release_artifact_policy_for_asset_name
from facebook_monitor.updates.artifacts import sanitize_release_asset_name
from facebook_monitor.updates.download import calculate_sha256
from facebook_monitor.updates.handoff import PendingUpdate
from facebook_monitor.updates.handoff import load_pending_update
from facebook_monitor.updates.handoff import validate_pending_update_artifact_set
from facebook_monitor.updates.handoff import validate_pending_update_paths
from facebook_monitor.updates.manifest import verify_release_manifest
from facebook_monitor.updates.platforms import UpdaterLayoutPolicy
from facebook_monitor.updates.platforms import detect_layout_policy
from facebook_monitor.updates.trust import TRUSTED_RELEASE_PUBLIC_KEYS
from facebook_monitor.version import APP_VERSION
from facebook_monitor.versioning import parse_version


@dataclass(frozen=True)
class UpdaterApplyResult:
    """更新套用結果。"""

    status: str
    applied: bool
    message: str
    backup_dir: Path | None = None
    staging_dir: Path | None = None


@dataclass(frozen=True)
class PreparedUpdateStage:
    """保存已解壓並驗證完成的更新 staging 資料。"""

    layout_policy: UpdaterLayoutPolicy
    staging_dir: Path
    staging_app_root: Path


CONSUMED_PENDING_UPDATE_MARKER_NAME = "pending_update.applied.json"
CONSUMED_PENDING_UPDATE_MARKER_SCHEMA_VERSION = 1


def apply_pending_update_file(
    path: Path,
    *,
    wait_for_lock_seconds: float = 0,
    wait_for_operation_lock_seconds: float = 0,
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
        wait_for_operation_lock_seconds=wait_for_operation_lock_seconds,
        poll_seconds=poll_seconds,
        log_path=log_path,
    )


def apply_loaded_pending_update_file(
    pending: PendingUpdate,
    path: Path,
    *,
    wait_for_lock_seconds: float = 0,
    wait_for_operation_lock_seconds: float = 0,
    poll_seconds: float = 1,
    log_path: Path | None = None,
) -> UpdaterApplyResult:
    """使用已讀取的 pending update 套用更新並處理 handoff cleanup。"""

    result, cleanup_warnings = _apply_loaded_pending_update_file_under_operation_lock(
        pending,
        path,
        wait_for_lock_seconds=wait_for_lock_seconds,
        wait_for_operation_lock_seconds=wait_for_operation_lock_seconds,
        poll_seconds=poll_seconds,
    )
    _append_updater_log(log_path, result)
    _append_cleanup_warning_log(log_path, cleanup_warnings)
    return result


def apply_pending_update(
    pending: PendingUpdate,
    *,
    wait_for_lock_seconds: float = 0,
    wait_for_operation_lock_seconds: float = 0,
    poll_seconds: float = 1,
) -> UpdaterApplyResult:
    """套用已驗證更新包；主程式仍執行時會拒絕替換。"""

    try:
        with _wait_for_update_operation_lock(
            pending.runtime_dir,
            wait_for_operation_lock_seconds=wait_for_operation_lock_seconds,
            poll_seconds=poll_seconds,
        ):
            return _apply_pending_update_locked(
                pending,
                wait_for_lock_seconds=wait_for_lock_seconds,
                poll_seconds=poll_seconds,
            )
    except UpdateOperationLockError as exc:
        return UpdaterApplyResult(
            status="operation_lock",
            applied=False,
            message=str(exc),
        )


def _apply_loaded_pending_update_file_under_operation_lock(
    pending: PendingUpdate,
    path: Path,
    *,
    wait_for_lock_seconds: float,
    wait_for_operation_lock_seconds: float,
    poll_seconds: float,
) -> tuple[UpdaterApplyResult, tuple[str, ...]]:
    """在同一把 operation lock 內完成 handoff 確認、套用與 cleanup。"""

    try:
        with _wait_for_update_operation_lock(
            pending.runtime_dir,
            wait_for_operation_lock_seconds=wait_for_operation_lock_seconds,
            poll_seconds=poll_seconds,
        ):
            stale_result = _validate_loaded_pending_update_file_is_current(pending, path)
            if stale_result is not None:
                return stale_result, ()
            result = _apply_pending_update_locked(
                pending,
                wait_for_lock_seconds=wait_for_lock_seconds,
                poll_seconds=poll_seconds,
            )
            cleanup_warnings: tuple[str, ...] = ()
            if result.applied:
                cleanup_warnings = _cleanup_loaded_pending_update_file(
                    pending,
                    path,
                    result,
                )
            return result, cleanup_warnings
    except UpdateOperationLockError as exc:
        return (
            UpdaterApplyResult(
                status="operation_lock",
                applied=False,
                message=str(exc),
            ),
            (),
        )


def _validate_loaded_pending_update_file_is_current(
    pending: PendingUpdate,
    path: Path,
) -> UpdaterApplyResult | None:
    """確認已載入的 pending 仍是目前 handoff，避免 stale updater 重複套用。"""

    if _consumed_pending_update_marker_matches(
        pending,
        _consumed_pending_update_marker_path(path),
        pending_path=path,
    ) or _consumed_pending_update_marker_matches(pending, path, pending_path=path):
        return UpdaterApplyResult(
            status="pending_update_already_applied",
            applied=False,
            message=str(path),
        )
    try:
        current = load_pending_update(path)
    except FileNotFoundError:
        return UpdaterApplyResult(
            status="pending_update_missing",
            applied=False,
            message=str(path),
        )
    except (OSError, ValueError) as exc:
        return UpdaterApplyResult(
            status="failed",
            applied=False,
            message=str(exc),
        )
    if current != pending:
        return UpdaterApplyResult(
            status="pending_update_changed",
            applied=False,
            message=str(path),
        )
    return None


def _cleanup_loaded_pending_update_file(
    pending: PendingUpdate,
    path: Path,
    result: UpdaterApplyResult,
) -> tuple[str, ...]:
    """成功套用後，在 operation lock 釋放前清除 handoff 與舊備份。"""

    return (
        *_write_consumed_pending_update_marker(pending, path),
        *_cleanup_applied_update(path, pending),
        *_cleanup_old_backup_dirs(
            pending.runtime_dir / BACKUP_DIR_NAME,
            keep_count=BACKUP_RETENTION_COUNT,
            preserve=result.backup_dir,
        ),
    )


def _write_consumed_pending_update_marker(
    pending: PendingUpdate,
    path: Path,
) -> tuple[str, ...]:
    """記錄 handoff 已成功套用，避免 cleanup 失敗後被第二個 updater 重跑。"""

    marker_path = _consumed_pending_update_marker_path(path)
    tmp_path = marker_path.with_name(f".{marker_path.name}.{uuid.uuid4().hex}.tmp")
    payload = _consumed_pending_update_marker_payload(pending, path)
    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        with tmp_path.open("x", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
            file.write("\n")
        tmp_path.replace(marker_path)
    except OSError as exc:
        return (
            f"consumed_pending_marker:{type(exc).__name__}:{exc}",
            *_overwrite_pending_update_with_consumed_marker(path, payload),
        )
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
    return ()


def _overwrite_pending_update_with_consumed_marker(
    path: Path,
    payload: dict[str, object],
) -> tuple[str, ...]:
    """consumed marker 寫入失敗時，退回覆寫 pending 檔避免重複套用。"""

    try:
        with path.open("w", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
            file.write("\n")
    except OSError as exc:
        return (f"consumed_pending_fallback:{type(exc).__name__}:{exc}",)
    return ()


def _consumed_pending_update_marker_matches(
    pending: PendingUpdate,
    marker_path: Path,
    *,
    pending_path: Path,
) -> bool:
    """回傳目前 handoff 是否已被前一個 updater 標記為成功套用。"""

    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload == _consumed_pending_update_marker_payload(pending, pending_path)


def _consumed_pending_update_marker_payload(
    pending: PendingUpdate,
    path: Path,
) -> dict[str, object]:
    """建立 consumed marker payload；只放判斷同一 handoff 必要欄位。"""

    return {
        "schema_version": CONSUMED_PENDING_UPDATE_MARKER_SCHEMA_VERSION,
        "handoff_consumed": True,
        "pending_path": str(path.resolve()),
        "version": pending.version,
        "repository": pending.repository,
        "asset_name": pending.asset_name,
        "zip_path": str(pending.zip_path.resolve()),
        "expected_sha256": pending.expected_sha256.casefold(),
        "manifest_sha256": pending.manifest_sha256.casefold(),
        "created_at": pending.created_at,
    }


def _consumed_pending_update_marker_path(path: Path) -> Path:
    """回傳 pending handoff 對應的 consumed marker 路徑。"""

    return path.with_name(CONSUMED_PENDING_UPDATE_MARKER_NAME)


@contextmanager
def _wait_for_update_operation_lock(
    runtime_dir: Path,
    *,
    wait_for_operation_lock_seconds: float,
    poll_seconds: float,
) -> Iterator[UpdateOperationLock]:
    """等待 settings route 釋放 updater operation lock，再進入套用流程。"""

    deadline = time.monotonic() + max(0, wait_for_operation_lock_seconds)
    last_error: UpdateOperationLockError | None = None
    while True:
        try:
            with ensure_update_operation_lock(runtime_dir, "updater-apply") as lock:
                yield lock
                return
        except UpdateOperationLockError as exc:
            last_error = exc
            if time.monotonic() >= deadline:
                raise last_error
            time.sleep(max(0.1, poll_seconds))


def _apply_pending_update_locked(
    pending: PendingUpdate,
    *,
    wait_for_lock_seconds: float,
    poll_seconds: float,
) -> UpdaterApplyResult:
    """已持有 operation lock 時執行實際套用流程。"""

    try:
        validate_pending_update_paths(pending)
        validate_pending_update_artifact_set(pending)
        _validate_pending_version_is_newer(pending)
        _validate_pending_artifact_policy_matches_install(pending)
        _validate_pending_manifest_trust(pending)
        _validate_pending_hash(pending)
        with _wait_for_app_lock(
            pending.runtime_dir,
            wait_for_lock_seconds=wait_for_lock_seconds,
            poll_seconds=poll_seconds,
        ):
            prepared = _prepare_update_stage(pending)
            backup_dir = _replace_current_app_from_stage(pending, prepared)
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
        staging_dir=prepared.staging_dir,
    )


def _prepare_update_stage(pending: PendingUpdate) -> PreparedUpdateStage:
    """解壓並驗證更新包 staging，尚不觸碰目前 app files。"""

    layout_policy = detect_layout_policy(pending.app_base_dir)
    validate_macos_zip_executable_bits(
        pending.zip_path,
        layout_policy=layout_policy,
    )
    staging_dir = _prepare_empty_dir(
        pending.runtime_dir / STAGING_DIR_NAME / sanitize_release_asset_name(pending.version),
        work_root=pending.runtime_dir,
    )
    safe_extract_zip(pending.zip_path, staging_dir)
    staging_app_root = find_staging_app_root(
        staging_dir,
        layout_policy=layout_policy,
    )
    validate_staging_app_root(
        staging_app_root,
        layout_policy=layout_policy,
        expected_version=pending.version,
    )
    return PreparedUpdateStage(
        layout_policy=layout_policy,
        staging_dir=staging_dir,
        staging_app_root=staging_app_root,
    )


def _replace_current_app_from_stage(
    pending: PendingUpdate,
    prepared: PreparedUpdateStage,
) -> Path:
    """備份目前 app files 後用已驗證 staging 替換，失敗時嘗試 rollback。"""

    validate_current_app_root(
        pending.app_base_dir,
        layout_policy=prepared.layout_policy,
        data_dir=pending.data_dir,
    )
    backup_dir = _prepare_empty_dir(
        pending.runtime_dir / BACKUP_DIR_NAME / _backup_folder_name(pending.version),
        work_root=pending.runtime_dir,
    )
    backup_current_app_files(
        app_base_dir=pending.app_base_dir,
        backup_dir=backup_dir,
        data_dir=pending.data_dir,
    )
    try:
        replace_app_files(
            staging_app_root=prepared.staging_app_root,
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
    return backup_dir


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


def _validate_pending_manifest_trust(pending: PendingUpdate) -> None:
    """套用前重驗 signed manifest，避免 handoff 後信任資料被一起改寫。"""

    if (
        pending.manifest_path is None
        or pending.manifest_signature_path is None
        or not pending.manifest_sha256
        or not pending.manifest_key_id
    ):
        raise ValueError("pending_manifest_required")
    manifest_actual = calculate_sha256(pending.manifest_path)
    if manifest_actual != pending.manifest_sha256:
        raise ValueError("pending_manifest_sha256_mismatch")
    artifact_policy = _pending_artifact_policy(pending)
    verified = verify_release_manifest(
        manifest_bytes=pending.manifest_path.read_bytes(),
        signature_bytes=pending.manifest_signature_path.read_bytes(),
        expected_version=pending.version,
        expected_repository=pending.repository,
        expected_asset_name=pending.asset_name,
        expected_platform=artifact_policy.platform_key,
        trusted_public_keys=TRUSTED_RELEASE_PUBLIC_KEYS,
    )
    if verified.key_id != pending.manifest_key_id:
        raise ValueError("pending_manifest_key_mismatch")
    if verified.manifest_sha256 != pending.manifest_sha256.casefold():
        raise ValueError("pending_manifest_sha256_mismatch")
    if verified.asset.sha256 != pending.expected_sha256.casefold():
        raise ValueError("pending_manifest_asset_sha256_mismatch")
    if pending.zip_path.stat().st_size != verified.asset.size:
        raise ValueError("pending_manifest_asset_size_mismatch")


def _validate_pending_version_is_newer(pending: PendingUpdate) -> None:
    """避免過期 handoff 在目前 app 已升級後套回舊版或同版。"""

    try:
        pending_version = parse_version(pending.version)
        current_version = parse_version(APP_VERSION)
    except ValueError as exc:
        raise ValueError("pending_update_version_invalid") from exc
    if pending_version.sort_key() <= current_version.sort_key():
        raise ValueError("pending_update_not_newer")


def _validate_pending_artifact_policy_matches_install(pending: PendingUpdate) -> None:
    """確認 pending asset 平台與目前 app layout 一致。"""

    artifact_policy = _pending_artifact_policy(pending)
    layout_policy = detect_layout_policy(pending.app_base_dir)
    if artifact_policy.platform_key != layout_policy.platform_key:
        raise ValueError("pending_update_artifact_platform_mismatch")


def _pending_artifact_policy(pending: PendingUpdate) -> UpdateArtifactPolicy:
    """依 pending asset name 找出 release artifact policy。"""

    artifact_policy = release_artifact_policy_for_asset_name(pending.asset_name)
    if artifact_policy is None:
        raise ValueError("pending_manifest_asset_platform_unknown")
    return artifact_policy


def _validate_pending_hash(pending: PendingUpdate) -> None:
    """套用前重算 zip SHA256，避免 handoff 後檔案被替換。"""

    actual = calculate_sha256(pending.zip_path)
    if actual != pending.expected_sha256:
        raise ValueError("pending_zip_sha256_mismatch")


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
