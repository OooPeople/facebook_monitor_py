"""Pending update codec 與 validation 邊界測試。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from facebook_monitor.updates.download import VERIFIED_DOWNLOAD_SET_MARKER_NAME
from facebook_monitor.updates.download import VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION
from facebook_monitor.updates.pending_update_codec import pending_update_from_payload
from facebook_monitor.updates.pending_update_models import PendingUpdate
from facebook_monitor.updates.pending_update_models import pending_update_path
from facebook_monitor.updates.pending_update_validation import (
    validate_pending_update_artifact_set,
    validate_pending_update_paths,
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


def test_pending_update_integrity_rejects_missing_zip(tmp_path: Path) -> None:
    """pending update 指向的 zip 必須仍存在。"""

    pending = _pending_update_for_integrity(tmp_path)
    pending.zip_path.unlink()

    with pytest.raises(ValueError, match="pending_update_zip_missing"):
        validate_pending_update_payload_integrity(pending)


def test_pending_update_integrity_rejects_empty_manifest_sha(
    tmp_path: Path,
) -> None:
    """manifest SHA 不可為空，避免 signed manifest 驗證結果遺失。"""

    pending = _pending_update_for_integrity(tmp_path)

    with pytest.raises(ValueError, match="pending_update_manifest_missing"):
        validate_pending_update_payload_integrity(replace(pending, manifest_sha256=""))


def test_pending_update_integrity_rejects_missing_manifest_file(
    tmp_path: Path,
) -> None:
    """pending update 指向的 signed manifest 必須仍存在。"""

    pending = _pending_update_for_integrity(tmp_path)
    assert pending.manifest_path is not None
    pending.manifest_path.unlink()

    with pytest.raises(ValueError, match="pending_update_manifest_missing"):
        validate_pending_update_payload_integrity(pending)


def test_pending_update_integrity_rejects_missing_signature_file(
    tmp_path: Path,
) -> None:
    """pending update 指向的 manifest signature 必須仍存在。"""

    pending = _pending_update_for_integrity(tmp_path)
    assert pending.manifest_signature_path is not None
    pending.manifest_signature_path.unlink()

    with pytest.raises(
        ValueError,
        match="pending_update_manifest_signature_missing",
    ):
        validate_pending_update_payload_integrity(pending)


def test_pending_update_paths_reject_invalid_repository(tmp_path: Path) -> None:
    """pending repository 必須保留 owner/repo 形式，避免信任邊界漂移。"""

    pending = _pending_update_for_integrity(tmp_path)

    with pytest.raises(ValueError, match="pending_update_repository_invalid"):
        validate_pending_update_paths(replace(pending, repository="facebook_monitor_py"))


def test_pending_update_paths_reject_runtime_dir_mismatch(tmp_path: Path) -> None:
    """runtime dir 必須固定在 data/runtime，避免 pending 指向任意操作目錄。"""

    pending = _pending_update_for_integrity(tmp_path)

    with pytest.raises(ValueError, match="pending_update_runtime_dir_mismatch"):
        validate_pending_update_paths(
            replace(pending, runtime_dir=pending.data_dir / "other-runtime")
        )


def test_pending_update_paths_reject_app_data_overlap(tmp_path: Path) -> None:
    """app 與 data dir 不可重疊，避免 updater 清理自己的持久資料。"""

    pending = _pending_update_for_integrity(tmp_path)

    with pytest.raises(ValueError, match="pending_update_app_data_overlap"):
        validate_pending_update_paths(
            replace(
                pending,
                data_dir=pending.app_base_dir,
                runtime_dir=pending.app_base_dir / "runtime",
            )
        )


def test_pending_update_paths_reject_manifest_outside_updates_dir(
    tmp_path: Path,
) -> None:
    """signed manifest 不可位於 data/updates 以外。"""

    pending = _pending_update_for_integrity(tmp_path)
    outside_manifest = tmp_path / "manifest.json"

    with pytest.raises(ValueError, match="pending_update_manifest_outside_updates_dir"):
        validate_pending_update_paths(replace(pending, manifest_path=outside_manifest))


def test_pending_update_paths_reject_signature_outside_updates_dir(
    tmp_path: Path,
) -> None:
    """manifest signature 不可位於 data/updates 以外。"""

    pending = _pending_update_for_integrity(tmp_path)
    outside_signature = tmp_path / "manifest.json.sig"

    with pytest.raises(
        ValueError,
        match="pending_update_manifest_signature_outside_updates_dir",
    ):
        validate_pending_update_paths(
            replace(pending, manifest_signature_path=outside_signature)
        )


def test_pending_update_paths_reject_pending_file_path_mismatch(
    tmp_path: Path,
) -> None:
    """呼叫端讀到的 pending 檔必須是該 runtime dir 的正式 pending path。"""

    pending = _pending_update_for_integrity(tmp_path)

    with pytest.raises(ValueError, match="pending_update_path_mismatch"):
        validate_pending_update_paths(
            pending,
            pending_path=pending_update_path(pending.runtime_dir.parent) / "other.json",
        )


def test_pending_update_paths_reject_db_outside_data_dir(tmp_path: Path) -> None:
    """pending DB path 不可逃出 data dir。"""

    pending = _pending_update_for_integrity(tmp_path)

    with pytest.raises(ValueError, match="pending_update_db_outside_data_dir"):
        validate_pending_update_paths(
            replace(pending, db_path=tmp_path / "outside.db")
        )


def test_pending_update_paths_reject_profile_outside_profiles_dir(
    tmp_path: Path,
) -> None:
    """profile dir 必須留在 data/profiles 底下。"""

    pending = _pending_update_for_integrity(tmp_path)

    with pytest.raises(ValueError, match="pending_update_profile_outside_profiles_dir"):
        validate_pending_update_paths(
            replace(pending, profile_dir=pending.data_dir / "not-profiles")
        )


def test_pending_update_paths_reject_logs_dir_equal_to_app_root(
    tmp_path: Path,
) -> None:
    """logs dir 不可等於 app root，避免 updater 誤把程式根目錄當 logs。"""

    pending = _pending_update_for_integrity(tmp_path)

    with pytest.raises(ValueError, match="pending_update_logs_dir_unsafe"):
        validate_pending_update_paths(replace(pending, logs_dir=pending.app_base_dir))


def test_pending_update_paths_accept_expected_pending_file_path(
    tmp_path: Path,
) -> None:
    """正常 pending 檔路徑要通過 path trust-boundary 檢查。"""

    pending = _pending_update_for_integrity(tmp_path)

    validate_pending_update_paths(
        pending,
        pending_path=pending_update_path(pending.runtime_dir),
    )


def test_pending_update_artifact_set_accepts_verified_attempt_set(
    tmp_path: Path,
) -> None:
    """完整 verified attempt artifact set 應通過 pending atomic set 檢查。"""

    pending = _pending_update_for_integrity(tmp_path)
    _write_verified_artifact_set(pending)

    validate_pending_update_artifact_set(pending)


def test_pending_update_artifact_set_rejects_loose_version_dir_zip(
    tmp_path: Path,
) -> None:
    """zip 不能直接放在 updates/<version>，必須屬於 attempt set。"""

    pending = _pending_update_for_integrity(tmp_path)
    loose_dir = pending.data_dir / "updates" / pending.version
    loose_dir.mkdir(parents=True, exist_ok=True)
    loose_zip = loose_dir / pending.zip_path.name
    loose_zip.write_bytes(pending.zip_path.read_bytes())

    with pytest.raises(ValueError, match="pending_update_artifact_set_invalid"):
        validate_pending_update_artifact_set(replace(pending, zip_path=loose_zip))


def test_pending_update_artifact_set_rejects_non_attempt_set_name(
    tmp_path: Path,
) -> None:
    """verified set 目錄名稱必須以 attempt- 開頭。"""

    pending = _pending_update_for_integrity(tmp_path)
    bad_dir = pending.zip_path.parent.with_name("manual")
    bad_dir.mkdir(parents=True)
    bad_zip = bad_dir / pending.zip_path.name
    bad_zip.write_bytes(pending.zip_path.read_bytes())

    with pytest.raises(ValueError, match="pending_update_artifact_set_invalid"):
        validate_pending_update_artifact_set(replace(pending, zip_path=bad_zip))


def test_pending_update_artifact_set_rejects_missing_sha256_sidecar(
    tmp_path: Path,
) -> None:
    """verified set 缺少 zip SHA256 sidecar 時不可套用。"""

    pending = _pending_update_for_integrity(tmp_path)
    _write_verified_artifact_set(pending)
    pending.zip_path.with_name(pending.zip_path.name + ".sha256").unlink()

    with pytest.raises(ValueError, match="pending_update_sha256_missing"):
        validate_pending_update_artifact_set(pending)


def test_pending_update_artifact_set_rejects_missing_verified_marker(
    tmp_path: Path,
) -> None:
    """verified set marker 是 atomic artifact set 完成的必要證據。"""

    pending = _pending_update_for_integrity(tmp_path)
    marker_path = _write_verified_artifact_set(pending)
    marker_path.unlink()

    with pytest.raises(ValueError, match="pending_update_verified_set_missing"):
        validate_pending_update_artifact_set(pending)


def test_pending_update_artifact_set_rejects_reparse_attempt_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """attempt set dir 若是 symlink/junction 類 reparse point，不可套用。"""

    pending = _pending_update_for_integrity(tmp_path)
    _write_verified_artifact_set(pending)
    set_dir = pending.zip_path.resolve().parent

    monkeypatch.setattr(
        "facebook_monitor.updates.pending_update_validation.is_reparse_or_symlink",
        lambda path: path == set_dir,
    )

    with pytest.raises(ValueError, match="pending_update_artifact_set_unsafe"):
        validate_pending_update_artifact_set(pending)


def test_pending_update_artifact_set_rejects_manifest_from_other_set(
    tmp_path: Path,
) -> None:
    """manifest 與 zip 不在同一 attempt set 時不可套用。"""

    pending = _pending_update_for_integrity(tmp_path)
    assert pending.manifest_path is not None
    _write_verified_artifact_set(pending)
    other_dir = pending.zip_path.parent.parent / "attempt-other"
    other_dir.mkdir()
    other_manifest = other_dir / pending.manifest_path.name
    other_manifest.write_bytes(pending.manifest_path.read_bytes())

    with pytest.raises(ValueError, match="download_result_verified_set_mismatch"):
        validate_pending_update_artifact_set(
            replace(pending, manifest_path=other_manifest)
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


def _write_verified_artifact_set(pending: PendingUpdate) -> Path:
    """補齊 validate_pending_update_artifact_set 需要的 sidecar 與 marker。"""

    assert pending.manifest_path is not None
    assert pending.manifest_signature_path is not None
    sha256_path = pending.zip_path.with_name(pending.zip_path.name + ".sha256")
    sha256_path.write_text(
        f"{pending.actual_sha256}  {pending.zip_path.name}\n",
        encoding="utf-8",
    )
    marker_path = pending.zip_path.parent / VERIFIED_DOWNLOAD_SET_MARKER_NAME
    marker_path.write_text(
        json.dumps(
            {
                "schema_version": VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION,
                "asset_name": pending.zip_path.name,
                "asset_sha256": pending.actual_sha256,
                "asset_size": pending.zip_path.stat().st_size,
                "sha256_name": sha256_path.name,
                "sha256_sha256": hashlib.sha256(sha256_path.read_bytes()).hexdigest(),
                "manifest_name": pending.manifest_path.name,
                "manifest_sha256": pending.manifest_sha256,
                "manifest_key_id": pending.manifest_key_id,
                "manifest_signature_name": pending.manifest_signature_path.name,
                "manifest_signature_sha256": hashlib.sha256(
                    pending.manifest_signature_path.read_bytes()
                ).hexdigest(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return marker_path
