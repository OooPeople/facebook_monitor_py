"""更新檔下載與 SHA256 驗證。

職責：將已知 GitHub Release asset 下載到 runtime data dir 底下，
並用對應 SHA256 asset 驗證完整性。此模組不解壓、不替換程式檔、
也不嘗試關閉或重啟主程式。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import AsyncIterator

import httpx

from facebook_monitor.core.defaults import PYTHON_UPDATER_RUNTIME_DEFAULTS
from facebook_monitor.updates.artifacts import sanitize_release_asset_name
from facebook_monitor.updates.checksum import HASH_CHUNK_SIZE
from facebook_monitor.updates.checksum import calculate_sha256 as _calculate_sha256
from facebook_monitor.updates.checksum import read_sha256_sidecar
from facebook_monitor.updates.release_check import UpdateCheckResult
from facebook_monitor.updates.validation import has_unsafe_existing_path_component
from facebook_monitor.updates.validation import is_reparse_or_symlink


DOWNLOAD_CHUNK_SIZE = HASH_CHUNK_SIZE
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
    timeout_seconds: float = PYTHON_UPDATER_RUNTIME_DEFAULTS.timeout_seconds,
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
        updates_root = Path(updates_dir).expanduser().absolute()
        destination_dir = updates_root / version_dir_name
        file_path = destination_dir / asset_name
        sha256_path = destination_dir / sha256_name
        staged_file_path = _staging_destination(file_path)
        staged_sha256_path = _staging_destination(sha256_path)
        ensure_child_path(updates_root, destination_dir)
        ensure_safe_download_path(destination_dir, updates_root=updates_root)
    except ValueError as exc:
        return _failure(str(exc))
    try:
        _prepare_destination_dir(destination_dir, updates_root=updates_root)
        _prepare_download_destinations(
            file_path,
            sha256_path,
            staged_file_path,
            staged_sha256_path,
            updates_root=updates_root,
        )
        async with httpx.AsyncClient(
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=True,
        ) as client:
            await _download_file(
                client=client,
                url=update_check.asset_download_url,
                destination=staged_file_path,
                updates_root=updates_root,
                max_bytes=max_asset_bytes,
            )
            await _download_file(
                client=client,
                url=update_check.sha256_asset_download_url,
                destination=staged_sha256_path,
                updates_root=updates_root,
                max_bytes=max_sha256_bytes,
            )
        expected_sha256 = read_expected_sha256(
            staged_sha256_path,
            expected_filename=asset_name,
        )
        actual_sha256 = calculate_sha256(staged_file_path)
        if expected_sha256 != actual_sha256:
            _cleanup_download_artifacts(
                staged_file_path,
                staged_sha256_path,
                updates_root=updates_root,
            )
            return UpdateDownloadResult(
                status="sha256_mismatch",
                downloaded=True,
                verified=False,
                file_path=file_path,
                sha256_path=sha256_path,
                expected_sha256=expected_sha256,
                actual_sha256=actual_sha256,
                failure_reason="sha256_mismatch",
            )
        _publish_verified_download(
            staged_file_path=staged_file_path,
            file_path=file_path,
            staged_sha256_path=staged_sha256_path,
            sha256_path=sha256_path,
            updates_root=updates_root,
        )
    except httpx.HTTPStatusError as exc:
        _cleanup_download_artifacts(
            staged_file_path,
            staged_sha256_path,
            updates_root=updates_root,
        )
        return _failure(
            f"download_http_{exc.response.status_code}",
            file_path=file_path,
            sha256_path=sha256_path,
        )
    except httpx.HTTPError as exc:
        _cleanup_download_artifacts(
            staged_file_path,
            staged_sha256_path,
            updates_root=updates_root,
        )
        return _failure(
            f"download_error:{exc.__class__.__name__}",
            file_path=file_path,
            sha256_path=sha256_path,
        )
    except ValueError as exc:
        _cleanup_download_artifacts(
            staged_file_path,
            staged_sha256_path,
            updates_root=updates_root,
        )
        return _failure(str(exc), file_path=file_path, sha256_path=sha256_path)
    except OSError as exc:
        _cleanup_download_artifacts(
            staged_file_path,
            staged_sha256_path,
            updates_root=updates_root,
        )
        return _failure(
            f"download_io_error:{exc.__class__.__name__}",
            file_path=file_path,
            sha256_path=sha256_path,
        )
    return UpdateDownloadResult(
        status="verified",
        downloaded=True,
        verified=True,
        file_path=file_path,
        sha256_path=sha256_path,
        expected_sha256=expected_sha256,
        actual_sha256=actual_sha256,
        failure_reason="",
    )


def ensure_child_path(parent: Path, child: Path) -> None:
    """確認 child 路徑位於 parent 底下。"""

    if not child.is_relative_to(parent):
        raise ValueError("download_path_outside_updates_dir")


def ensure_safe_download_path(path: Path, *, updates_root: Path) -> None:
    """確認 download path 不會經由 symlink/junction 寫到 updates dir 外。"""

    absolute_path = path.absolute()
    absolute_updates_root = updates_root.absolute()
    ensure_child_path(absolute_updates_root, absolute_path)
    if has_unsafe_existing_path_component(
        absolute_path,
        root=absolute_updates_root.parent,
    ):
        raise ValueError("download_path_unsafe")


def read_expected_sha256(path: Path, *, expected_filename: str) -> str:
    """讀取 `.sha256` 檔案，支援常見 `hash  filename` 格式。"""

    return read_sha256_sidecar(path, expected_filename=expected_filename)


def calculate_sha256(path: Path) -> str:
    """計算檔案 SHA256。"""

    return _calculate_sha256(path)


async def _download_file(
    *,
    client: httpx.AsyncClient,
    url: str,
    destination: Path,
    updates_root: Path,
    max_bytes: int,
) -> None:
    """串流下載單一檔案；完成前使用 `.tmp` 避免半成品被當成可用。"""

    tmp_destination = destination.with_name(destination.name + ".tmp")
    try:
        _prepare_download_tmp(
            destination,
            tmp_destination,
            updates_root=updates_root,
        )
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            content_length = _parse_content_length(response)
            if content_length is not None and content_length > max_bytes:
                raise ValueError("download_too_large")
            downloaded_bytes = 0
            with tmp_destination.open("xb") as file:
                async for chunk in _aiter_response_bytes(response):
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


def _staging_destination(destination: Path) -> Path:
    """回傳驗證完成前使用的 staging 路徑。"""

    return destination.with_name(destination.name + ".download")


def _prepare_destination_dir(path: Path, *, updates_root: Path) -> None:
    """建立安全的下載版本目錄；既有非目錄或連結一律拒絕。"""

    ensure_safe_download_path(path, updates_root=updates_root)
    if is_reparse_or_symlink(path):
        raise ValueError("download_path_unsafe")
    if path.exists():
        if not path.is_dir():
            raise ValueError("download_path_unsafe")
        return
    path.mkdir(parents=True, exist_ok=True)
    ensure_safe_download_path(path, updates_root=updates_root)


def _prepare_download_destinations(
    *destinations: Path,
    updates_root: Path,
) -> None:
    """下載前準備所有正式與 staging 路徑，並移除安全範圍內 stale `.tmp`。"""

    for destination in destinations:
        _prepare_download_tmp(
            destination,
            destination.with_name(destination.name + ".tmp"),
            updates_root=updates_root,
        )


def _publish_verified_download(
    *,
    staged_file_path: Path,
    file_path: Path,
    staged_sha256_path: Path,
    sha256_path: Path,
    updates_root: Path,
) -> None:
    """驗證完成後才將 staging 檔發布到正式檔名。"""

    for destination in (file_path, sha256_path):
        _ensure_download_destination_available(destination, updates_root=updates_root)
    published_paths: list[Path] = []
    try:
        staged_file_path.replace(file_path)
        published_paths.append(file_path)
        staged_sha256_path.replace(sha256_path)
        published_paths.append(sha256_path)
    except Exception:
        _cleanup_download_artifacts(
            *published_paths,
            staged_file_path,
            staged_sha256_path,
            updates_root=updates_root,
        )
        raise


def _prepare_download_tmp(
    destination: Path,
    tmp_destination: Path,
    *,
    updates_root: Path,
) -> None:
    """準備下載暫存檔；不得 follow 既有 symlink/junction。"""

    _ensure_download_destination_available(destination, updates_root=updates_root)
    _ensure_download_destination_available(tmp_destination, updates_root=updates_root)
    if tmp_destination.exists():
        try:
            tmp_destination.unlink()
        except OSError as exc:
            raise ValueError("download_path_unsafe") from exc


def _ensure_download_destination_available(
    destination: Path,
    *,
    updates_root: Path,
) -> None:
    """確認下載目的地可安全建立或覆寫。"""

    ensure_safe_download_path(destination.parent, updates_root=updates_root)
    ensure_safe_download_path(destination, updates_root=updates_root)
    if is_reparse_or_symlink(destination):
        raise ValueError("download_path_unsafe")
    if destination.exists() and not destination.is_file():
        raise ValueError("download_path_unsafe")


def _cleanup_download_artifacts(
    *paths: Path,
    updates_root: Path,
) -> None:
    """清掉下載流程留下的安全範圍內暫存與目的檔。"""

    for path in paths:
        _safe_unlink_download_path(
            path.with_name(path.name + ".tmp"),
            updates_root=updates_root,
        )
        _safe_unlink_download_path(path, updates_root=updates_root)


def _safe_unlink_download_path(path: Path, *, updates_root: Path) -> None:
    """只移除確認仍在安全 updates tree 內的一般檔或 symlink。"""

    try:
        ensure_safe_download_path(path, updates_root=updates_root)
    except ValueError:
        return
    try:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
    except OSError:
        pass


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
        if _is_macos():
            subprocess.Popen(  # noqa: S603, S607
                ["open", str(target)],
                close_fds=True,
                start_new_session=True,
            )
            return True
        return False
    except OSError:
        return False


def _is_windows() -> bool:
    """集中平台判斷，方便測試替換。"""

    return sys.platform == "win32"


def _is_macos() -> bool:
    """集中平台判斷，方便測試替換。"""

    return sys.platform == "darwin"
