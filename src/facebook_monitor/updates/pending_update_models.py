"""Pending update handoff models。

職責：定義主程式與獨立 updater 共用的 pending update schema 與路徑。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PENDING_UPDATE_FILE_NAME = "pending_update.json"
PENDING_UPDATE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PendingUpdate:
    """獨立 updater 套用更新需要的最小資訊。"""

    schema_version: int
    version: str
    repository: str
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
    manifest_path: Path | None = None
    manifest_signature_path: Path | None = None
    manifest_sha256: str = ""
    manifest_key_id: str = ""


def pending_update_path(runtime_dir: Path) -> Path:
    """回傳 runtime dir 底下的 pending update 檔案路徑。"""

    return runtime_dir / PENDING_UPDATE_FILE_NAME


__all__ = [
    "PENDING_UPDATE_FILE_NAME",
    "PENDING_UPDATE_SCHEMA_VERSION",
    "PendingUpdate",
    "pending_update_path",
]
