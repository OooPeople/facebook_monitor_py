"""GitHub Release 更新檢查。

職責：查詢受信任 GitHub repository 的 release metadata，判斷目前
Windows portable build 是否已有新版可下載。此模組只查 metadata，
不下載 zip，也不套用更新。
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Any
from urllib.parse import quote

import httpx

from facebook_monitor.updates.artifacts import UpdateArtifactPolicy
from facebook_monitor.updates.artifacts import current_update_artifact_policy
from facebook_monitor.updates.artifacts import WINDOWS_PORTABLE_POLICY


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


@dataclass(frozen=True)
class ParsedVersion:
    """可比較的簡化版本表示，支援目前 release 使用的 stable / rc 版本。"""

    release: tuple[int, ...]
    prerelease_label: str
    prerelease_number: int

    def sort_key(self) -> tuple[tuple[int, ...], int, int]:
        """回傳版本排序 key；同 release 下 stable 大於 rc。"""

        prerelease_rank = 1 if not self.prerelease_label else 0
        return (self.release, prerelease_rank, self.prerelease_number)


def build_idle_update_check(
    *,
    current_version: str,
    channel: str = "stable",
    repository: str | None = None,
) -> UpdateCheckResult:
    """建立尚未查詢 GitHub 前的設定頁狀態。"""

    normalized_channel = normalize_channel(channel)
    resolved_repository = repository or configured_update_repository()
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
    timeout_seconds: float = 10.0,
    artifact_policy: UpdateArtifactPolicy | None = None,
) -> UpdateCheckResult:
    """查詢 GitHub Releases，回傳目前版本與遠端 release 的比較結果。"""

    normalized_channel = normalize_channel(channel)
    resolved_repository = repository or configured_update_repository()
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
) -> UpdateCheckResult:
    """依單筆 GitHub release payload 判斷是否有可用 release asset。"""

    resolved_policy = artifact_policy or current_update_artifact_policy()
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

    assets = parse_release_assets(release.get("assets", []))
    portable_asset = find_portable_asset(
        assets,
        latest_version=latest_version,
        policy=resolved_policy,
    )
    sha256_asset = find_sha256_asset(assets, portable_asset=portable_asset)
    if parsed_latest.sort_key() <= parsed_current.sort_key():
        return UpdateCheckResult(
            checked=True,
            status="current",
            channel=channel,
            repository=repository,
            current_version=current_version,
            latest_version=current_version,
            update_available=False,
            summary="目前已是最新版本",
            detail="" if latest_version == current_version else (
                f"GitHub 最新 release 是 {latest_version}，不高於目前版本。"
            ),
            release_url=release_url,
            asset_name="",
            asset_download_url="",
            sha256_asset_name="",
            sha256_asset_download_url="",
            failure_reason="",
        )
    if portable_asset is None:
        if has_version_mismatched_portable_asset(
            assets,
            latest_version=latest_version,
            policy=resolved_policy,
        ):
            return UpdateCheckResult(
                checked=True,
                status="asset_version_mismatch",
                channel=channel,
                repository=repository,
                current_version=current_version,
                latest_version=latest_version,
                update_available=False,
                summary=f"找到新版，但 {resolved_policy.display_label} zip 版本不符",
                detail="Release asset 檔名版本必須與 GitHub tag version 完全一致。",
                release_url=release_url,
                asset_name="",
                asset_download_url="",
                sha256_asset_name="",
                sha256_asset_download_url="",
                failure_reason="asset_version_mismatch",
            )
        return UpdateCheckResult(
            checked=True,
            status="asset_missing",
            channel=channel,
            repository=repository,
            current_version=current_version,
            latest_version=latest_version,
            update_available=False,
            summary=f"找到新版，但沒有 {resolved_policy.display_label} zip",
            detail=f"Release asset 未包含預期的 {resolved_policy.display_label} zip。",
            release_url=release_url,
            asset_name="",
            asset_download_url="",
            sha256_asset_name="",
            sha256_asset_download_url="",
            failure_reason="asset_missing",
        )
    return UpdateCheckResult(
        checked=True,
        status="available",
        channel=channel,
        repository=repository,
        current_version=current_version,
        latest_version=latest_version,
        update_available=True,
        summary=f"有新版 {latest_version}",
        detail="目前只提供檢查，不會下載或套用更新。",
        release_url=release_url,
        asset_name=portable_asset.name,
        asset_download_url=portable_asset.download_url,
        sha256_asset_name=sha256_asset.name if sha256_asset else "",
        sha256_asset_download_url=sha256_asset.download_url if sha256_asset else "",
        failure_reason="" if sha256_asset else "sha256_asset_missing",
    )


def configured_update_repository() -> str:
    """取得受信任 GitHub repository，允許開發/測試用 env 覆寫。"""

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


def normalize_version_text(value: str) -> str:
    """移除 Git tag 常見前綴，保留 app version 本體。"""

    normalized = value.strip()
    if normalized.startswith(("v", "V")):
        normalized = normalized[1:]
    return normalized


def parse_version(value: str) -> ParsedVersion:
    """解析專案目前使用的簡化語意版本。"""

    normalized = normalize_version_text(value).replace("-rc", "rc")
    match = re.fullmatch(r"(\d+(?:\.\d+)*)(?:rc(\d+))?", normalized)
    if match is None:
        raise ValueError("invalid_version")
    release = tuple(int(part) for part in match.group(1).split("."))
    prerelease_number = int(match.group(2) or 0)
    prerelease_label = "rc" if match.group(2) else ""
    return ParsedVersion(
        release=release,
        prerelease_label=prerelease_label,
        prerelease_number=prerelease_number,
    )


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
        if asset.name.startswith("facebook-monitor-") and asset.name.endswith(policy.asset_suffix):
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
    expected_name = portable_asset.name + ".sha256"
    for asset in assets:
        if asset.name == expected_name:
            return asset
    return None


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
