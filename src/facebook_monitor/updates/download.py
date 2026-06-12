"""更新檔下載、signed manifest 與 SHA256 驗證。

職責：將已知 GitHub Release asset 下載到 runtime data dir 底下，
先驗 signed manifest 與同名 SHA256 sidecar，再用 manifest hash 驗證 zip
完整性。此模組不解壓、不替換程式檔，也不嘗試關閉或重啟主程式。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import AsyncIterator
import uuid

import httpx

from facebook_monitor.core.defaults import PYTHON_UPDATER_RUNTIME_DEFAULTS
from facebook_monitor.runtime.update_operation_lock import ensure_update_operation_lock
from facebook_monitor.runtime.update_operation_lock import UpdateOperationLockError
from facebook_monitor.updates.artifacts import release_artifact_policy_for_asset_name
from facebook_monitor.updates.artifacts import sanitize_release_asset_name
from facebook_monitor.updates.checksum import HASH_CHUNK_SIZE
from facebook_monitor.updates.checksum import calculate_sha256 as _calculate_sha256
from facebook_monitor.updates.checksum import read_sha256_sidecar
from facebook_monitor.updates.download_url_policy import validate_final_release_download_url
from facebook_monitor.updates.download_url_policy import validate_initial_release_download_url
from facebook_monitor.updates.manifest import VerifiedReleaseManifest
from facebook_monitor.updates.manifest import verify_release_manifest
from facebook_monitor.updates.release_check import UpdateCheckResult
from facebook_monitor.updates.validation import has_unsafe_existing_path_component
from facebook_monitor.updates.validation import is_reparse_or_symlink


DOWNLOAD_CHUNK_SIZE = HASH_CHUNK_SIZE
MAX_UPDATE_DOWNLOAD_BYTES = 1024 * 1024 * 1024
MAX_SHA256_DOWNLOAD_BYTES = 1024 * 1024
MAX_MANIFEST_DOWNLOAD_BYTES = 1024 * 1024
MAX_MANIFEST_SIGNATURE_DOWNLOAD_BYTES = 4096
VERIFIED_DOWNLOAD_SET_MARKER_NAME = "verified-download.json"
VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class UpdateDownloadResult:
    """更新檔下載與驗證結果。"""

    status: str
    downloaded: bool
    verified: bool
    file_path: Path | None
    sha256_path: Path | None
    expected_sha256: str
    actual_sha256: str
    failure_reason: str
    manifest_path: Path | None = None
    manifest_signature_path: Path | None = None
    manifest_sha256: str = ""
    manifest_key_id: str = ""
    verified_set_marker_path: Path | None = None


@dataclass(frozen=True)
class UpdateDownloadPlan:
    """保存單次更新下載的正式與 staging 路徑。"""

    asset_name: str
    sha256_name: str
    manifest_name: str
    manifest_signature_name: str
    updates_root: Path
    destination_dir: Path
    verified_set_dir: Path
    staged_set_dir: Path
    file_path: Path
    sha256_path: Path | None
    manifest_path: Path
    manifest_signature_path: Path
    verified_set_marker_path: Path
    staged_file_path: Path
    staged_sha256_path: Path | None
    staged_manifest_path: Path
    staged_manifest_signature_path: Path


@dataclass(frozen=True)
class StagedAssetVerification:
    """保存 staged zip 與 signed manifest 驗證結果。"""

    manifest: VerifiedReleaseManifest
    expected_sha256: str
    actual_sha256: str


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
            _operation_runtime_dir_for_updates_dir(updates_dir),
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
        plan = _build_download_plan(update_check=update_check, updates_dir=updates_dir)
    except ValueError as exc:
        return _failure(str(exc))
    try:
        _prepare_download_plan(plan)
        async with httpx.AsyncClient(
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=True,
        ) as client:
            manifest = await _download_staged_release_files(
                client=client,
                update_check=update_check,
                plan=plan,
                max_sha256_bytes=max_sha256_bytes,
                max_asset_bytes=max_asset_bytes,
                trusted_public_keys=trusted_public_keys,
            )
        verification = _verify_staged_asset(plan=plan, manifest=manifest)
        if verification.expected_sha256 != verification.actual_sha256:
            _cleanup_staged_download(plan)
            return _sha256_mismatch_result(plan, verification)
        _publish_verified_download_plan(plan, verification)
    except httpx.HTTPStatusError as exc:
        _cleanup_staged_download(plan)
        return _failure_for_plan(
            f"download_http_{exc.response.status_code}",
            plan=plan,
        )
    except httpx.HTTPError as exc:
        _cleanup_staged_download(plan)
        return _failure_for_plan(
            f"download_error:{exc.__class__.__name__}",
            plan=plan,
        )
    except ValueError as exc:
        _cleanup_staged_download(plan)
        return _failure_for_plan(
            str(exc),
            plan=plan,
        )
    except OSError as exc:
        _cleanup_staged_download(plan)
        return _failure_for_plan(
            f"download_io_error:{exc.__class__.__name__}",
            plan=plan,
        )
    return _verified_result(plan, verification)


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


def read_expected_sha256(path: Path, *, expected_filename: str) -> str:
    """讀取 `.sha256` 檔案，支援常見 `hash  filename` 格式。"""

    return read_sha256_sidecar(path, expected_filename=expected_filename)


def calculate_sha256(path: Path) -> str:
    """計算檔案 SHA256。"""

    return _calculate_sha256(path)


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


def _build_download_plan(
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


def _prepare_download_plan(plan: UpdateDownloadPlan) -> None:
    """建立下載目錄並檢查正式 / staging 目的地安全性。"""

    _prepare_destination_dir(plan.destination_dir, updates_root=plan.updates_root)
    _prepare_attempt_set_dir(plan.staged_set_dir, updates_root=plan.updates_root)
    _ensure_download_set_destination_available(
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
    _prepare_download_destinations(
        *destinations,
        updates_root=plan.updates_root,
    )


async def _download_staged_release_files(
    *,
    client: httpx.AsyncClient,
    update_check: UpdateCheckResult,
    plan: UpdateDownloadPlan,
    max_sha256_bytes: int,
    max_asset_bytes: int,
    trusted_public_keys: Mapping[str, str] | None,
) -> VerifiedReleaseManifest:
    """依序下載並驗證 manifest、sidecar 與 update asset 到 staging path。"""

    await _download_file(
        client=client,
        url=update_check.manifest_asset_download_url,
        destination=plan.staged_manifest_path,
        updates_root=plan.updates_root,
        max_bytes=MAX_MANIFEST_DOWNLOAD_BYTES,
        expected_asset_name=plan.manifest_name,
    )
    await _download_file(
        client=client,
        url=update_check.manifest_signature_asset_download_url,
        destination=plan.staged_manifest_signature_path,
        updates_root=plan.updates_root,
        max_bytes=MAX_MANIFEST_SIGNATURE_DOWNLOAD_BYTES,
        expected_asset_name=plan.manifest_signature_name,
    )
    manifest = _verify_staged_manifest(
        update_check=update_check,
        manifest_path=plan.staged_manifest_path,
        signature_path=plan.staged_manifest_signature_path,
        asset_name=plan.asset_name,
        trusted_public_keys=trusted_public_keys,
    )
    if plan.sha256_path is not None and plan.staged_sha256_path is not None:
        await _download_file(
            client=client,
            url=update_check.sha256_asset_download_url,
            destination=plan.staged_sha256_path,
            updates_root=plan.updates_root,
            max_bytes=max_sha256_bytes,
            expected_asset_name=plan.sha256_name,
        )
        sidecar_sha256 = read_expected_sha256(
            plan.staged_sha256_path,
            expected_filename=plan.asset_name,
        )
        if sidecar_sha256 != manifest.asset.sha256:
            raise ValueError("sha256_sidecar_manifest_mismatch")
    await _download_file(
        client=client,
        url=update_check.asset_download_url,
        destination=plan.staged_file_path,
        updates_root=plan.updates_root,
        max_bytes=max_asset_bytes,
        expected_asset_name=plan.asset_name,
    )
    return manifest


def _verify_staged_asset(
    *,
    plan: UpdateDownloadPlan,
    manifest: VerifiedReleaseManifest,
) -> StagedAssetVerification:
    """比對 staged zip 的 size 與 SHA256。"""

    expected_sha256 = manifest.asset.sha256
    actual_size = plan.staged_file_path.stat().st_size
    if actual_size != manifest.asset.size:
        raise ValueError("manifest_asset_size_mismatch")
    actual_sha256 = calculate_sha256(plan.staged_file_path)
    return StagedAssetVerification(
        manifest=manifest,
        expected_sha256=expected_sha256,
        actual_sha256=actual_sha256,
    )


def _cleanup_staged_download(plan: UpdateDownloadPlan) -> None:
    """清除 staged manifest、signature、sidecar 與 zip。"""

    _cleanup_download_dir(plan.staged_set_dir, updates_root=plan.updates_root)


def _publish_verified_download_plan(
    plan: UpdateDownloadPlan,
    verification: StagedAssetVerification,
) -> None:
    """將驗證通過的 staging set 發布為正式 artifact set。"""

    _publish_verified_download(
        staged_file_path=plan.staged_file_path,
        staged_sha256_path=plan.staged_sha256_path,
        staged_manifest_path=plan.staged_manifest_path,
        staged_manifest_signature_path=plan.staged_manifest_signature_path,
        staged_set_dir=plan.staged_set_dir,
        verified_set_dir=plan.verified_set_dir,
        updates_root=plan.updates_root,
        verification=verification,
    )


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


async def _download_file(
    *,
    client: httpx.AsyncClient,
    url: str,
    destination: Path,
    updates_root: Path,
    max_bytes: int,
    expected_asset_name: str,
) -> None:
    """串流下載單一檔案；完成前使用 `.tmp` 避免半成品被當成可用。"""

    tmp_destination = destination.with_name(destination.name + ".tmp")
    try:
        _prepare_download_tmp(
            destination,
            tmp_destination,
            updates_root=updates_root,
        )
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            validate_final_release_download_url(
                str(response.url),
                expected_asset_name=expected_asset_name,
            )
            content_length = _parse_content_length(response)
            if content_length is not None and content_length > max_bytes:
                raise ValueError("download_too_large")
            downloaded_bytes = 0
            with tmp_destination.open("xb") as file:
                async for chunk in _aiter_response_bytes(response):
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > max_bytes:
                        raise ValueError("download_too_large")
                    file.write(chunk)
        ensure_safe_download_path(destination, updates_root=updates_root)
        if is_reparse_or_symlink(destination):
            raise ValueError("download_path_unsafe")
        tmp_destination.replace(destination)
    except FileExistsError as exc:
        raise ValueError("download_path_unsafe") from exc
    except Exception:
        try:
            tmp_destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _verify_staged_manifest(
    *,
    update_check: UpdateCheckResult,
    manifest_path: Path,
    signature_path: Path,
    asset_name: str,
    trusted_public_keys: Mapping[str, str] | None,
) -> VerifiedReleaseManifest:
    """驗證已下載 manifest，並確認 asset platform 與檔名一致。"""

    policy = release_artifact_policy_for_asset_name(asset_name)
    if policy is None:
        raise ValueError("manifest_asset_platform_unknown")
    return verify_release_manifest(
        manifest_bytes=manifest_path.read_bytes(),
        signature_bytes=signature_path.read_bytes(),
        expected_version=update_check.latest_version,
        expected_repository=update_check.repository,
        expected_asset_name=asset_name,
        expected_platform=policy.platform_key,
        trusted_public_keys=trusted_public_keys,
    )


def _prepare_destination_dir(path: Path, *, updates_root: Path) -> None:
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


def _prepare_attempt_set_dir(path: Path, *, updates_root: Path) -> None:
    """建立單次下載 staging set 目錄；既有路徑一律視為不安全。"""

    _ensure_download_set_destination_available(path, updates_root=updates_root)
    path.mkdir()
    ensure_safe_download_path(path, updates_root=updates_root)
    if is_reparse_or_symlink(path):
        raise ValueError("download_path_unsafe")


def _ensure_download_set_destination_available(
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


def _prepare_download_destinations(
    *destinations: Path,
    updates_root: Path,
) -> None:
    """下載前準備所有正式與 staging 路徑，並移除安全範圍內 stale `.tmp`。"""

    for destination in destinations:
        _prepare_download_tmp(
            destination,
            destination.with_name(destination.name + ".tmp"),
            updates_root=updates_root,
        )


def _publish_verified_download(
    *,
    staged_file_path: Path,
    staged_sha256_path: Path | None,
    staged_manifest_path: Path,
    staged_manifest_signature_path: Path,
    staged_set_dir: Path,
    verified_set_dir: Path,
    updates_root: Path,
    verification: StagedAssetVerification,
) -> None:
    """驗證完成後才將 staging set 原子發布到正式 set 目錄。"""

    _ensure_download_set_destination_available(
        verified_set_dir,
        updates_root=updates_root,
    )
    staged_marker_path = staged_set_dir / VERIFIED_DOWNLOAD_SET_MARKER_NAME
    try:
        _write_verified_download_set_marker(
            staged_marker_path,
            file_path=staged_file_path,
            sha256_path=staged_sha256_path,
            manifest_path=staged_manifest_path,
            manifest_signature_path=staged_manifest_signature_path,
            verification=verification,
            updates_root=updates_root,
        )
        staged_set_dir.replace(verified_set_dir)
    except Exception:
        _cleanup_download_dir(staged_set_dir, updates_root=updates_root)
        _cleanup_download_dir(verified_set_dir, updates_root=updates_root)
        raise


def validate_verified_download_set(download_result: UpdateDownloadResult) -> None:
    """確認 verified download set marker 與目前檔案仍一致。"""

    if not download_result.verified or download_result.file_path is None:
        raise ValueError("download_result_not_verified")
    marker_path = download_result.verified_set_marker_path or _verified_marker_path_for(
        download_result.file_path
    )
    _validate_verified_download_set_paths(download_result, marker_path)
    payload = _load_verified_download_set_marker(marker_path)
    _validate_verified_marker_asset(payload, download_result)
    if (
        download_result.manifest_path is None
        or download_result.manifest_signature_path is None
    ):
        raise ValueError("download_result_manifest_missing")
    _validate_verified_marker_manifest(payload, download_result)
    _validate_verified_marker_signature(payload, download_result)
    _validate_verified_marker_sidecar(payload, download_result)


def _validate_verified_download_set_paths(
    download_result: UpdateDownloadResult,
    marker_path: Path,
) -> None:
    """確認 verified download set 的所有檔案位於同一個目錄。"""

    assert download_result.file_path is not None
    set_dir = download_result.file_path.parent
    if marker_path.parent != set_dir:
        raise ValueError("download_result_verified_set_mismatch")
    if (
        download_result.manifest_path is not None
        and download_result.manifest_path.parent != set_dir
    ):
        raise ValueError("download_result_verified_set_mismatch")
    if (
        download_result.manifest_signature_path is not None
        and download_result.manifest_signature_path.parent != set_dir
    ):
        raise ValueError("download_result_verified_set_mismatch")
    if (
        download_result.sha256_path is not None
        and download_result.sha256_path.parent != set_dir
    ):
        raise ValueError("download_result_verified_set_mismatch")


def _load_verified_download_set_marker(marker_path: Path) -> dict[str, object]:
    """讀取 verified set marker 並確認 schema。"""

    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("download_result_verified_set_missing") from exc
    if not isinstance(payload, dict):
        raise ValueError("download_result_verified_set_invalid")
    if payload.get("schema_version") != VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION:
        raise ValueError("download_result_verified_set_invalid")
    return payload


def _validate_verified_marker_asset(
    payload: Mapping[str, object],
    download_result: UpdateDownloadResult,
) -> None:
    """確認 marker 的 zip 名稱與 hash 仍吻合。"""

    assert download_result.file_path is not None
    expected_sha256 = download_result.expected_sha256.casefold()
    _require_marker_value(payload, "asset_name", download_result.file_path.name)
    _require_marker_value(payload, "asset_sha256", expected_sha256)
    _require_file_sha256(download_result.file_path, expected_sha256)


def _validate_verified_marker_manifest(
    payload: Mapping[str, object],
    download_result: UpdateDownloadResult,
) -> None:
    """確認 marker 的 manifest 名稱與 hash 仍吻合。"""

    assert download_result.manifest_path is not None
    manifest_sha256 = download_result.manifest_sha256.casefold()
    if payload.get("manifest_name") != download_result.manifest_path.name:
        raise ValueError("download_result_verified_set_mismatch")
    _require_marker_value(payload, "manifest_sha256", manifest_sha256)
    _require_file_sha256(download_result.manifest_path, manifest_sha256)


def _validate_verified_marker_signature(
    payload: Mapping[str, object],
    download_result: UpdateDownloadResult,
) -> None:
    """確認 marker 的 detached signature 名稱與 hash 仍吻合。"""

    assert download_result.manifest_signature_path is not None
    _require_marker_value(
        payload,
        "manifest_signature_name",
        download_result.manifest_signature_path.name,
    )
    _require_marker_value(
        payload,
        "manifest_signature_sha256",
        calculate_sha256(download_result.manifest_signature_path).casefold(),
    )


def _validate_verified_marker_sidecar(
    payload: Mapping[str, object],
    download_result: UpdateDownloadResult,
) -> None:
    """確認 marker 的 SHA256 sidecar 名稱與 hash 仍吻合。"""

    if download_result.sha256_path is not None:
        _require_marker_value(payload, "sha256_name", download_result.sha256_path.name)
        _require_marker_value(
            payload,
            "sha256_sha256",
            calculate_sha256(download_result.sha256_path).casefold(),
        )
        return
    if payload.get("sha256_name"):
        raise ValueError("download_result_verified_set_mismatch")


def _require_marker_value(
    payload: Mapping[str, object],
    key: str,
    expected: str,
) -> None:
    """確認 marker 欄位值等於預期字串。"""

    if payload.get(key) != expected:
        raise ValueError("download_result_verified_set_mismatch")


def _require_file_sha256(path: Path, expected_sha256: str) -> None:
    """確認檔案 hash 等於 marker / download result 內的預期值。"""

    if calculate_sha256(path) != expected_sha256:
        raise ValueError("download_result_verified_set_mismatch")


def _write_verified_download_set_marker(
    marker_path: Path,
    *,
    file_path: Path,
    sha256_path: Path | None,
    manifest_path: Path,
    manifest_signature_path: Path,
    verification: StagedAssetVerification,
    updates_root: Path,
) -> None:
    """以最後一步 atomic marker 發布整組 verified download。"""

    payload = {
        "schema_version": VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION,
        "asset_name": file_path.name,
        "asset_sha256": verification.actual_sha256.casefold(),
        "asset_size": file_path.stat().st_size,
        "sha256_name": sha256_path.name if sha256_path is not None else "",
        "sha256_sha256": (
            calculate_sha256(sha256_path).casefold()
            if sha256_path is not None
            else ""
        ),
        "manifest_name": manifest_path.name,
        "manifest_sha256": verification.manifest.manifest_sha256.casefold(),
        "manifest_key_id": verification.manifest.key_id,
        "manifest_signature_name": manifest_signature_path.name,
        "manifest_signature_sha256": calculate_sha256(
            manifest_signature_path
        ).casefold(),
    }
    tmp_path = marker_path.with_name(f".{marker_path.name}.{uuid.uuid4().hex}.tmp")
    _prepare_download_tmp(marker_path, tmp_path, updates_root=updates_root)
    try:
        with tmp_path.open("x", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
            file.write("\n")
        tmp_path.replace(marker_path)
    except FileExistsError as exc:
        raise ValueError("download_path_unsafe") from exc
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _verified_marker_path_for(file_path: Path) -> Path:
    """依更新 zip 路徑回推 verified set marker 路徑。"""

    return file_path.parent / VERIFIED_DOWNLOAD_SET_MARKER_NAME


def _prepare_download_tmp(
    destination: Path,
    tmp_destination: Path,
    *,
    updates_root: Path,
) -> None:
    """準備下載暫存檔；不得 follow 既有 symlink/junction。"""

    _ensure_download_destination_available(destination, updates_root=updates_root)
    _ensure_download_destination_available(tmp_destination, updates_root=updates_root)
    if tmp_destination.exists():
        try:
            tmp_destination.unlink()
        except OSError as exc:
            raise ValueError("download_path_unsafe") from exc


def _ensure_download_destination_available(
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


def _cleanup_download_dir(path: Path, *, updates_root: Path) -> None:
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


async def _aiter_response_bytes(response: httpx.Response) -> AsyncIterator[bytes]:
    """回傳非空下載 chunks。"""

    async for chunk in response.aiter_bytes(chunk_size=DOWNLOAD_CHUNK_SIZE):
        if chunk:
            yield chunk


def _parse_content_length(response: httpx.Response) -> int | None:
    """讀取 Content-Length；格式不合法時交由串流累計上限保護。"""

    value = response.headers.get("content-length")
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return max(0, parsed)


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

    return UpdateDownloadResult(
        status="failed",
        downloaded=False,
        verified=False,
        file_path=file_path,
        sha256_path=sha256_path,
        expected_sha256="",
        actual_sha256="",
        failure_reason=reason,
        manifest_path=manifest_path,
        manifest_signature_path=manifest_signature_path,
        verified_set_marker_path=verified_set_marker_path,
    )


def _operation_runtime_dir_for_updates_dir(updates_dir: Path) -> Path:
    """依 RuntimePaths 慣例從 updates dir 推導 updater operation lock 目錄。"""

    return Path(updates_dir).expanduser().resolve().parent / "runtime"


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
