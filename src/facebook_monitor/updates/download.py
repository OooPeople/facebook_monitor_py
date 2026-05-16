"""更新檔下載與 SHA256 驗證。

職責：將已知 GitHub Release asset 下載到 runtime data dir 底下，
並用對應 SHA256 asset 驗證完整性。此模組不解壓、不替換程式檔、
也不嘗試關閉或重啟主程式。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import sys
from typing import AsyncIterator

import httpx

from facebook_monitor.updates.release_check import UpdateCheckResult


DOWNLOAD_CHUNK_SIZE = 1024 * 1024
MAX_UPDATE_DOWNLOAD_BYTES = 1024 * 1024 * 1024
MAX_SHA256_DOWNLOAD_BYTES = 1024 * 1024


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


async def download_and_verify_update(
    *,
    update_check: UpdateCheckResult,
    updates_dir: Path,
    timeout_seconds: float = 120.0,
    transport: httpx.AsyncBaseTransport | None = None,
    max_asset_bytes: int = MAX_UPDATE_DOWNLOAD_BYTES,
    max_sha256_bytes: int = MAX_SHA256_DOWNLOAD_BYTES,
) -> UpdateDownloadResult:
    """下載更新 zip 與 SHA256 asset，驗證通過後保留於 updates dir。"""

    if not update_check.update_available or not update_check.asset_name:
        return _failure("update_not_available")
    if not update_check.asset_download_url:
        return _failure("asset_download_url_missing")
    if not update_check.sha256_asset_name:
        return _failure("sha256_asset_missing")
    if not update_check.sha256_asset_download_url:
        return _failure("sha256_asset_url_missing")
    try:
        asset_name = sanitize_release_asset_name(update_check.asset_name)
        sha256_name = sanitize_release_asset_name(update_check.sha256_asset_name)
        version_dir_name = sanitize_release_asset_name(update_check.latest_version)
        destination_dir = (updates_dir / version_dir_name).resolve()
        ensure_child_path(updates_dir.resolve(), destination_dir)
    except ValueError as exc:
        return _failure(str(exc))
    destination_dir.mkdir(parents=True, exist_ok=True)
    file_path = destination_dir / asset_name
    sha256_path = destination_dir / sha256_name
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, transport=transport) as client:
            await _download_file(
                client=client,
                url=update_check.asset_download_url,
                destination=file_path,
                max_bytes=max_asset_bytes,
            )
            await _download_file(
                client=client,
                url=update_check.sha256_asset_download_url,
                destination=sha256_path,
                max_bytes=max_sha256_bytes,
            )
    except httpx.HTTPStatusError as exc:
        return _failure(
            f"download_http_{exc.response.status_code}",
            file_path=file_path,
            sha256_path=sha256_path,
        )
    except httpx.HTTPError as exc:
        return _failure(
            f"download_error:{exc.__class__.__name__}",
            file_path=file_path,
            sha256_path=sha256_path,
        )
    except ValueError as exc:
        return _failure(str(exc), file_path=file_path, sha256_path=sha256_path)

    try:
        expected_sha256 = read_expected_sha256(sha256_path, expected_filename=asset_name)
        actual_sha256 = calculate_sha256(file_path)
    except ValueError as exc:
        return _failure(str(exc), file_path=file_path, sha256_path=sha256_path)
    verified = expected_sha256 == actual_sha256
    return UpdateDownloadResult(
        status="verified" if verified else "sha256_mismatch",
        downloaded=True,
        verified=verified,
        file_path=file_path,
        sha256_path=sha256_path,
        expected_sha256=expected_sha256,
        actual_sha256=actual_sha256,
        failure_reason="" if verified else "sha256_mismatch",
    )


def sanitize_release_asset_name(value: str) -> str:
    """限制 release asset 檔名，避免下載結果逃出 updates dir。"""

    name = Path(value).name.strip()
    if name != value.strip() or not name:
        raise ValueError("invalid_asset_name")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError("invalid_asset_name")
    return name


def ensure_child_path(parent: Path, child: Path) -> None:
    """確認 child 路徑位於 parent 底下。"""

    if not child.is_relative_to(parent):
        raise ValueError("download_path_outside_updates_dir")


def read_expected_sha256(path: Path, *, expected_filename: str) -> str:
    """讀取 `.sha256` 檔案，支援常見 `hash  filename` 格式。"""

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("sha256_file_empty")
    first_line = text.splitlines()[0].strip()
    candidate = first_line.split()[0].casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", candidate):
        raise ValueError("sha256_file_invalid")
    parts = first_line.split(maxsplit=1)
    if len(parts) == 2:
        filename = parts[1].lstrip("*").strip()
        if filename and Path(filename).name != expected_filename:
            raise ValueError("sha256_filename_mismatch")
    return candidate


def calculate_sha256(path: Path) -> str:
    """計算檔案 SHA256。"""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(DOWNLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _download_file(
    *,
    client: httpx.AsyncClient,
    url: str,
    destination: Path,
    max_bytes: int,
) -> None:
    """串流下載單一檔案；完成前使用 `.tmp` 避免半成品被當成可用。"""

    tmp_destination = destination.with_name(destination.name + ".tmp")
    try:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            content_length = _parse_content_length(response)
            if content_length is not None and content_length > max_bytes:
                raise ValueError("download_too_large")
            downloaded_bytes = 0
            with tmp_destination.open("wb") as file:
                async for chunk in _aiter_response_bytes(response):
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > max_bytes:
                        raise ValueError("download_too_large")
                    file.write(chunk)
        tmp_destination.replace(destination)
    except Exception:
        try:
            tmp_destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise


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
    )


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
        return False
    except OSError:
        return False


def _is_windows() -> bool:
    """集中平台判斷，方便測試替換。"""

    return sys.platform == "win32"
