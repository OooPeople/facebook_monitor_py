"""更新檔下載、signed manifest 與 SHA256 驗證。

職責：將已知 GitHub Release asset 下載到 runtime data dir 底下，
先驗 signed manifest 與同名 SHA256 sidecar，再用 manifest hash 驗證 zip
完整性。此模組不解壓、不替換程式檔，也不嘗試關閉或重啟主程式。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import subprocess
import sys

import httpx

from facebook_monitor.core.defaults import PYTHON_UPDATER_RUNTIME_DEFAULTS
from facebook_monitor.runtime.update_operation_lock import ensure_update_operation_lock
from facebook_monitor.runtime.update_operation_lock import UpdateOperationLockError
from facebook_monitor.updates.download_fetch import download_staged_release_files
from facebook_monitor.updates.download_fetch import verify_staged_asset
from facebook_monitor.updates.download_models import MAX_SHA256_DOWNLOAD_BYTES
from facebook_monitor.updates.download_models import MAX_UPDATE_DOWNLOAD_BYTES
from facebook_monitor.updates.download_models import StagedAssetVerification
from facebook_monitor.updates.download_models import UpdateDownloadPlan
from facebook_monitor.updates.download_models import UpdateDownloadResult
from facebook_monitor.updates.download_models import VERIFIED_DOWNLOAD_SET_MARKER_NAME
from facebook_monitor.updates.download_models import (
    VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION,
)
from facebook_monitor.updates.download_models import make_failure_download_result
from facebook_monitor.updates.download_paths import build_download_plan
from facebook_monitor.updates.download_paths import cleanup_staged_download
from facebook_monitor.updates.download_paths import ensure_child_path
from facebook_monitor.updates.download_paths import ensure_safe_download_path
from facebook_monitor.updates.download_paths import operation_runtime_dir_for_updates_dir
from facebook_monitor.updates.download_paths import prepare_download_plan
from facebook_monitor.updates.download_publish import publish_verified_download_plan
from facebook_monitor.updates.download_publish import validate_verified_download_set
from facebook_monitor.updates.release_check import UpdateCheckResult


async def download_and_verify_update(
    *,
    update_check: UpdateCheckResult,
    updates_dir: Path,
    timeout_seconds: float = PYTHON_UPDATER_RUNTIME_DEFAULTS.timeout_seconds,
    transport: httpx.AsyncBaseTransport | None = None,
    max_asset_bytes: int = MAX_UPDATE_DOWNLOAD_BYTES,
    max_sha256_bytes: int = MAX_SHA256_DOWNLOAD_BYTES,
    trusted_public_keys: Mapping[str, str] | None = None,
) -> UpdateDownloadResult:
    """下載更新 zip 與 signed manifest，驗證通過後保留於 updates dir。"""

    try:
        with ensure_update_operation_lock(
            operation_runtime_dir_for_updates_dir(updates_dir),
            "download-and-verify-update",
        ):
            return await _download_and_verify_update_locked(
                update_check=update_check,
                updates_dir=updates_dir,
                timeout_seconds=timeout_seconds,
                transport=transport,
                max_asset_bytes=max_asset_bytes,
                max_sha256_bytes=max_sha256_bytes,
                trusted_public_keys=trusted_public_keys,
            )
    except UpdateOperationLockError:
        return _failure("update_operation_locked")


async def _download_and_verify_update_locked(
    *,
    update_check: UpdateCheckResult,
    updates_dir: Path,
    timeout_seconds: float,
    transport: httpx.AsyncBaseTransport | None,
    max_asset_bytes: int,
    max_sha256_bytes: int,
    trusted_public_keys: Mapping[str, str] | None,
) -> UpdateDownloadResult:
    """已持有 operation lock 時執行實際下載與驗證。"""

    missing_reason = _missing_update_download_reason(update_check)
    if missing_reason:
        return _failure(missing_reason)
    try:
        plan = build_download_plan(update_check=update_check, updates_dir=updates_dir)
    except ValueError as exc:
        return _failure(str(exc))
    try:
        prepare_download_plan(plan)
        async with httpx.AsyncClient(
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=True,
        ) as client:
            manifest = await download_staged_release_files(
                client=client,
                update_check=update_check,
                plan=plan,
                max_sha256_bytes=max_sha256_bytes,
                max_asset_bytes=max_asset_bytes,
                trusted_public_keys=trusted_public_keys,
            )
        verification = verify_staged_asset(plan=plan, manifest=manifest)
        if verification.expected_sha256 != verification.actual_sha256:
            cleanup_staged_download(plan)
            return _sha256_mismatch_result(plan, verification)
        publish_verified_download_plan(plan, verification)
    except httpx.HTTPStatusError as exc:
        cleanup_staged_download(plan)
        return _failure_for_plan(
            f"download_http_{exc.response.status_code}",
            plan=plan,
        )
    except httpx.HTTPError as exc:
        cleanup_staged_download(plan)
        return _failure_for_plan(
            f"download_error:{exc.__class__.__name__}",
            plan=plan,
        )
    except ValueError as exc:
        cleanup_staged_download(plan)
        return _failure_for_plan(
            str(exc),
            plan=plan,
        )
    except OSError as exc:
        cleanup_staged_download(plan)
        return _failure_for_plan(
            f"download_io_error:{exc.__class__.__name__}",
            plan=plan,
        )
    return _verified_result(plan, verification)


def _missing_update_download_reason(update_check: UpdateCheckResult) -> str:
    """回傳 update check 缺少必要下載資訊的原因。"""

    if not update_check.update_available or not update_check.asset_name:
        return "update_not_available"
    if not update_check.asset_download_url:
        return "asset_download_url_missing"
    if not update_check.sha256_asset_name:
        return "sha256_asset_missing"
    if not update_check.sha256_asset_download_url:
        return "sha256_asset_url_missing"
    if not update_check.manifest_asset_name:
        return "manifest_file_missing"
    if not update_check.manifest_asset_download_url:
        return "manifest_asset_url_missing"
    if not update_check.manifest_signature_asset_name:
        return "manifest_signature_asset_missing"
    if not update_check.manifest_signature_asset_download_url:
        return "manifest_signature_asset_url_missing"
    return ""


def _sha256_mismatch_result(
    plan: UpdateDownloadPlan,
    verification: StagedAssetVerification,
) -> UpdateDownloadResult:
    """建立 zip hash mismatch 的特殊結果，保留 downloaded=True 語義。"""

    return UpdateDownloadResult(
        status="sha256_mismatch",
        downloaded=True,
        verified=False,
        file_path=plan.file_path,
        sha256_path=plan.sha256_path,
        expected_sha256=verification.expected_sha256,
        actual_sha256=verification.actual_sha256,
        failure_reason="sha256_mismatch",
        manifest_path=plan.manifest_path,
        manifest_signature_path=plan.manifest_signature_path,
        manifest_sha256=verification.manifest.manifest_sha256,
        manifest_key_id=verification.manifest.key_id,
        verified_set_marker_path=plan.verified_set_marker_path,
    )


def _verified_result(
    plan: UpdateDownloadPlan,
    verification: StagedAssetVerification,
) -> UpdateDownloadResult:
    """建立 verified download result。"""

    return UpdateDownloadResult(
        status="verified",
        downloaded=True,
        verified=True,
        file_path=plan.file_path,
        sha256_path=plan.sha256_path,
        expected_sha256=verification.expected_sha256,
        actual_sha256=verification.actual_sha256,
        failure_reason="",
        manifest_path=plan.manifest_path,
        manifest_signature_path=plan.manifest_signature_path,
        manifest_sha256=verification.manifest.manifest_sha256,
        manifest_key_id=verification.manifest.key_id,
        verified_set_marker_path=plan.verified_set_marker_path,
    )


def _failure_for_plan(reason: str, *, plan: UpdateDownloadPlan) -> UpdateDownloadResult:
    """建立帶下載路徑資訊的 failure result。"""

    return _failure(
        reason,
        file_path=plan.file_path,
        sha256_path=plan.sha256_path,
        manifest_path=plan.manifest_path,
        manifest_signature_path=plan.manifest_signature_path,
        verified_set_marker_path=plan.verified_set_marker_path,
    )


def _failure(
    reason: str,
    *,
    file_path: Path | None = None,
    sha256_path: Path | None = None,
    manifest_path: Path | None = None,
    manifest_signature_path: Path | None = None,
    verified_set_marker_path: Path | None = None,
) -> UpdateDownloadResult:
    """建立下載失敗結果。"""

    return make_failure_download_result(
        reason,
        file_path=file_path,
        sha256_path=sha256_path,
        manifest_path=manifest_path,
        manifest_signature_path=manifest_signature_path,
        verified_set_marker_path=verified_set_marker_path,
    )


def reveal_in_file_manager(path: Path) -> bool:
    """開啟檔案所在資料夾；失敗時回傳 False，避免影響主程式。"""

    try:
        if not path.exists():
            return False
        if path.is_file():
            target = path.parent
        else:
            target = path
        if _is_windows():
            import os

            startfile = getattr(os, "startfile", None)
            if not callable(startfile):
                return False
            startfile(str(target))
            return True
        if _is_macos():
            subprocess.Popen(  # noqa: S603, S607
                ["open", str(target)],
                close_fds=True,
                start_new_session=True,
            )
            return True
        return False
    except OSError:
        return False


def _is_windows() -> bool:
    """集中平台判斷，方便測試替換。"""

    return sys.platform == "win32"


def _is_macos() -> bool:
    """集中平台判斷，方便測試替換。"""

    return sys.platform == "darwin"


__all__ = [
    "MAX_SHA256_DOWNLOAD_BYTES",
    "MAX_UPDATE_DOWNLOAD_BYTES",
    "UpdateDownloadPlan",
    "UpdateDownloadResult",
    "VERIFIED_DOWNLOAD_SET_MARKER_NAME",
    "VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION",
    "download_and_verify_update",
    "ensure_child_path",
    "ensure_safe_download_path",
    "reveal_in_file_manager",
    "validate_verified_download_set",
]
