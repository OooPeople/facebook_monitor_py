"""獨立 updater 套用流程測試。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from facebook_monitor.updates import apply as updater_apply
from facebook_monitor.updates.apply import apply_pending_update
from facebook_monitor.updates.platforms import MACOS_ARM64_LAYOUT_POLICY
from tests.updates.apply_test_helpers import make_macos_app_root
from tests.updates.apply_test_helpers import make_macos_update_zip
from tests.updates.apply_test_helpers import make_update_zip
from tests.updates.apply_test_helpers import pending_update


def test_apply_pending_update_rejects_asset_policy_mismatched_to_app_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pending asset platform 必須與目前 app layout policy 一致。"""

    monkeypatch.setattr(
        updater_apply,
        "detect_layout_policy",
        lambda app_base_dir: MACOS_ARM64_LAYOUT_POLICY,
    )
    zip_path = tmp_path / "app" / "data" / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert not result.applied
    assert result.message == "pending_update_artifact_platform_mismatch"


def test_apply_pending_update_refuses_symlinked_staging_dir(tmp_path: Path) -> None:
    """staging dir 若被 symlink 到外部，updater 不可 follow 後刪除 target。"""

    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_macos_update_zip(zip_path, app_text="new")
    outside = tmp_path / "outside"
    outside.mkdir()
    keep = outside / "keep.txt"
    keep.write_text("do not delete", encoding="utf-8")
    staging_dir = data_dir / "runtime" / "update_staging" / "0.1.0"
    staging_dir.parent.mkdir(parents=True)
    try:
        staging_dir.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        return

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message == "update_work_dir_unsafe"
    assert keep.read_text(encoding="utf-8") == "do not delete"
    assert (app_root / "facebook-monitor").read_bytes().endswith(b"old")


def test_apply_pending_update_refuses_symlinked_staging_parent(tmp_path: Path) -> None:
    """staging parent 若是 symlink，updater 不可 follow 後寫入外部目錄。"""

    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_macos_update_zip(zip_path, app_text="new")
    outside = tmp_path / "outside"
    outside.mkdir()
    staging_parent = data_dir / "runtime" / "update_staging"
    staging_parent.parent.mkdir(parents=True)
    try:
        staging_parent.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        return

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message == "update_work_dir_unsafe"
    assert list(outside.iterdir()) == []
    assert (app_root / "facebook-monitor").read_bytes().endswith(b"old")


def test_apply_pending_update_refuses_symlinked_backup_parent(tmp_path: Path) -> None:
    """backup parent 若是 symlink，updater 不可 follow 後寫入外部目錄。"""

    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_macos_update_zip(zip_path, app_text="new")
    outside = tmp_path / "outside"
    outside.mkdir()
    backup_parent = data_dir / "runtime" / "update_backups"
    backup_parent.parent.mkdir(parents=True)
    try:
        backup_parent.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        return

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message == "update_work_dir_unsafe"
    assert list(outside.iterdir()) == []
    assert (app_root / "facebook-monitor").read_bytes().endswith(b"old")


def test_apply_pending_update_rejects_current_symlink_to_data(tmp_path: Path) -> None:
    """目前 app root 內的 symlink 不可指向 preserved data/profile 路徑。"""

    if os.name == "nt":
        return
    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    (data_dir / "profiles").mkdir(parents=True)
    (app_root / "profile-link").symlink_to("data/profiles")
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_macos_update_zip(zip_path, app_text="new")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message.startswith("app_path_unsafe:")
    assert (app_root / "facebook-monitor").read_bytes().endswith(b"old")
