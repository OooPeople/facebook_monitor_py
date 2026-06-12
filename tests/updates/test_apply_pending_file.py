"""獨立 updater 套用流程測試。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from facebook_monitor.updates import apply as updater_apply
from facebook_monitor.updates.apply import apply_loaded_pending_update_file
from facebook_monitor.updates.apply import apply_pending_update
from facebook_monitor.updates.apply import apply_pending_update_file
from facebook_monitor.updates.download import VERIFIED_DOWNLOAD_SET_MARKER_NAME
from tests.updates.apply_test_helpers import make_app_root
from tests.updates.apply_test_helpers import make_update_zip
from tests.updates.apply_test_helpers import pending_file_payload
from tests.updates.apply_test_helpers import pending_update
from tests.updates.apply_test_helpers import TEST_VERSION


def test_apply_pending_update_rejects_pending_version_not_newer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """apply 階段不可套用已經不高於目前版本的 pending update。"""

    monkeypatch.setattr(updater_apply, "APP_VERSION", TEST_VERSION)
    zip_path = tmp_path / "app" / "data" / "updates" / "0.1.0" / "attempt-test" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert not result.applied
    assert result.message == "pending_update_not_newer"


def test_apply_pending_update_rejects_loose_artifact_set(tmp_path: Path) -> None:
    """direct apply API 也不可消費 updates/<version> 下的 loose artifact。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    zip_path = tmp_path / "app" / "data" / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert not result.applied
    assert result.message == "pending_update_artifact_set_invalid"


def test_apply_pending_update_file_writes_result_log(tmp_path: Path) -> None:
    """updater CLI path 會把套用結果寫進 updater log。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    data_dir = app_root / "data"
    runtime_dir = data_dir / "runtime"
    runtime_dir.mkdir(parents=True)
    zip_path = data_dir / "updates" / "0.1.0" / "attempt-test" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    pending = pending_update(tmp_path, zip_path=zip_path, digest=digest)
    pending_path = runtime_dir / "pending_update.json"
    pending_path.write_text(
        json.dumps(pending_file_payload(pending)),
        encoding="utf-8",
    )
    log_path = data_dir / "logs" / "updater.log"

    result = apply_pending_update_file(pending_path, log_path=log_path)

    assert result.status == "applied"
    assert "status=applied applied=true message=updated" in log_path.read_text(encoding="utf-8")
    assert not pending_path.exists()
    assert not zip_path.exists()
    assert not zip_path.with_name(zip_path.name + ".sha256").exists()


def test_apply_pending_update_file_removes_verified_sha256_asset(tmp_path: Path) -> None:
    """成功套用後會移除本次下載的 zip 與 `.sha256`，避免更新檔長期殘留。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    data_dir = app_root / "data"
    runtime_dir = data_dir / "runtime"
    runtime_dir.mkdir(parents=True)
    zip_path = data_dir / "updates" / "0.1.0" / "attempt-test" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}\n",
        encoding="utf-8",
    )
    marker_path = zip_path.parent / VERIFIED_DOWNLOAD_SET_MARKER_NAME
    marker_path.write_text("{}", encoding="utf-8")
    pending = pending_update(tmp_path, zip_path=zip_path, digest=digest)
    pending_path = runtime_dir / "pending_update.json"
    pending_path.write_text(
        json.dumps(pending_file_payload(pending)),
        encoding="utf-8",
    )

    result = apply_pending_update_file(pending_path)

    assert result.applied
    assert not zip_path.exists()
    assert not zip_path.with_name(zip_path.name + ".sha256").exists()
    assert not marker_path.exists()
    assert not pending_path.exists()


def test_apply_pending_update_file_prunes_old_backups(tmp_path: Path) -> None:
    """成功套用後只保留本次 backup，避免舊備份無限制累積。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    data_dir = app_root / "data"
    runtime_dir = data_dir / "runtime"
    backup_root = runtime_dir / "update_backups"
    runtime_dir.mkdir(parents=True)
    for index in range(5):
        old_backup = backup_root / (f"0.1.0-20260517T00000{index}000000Z-deadbee{index}")
        old_backup.mkdir(parents=True)
        (old_backup / "marker.txt").write_text(str(index), encoding="utf-8")
    zip_path = data_dir / "updates" / "0.1.0" / "attempt-test" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    pending = pending_update(tmp_path, zip_path=zip_path, digest=digest)
    pending_path = runtime_dir / "pending_update.json"
    pending_path.write_text(
        json.dumps(pending_file_payload(pending)),
        encoding="utf-8",
    )

    result = apply_pending_update_file(pending_path)

    assert result.applied
    assert result.backup_dir is not None
    retained = {path.name for path in backup_root.iterdir() if path.is_dir()}
    assert retained == {result.backup_dir.name}


def test_apply_loaded_pending_update_file_rejects_stale_handoff_after_cleanup(
    tmp_path: Path,
) -> None:
    """第二個已讀取舊 pending 的 updater 不可在 cleanup 後重複套用同一包。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    data_dir = app_root / "data"
    runtime_dir = data_dir / "runtime"
    runtime_dir.mkdir(parents=True)
    zip_path = data_dir / "updates" / "0.1.0" / "attempt-test" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    pending = pending_update(tmp_path, zip_path=zip_path, digest=digest)
    pending_path = runtime_dir / "pending_update.json"
    pending_path.write_text(
        json.dumps(pending_file_payload(pending)),
        encoding="utf-8",
    )

    first = apply_loaded_pending_update_file(pending, pending_path)
    second = apply_loaded_pending_update_file(pending, pending_path)

    assert first.applied
    assert second.status == "pending_update_already_applied"
    assert not second.applied
    backup_root = runtime_dir / "update_backups"
    assert len([path for path in backup_root.iterdir() if path.is_dir()]) == 1
def test_apply_loaded_pending_update_file_consumed_marker_blocks_cleanup_failure_reapply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """即使 cleanup 沒刪掉 pending，成功套用 marker 也要阻止第二個 updater 重跑。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    data_dir = app_root / "data"
    runtime_dir = data_dir / "runtime"
    runtime_dir.mkdir(parents=True)
    zip_path = data_dir / "updates" / "0.1.0" / "attempt-test" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    pending = pending_update(tmp_path, zip_path=zip_path, digest=digest)
    pending_path = runtime_dir / "pending_update.json"
    pending_path.write_text(
        json.dumps(pending_file_payload(pending)),
        encoding="utf-8",
    )

    def fake_cleanup_applied_update(*args, **kwargs) -> tuple[str, ...]:
        return ("pending:EACCES",)

    monkeypatch.setattr(
        "facebook_monitor.updates.apply._cleanup_applied_update",
        fake_cleanup_applied_update,
    )

    first = apply_loaded_pending_update_file(pending, pending_path)
    second = apply_loaded_pending_update_file(pending, pending_path)

    assert first.applied
    assert pending_path.exists()
    assert second.status == "pending_update_already_applied"
    assert not second.applied
    backup_root = runtime_dir / "update_backups"
    assert len([path for path in backup_root.iterdir() if path.is_dir()]) == 1


def test_apply_loaded_pending_update_file_consumed_fallback_blocks_marker_failure_reapply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """consumed marker 寫入失敗時，覆寫 pending 的 fallback 仍要阻止重跑。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    data_dir = app_root / "data"
    runtime_dir = data_dir / "runtime"
    runtime_dir.mkdir(parents=True)
    zip_path = data_dir / "updates" / "0.1.0" / "attempt-test" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    pending = pending_update(tmp_path, zip_path=zip_path, digest=digest)
    pending_path = runtime_dir / "pending_update.json"
    pending_path.write_text(
        json.dumps(pending_file_payload(pending)),
        encoding="utf-8",
    )
    blocked_parent = tmp_path / "blocked-marker-parent"
    blocked_parent.write_text("not a directory", encoding="utf-8")

    def fake_cleanup_applied_update(*args, **kwargs) -> tuple[str, ...]:
        return ("pending:EACCES",)

    monkeypatch.setattr(
        "facebook_monitor.updates.apply._cleanup_applied_update",
        fake_cleanup_applied_update,
    )
    monkeypatch.setattr(
        "facebook_monitor.updates.apply._consumed_pending_update_marker_path",
        lambda path: blocked_parent / "pending_update.applied.json",
    )

    first = apply_loaded_pending_update_file(pending, pending_path)
    second = apply_loaded_pending_update_file(pending, pending_path)

    consumed_payload = json.loads(pending_path.read_text(encoding="utf-8"))
    assert first.applied
    assert consumed_payload["handoff_consumed"] is True
    assert second.status == "pending_update_already_applied"
    assert not second.applied
    backup_root = runtime_dir / "update_backups"
    assert len([path for path in backup_root.iterdir() if path.is_dir()]) == 1
