"""Verified download set marker validation tests。"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from facebook_monitor.updates.download_models import UpdateDownloadResult
from facebook_monitor.updates.download_models import VERIFIED_DOWNLOAD_SET_MARKER_NAME
from facebook_monitor.updates.download_models import (
    VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION,
)
from facebook_monitor.updates.download_publish import load_verified_download_set_marker
from facebook_monitor.updates.download_publish import validate_verified_download_set


def test_validate_verified_download_set_rejects_unverified_result(
    tmp_path: Path,
) -> None:
    """未標示 verified 的 download result 不可被當成可套用 artifact set。"""

    result = _verified_result(tmp_path)

    with pytest.raises(ValueError, match="download_result_not_verified"):
        validate_verified_download_set(replace(result, verified=False))


@pytest.mark.parametrize("payload", [[], {"schema_version": 999}])
def test_load_verified_download_set_marker_rejects_invalid_payload(
    tmp_path: Path,
    payload: object,
) -> None:
    """verified set marker 必須是目前 schema 的 JSON object。"""

    marker_path = tmp_path / VERIFIED_DOWNLOAD_SET_MARKER_NAME
    marker_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="download_result_verified_set_invalid"):
        load_verified_download_set_marker(marker_path)


def test_validate_verified_download_set_rejects_asset_hash_mismatch(
    tmp_path: Path,
) -> None:
    """marker 內的 asset hash 若和 result/檔案不一致，不可通過驗證。"""

    result = _verified_result(tmp_path)
    _rewrite_marker(result, {"asset_sha256": "b" * 64})

    with pytest.raises(ValueError, match="download_result_verified_set_mismatch"):
        validate_verified_download_set(result)


def test_validate_verified_download_set_rejects_signature_hash_mismatch(
    tmp_path: Path,
) -> None:
    """manifest signature hash 改變時，verified set 必須失效。"""

    result = _verified_result(tmp_path)
    assert result.manifest_signature_path is not None
    result.manifest_signature_path.write_bytes(b"changed-signature")

    with pytest.raises(ValueError, match="download_result_verified_set_mismatch"):
        validate_verified_download_set(result)


def test_validate_verified_download_set_rejects_sidecar_marker_without_sidecar(
    tmp_path: Path,
) -> None:
    """result 沒有 sidecar path 時，marker 不可宣稱存在 sidecar。"""

    result = _verified_result(tmp_path)

    with pytest.raises(ValueError, match="download_result_verified_set_mismatch"):
        validate_verified_download_set(replace(result, sha256_path=None))


def _verified_result(tmp_path: Path) -> UpdateDownloadResult:
    """建立最小 verified download set 與 marker fixture。"""

    set_dir = tmp_path / "updates" / "0.1.0" / "attempt-test"
    set_dir.mkdir(parents=True)
    file_path = set_dir / "app.zip"
    sha256_path = set_dir / "app.zip.sha256"
    manifest_path = set_dir / "manifest.json"
    signature_path = set_dir / "manifest.json.sig"
    file_path.write_bytes(b"zip")
    sha256_path.write_text(
        f"{hashlib.sha256(b'zip').hexdigest()}  app.zip\n",
        encoding="utf-8",
    )
    manifest_path.write_bytes(b"manifest")
    signature_path.write_bytes(b"signature")
    result = UpdateDownloadResult(
        status="verified",
        downloaded=True,
        verified=True,
        file_path=file_path,
        sha256_path=sha256_path,
        expected_sha256=hashlib.sha256(b"zip").hexdigest(),
        actual_sha256=hashlib.sha256(b"zip").hexdigest(),
        failure_reason="",
        manifest_path=manifest_path,
        manifest_signature_path=signature_path,
        manifest_sha256=hashlib.sha256(b"manifest").hexdigest(),
        manifest_key_id="test-key",
        verified_set_marker_path=set_dir / VERIFIED_DOWNLOAD_SET_MARKER_NAME,
    )
    _rewrite_marker(result, {})
    return result


def _rewrite_marker(
    result: UpdateDownloadResult,
    overrides: dict[str, object],
) -> None:
    """寫入 verified set marker，允許測試覆寫單一欄位。"""

    assert result.file_path is not None
    assert result.sha256_path is not None
    assert result.manifest_path is not None
    assert result.manifest_signature_path is not None
    assert result.verified_set_marker_path is not None
    payload = {
        "schema_version": VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION,
        "asset_name": result.file_path.name,
        "asset_sha256": result.actual_sha256,
        "asset_size": result.file_path.stat().st_size,
        "sha256_name": result.sha256_path.name,
        "sha256_sha256": hashlib.sha256(result.sha256_path.read_bytes()).hexdigest(),
        "manifest_name": result.manifest_path.name,
        "manifest_sha256": result.manifest_sha256,
        "manifest_key_id": result.manifest_key_id,
        "manifest_signature_name": result.manifest_signature_path.name,
        "manifest_signature_sha256": hashlib.sha256(
            result.manifest_signature_path.read_bytes()
        ).hexdigest(),
    }
    payload.update(overrides)
    result.verified_set_marker_path.write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )
