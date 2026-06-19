"""Updater release asset download and staged verification helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Mapping
from pathlib import Path

import httpx

from facebook_monitor.updates.artifacts import release_artifact_policy_for_asset_name
from facebook_monitor.updates.checksum import calculate_sha256
from facebook_monitor.updates.checksum import read_sha256_sidecar
from facebook_monitor.updates.download_models import DOWNLOAD_CHUNK_SIZE
from facebook_monitor.updates.download_models import MAX_MANIFEST_DOWNLOAD_BYTES
from facebook_monitor.updates.download_models import (
    MAX_MANIFEST_SIGNATURE_DOWNLOAD_BYTES,
)
from facebook_monitor.updates.download_models import StagedAssetVerification
from facebook_monitor.updates.download_models import UpdateDownloadPlan
from facebook_monitor.updates.download_url_policy import validate_final_release_download_url
from facebook_monitor.updates.manifest import VerifiedReleaseManifest
from facebook_monitor.updates.manifest import verify_release_manifest
from facebook_monitor.updates.release_check import UpdateCheckResult
from facebook_monitor.updates.validation import is_reparse_or_symlink
from facebook_monitor.updates.download_paths import ensure_safe_download_path
from facebook_monitor.updates.download_paths import prepare_download_tmp


async def download_staged_release_files(
    *,
    client: httpx.AsyncClient,
    update_check: UpdateCheckResult,
    plan: UpdateDownloadPlan,
    max_sha256_bytes: int,
    max_asset_bytes: int,
    trusted_public_keys: Mapping[str, str] | None,
) -> VerifiedReleaseManifest:
    """依序下載並驗證 manifest、sidecar 與 update asset 到 staging path。"""

    await download_file(
        client=client,
        url=update_check.manifest_asset_download_url,
        destination=plan.staged_manifest_path,
        updates_root=plan.updates_root,
        max_bytes=MAX_MANIFEST_DOWNLOAD_BYTES,
        expected_asset_name=plan.manifest_name,
    )
    await download_file(
        client=client,
        url=update_check.manifest_signature_asset_download_url,
        destination=plan.staged_manifest_signature_path,
        updates_root=plan.updates_root,
        max_bytes=MAX_MANIFEST_SIGNATURE_DOWNLOAD_BYTES,
        expected_asset_name=plan.manifest_signature_name,
    )
    manifest = verify_staged_manifest(
        update_check=update_check,
        manifest_path=plan.staged_manifest_path,
        signature_path=plan.staged_manifest_signature_path,
        asset_name=plan.asset_name,
        trusted_public_keys=trusted_public_keys,
    )
    if plan.sha256_path is not None and plan.staged_sha256_path is not None:
        await download_file(
            client=client,
            url=update_check.sha256_asset_download_url,
            destination=plan.staged_sha256_path,
            updates_root=plan.updates_root,
            max_bytes=max_sha256_bytes,
            expected_asset_name=plan.sha256_name,
        )
        sidecar_sha256 = read_sha256_sidecar(
            plan.staged_sha256_path,
            expected_filename=plan.asset_name,
        )
        if sidecar_sha256 != manifest.asset.sha256:
            raise ValueError("sha256_sidecar_manifest_mismatch")
    await download_file(
        client=client,
        url=update_check.asset_download_url,
        destination=plan.staged_file_path,
        updates_root=plan.updates_root,
        max_bytes=max_asset_bytes,
        expected_asset_name=plan.asset_name,
    )
    return manifest


def verify_staged_asset(
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


async def download_file(
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
        prepare_download_tmp(
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
            content_length = parse_content_length(response)
            if content_length is not None and content_length > max_bytes:
                raise ValueError("download_too_large")
            downloaded_bytes = 0
            with tmp_destination.open("xb") as file:
                async for chunk in aiter_response_bytes(response):
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


def verify_staged_manifest(
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


async def aiter_response_bytes(response: httpx.Response) -> AsyncIterator[bytes]:
    """回傳非空下載 chunks。"""

    async for chunk in response.aiter_bytes(chunk_size=DOWNLOAD_CHUNK_SIZE):
        if chunk:
            yield chunk


def parse_content_length(response: httpx.Response) -> int | None:
    """讀取 Content-Length；格式不合法時交由串流累計上限保護。"""

    value = response.headers.get("content-length")
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return max(0, parsed)


__all__ = [
    "aiter_response_bytes",
    "download_file",
    "download_staged_release_files",
    "parse_content_length",
    "verify_staged_asset",
    "verify_staged_manifest",
]
