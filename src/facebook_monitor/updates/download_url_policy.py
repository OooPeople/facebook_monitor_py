"""Release asset download URL policy。

職責：在下載 updater artifact 前再次確認 URL 來源仍是 GitHub release
asset 邊界，避免下載器過度信任 release metadata 或 redirect。
"""

from __future__ import annotations

from urllib.parse import unquote
from urllib.parse import urlsplit


GITHUB_RELEASE_HOSTS = frozenset({"github.com", "www.github.com"})
GITHUB_RELEASE_REDIRECT_HOSTS = frozenset(
    {
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)


def validate_initial_release_download_url(
    url: str,
    *,
    expected_asset_name: str,
    repository: str,
) -> str:
    """驗證 GitHub API 提供的初始 browser_download_url。"""

    parsed = _split_https_url(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if host not in GITHUB_RELEASE_HOSTS:
        raise ValueError("release_download_url_host_not_allowed")
    if parsed.query or parsed.fragment:
        raise ValueError("release_download_url_extra_parts_not_allowed")
    _validate_github_release_path(
        parsed.path,
        expected_asset_name=expected_asset_name,
        repository=repository,
    )
    return parsed.geturl()


def validate_final_release_download_url(
    url: str,
    *,
    expected_asset_name: str,
) -> str:
    """驗證 redirect 後最終下載 URL 仍在 GitHub asset host allowlist。"""

    parsed = _split_https_url(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if host in GITHUB_RELEASE_REDIRECT_HOSTS:
        return parsed.geturl()
    if host in GITHUB_RELEASE_HOSTS:
        asset_name = _url_path_basename(parsed.path)
        if asset_name != expected_asset_name:
            raise ValueError("release_download_url_asset_mismatch")
        return parsed.geturl()
    raise ValueError("release_download_url_host_not_allowed")


def _split_https_url(url: str):
    """解析 URL 並套用共用的 HTTPS / userinfo policy。"""

    try:
        parsed = urlsplit(str(url or "").strip())
    except ValueError as exc:
        raise ValueError("release_download_url_invalid") from exc
    if parsed.scheme.casefold() != "https":
        raise ValueError("release_download_url_must_be_https")
    if parsed.username or parsed.password:
        raise ValueError("release_download_url_userinfo_not_allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("release_download_url_port_not_allowed") from exc
    if port not in (None, 443):
        raise ValueError("release_download_url_port_not_allowed")
    return parsed


def _validate_github_release_path(
    path: str,
    *,
    expected_asset_name: str,
    repository: str,
) -> None:
    """確認 path 指向指定 repository 的 releases/download asset。"""

    owner_repo = tuple(part.strip() for part in repository.split("/", 1))
    if len(owner_repo) != 2 or not all(owner_repo):
        raise ValueError("release_download_url_repository_invalid")
    owner, repo = owner_repo
    normalized_path = unquote(path)
    expected_prefix = f"/{owner}/{repo}/releases/download/"
    if not normalized_path.startswith(expected_prefix):
        raise ValueError("release_download_url_repository_mismatch")
    if _url_path_basename(normalized_path) != expected_asset_name:
        raise ValueError("release_download_url_asset_mismatch")


def _url_path_basename(path: str) -> str:
    """取 URL path 的最後一段並 URL-decode。"""

    return unquote(str(path or "").rstrip("/").rsplit("/", 1)[-1])
