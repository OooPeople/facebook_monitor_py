"""更新交接檔。

職責：在主程式完成下載與 SHA256 驗證後，寫出獨立 updater 可讀取的
pending update JSON。此檔只描述已驗證 zip、目前 app/data/runtime 路徑
與雜湊，不包含 secrets、cookies、tokens 或任意執行命令。
"""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
import json
from pathlib import Path
import re
from typing import Any
import uuid

from facebook_monitor.runtime.paths import RuntimePaths
from facebook_monitor.updates.artifacts import sanitize_release_asset_name
from facebook_monitor.updates.download import UpdateDownloadResult
from facebook_monitor.updates.release_check import UpdateCheckResult
from facebook_monitor.updates.validation import is_dangerous_root
from facebook_monitor.updates.validation import is_reparse_or_symlink


PENDING_UPDATE_FILE_NAME = "pending_update.json"
PENDING_UPDATE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PendingUpdate:
    """獨立 updater 套用更新需要的最小資訊。"""

    schema_version: int
    version: str
    asset_name: str
    zip_path: Path
    expected_sha256: str
    actual_sha256: str
    app_base_dir: Path
    data_dir: Path
    db_path: Path
    profile_dir: Path
    logs_dir: Path
    runtime_dir: Path
    created_at: str


def pending_update_path(runtime_dir: Path) -> Path:
    """回傳 runtime dir 底下的 pending update 檔案路徑。"""

    return runtime_dir / PENDING_UPDATE_FILE_NAME


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
    pending = PendingUpdate(
        schema_version=PENDING_UPDATE_SCHEMA_VERSION,
        version=update_check.latest_version,
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
    )
    validate_pending_update_paths(pending)
    destination = pending_update_path(paths.runtime_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(destination, _pending_to_json_dict(pending))
    return pending


def load_pending_update(path: Path) -> PendingUpdate:
    """讀取並驗證 pending update JSON。"""

    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("pending_update_invalid")
    schema_version = int(payload.get("schema_version", 0))
    if schema_version != PENDING_UPDATE_SCHEMA_VERSION:
        raise ValueError("pending_update_schema_unsupported")
    try:
        pending = PendingUpdate(
            schema_version=schema_version,
            version=str(payload["version"]),
            asset_name=str(payload["asset_name"]),
            zip_path=Path(str(payload["zip_path"])).resolve(),
            expected_sha256=str(payload["expected_sha256"]),
            actual_sha256=str(payload["actual_sha256"]),
            app_base_dir=Path(str(payload["app_base_dir"])).resolve(),
            data_dir=Path(str(payload["data_dir"])).resolve(),
            db_path=Path(str(payload["db_path"])).resolve(),
            profile_dir=Path(str(payload["profile_dir"])).resolve(),
            logs_dir=Path(str(payload["logs_dir"])).resolve(),
            runtime_dir=Path(str(payload["runtime_dir"])).resolve(),
            created_at=str(payload["created_at"]),
        )
    except KeyError as exc:
        raise ValueError("pending_update_missing_field") from exc
    if pending.expected_sha256 != pending.actual_sha256:
        raise ValueError("pending_update_sha256_mismatch")
    if not re.fullmatch(r"[0-9a-f]{64}", pending.expected_sha256.casefold()):
        raise ValueError("pending_update_sha256_invalid")
    if not pending.zip_path.is_file():
        raise ValueError("pending_update_zip_missing")
    validate_pending_update_paths(pending, pending_path=path)
    return pending


def validate_pending_update_paths(
    pending: PendingUpdate,
    *,
    pending_path: Path | None = None,
) -> None:
    """驗證 pending update 的路徑仍落在 updater 可接受的安全邊界內。"""

    sanitize_release_asset_name(pending.version)
    sanitize_release_asset_name(pending.asset_name)
    app_base_dir = pending.app_base_dir.resolve()
    data_dir = pending.data_dir.resolve()
    runtime_dir = pending.runtime_dir.resolve()
    zip_path = pending.zip_path.resolve()
    updates_dir = data_dir / "updates"
    profiles_dir = data_dir / "profiles"

    if is_dangerous_root(app_base_dir) or is_dangerous_root(data_dir):
        raise ValueError("pending_update_path_dangerous")
    if app_base_dir == data_dir:
        raise ValueError("pending_update_app_data_overlap")
    if data_dir.is_relative_to(app_base_dir) and data_dir != app_base_dir / "data":
        raise ValueError("pending_update_data_dir_must_be_app_data")
    if runtime_dir != data_dir / "runtime":
        raise ValueError("pending_update_runtime_dir_mismatch")
    if not zip_path.is_relative_to(updates_dir):
        raise ValueError("pending_update_zip_outside_updates_dir")
    if not pending.db_path.resolve().is_relative_to(data_dir):
        raise ValueError("pending_update_db_outside_data_dir")
    if not pending.profile_dir.resolve().is_relative_to(profiles_dir):
        raise ValueError("pending_update_profile_outside_profiles_dir")
    logs_dir = pending.logs_dir.resolve()
    if logs_dir == app_base_dir:
        raise ValueError("pending_update_logs_dir_unsafe")
    if logs_dir.is_relative_to(app_base_dir) and not logs_dir.is_relative_to(data_dir):
        raise ValueError("pending_update_logs_dir_unsafe")
    if pending_path is not None:
        expected_pending_path = pending_update_path(runtime_dir).resolve()
        if pending_path.resolve() != expected_pending_path:
            raise ValueError("pending_update_path_mismatch")


def _pending_to_json_dict(pending: PendingUpdate) -> dict[str, Any]:
    """將 dataclass 轉成 JSON-safe dict。"""

    payload = asdict(pending)
    for key in (
        "zip_path",
        "app_base_dir",
        "data_dir",
        "db_path",
        "profile_dir",
        "logs_dir",
        "runtime_dir",
    ):
        payload[key] = str(payload[key])
    return payload


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
