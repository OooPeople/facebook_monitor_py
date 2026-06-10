"""GitHub Release 更新檢查。

職責：查詢受信任 GitHub repository 的 release metadata，依目前平台
判斷是否已有新版 release artifact 可下載。此模組只查 metadata，
不下載 zip，也不套用更新。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from facebook_monitor.updates.artifacts import UpdateArtifactPolicy
from facebook_monitor.updates.artifacts import UpdateRuntimePlatform
from facebook_monitor.updates.artifacts import current_update_runtime_platform
from facebook_monitor.updates.artifacts import is_release_asset_name_for_policy
from facebook_monitor.updates.artifacts import release_sha256_asset_name
from facebook_monitor.updates.artifacts import WINDOWS_PORTABLE_POLICY
from facebook_monitor.updates.manifest import release_manifest_asset_name
from facebook_monitor.updates.manifest import release_manifest_signature_asset_name
from facebook_monitor.versioning import normalize_version_text
from facebook_monitor.versioning import parse_version


DEFAULT_UPDATE_REPOSITORY = "OooPeople/facebook_monitor_py"
UPDATE_REPOSITORY_ENV = "FACEBOOK_MONITOR_UPDATE_REPOSITORY"
GITHUB_API_BASE_URL = "https://api.github.com"
SUPPORTED_CHANNELS = frozenset({"stable", "preview"})


@dataclass(frozen=True)
class ReleaseAsset:
    """GitHub release asset metadata。"""

    name: str
    download_url: str


@dataclass(frozen=True)
class UpdateCheckResult:
    """設定頁顯示用更新檢查結果。"""

    checked: bool
    status: str
    channel: str
    repository: str
    current_version: str
    latest_version: str
    update_available: bool
    summary: str
    detail: str
    release_url: str
    asset_name: str
    asset_download_url: str
    sha256_asset_name: str
    sha256_asset_download_url: str
    failure_reason: str
    manifest_asset_name: str = ""
    manifest_asset_download_url: str = ""
    manifest_signature_asset_name: str = ""
    manifest_signature_asset_download_url: str = ""


@dataclass(frozen=True)
class ReleaseAssetBundle:
    """保存單一 release 中與目前平台更新相關的 assets。"""

    portable_asset: ReleaseAsset | None
    sha256_asset: ReleaseAsset | None
    manifest_asset: ReleaseAsset | None
    manifest_signature_asset: ReleaseAsset | None


def build_idle_update_check(
    *,
    current_version: str,
    channel: str = "stable",
    repository: str | None = None,
    allow_env_repository_override: bool = True,
) -> UpdateCheckResult:
    """建立尚未查詢 GitHub 前的設定頁狀態。"""

    normalized_channel = normalize_channel(channel)
    resolved_repository = repository or configured_update_repository(
        allow_env_override=allow_env_repository_override
    )
    return UpdateCheckResult(
        checked=False,
        status="not_checked",
        channel=normalized_channel,
        repository=resolved_repository,
        current_version=current_version,
        latest_version="",
        update_available=False,
        summary="尚未檢查更新",
        detail="",
        release_url="",
        asset_name="",
        asset_download_url="",
        sha256_asset_name="",
        sha256_asset_download_url="",
        failure_reason="",
    )


async def check_github_release_updates(
    *,
    current_version: str,
    channel: str = "stable",
    repository: str | None = None,
    allow_env_repository_override: bool = True,
    timeout_seconds: float = 10.0,
    artifact_policy: UpdateArtifactPolicy | None = None,
) -> UpdateCheckResult:
    """查詢 GitHub Releases，回傳目前版本與遠端 release 的比較結果。"""

    normalized_channel = normalize_channel(channel)
    resolved_repository = repository or configured_update_repository(
        allow_env_override=allow_env_repository_override
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "facebook-monitor-update-check",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, headers=headers) as client:
            release = await _fetch_release(
                client=client,
                repository=resolved_repository,
                channel=normalized_channel,
            )
    except httpx.HTTPStatusError as exc:
        return _failure_result(
            current_version=current_version,
            channel=normalized_channel,
            repository=resolved_repository,
            reason=_http_status_reason(exc.response),
        )
    except httpx.HTTPError as exc:
        return _failure_result(
            current_version=current_version,
            channel=normalized_channel,
            repository=resolved_repository,
            reason=f"network_error:{exc.__class__.__name__}",
        )
    except ValueError as exc:
        return _failure_result(
            current_version=current_version,
            channel=normalized_channel,
            repository=resolved_repository,
            reason=str(exc),
        )
    return evaluate_release(
        current_version=current_version,
        channel=normalized_channel,
        repository=resolved_repository,
        release=release,
        artifact_policy=artifact_policy,
    )


def evaluate_release(
    *,
    current_version: str,
    channel: str,
    repository: str,
    release: dict[str, Any],
    artifact_policy: UpdateArtifactPolicy | None = None,
    runtime_platform: UpdateRuntimePlatform | None = None,
) -> UpdateCheckResult:
    """依單筆 GitHub release payload 判斷是否有可用 release asset。"""

    tag_name = str(release.get("tag_name", "")).strip()
    latest_version = normalize_version_text(tag_name)
    release_url = str(release.get("html_url", "")).strip()
    if not latest_version:
        return _failure_result(
            current_version=current_version,
            channel=channel,
            repository=repository,
            reason="missing_tag_name",
        )
    try:
        parsed_current = parse_version(current_version)
        parsed_latest = parse_version(latest_version)
    except ValueError as exc:
        return _failure_result(
            current_version=current_version,
            channel=channel,
            repository=repository,
            reason=str(exc),
            latest_version=latest_version,
            release_url=release_url,
        )

    if parsed_latest.sort_key() <= parsed_current.sort_key():
        return _checked_release_result(
            status="current",
            channel=channel,
            repository=repository,
            current_version=current_version,
            latest_version=current_version,
            summary="目前已是最新版本",
            detail="" if latest_version == current_version else (
                f"GitHub 最新 release 是 {latest_version}，不高於目前版本。"
            ),
            release_url=release_url,
            failure_reason="",
        )
    resolved_platform = runtime_platform or current_update_runtime_platform()
    resolved_policy = artifact_policy or resolved_platform.artifact_policy
    if resolved_policy is None:
        return _checked_release_result(
            status="platform_unsupported",
            channel=channel,
            repository=repository,
            current_version=current_version,
            latest_version=latest_version,
            summary=f"找到新版 {latest_version}，但目前平台沒有對應更新檔",
            detail=resolved_platform.unsupported_reason
            or "目前平台沒有對應的更新檔，只支援檢查版本資訊。",
            release_url=release_url,
            failure_reason="platform_unsupported",
        )

    assets = parse_release_assets(release.get("assets", []))
    asset_bundle = _release_asset_bundle(
        assets,
        latest_version=latest_version,
        policy=resolved_policy,
    )
    if asset_bundle.portable_asset is None:
        if has_version_mismatched_portable_asset(
            assets,
            latest_version=latest_version,
            policy=resolved_policy,
        ):
            return _checked_release_result(
                status="asset_version_mismatch",
                channel=channel,
                repository=repository,
                current_version=current_version,
                latest_version=latest_version,
                summary=f"找到新版，但 {resolved_policy.display_label} zip 版本不符",
                detail="Release asset 檔名版本必須與 GitHub tag version 完全一致。",
                release_url=release_url,
                failure_reason="asset_version_mismatch",
            )
        return _checked_release_result(
            status="asset_missing",
            channel=channel,
            repository=repository,
            current_version=current_version,
            latest_version=latest_version,
            summary=f"找到新版，但沒有 {resolved_policy.display_label} zip",
            detail=f"Release asset 未包含預期的 {resolved_policy.display_label} zip。",
            release_url=release_url,
            failure_reason="asset_missing",
        )
    if asset_bundle.sha256_asset is None:
        return _checked_release_result(
            status="sha256_asset_missing",
            channel=channel,
            repository=repository,
            current_version=current_version,
            latest_version=latest_version,
            summary=f"找到新版 {latest_version}，但缺少 SHA256 sidecar",
            detail="Release 必須包含與更新 zip 同名的 .sha256 檔，才能自動下載或套用更新。",
            release_url=release_url,
            failure_reason="sha256_asset_missing",
            assets=asset_bundle,
        )
    manifest_failure_reason = _manifest_failure_reason(
        manifest_asset=asset_bundle.manifest_asset,
        manifest_signature_asset=asset_bundle.manifest_signature_asset,
    )
    if manifest_failure_reason:
        return _checked_release_result(
            status=manifest_failure_reason,
            channel=channel,
            repository=repository,
            current_version=current_version,
            latest_version=latest_version,
            summary=f"找到新版 {latest_version}，但缺少 signed manifest",
            detail="Release 必須同時包含 signed manifest 與 detached signature 才能自動下載或套用更新。",
            release_url=release_url,
            failure_reason=manifest_failure_reason,
            assets=asset_bundle,
        )
    return _checked_release_result(
        status="available",
        channel=channel,
        repository=repository,
        current_version=current_version,
        latest_version=latest_version,
        update_available=True,
        summary=f"有新版 {latest_version}",
        detail="下載與套用能力會依目前 runtime 支援與 signed manifest 狀態決定。",
        release_url=release_url,
        failure_reason="",
        assets=asset_bundle,
    )


def _release_asset_bundle(
    assets: tuple[ReleaseAsset, ...],
    *,
    latest_version: str,
    policy: UpdateArtifactPolicy,
) -> ReleaseAssetBundle:
    """集中尋找目前平台需要的 release assets。"""

    portable_asset = find_portable_asset(
        assets,
        latest_version=latest_version,
        policy=policy,
    )
    return ReleaseAssetBundle(
        portable_asset=portable_asset,
        sha256_asset=find_sha256_asset(assets, portable_asset=portable_asset),
        manifest_asset=find_manifest_asset(assets, latest_version=latest_version),
        manifest_signature_asset=find_manifest_signature_asset(
            assets,
            latest_version=latest_version,
        ),
    )


def _checked_release_result(
    *,
    status: str,
    channel: str,
    repository: str,
    current_version: str,
    latest_version: str,
    summary: str,
    detail: str,
    release_url: str,
    failure_reason: str,
    update_available: bool = False,
    assets: ReleaseAssetBundle | None = None,
) -> UpdateCheckResult:
    """建立已完成 GitHub metadata 檢查後的結果。"""

    portable_asset = assets.portable_asset if assets else None
    sha256_asset = assets.sha256_asset if assets else None
    manifest_asset = assets.manifest_asset if assets else None
    manifest_signature_asset = assets.manifest_signature_asset if assets else None
    return UpdateCheckResult(
        checked=True,
        status=status,
        channel=channel,
        repository=repository,
        current_version=current_version,
        latest_version=latest_version,
        update_available=update_available,
        summary=summary,
        detail=detail,
        release_url=release_url,
        asset_name=portable_asset.name if portable_asset else "",
        asset_download_url=portable_asset.download_url if portable_asset else "",
        sha256_asset_name=sha256_asset.name if sha256_asset else "",
        sha256_asset_download_url=sha256_asset.download_url if sha256_asset else "",
        failure_reason=failure_reason,
        manifest_asset_name=manifest_asset.name if manifest_asset else "",
        manifest_asset_download_url=manifest_asset.download_url if manifest_asset else "",
        manifest_signature_asset_name=manifest_signature_asset.name
        if manifest_signature_asset
        else "",
        manifest_signature_asset_download_url=manifest_signature_asset.download_url
        if manifest_signature_asset
        else "",
    )


def configured_update_repository(*, allow_env_override: bool = True) -> str:
    """取得受信任 GitHub repository；正式 frozen 路徑可禁用 env 覆寫。"""

    if not allow_env_override:
        return DEFAULT_UPDATE_REPOSITORY
    value = os.environ.get(UPDATE_REPOSITORY_ENV, DEFAULT_UPDATE_REPOSITORY).strip()
    if not value or "/" not in value:
        return DEFAULT_UPDATE_REPOSITORY
    return value


def normalize_channel(channel: str) -> str:
    """整理更新 channel，未知值退回 stable。"""

    normalized = channel.strip().casefold()
    if normalized not in SUPPORTED_CHANNELS:
        return "stable"
    return normalized


def parse_release_assets(value: object) -> tuple[ReleaseAsset, ...]:
    """整理 GitHub release assets，只保留名稱與下載 URL。"""

    if not isinstance(value, list):
        return ()
    assets: list[ReleaseAsset] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        download_url = str(item.get("browser_download_url", "")).strip()
        if name and download_url:
            assets.append(ReleaseAsset(name=name, download_url=download_url))
    return tuple(assets)


def find_windows_portable_asset(
    assets: tuple[ReleaseAsset, ...],
    *,
    latest_version: str,
) -> ReleaseAsset | None:
    """尋找 Windows portable zip；只接受符合 release version 的精確檔名。"""

    return find_portable_asset(
        assets,
        latest_version=latest_version,
        policy=WINDOWS_PORTABLE_POLICY,
    )


def find_portable_asset(
    assets: tuple[ReleaseAsset, ...],
    *,
    latest_version: str,
    policy: UpdateArtifactPolicy,
) -> ReleaseAsset | None:
    """尋找指定平台 portable zip；只接受符合 release version 的精確檔名。"""

    expected_name = policy.asset_name(latest_version)
    for asset in assets:
        if asset.name == expected_name:
            return asset
    return None


def has_version_mismatched_windows_portable_asset(
    assets: tuple[ReleaseAsset, ...],
    *,
    latest_version: str,
) -> bool:
    """判斷 release 是否含有 portable zip，但檔名版本未對齊 tag。"""

    return has_version_mismatched_portable_asset(
        assets,
        latest_version=latest_version,
        policy=WINDOWS_PORTABLE_POLICY,
    )


def has_version_mismatched_portable_asset(
    assets: tuple[ReleaseAsset, ...],
    *,
    latest_version: str,
    policy: UpdateArtifactPolicy,
) -> bool:
    """判斷 release 是否含有指定平台 zip，但檔名版本未對齊 tag。"""

    expected_name = policy.asset_name(latest_version)
    for asset in assets:
        if is_release_asset_name_for_policy(asset.name, policy=policy):
            return asset.name != expected_name
    return False


def find_sha256_asset(
    assets: tuple[ReleaseAsset, ...],
    *,
    portable_asset: ReleaseAsset | None,
) -> ReleaseAsset | None:
    """尋找 portable zip 對應的 SHA256 asset。"""

    if portable_asset is None:
        return None
    expected_name = release_sha256_asset_name(portable_asset.name)
    for asset in assets:
        if asset.name == expected_name:
            return asset
    return None


def find_manifest_asset(
    assets: tuple[ReleaseAsset, ...],
    *,
    latest_version: str,
) -> ReleaseAsset | None:
    """尋找 release manifest asset。"""

    expected_name = release_manifest_asset_name(latest_version)
    for asset in assets:
        if asset.name == expected_name:
            return asset
    return None


def find_manifest_signature_asset(
    assets: tuple[ReleaseAsset, ...],
    *,
    latest_version: str,
) -> ReleaseAsset | None:
    """尋找 release manifest detached signature asset。"""

    expected_name = release_manifest_signature_asset_name(latest_version)
    for asset in assets:
        if asset.name == expected_name:
            return asset
    return None


def _manifest_failure_reason(
    *,
    manifest_asset: ReleaseAsset | None,
    manifest_signature_asset: ReleaseAsset | None,
) -> str:
    """回傳會阻擋自動下載的 manifest metadata 缺漏原因。"""

    if manifest_asset is None:
        return "manifest_file_missing"
    if manifest_signature_asset is None:
        return "manifest_signature_asset_missing"
    return ""


async def _fetch_release(
    *,
    client: httpx.AsyncClient,
    repository: str,
    channel: str,
) -> dict[str, Any]:
    """依 channel 從 GitHub API 取得 release payload。"""

    encoded_repository = "/".join(quote(part, safe="") for part in repository.split("/", 1))
    if channel == "preview":
        response = await client.get(
            f"{GITHUB_API_BASE_URL}/repos/{encoded_repository}/releases",
            params={"per_page": "20"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("invalid_github_payload")
        for item in payload:
            if isinstance(item, dict) and not bool(item.get("draft", False)):
                return item
        raise ValueError("release_not_found")
    response = await client.get(
        f"{GITHUB_API_BASE_URL}/repos/{encoded_repository}/releases/latest"
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("invalid_github_payload")
    return payload


def _failure_result(
    *,
    current_version: str,
    channel: str,
    repository: str,
    reason: str,
    latest_version: str = "",
    release_url: str = "",
) -> UpdateCheckResult:
    """建立查詢失敗結果，保留可顯示 reason。"""

    return UpdateCheckResult(
        checked=True,
        status="unavailable",
        channel=channel,
        repository=repository,
        current_version=current_version,
        latest_version=latest_version,
        update_available=False,
        summary="無法檢查更新",
        detail=reason,
        release_url=release_url,
        asset_name="",
        asset_download_url="",
        sha256_asset_name="",
        sha256_asset_download_url="",
        failure_reason=reason,
    )


def _http_status_reason(response: httpx.Response) -> str:
    """整理 GitHub HTTP status，避免把完整 response body 直接顯示。"""

    if response.status_code == 403:
        return "github_api_forbidden_or_rate_limited"
    if response.status_code == 404:
        return "github_release_not_found"
    return f"github_api_http_{response.status_code}"
