"""Pending update codec 與 validation 邊界測試。"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from facebook_monitor.updates.pending_update_codec import pending_update_from_payload
from facebook_monitor.updates.pending_update_models import PendingUpdate
from facebook_monitor.updates.pending_update_validation import (
    validate_pending_update_payload_integrity,
)


def test_pending_update_codec_rejects_invalid_payload_type() -> None:
    """pending JSON root 必須是 object。"""

    with pytest.raises(ValueError, match="pending_update_invalid"):
        pending_update_from_payload([])


def test_pending_update_codec_rejects_unsupported_schema() -> None:
    """schema version 不符時要保留既有錯誤字串。"""

    with pytest.raises(ValueError, match="pending_update_schema_unsupported"):
        pending_update_from_payload({"schema_version": 999})


def test_pending_update_codec_rejects_missing_required_field() -> None:
    """缺少必要欄位時要回報 missing field，不暴露 KeyError。"""

    with pytest.raises(ValueError, match="pending_update_missing_field"):
        pending_update_from_payload({"schema_version": 1})


def test_pending_update_integrity_rejects_sha_mismatch(tmp_path: Path) -> None:
    """expected/actual SHA 不一致時必須先拒絕。"""

    pending = _pending_update_for_integrity(tmp_path)

    with pytest.raises(ValueError, match="pending_update_sha256_mismatch"):
        validate_pending_update_payload_integrity(
            replace(pending, actual_sha256="b" * 64)
        )


def test_pending_update_integrity_rejects_invalid_sha(tmp_path: Path) -> None:
    """asset SHA 格式必須是 64 位十六進位。"""

    pending = _pending_update_for_integrity(tmp_path)

    with pytest.raises(ValueError, match="pending_update_sha256_invalid"):
        validate_pending_update_payload_integrity(
            replace(
                pending,
                expected_sha256="not-a-sha",
                actual_sha256="not-a-sha",
            )
        )


def test_pending_update_integrity_rejects_invalid_manifest_sha(
    tmp_path: Path,
) -> None:
    """manifest SHA 格式錯誤時不可進入後續檔案驗證。"""

    pending = _pending_update_for_integrity(tmp_path)

    with pytest.raises(ValueError, match="pending_update_manifest_sha256_invalid"):
        validate_pending_update_payload_integrity(
            replace(pending, manifest_sha256="not-a-sha")
        )


def test_pending_update_integrity_rejects_missing_manifest_key(
    tmp_path: Path,
) -> None:
    """manifest key id 是 updater trust boundary 的必要欄位。"""

    pending = _pending_update_for_integrity(tmp_path)

    with pytest.raises(ValueError, match="pending_update_manifest_key_missing"):
        validate_pending_update_payload_integrity(
            replace(pending, manifest_key_id="")
        )


def _pending_update_for_integrity(tmp_path: Path) -> PendingUpdate:
    """建立通過 payload integrity 檢查所需的最小 pending update。"""

    data_dir = tmp_path / "data"
    app_dir = tmp_path / "app"
    artifact_dir = data_dir / "updates" / "0.1.0" / "attempt-test"
    artifact_dir.mkdir(parents=True)
    zip_path = artifact_dir / "app.zip"
    manifest_path = artifact_dir / "manifest.json"
    signature_path = artifact_dir / "manifest.json.sig"
    zip_path.write_bytes(b"zip")
    manifest_path.write_text("manifest", encoding="utf-8")
    signature_path.write_text("sig", encoding="utf-8")
    zip_sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    return PendingUpdate(
        schema_version=1,
        version="0.1.0",
        repository="OooPeople/facebook_monitor_py",
        asset_name=zip_path.name,
        zip_path=zip_path,
        expected_sha256=zip_sha,
        actual_sha256=zip_sha,
        app_base_dir=app_dir,
        data_dir=data_dir,
        db_path=data_dir / "app.db",
        profile_dir=data_dir / "profiles" / "automation_default",
        logs_dir=data_dir / "logs",
        runtime_dir=data_dir / "runtime",
        created_at="2026-05-17T00:00:00+00:00",
        manifest_path=manifest_path,
        manifest_signature_path=signature_path,
        manifest_sha256=manifest_sha,
        manifest_key_id="test-key",
    )
