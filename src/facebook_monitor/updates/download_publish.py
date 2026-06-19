"""Updater verified download publish and marker validation."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import uuid

from facebook_monitor.updates.checksum import calculate_sha256
from facebook_monitor.updates.download_models import StagedAssetVerification
from facebook_monitor.updates.download_models import UpdateDownloadPlan
from facebook_monitor.updates.download_models import UpdateDownloadResult
from facebook_monitor.updates.download_models import VERIFIED_DOWNLOAD_SET_MARKER_NAME
from facebook_monitor.updates.download_models import (
    VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION,
)
from facebook_monitor.updates.download_paths import cleanup_download_dir
from facebook_monitor.updates.download_paths import ensure_download_set_destination_available
from facebook_monitor.updates.download_paths import prepare_download_tmp


def publish_verified_download_plan(
    plan: UpdateDownloadPlan,
    verification: StagedAssetVerification,
) -> None:
    """將驗證通過的 staging set 發布為正式 artifact set。"""

    publish_verified_download(
        staged_file_path=plan.staged_file_path,
        staged_sha256_path=plan.staged_sha256_path,
        staged_manifest_path=plan.staged_manifest_path,
        staged_manifest_signature_path=plan.staged_manifest_signature_path,
        staged_set_dir=plan.staged_set_dir,
        verified_set_dir=plan.verified_set_dir,
        updates_root=plan.updates_root,
        verification=verification,
    )


def publish_verified_download(
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

    ensure_download_set_destination_available(
        verified_set_dir,
        updates_root=updates_root,
    )
    staged_marker_path = staged_set_dir / VERIFIED_DOWNLOAD_SET_MARKER_NAME
    try:
        write_verified_download_set_marker(
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
        cleanup_download_dir(staged_set_dir, updates_root=updates_root)
        cleanup_download_dir(verified_set_dir, updates_root=updates_root)
        raise


def validate_verified_download_set(download_result: UpdateDownloadResult) -> None:
    """確認 verified download set marker 與目前檔案仍一致。"""

    if not download_result.verified or download_result.file_path is None:
        raise ValueError("download_result_not_verified")
    marker_path = download_result.verified_set_marker_path or verified_marker_path_for(
        download_result.file_path
    )
    validate_verified_download_set_paths(download_result, marker_path)
    payload = load_verified_download_set_marker(marker_path)
    validate_verified_marker_asset(payload, download_result)
    if (
        download_result.manifest_path is None
        or download_result.manifest_signature_path is None
    ):
        raise ValueError("download_result_manifest_missing")
    validate_verified_marker_manifest(payload, download_result)
    validate_verified_marker_signature(payload, download_result)
    validate_verified_marker_sidecar(payload, download_result)


def validate_verified_download_set_paths(
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


def load_verified_download_set_marker(marker_path: Path) -> dict[str, object]:
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


def validate_verified_marker_asset(
    payload: Mapping[str, object],
    download_result: UpdateDownloadResult,
) -> None:
    """確認 marker 的 zip 名稱與 hash 仍吻合。"""

    assert download_result.file_path is not None
    expected_sha256 = download_result.expected_sha256.casefold()
    require_marker_value(payload, "asset_name", download_result.file_path.name)
    require_marker_value(payload, "asset_sha256", expected_sha256)
    require_file_sha256(download_result.file_path, expected_sha256)


def validate_verified_marker_manifest(
    payload: Mapping[str, object],
    download_result: UpdateDownloadResult,
) -> None:
    """確認 marker 的 manifest 名稱與 hash 仍吻合。"""

    assert download_result.manifest_path is not None
    manifest_sha256 = download_result.manifest_sha256.casefold()
    if payload.get("manifest_name") != download_result.manifest_path.name:
        raise ValueError("download_result_verified_set_mismatch")
    require_marker_value(payload, "manifest_sha256", manifest_sha256)
    require_file_sha256(download_result.manifest_path, manifest_sha256)


def validate_verified_marker_signature(
    payload: Mapping[str, object],
    download_result: UpdateDownloadResult,
) -> None:
    """確認 marker 的 detached signature 名稱與 hash 仍吻合。"""

    assert download_result.manifest_signature_path is not None
    require_marker_value(
        payload,
        "manifest_signature_name",
        download_result.manifest_signature_path.name,
    )
    require_marker_value(
        payload,
        "manifest_signature_sha256",
        calculate_sha256(download_result.manifest_signature_path).casefold(),
    )


def validate_verified_marker_sidecar(
    payload: Mapping[str, object],
    download_result: UpdateDownloadResult,
) -> None:
    """確認 marker 的 SHA256 sidecar 名稱與 hash 仍吻合。"""

    if download_result.sha256_path is not None:
        require_marker_value(payload, "sha256_name", download_result.sha256_path.name)
        require_marker_value(
            payload,
            "sha256_sha256",
            calculate_sha256(download_result.sha256_path).casefold(),
        )
        return
    if payload.get("sha256_name"):
        raise ValueError("download_result_verified_set_mismatch")


def require_marker_value(
    payload: Mapping[str, object],
    key: str,
    expected: str,
) -> None:
    """確認 marker 欄位值等於預期字串。"""

    if payload.get(key) != expected:
        raise ValueError("download_result_verified_set_mismatch")


def require_file_sha256(path: Path, expected_sha256: str) -> None:
    """確認檔案 hash 等於 marker / download result 內的預期值。"""

    if calculate_sha256(path) != expected_sha256:
        raise ValueError("download_result_verified_set_mismatch")


def write_verified_download_set_marker(
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
    prepare_download_tmp(marker_path, tmp_path, updates_root=updates_root)
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


def verified_marker_path_for(file_path: Path) -> Path:
    """依更新 zip 路徑回推 verified set marker 路徑。"""

    return file_path.parent / VERIFIED_DOWNLOAD_SET_MARKER_NAME


__all__ = [
    "load_verified_download_set_marker",
    "publish_verified_download",
    "publish_verified_download_plan",
    "require_file_sha256",
    "require_marker_value",
    "validate_verified_download_set",
    "validate_verified_download_set_paths",
    "validate_verified_marker_asset",
    "validate_verified_marker_manifest",
    "validate_verified_marker_sidecar",
    "validate_verified_marker_signature",
    "verified_marker_path_for",
    "write_verified_download_set_marker",
]
