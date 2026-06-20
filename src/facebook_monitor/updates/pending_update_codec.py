"""Pending update JSON codec。

職責：在 trusted model 與 JSON-safe payload 間轉換，不碰檔案 IO。
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from facebook_monitor.updates.pending_update_models import PENDING_UPDATE_SCHEMA_VERSION
from facebook_monitor.updates.pending_update_models import PendingUpdate


def pending_update_to_json_dict(pending: PendingUpdate) -> dict[str, Any]:
    """將 pending update dataclass 轉成 JSON-safe dict。"""

    payload = asdict(pending)
    for key in (
        "zip_path",
        "app_base_dir",
        "data_dir",
        "db_path",
        "profile_dir",
        "logs_dir",
        "runtime_dir",
        "manifest_path",
        "manifest_signature_path",
    ):
        payload[key] = str(payload[key]) if payload[key] is not None else None
    return payload


def pending_update_from_payload(payload: object) -> PendingUpdate:
    """將 JSON payload 轉成 pending update model，保留 schema/欄位錯誤語義。"""

    if not isinstance(payload, dict):
        raise ValueError("pending_update_invalid")
    schema_version = int(payload.get("schema_version", 0))
    if schema_version != PENDING_UPDATE_SCHEMA_VERSION:
        raise ValueError("pending_update_schema_unsupported")
    try:
        return PendingUpdate(
            schema_version=schema_version,
            version=str(payload["version"]),
            repository=str(payload["repository"]),
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
            manifest_path=optional_payload_path(payload.get("manifest_path")),
            manifest_signature_path=optional_payload_path(
                payload.get("manifest_signature_path")
            ),
            manifest_sha256=str(payload.get("manifest_sha256", "")),
            manifest_key_id=str(payload.get("manifest_key_id", "")),
        )
    except KeyError as exc:
        raise ValueError("pending_update_missing_field") from exc


def optional_payload_path(value: object) -> Path | None:
    """讀取可為空的 pending update 路徑欄位。"""

    if value in (None, ""):
        return None
    return Path(str(value)).resolve()


__all__ = [
    "optional_payload_path",
    "pending_update_from_payload",
    "pending_update_to_json_dict",
]
