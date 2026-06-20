"""更新交接檔寫入入口。

職責：在主程式完成下載與 SHA256 驗證後，寫出獨立 updater 可讀取的
pending update JSON。此檔只描述已驗證 zip、目前 app/data/runtime 路徑
與雜湊，不包含 secrets、cookies、tokens 或任意執行命令。
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
import json
from pathlib import Path
from typing import Any
import uuid

from facebook_monitor.runtime.paths import RuntimePaths
from facebook_monitor.updates.download import UpdateDownloadResult
from facebook_monitor.updates.download import validate_verified_download_set
from facebook_monitor.updates.pending_update_codec import pending_update_to_json_dict
from facebook_monitor.updates.pending_update_models import PENDING_UPDATE_SCHEMA_VERSION
from facebook_monitor.updates.pending_update_models import PendingUpdate
from facebook_monitor.updates.pending_update_models import pending_update_path
from facebook_monitor.updates.pending_update_validation import (
    validate_pending_update_artifact_set,
)
from facebook_monitor.updates.pending_update_validation import validate_pending_update_paths
from facebook_monitor.updates.release_check import UpdateCheckResult
from facebook_monitor.updates.validation import is_reparse_or_symlink


def write_pending_update(
    *,
    update_check: UpdateCheckResult,
    download_result: UpdateDownloadResult,
    paths: RuntimePaths,
) -> PendingUpdate:
    """寫出 pending update JSON，供獨立 updater 在主程式關閉後套用。"""

    if not download_result.verified or download_result.file_path is None:
        raise ValueError("download_result_not_verified")
    if not download_result.file_path.is_file():
        raise ValueError("download_result_file_missing")
    if download_result.manifest_path is None or not download_result.manifest_path.is_file():
        raise ValueError("download_result_manifest_missing")
    if (
        download_result.manifest_signature_path is None
        or not download_result.manifest_signature_path.is_file()
    ):
        raise ValueError("download_result_manifest_signature_missing")
    if not download_result.manifest_sha256:
        raise ValueError("download_result_manifest_missing")
    if not download_result.manifest_key_id:
        raise ValueError("download_result_manifest_key_missing")
    validate_verified_download_set(download_result)
    pending = PendingUpdate(
        schema_version=PENDING_UPDATE_SCHEMA_VERSION,
        version=update_check.latest_version,
        repository=update_check.repository,
        asset_name=update_check.asset_name,
        zip_path=download_result.file_path.resolve(),
        expected_sha256=download_result.expected_sha256,
        actual_sha256=download_result.actual_sha256,
        app_base_dir=paths.app_base_dir.resolve(),
        data_dir=paths.data_dir.resolve(),
        db_path=paths.db_path.resolve(),
        profile_dir=paths.profile_dir.resolve(),
        logs_dir=paths.logs_dir.resolve(),
        runtime_dir=paths.runtime_dir.resolve(),
        created_at=datetime.now(timezone.utc).isoformat(),
        manifest_path=download_result.manifest_path.resolve()
        if download_result.manifest_path is not None
        else None,
        manifest_signature_path=download_result.manifest_signature_path.resolve()
        if download_result.manifest_signature_path is not None
        else None,
        manifest_sha256=download_result.manifest_sha256,
        manifest_key_id=download_result.manifest_key_id,
    )
    validate_pending_update_paths(pending)
    validate_pending_update_artifact_set(pending)
    destination = pending_update_path(paths.runtime_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(destination, pending_update_to_json_dict(pending))
    return pending


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """同目錄 atomic replace，避免 updater 讀到半寫入 JSON。"""

    if is_reparse_or_symlink(path.parent):
        raise ValueError("pending_update_dir_unsafe")
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("x", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, indent=2))
            file.write("\n")
    except FileExistsError as exc:
        raise ValueError("pending_update_tmp_unsafe") from exc
    try:
        tmp_path.chmod(0o600)
    except OSError:
        pass
    try:
        tmp_path.replace(path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
