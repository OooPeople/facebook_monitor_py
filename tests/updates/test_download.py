"""更新檔下載與 SHA256 驗證測試。"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import httpx

from facebook_monitor.updates.download import download_and_verify_update
from facebook_monitor.updates.download import read_expected_sha256
from facebook_monitor.updates.download import sanitize_release_asset_name
from facebook_monitor.updates.release_check import UpdateCheckResult


def update_check() -> UpdateCheckResult:
    """建立測試用可更新結果。"""

    return UpdateCheckResult(
        checked=True,
        status="available",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        current_version="0.1.0-rc1",
        latest_version="0.1.0",
        update_available=True,
        summary="有新版 0.1.0",
        detail="",
        release_url="https://github.com/OooPeople/facebook_monitor_py/releases/tag/v0.1.0",
        asset_name="facebook-monitor-0.1.0-windows-portable.zip",
        asset_download_url="https://downloads.example.test/app.zip",
        sha256_asset_name="facebook-monitor-0.1.0-windows-portable.zip.sha256",
        sha256_asset_download_url="https://downloads.example.test/app.zip.sha256",
        failure_reason="",
    )


def mock_transport(*, zip_bytes: bytes, sha256_text: str) -> httpx.MockTransport:
    """建立下載 zip 與 sha256 的 mock transport。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://downloads.example.test/app.zip":
            return httpx.Response(200, content=zip_bytes)
        if str(request.url) == "https://downloads.example.test/app.zip.sha256":
            return httpx.Response(200, text=sha256_text)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def redirect_transport(*, zip_bytes: bytes, sha256_text: str) -> httpx.MockTransport:
    """模擬 GitHub release asset 下載會先回 302 redirect。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://downloads.example.test/app.zip":
            return httpx.Response(302, headers={"location": "https://objects.example.test/app.zip"})
        if str(request.url) == "https://downloads.example.test/app.zip.sha256":
            return httpx.Response(
                302,
                headers={"location": "https://objects.example.test/app.zip.sha256"},
            )
        if str(request.url) == "https://objects.example.test/app.zip":
            return httpx.Response(200, content=zip_bytes)
        if str(request.url) == "https://objects.example.test/app.zip.sha256":
            return httpx.Response(200, text=sha256_text)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_download_and_verify_update_stores_verified_zip_under_updates_dir(
    tmp_path: Path,
) -> None:
    """更新 zip 與 SHA256 通過時，檔案只留在 updates dir 底下。"""

    zip_bytes = b"fake portable zip"
    digest = hashlib.sha256(zip_bytes).hexdigest()
    result = asyncio.run(
        download_and_verify_update(
            update_check=update_check(),
            updates_dir=tmp_path / "updates",
            transport=mock_transport(
                zip_bytes=zip_bytes,
                sha256_text=f"{digest}  facebook-monitor-0.1.0-windows-portable.zip\n",
            ),
        )
    )

    assert result.status == "verified"
    assert result.downloaded
    assert result.verified
    assert result.file_path is not None
    assert result.file_path.read_bytes() == zip_bytes
    assert result.file_path.is_relative_to((tmp_path / "updates").resolve())
    assert result.expected_sha256 == digest
    assert result.actual_sha256 == digest


def test_download_and_verify_update_follows_github_asset_redirects(
    tmp_path: Path,
) -> None:
    """GitHub Release asset 下載會經過 302，下載器必須跟隨 redirect。"""

    zip_bytes = b"redirected portable zip"
    digest = hashlib.sha256(zip_bytes).hexdigest()
    result = asyncio.run(
        download_and_verify_update(
            update_check=update_check(),
            updates_dir=tmp_path / "updates",
            transport=redirect_transport(
                zip_bytes=zip_bytes,
                sha256_text=f"{digest}  facebook-monitor-0.1.0-windows-portable.zip\n",
            ),
        )
    )

    assert result.status == "verified"
    assert result.verified
    assert result.file_path is not None
    assert result.file_path.read_bytes() == zip_bytes


def test_download_and_verify_update_rejects_sha256_mismatch(tmp_path: Path) -> None:
    """SHA256 不一致時不可標示為 verified。"""

    result = asyncio.run(
        download_and_verify_update(
            update_check=update_check(),
            updates_dir=tmp_path / "updates",
            transport=mock_transport(
                zip_bytes=b"actual",
                sha256_text=f"{hashlib.sha256(b'expected').hexdigest()}  "
                "facebook-monitor-0.1.0-windows-portable.zip\n",
            ),
        )
    )

    assert result.status == "sha256_mismatch"
    assert result.downloaded
    assert not result.verified
    assert result.failure_reason == "sha256_mismatch"


def test_download_and_verify_update_rejects_oversized_asset(tmp_path: Path) -> None:
    """下載時會限制 zip 大小，避免 updates dir 被異常 asset 填滿。"""

    result = asyncio.run(
        download_and_verify_update(
            update_check=update_check(),
            updates_dir=tmp_path / "updates",
            transport=mock_transport(
                zip_bytes=b"actual",
                sha256_text=f"{hashlib.sha256(b'actual').hexdigest()}  "
                "facebook-monitor-0.1.0-windows-portable.zip\n",
            ),
            max_asset_bytes=3,
        )
    )

    assert result.status == "failed"
    assert result.failure_reason == "download_too_large"
    assert result.file_path is not None
    assert not result.file_path.exists()
    assert not result.file_path.with_name(result.file_path.name + ".tmp").exists()


def test_download_and_verify_update_requires_sha256_url(tmp_path: Path) -> None:
    """缺 SHA256 URL 時不下載 zip。"""

    check = update_check()
    check = UpdateCheckResult(
        checked=check.checked,
        status=check.status,
        channel=check.channel,
        repository=check.repository,
        current_version=check.current_version,
        latest_version=check.latest_version,
        update_available=check.update_available,
        summary=check.summary,
        detail=check.detail,
        release_url=check.release_url,
        asset_name=check.asset_name,
        asset_download_url=check.asset_download_url,
        sha256_asset_name=check.sha256_asset_name,
        sha256_asset_download_url="",
        failure_reason=check.failure_reason,
    )

    result = asyncio.run(
        download_and_verify_update(
            update_check=check,
            updates_dir=tmp_path / "updates",
            transport=mock_transport(zip_bytes=b"unused", sha256_text="unused"),
        )
    )

    assert result.status == "failed"
    assert result.failure_reason == "sha256_asset_url_missing"
    assert not (tmp_path / "updates").exists()


def test_download_and_verify_update_rejects_invalid_version_dir(tmp_path: Path) -> None:
    """遠端版本字串不能讓下載資料夾逃出 updates dir。"""

    check = update_check()
    check = UpdateCheckResult(
        checked=check.checked,
        status=check.status,
        channel=check.channel,
        repository=check.repository,
        current_version=check.current_version,
        latest_version="../0.1.0",
        update_available=check.update_available,
        summary=check.summary,
        detail=check.detail,
        release_url=check.release_url,
        asset_name=check.asset_name,
        asset_download_url=check.asset_download_url,
        sha256_asset_name=check.sha256_asset_name,
        sha256_asset_download_url=check.sha256_asset_download_url,
        failure_reason=check.failure_reason,
    )

    result = asyncio.run(
        download_and_verify_update(
            update_check=check,
            updates_dir=tmp_path / "updates",
            transport=mock_transport(zip_bytes=b"unused", sha256_text="unused"),
        )
    )

    assert result.status == "failed"
    assert result.failure_reason == "invalid_asset_name"
    assert not (tmp_path / "updates").exists()


def test_sanitize_release_asset_name_rejects_paths() -> None:
    """Release asset name 不能偷渡路徑。"""

    assert sanitize_release_asset_name("facebook-monitor-0.1.0-windows-portable.zip")
    for value in ("../app.zip", "folder/app.zip", "app zip"):
        try:
            sanitize_release_asset_name(value)
        except ValueError as exc:
            assert str(exc) == "invalid_asset_name"
        else:
            raise AssertionError("expected invalid asset name")


def test_read_expected_sha256_rejects_filename_mismatch(tmp_path: Path) -> None:
    """SHA256 檔案若指定另一個檔名，要拒絕驗證。"""

    sha_path = tmp_path / "app.zip.sha256"
    sha_path.write_text(
        f"{hashlib.sha256(b'app').hexdigest()}  other.zip\n",
        encoding="utf-8",
    )

    try:
        read_expected_sha256(sha_path, expected_filename="app.zip")
    except ValueError as exc:
        assert str(exc) == "sha256_filename_mismatch"
    else:
        raise AssertionError("expected filename mismatch")
