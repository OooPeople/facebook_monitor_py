"""獨立 updater 套用流程測試。"""

from __future__ import annotations

from pathlib import Path


from facebook_monitor.runtime.instance_lock import acquire_app_instance_lock
from facebook_monitor.updates import apply_replacement as updater_apply_replacement
from facebook_monitor.updates.apply import apply_pending_update
from tests.helpers.macos_bundle import MACHO_ARM64_BYTES
from tests.updates.apply_test_helpers import make_app_root
from tests.updates.apply_test_helpers import make_macos_app_root
from tests.updates.apply_test_helpers import make_macos_update_zip
from tests.updates.apply_test_helpers import make_update_zip
from tests.updates.apply_test_helpers import pending_update


def test_apply_pending_update_replaces_app_files_but_preserves_data(tmp_path: Path) -> None:
    """updater 會替換 app files，並保留 portable data dir。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    (data_dir / "app.db").write_text("user data", encoding="utf-8")
    zip_path = tmp_path / "app" / "data" / "updates" / "0.1.0" / "attempt-test" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "applied"
    assert result.applied
    assert (app_root / "facebook-monitor.exe").read_text(encoding="utf-8") == "new"
    assert (data_dir / "app.db").read_text(encoding="utf-8") == "user data"
    assert result.backup_dir is not None
    assert (result.backup_dir / "facebook-monitor.exe").read_text(encoding="utf-8") == "old"


def test_apply_pending_update_refuses_when_app_lock_is_held(tmp_path: Path) -> None:
    """主程式仍持有 app lock 時，updater 不替換檔案。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    zip_path = tmp_path / "app" / "data" / "updates" / "0.1.0" / "attempt-test" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    pending = pending_update(tmp_path, zip_path=zip_path, digest=digest)

    with acquire_app_instance_lock(pending.runtime_dir, "test"):
        result = apply_pending_update(pending)

    assert result.status == "app_running"
    assert not result.applied
    assert (app_root / "facebook-monitor.exe").read_text(encoding="utf-8") == "old"


def test_apply_pending_update_restores_backup_when_replace_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """replace 中途失敗時應還原舊 app files 並保留 data。"""

    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    (data_dir / "app.db").write_text("user data", encoding="utf-8")
    zip_path = data_dir / "updates" / "0.1.0" / "attempt-test" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_macos_update_zip(zip_path, app_text="new")
    original_copy_path = updater_apply_replacement._copy_path

    def flaky_copy_path(source: Path, destination: Path, *, source_root: Path) -> None:
        if source.name == "facebook-monitor" and source.read_bytes().endswith(b"new"):
            raise OSError("copy failed")
        original_copy_path(source, destination, source_root=source_root)

    monkeypatch.setattr(updater_apply_replacement, "_copy_path", flaky_copy_path)

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message == "copy failed"
    assert (app_root / "facebook-monitor").read_bytes().endswith(b"old")
    assert (data_dir / "app.db").read_text(encoding="utf-8") == "user data"
    launcher = (
        app_root / "Facebook Monitor.app" / "Contents" / "MacOS" / "facebook-monitor-launcher"
    )
    assert launcher.read_bytes() == MACHO_ARM64_BYTES
