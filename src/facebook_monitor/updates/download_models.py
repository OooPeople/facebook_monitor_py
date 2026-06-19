"""Updater download result models and constants."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from facebook_monitor.updates.checksum import HASH_CHUNK_SIZE
from facebook_monitor.updates.manifest import VerifiedReleaseManifest


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


def make_failure_download_result(
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


__all__ = [
    "DOWNLOAD_CHUNK_SIZE",
    "MAX_MANIFEST_DOWNLOAD_BYTES",
    "MAX_MANIFEST_SIGNATURE_DOWNLOAD_BYTES",
    "MAX_SHA256_DOWNLOAD_BYTES",
    "MAX_UPDATE_DOWNLOAD_BYTES",
    "StagedAssetVerification",
    "UpdateDownloadPlan",
    "UpdateDownloadResult",
    "VERIFIED_DOWNLOAD_SET_MARKER_NAME",
    "VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION",
    "make_failure_download_result",
]
