"""Release download URL policy 邊界測試。"""

from __future__ import annotations

import pytest

from facebook_monitor.updates.download_url_policy import (
    validate_final_release_download_url,
)
from facebook_monitor.updates.download_url_policy import (
    validate_initial_release_download_url,
)


REPOSITORY = "OooPeople/facebook_monitor_py"
ASSET_NAME = "facebook-monitor-0.1.0-windows-portable.zip"
INITIAL_URL = (
    "https://github.com/OooPeople/facebook_monitor_py/releases/download/"
    f"v0.1.0/{ASSET_NAME}"
)


def test_initial_release_download_url_accepts_exact_github_asset() -> None:
    """GitHub API 的初始 URL 必須能指回指定 repository 與 asset。"""

    assert (
        validate_initial_release_download_url(
            INITIAL_URL,
            expected_asset_name=ASSET_NAME,
            repository=REPOSITORY,
        )
        == INITIAL_URL
    )


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://github.com/OooPeople/facebook_monitor_py/releases/download/v0/a.zip", "release_download_url_must_be_https"),
        ("https://token@github.com/OooPeople/facebook_monitor_py/releases/download/v0/a.zip", "release_download_url_userinfo_not_allowed"),
        ("https://github.com:444/OooPeople/facebook_monitor_py/releases/download/v0/a.zip", "release_download_url_port_not_allowed"),
        (INITIAL_URL + "?token=abc", "release_download_url_extra_parts_not_allowed"),
        ("https://github.com/OooPeople/other/releases/download/v0.1.0/" + ASSET_NAME, "release_download_url_repository_mismatch"),
        ("https://github.com/OooPeople/facebook_monitor_py/releases/download/v0.1.0/other.zip", "release_download_url_asset_mismatch"),
        ("https://example.com/OooPeople/facebook_monitor_py/releases/download/v0.1.0/" + ASSET_NAME, "release_download_url_host_not_allowed"),
    ],
)
def test_initial_release_download_url_rejects_untrusted_parts(
    url: str,
    reason: str,
) -> None:
    """初始 URL 不可含 userinfo、非標準 port、query 或錯誤來源。"""

    with pytest.raises(ValueError, match=reason):
        validate_initial_release_download_url(
            url,
            expected_asset_name=ASSET_NAME,
            repository=REPOSITORY,
        )


def test_initial_release_download_url_rejects_invalid_repository() -> None:
    """repository 參數本身若不含 owner/repo，不可被當成可信來源。"""

    with pytest.raises(ValueError, match="release_download_url_repository_invalid"):
        validate_initial_release_download_url(
            INITIAL_URL,
            expected_asset_name=ASSET_NAME,
            repository="facebook_monitor_py",
        )


@pytest.mark.parametrize(
    "host",
    ["release-assets.githubusercontent.com", "objects.githubusercontent.com"],
)
def test_final_release_download_url_accepts_github_redirect_asset_host(
    host: str,
) -> None:
    """GitHub release redirect 的物件儲存 host 可帶簽章 query。"""

    url = f"https://{host}/artifact?signature=abc"

    assert (
        validate_final_release_download_url(url, expected_asset_name=ASSET_NAME)
        == url
    )


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://release-assets.githubusercontent.com/app.zip", "release_download_url_must_be_https"),
        ("https://token@release-assets.githubusercontent.com/app.zip", "release_download_url_userinfo_not_allowed"),
        ("https://release-assets.githubusercontent.com:444/app.zip", "release_download_url_port_not_allowed"),
        ("https://github.com/OooPeople/facebook_monitor_py/releases/download/v0.1.0/other.zip", "release_download_url_asset_mismatch"),
        ("https://example.com/app.zip", "release_download_url_host_not_allowed"),
    ],
)
def test_final_release_download_url_rejects_untrusted_redirects(
    url: str,
    reason: str,
) -> None:
    """最終下載 URL 仍必須留在 GitHub release asset allowlist。"""

    with pytest.raises(ValueError, match=reason):
        validate_final_release_download_url(url, expected_asset_name=ASSET_NAME)
