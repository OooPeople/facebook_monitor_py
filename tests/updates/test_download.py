"""更新檔下載與 SHA256 驗證測試。"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx
import pytest

from facebook_monitor.updates.download import download_and_verify_update
from facebook_monitor.updates.download import read_expected_sha256
from facebook_monitor.updates.manifest import release_manifest_asset_name
from facebook_monitor.updates.manifest import release_manifest_signature_asset_name
from facebook_monitor.updates.release_check import UpdateCheckResult


ASSET_NAME = "facebook-monitor-0.1.0-windows-portable.zip"
SHA256_NAME = f"{ASSET_NAME}.sha256"
MANIFEST_NAME = release_manifest_asset_name("0.1.0")
MANIFEST_SIGNATURE_NAME = release_manifest_signature_asset_name("0.1.0")
RELEASE_URL_PREFIX = "https://github.com/OooPeople/facebook_monitor_py/releases/download/v0.1.0"
ASSET_URL = f"{RELEASE_URL_PREFIX}/{ASSET_NAME}"
SHA256_URL = f"{RELEASE_URL_PREFIX}/{SHA256_NAME}"
MANIFEST_URL = f"{RELEASE_URL_PREFIX}/{MANIFEST_NAME}"
MANIFEST_SIGNATURE_URL = f"{RELEASE_URL_PREFIX}/{MANIFEST_SIGNATURE_NAME}"
TEST_PRIVATE_KEY = Ed25519PrivateKey.generate()
TEST_KEY_ID = "test-release-key"


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
        asset_name=ASSET_NAME,
        asset_download_url=ASSET_URL,
        sha256_asset_name=SHA256_NAME,
        sha256_asset_download_url=SHA256_URL,
        failure_reason="",
        manifest_asset_name=MANIFEST_NAME,
        manifest_asset_download_url=MANIFEST_URL,
        manifest_signature_asset_name=MANIFEST_SIGNATURE_NAME,
        manifest_signature_asset_download_url=MANIFEST_SIGNATURE_URL,
    )


def trusted_public_keys() -> dict[str, str]:
    """回傳測試用 release manifest public key。"""

    public_key = TEST_PRIVATE_KEY.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {TEST_KEY_ID: base64.b64encode(public_key).decode("ascii")}


def manifest_bytes_for(zip_bytes: bytes) -> bytes:
    """建立測試用 signed manifest bytes。"""

    payload = {
        "schema_version": 1,
        "version": "0.1.0",
        "repository": "OooPeople/facebook_monitor_py",
        "key_id": TEST_KEY_ID,
        "assets": [
            {
                "name": ASSET_NAME,
                "platform": "windows",
                "sha256": hashlib.sha256(zip_bytes).hexdigest(),
                "size": len(zip_bytes),
            }
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def signature_bytes_for(manifest_bytes: bytes) -> bytes:
    """簽出測試用 manifest detached signature。"""

    signature = TEST_PRIVATE_KEY.sign(manifest_bytes)
    return base64.b64encode(signature)


def mock_transport(*, zip_bytes: bytes, sha256_text: str) -> httpx.MockTransport:
    """建立下載 manifest、signature、zip 與 sha256 的 mock transport。"""

    manifest_bytes = manifest_bytes_for(zip_bytes)
    signature_bytes = signature_bytes_for(manifest_bytes)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == MANIFEST_URL:
            return httpx.Response(200, content=manifest_bytes)
        if str(request.url) == MANIFEST_SIGNATURE_URL:
            return httpx.Response(200, content=signature_bytes)
        if str(request.url) == ASSET_URL:
            return httpx.Response(200, content=zip_bytes)
        if str(request.url) == SHA256_URL:
            return httpx.Response(200, text=sha256_text)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def zip_mismatch_transport(
    *,
    manifest_zip_bytes: bytes,
    served_zip_bytes: bytes,
) -> httpx.MockTransport:
    """建立 manifest hash 與實際 zip 內容不同的 mock transport。"""

    manifest_bytes = manifest_bytes_for(manifest_zip_bytes)
    signature_bytes = signature_bytes_for(manifest_bytes)
    manifest_digest = hashlib.sha256(manifest_zip_bytes).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == MANIFEST_URL:
            return httpx.Response(200, content=manifest_bytes)
        if str(request.url) == MANIFEST_SIGNATURE_URL:
            return httpx.Response(200, content=signature_bytes)
        if str(request.url) == SHA256_URL:
            return httpx.Response(200, text=f"{manifest_digest}  {ASSET_NAME}\n")
        if str(request.url) == ASSET_URL:
            return httpx.Response(200, content=served_zip_bytes)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def failing_sha256_transport(*, zip_bytes: bytes, status_code: int = 500) -> httpx.MockTransport:
    """建立 zip 成功、SHA256 sidecar 失敗的 mock transport。"""

    manifest_bytes = manifest_bytes_for(zip_bytes)
    signature_bytes = signature_bytes_for(manifest_bytes)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == MANIFEST_URL:
            return httpx.Response(200, content=manifest_bytes)
        if str(request.url) == MANIFEST_SIGNATURE_URL:
            return httpx.Response(200, content=signature_bytes)
        if str(request.url) == ASSET_URL:
            return httpx.Response(200, content=zip_bytes)
        if str(request.url) == SHA256_URL:
            return httpx.Response(status_code)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def redirect_transport(*, zip_bytes: bytes, sha256_text: str) -> httpx.MockTransport:
    """模擬 GitHub release asset 下載會先回 302 redirect。"""

    manifest_bytes = manifest_bytes_for(zip_bytes)
    signature_bytes = signature_bytes_for(manifest_bytes)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == MANIFEST_URL:
            return httpx.Response(
                302,
                headers={"location": "https://release-assets.githubusercontent.com/manifest"},
            )
        if str(request.url) == MANIFEST_SIGNATURE_URL:
            return httpx.Response(
                302,
                headers={"location": "https://release-assets.githubusercontent.com/manifest.sig"},
            )
        if str(request.url) == ASSET_URL:
            return httpx.Response(
                302,
                headers={"location": "https://release-assets.githubusercontent.com/app.zip"},
            )
        if str(request.url) == SHA256_URL:
            return httpx.Response(
                302,
                headers={"location": "https://release-assets.githubusercontent.com/app.zip.sha256"},
            )
        if str(request.url) == "https://release-assets.githubusercontent.com/manifest":
            return httpx.Response(200, content=manifest_bytes)
        if str(request.url) == "https://release-assets.githubusercontent.com/manifest.sig":
            return httpx.Response(200, content=signature_bytes)
        if str(request.url) == "https://release-assets.githubusercontent.com/app.zip":
            return httpx.Response(200, content=zip_bytes)
        if str(request.url) == "https://release-assets.githubusercontent.com/app.zip.sha256":
            return httpx.Response(200, text=sha256_text)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def evil_redirect_transport(*, zip_bytes: bytes) -> httpx.MockTransport:
    """建立最終 redirect 離開 GitHub allowlist 的 mock transport。"""

    manifest_bytes = manifest_bytes_for(zip_bytes)
    signature_bytes = signature_bytes_for(manifest_bytes)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == MANIFEST_URL:
            return httpx.Response(200, content=manifest_bytes)
        if str(request.url) == MANIFEST_SIGNATURE_URL:
            return httpx.Response(200, content=signature_bytes)
        if str(request.url) == SHA256_URL:
            digest = hashlib.sha256(zip_bytes).hexdigest()
            return httpx.Response(200, text=f"{digest}  {ASSET_NAME}\n")
        if str(request.url) == ASSET_URL:
            return httpx.Response(302, headers={"location": "https://example.com/app.zip"})
        if str(request.url) == "https://example.com/app.zip":
            return httpx.Response(200, content=zip_bytes)
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
            trusted_public_keys=trusted_public_keys(),
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
            trusted_public_keys=trusted_public_keys(),
        )
    )

    assert result.status == "verified"
    assert result.verified
    assert result.file_path is not None
    assert result.file_path.read_bytes() == zip_bytes


def test_download_and_verify_update_rejects_sha256_sidecar_manifest_mismatch(
    tmp_path: Path,
) -> None:
    """SHA256 sidecar 與 signed manifest 不一致時不可下載 zip。"""

    result = asyncio.run(
        download_and_verify_update(
            update_check=update_check(),
            updates_dir=tmp_path / "updates",
            transport=mock_transport(
                zip_bytes=b"actual",
                sha256_text=f"{hashlib.sha256(b'expected').hexdigest()}  "
                "facebook-monitor-0.1.0-windows-portable.zip\n",
            ),
            trusted_public_keys=trusted_public_keys(),
        )
    )

    assert result.status == "failed"
    assert not result.downloaded
    assert not result.verified
    assert result.failure_reason == "sha256_sidecar_manifest_mismatch"


def test_download_and_verify_update_rejects_zip_hash_mismatch(tmp_path: Path) -> None:
    """zip 與 signed manifest hash 不一致時不可發布 verified download。"""

    result = asyncio.run(
        download_and_verify_update(
            update_check=update_check(),
            updates_dir=tmp_path / "updates",
            transport=zip_mismatch_transport(
                manifest_zip_bytes=b"expected",
                served_zip_bytes=b"changed!",
            ),
            trusted_public_keys=trusted_public_keys(),
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
            trusted_public_keys=trusted_public_keys(),
        )
    )

    assert result.status == "failed"
    assert result.failure_reason == "download_too_large"
    assert result.file_path is not None
    assert not result.file_path.exists()
    assert not result.file_path.with_name(result.file_path.name + ".tmp").exists()


def test_download_and_verify_update_removes_staged_zip_when_sha256_download_fails(
    tmp_path: Path,
) -> None:
    """SHA256 sidecar 下載失敗時，不可留下未驗證 zip 或 staging 檔。"""

    updates_dir = tmp_path / "updates"
    result = asyncio.run(
        download_and_verify_update(
            update_check=update_check(),
            updates_dir=updates_dir,
            transport=failing_sha256_transport(zip_bytes=b"actual", status_code=500),
            trusted_public_keys=trusted_public_keys(),
        )
    )

    assert result.status == "failed"
    assert result.failure_reason == "download_http_500"
    assert result.file_path is not None
    assert result.sha256_path is not None
    assert not result.file_path.exists()
    assert not result.sha256_path.exists()
    assert not result.file_path.with_name(result.file_path.name + ".download").exists()
    assert not result.sha256_path.with_name(result.sha256_path.name + ".download").exists()


def test_download_and_verify_update_requires_sha256_url(tmp_path: Path) -> None:
    """缺 SHA256 URL 時不下載 zip。"""

    check = replace(update_check(), sha256_asset_download_url="")

    result = asyncio.run(
        download_and_verify_update(
            update_check=check,
            updates_dir=tmp_path / "updates",
            transport=mock_transport(zip_bytes=b"unused", sha256_text="unused"),
            trusted_public_keys=trusted_public_keys(),
        )
    )

    assert result.status == "failed"
    assert result.failure_reason == "sha256_asset_url_missing"
    assert not (tmp_path / "updates").exists()


def test_download_and_verify_update_requires_signed_manifest(tmp_path: Path) -> None:
    """只有 SHA256 sidecar 沒 signed manifest 時不可下載 zip。"""

    check = replace(
        update_check(),
        manifest_asset_name="",
        manifest_asset_download_url="",
        manifest_signature_asset_name="",
        manifest_signature_asset_download_url="",
    )

    result = asyncio.run(
        download_and_verify_update(
            update_check=check,
            updates_dir=tmp_path / "updates",
            transport=mock_transport(zip_bytes=b"unused", sha256_text="unused"),
            trusted_public_keys=trusted_public_keys(),
        )
    )

    assert result.status == "failed"
    assert result.failure_reason == "manifest_file_missing"
    assert not (tmp_path / "updates").exists()


def test_download_and_verify_update_rejects_non_github_initial_url(
    tmp_path: Path,
) -> None:
    """Release metadata 中的初始下載 URL 必須在 GitHub release host。"""

    check = replace(update_check(), asset_download_url="https://example.com/app.zip")

    result = asyncio.run(
        download_and_verify_update(
            update_check=check,
            updates_dir=tmp_path / "updates",
            transport=mock_transport(zip_bytes=b"unused", sha256_text="unused"),
            trusted_public_keys=trusted_public_keys(),
        )
    )

    assert result.status == "failed"
    assert result.failure_reason == "release_download_url_host_not_allowed"
    assert not (tmp_path / "updates").exists()


def test_download_and_verify_update_rejects_non_github_final_redirect(
    tmp_path: Path,
) -> None:
    """GitHub 初始 URL redirect 到非 allowlist host 時必須停止下載。"""

    result = asyncio.run(
        download_and_verify_update(
            update_check=update_check(),
            updates_dir=tmp_path / "updates",
            transport=evil_redirect_transport(zip_bytes=b"zip"),
            trusted_public_keys=trusted_public_keys(),
        )
    )

    assert result.status == "failed"
    assert result.failure_reason == "release_download_url_host_not_allowed"


def test_download_and_verify_update_rejects_invalid_version_dir(tmp_path: Path) -> None:
    """遠端版本字串不能讓下載資料夾逃出 updates dir。"""

    check = replace(update_check(), latest_version="../0.1.0")

    result = asyncio.run(
        download_and_verify_update(
            update_check=check,
            updates_dir=tmp_path / "updates",
            transport=mock_transport(zip_bytes=b"unused", sha256_text="unused"),
            trusted_public_keys=trusted_public_keys(),
        )
    )

    assert result.status == "failed"
    assert result.failure_reason == "invalid_asset_name"
    assert not (tmp_path / "updates").exists()


def test_download_and_verify_update_rejects_symlinked_updates_dir(tmp_path: Path) -> None:
    """updates root 若被 symlink/junction 導到外部，下載前就必須拒絕。"""

    outside = tmp_path / "outside"
    outside.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    updates_link = data_dir / "updates"
    try:
        updates_link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    result = asyncio.run(
        download_and_verify_update(
            update_check=update_check(),
            updates_dir=updates_link,
            transport=mock_transport(
                zip_bytes=b"unused",
                sha256_text="unused",
            ),
            trusted_public_keys=trusted_public_keys(),
        )
    )

    assert result.status == "failed"
    assert result.failure_reason == "download_path_unsafe"
    assert list(outside.iterdir()) == []


def test_download_and_verify_update_rejects_existing_tmp_symlink(tmp_path: Path) -> None:
    """下載 `.tmp` 若已是 symlink，不可 follow 後覆寫外部檔。"""

    updates_dir = tmp_path / "updates"
    destination_dir = updates_dir / "0.1.0"
    destination_dir.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    tmp_link = destination_dir / "facebook-monitor-0.1.0-windows-portable.zip.tmp"
    try:
        tmp_link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"file symlink unavailable: {exc}")

    result = asyncio.run(
        download_and_verify_update(
            update_check=update_check(),
            updates_dir=updates_dir,
            transport=mock_transport(
                zip_bytes=b"actual",
                sha256_text=(
                    f"{hashlib.sha256(b'actual').hexdigest()}  "
                    "facebook-monitor-0.1.0-windows-portable.zip\n"
                ),
            ),
            trusted_public_keys=trusted_public_keys(),
        )
    )

    assert result.status == "failed"
    assert result.failure_reason == "download_path_unsafe"
    assert outside.read_text(encoding="utf-8") == "keep"


def test_download_and_verify_update_rejects_existing_sha256_tmp_symlink(
    tmp_path: Path,
) -> None:
    """SHA256 `.tmp` 若已是 symlink，不可先留下 zip staging 檔。"""

    updates_dir = tmp_path / "updates"
    destination_dir = updates_dir / "0.1.0"
    destination_dir.mkdir(parents=True)
    outside = tmp_path / "outside-sha.txt"
    outside.write_text("keep", encoding="utf-8")
    tmp_link = destination_dir / "facebook-monitor-0.1.0-windows-portable.zip.sha256.tmp"
    try:
        tmp_link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"file symlink unavailable: {exc}")

    result = asyncio.run(
        download_and_verify_update(
            update_check=update_check(),
            updates_dir=updates_dir,
            transport=mock_transport(
                zip_bytes=b"actual",
                sha256_text=(
                    f"{hashlib.sha256(b'actual').hexdigest()}  "
                    "facebook-monitor-0.1.0-windows-portable.zip\n"
                ),
            ),
            trusted_public_keys=trusted_public_keys(),
        )
    )

    assert result.status == "failed"
    assert result.failure_reason == "download_path_unsafe"
    assert result.file_path is not None
    assert not result.file_path.exists()
    assert not result.file_path.with_name(result.file_path.name + ".download").exists()
    assert outside.read_text(encoding="utf-8") == "keep"


def test_download_and_verify_update_reports_io_error_when_version_dir_is_file(
    tmp_path: Path,
) -> None:
    """updates/<version> 若是檔案，下載器要回傳 failure 而不是丟到 Web 500。"""

    version_path = tmp_path / "updates" / "0.1.0"
    version_path.parent.mkdir()
    version_path.write_text("not a directory", encoding="utf-8")

    result = asyncio.run(
        download_and_verify_update(
            update_check=update_check(),
            updates_dir=tmp_path / "updates",
            transport=mock_transport(
                zip_bytes=b"unused",
                sha256_text="unused",
            ),
            trusted_public_keys=trusted_public_keys(),
        )
    )

    assert result.status == "failed"
    assert result.failure_reason == "download_path_unsafe"


def test_download_and_verify_update_rejects_destination_directory(
    tmp_path: Path,
) -> None:
    """目的檔若已是目錄，要回傳可診斷的 unsafe path failure。"""

    destination_dir = tmp_path / "updates" / "0.1.0"
    (destination_dir / "facebook-monitor-0.1.0-windows-portable.zip").mkdir(
        parents=True
    )

    result = asyncio.run(
        download_and_verify_update(
            update_check=update_check(),
            updates_dir=tmp_path / "updates",
            transport=mock_transport(
                zip_bytes=b"unused",
                sha256_text="unused",
            ),
            trusted_public_keys=trusted_public_keys(),
        )
    )

    assert result.status == "failed"
    assert result.failure_reason == "download_path_unsafe"


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
