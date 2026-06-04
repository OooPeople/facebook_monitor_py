"""獨立 updater 套用流程測試。"""

from __future__ import annotations

import os
from pathlib import Path
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from facebook_monitor.updates.apply import apply_pending_update
from facebook_monitor.updates.apply import safe_extract_zip
from tests.helpers.macos_bundle import assert_posix_executable_when_supported
from tests.helpers.macos_bundle import assert_zip_member_executable
from tests.helpers.macos_bundle import write_path_to_zip_with_mode
from tests.helpers.macos_bundle import writestr_symlink


from tests.updates.apply_test_helpers import make_app_root
from tests.updates.apply_test_helpers import make_update_zip
from tests.updates.apply_test_helpers import pending_update

TEST_KEY_ID = "test-key"
TEST_PRIVATE_KEY = Ed25519PrivateKey.generate()
TEST_REPOSITORY = "OooPeople/facebook_monitor_py"
TEST_VERSION = "0.1.0"


def test_safe_extract_zip_rejects_path_traversal(tmp_path: Path) -> None:
    """zip 不能含有會逃出 staging dir 的 member path。"""

    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("../evil.txt", "bad")

    try:
        safe_extract_zip(zip_path, tmp_path / "staging")
    except ValueError as exc:
        assert str(exc) == "zip_member_path_unsafe"
    else:
        raise AssertionError("expected unsafe zip member to fail")


def test_safe_extract_zip_preserves_executable_bit(tmp_path: Path) -> None:
    """macOS updater 解壓 staging 時必須保留 executable bit。"""

    source = tmp_path / "source" / "facebook-monitor"
    source.parent.mkdir()
    source.write_text("app", encoding="utf-8")
    source.chmod(0o755)
    zip_path = tmp_path / "app.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        write_path_to_zip_with_mode(archive, source, "facebook-monitor/facebook-monitor", 0o755)
        assert_zip_member_executable(archive, "facebook-monitor/facebook-monitor")

    safe_extract_zip(zip_path, tmp_path / "staging")

    extracted = tmp_path / "staging" / "facebook-monitor" / "facebook-monitor"
    assert extracted.read_text(encoding="utf-8") == "app"
    assert_posix_executable_when_supported(extracted)


def test_safe_extract_zip_preserves_safe_symlink(tmp_path: Path) -> None:
    """POSIX zip symlink 若留在 staging tree 內，updater 會保留 symlink。"""

    if os.name == "nt":
        return
    zip_path = tmp_path / "app.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("facebook-monitor/_internal/lib.dylib", "lib")
        writestr_symlink(
            archive,
            "facebook-monitor/lib.dylib",
            "_internal/lib.dylib",
        )

    safe_extract_zip(zip_path, tmp_path / "staging")

    link = tmp_path / "staging" / "facebook-monitor" / "lib.dylib"
    assert link.is_symlink()
    assert link.readlink() == Path("_internal/lib.dylib")


def test_safe_extract_zip_rejects_escaping_symlink(tmp_path: Path) -> None:
    """zip symlink target 不可逃出 staging tree。"""

    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_symlink(archive, "facebook-monitor/link", "../../outside")

    try:
        safe_extract_zip(zip_path, tmp_path / "staging")
    except ValueError as exc:
        assert str(exc) == "zip_symlink_target_unsafe"
    else:
        raise AssertionError("expected escaping symlink to fail")


def test_safe_extract_zip_rejects_backslash_symlink_target(tmp_path: Path) -> None:
    """zip symlink target 必須是實際會被 POSIX symlink 使用的 slash path。"""

    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_symlink(archive, "facebook-monitor/link", "_internal\\lib.dylib")

    try:
        safe_extract_zip(zip_path, tmp_path / "staging")
    except ValueError as exc:
        assert str(exc) == "zip_symlink_target_unsafe"
    else:
        raise AssertionError("expected backslash symlink target to fail")


def test_safe_extract_zip_rejects_symlink_to_private_data(tmp_path: Path) -> None:
    """zip symlink 不可指向更新後會變成 preserved data/profile 的路徑。"""

    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_symlink(archive, "facebook-monitor/profile-link", "data/profiles")

    try:
        safe_extract_zip(zip_path, tmp_path / "staging")
    except ValueError as exc:
        assert str(exc) == "zip_symlink_target_unsafe"
    else:
        raise AssertionError("expected symlink to private data to fail")


def test_safe_extract_zip_rejects_member_under_symlink(tmp_path: Path) -> None:
    """zip 不可先建立 symlink directory 再把 member 寫入該路徑底下。"""

    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_symlink(archive, "facebook-monitor/link", "_internal")
        archive.writestr("facebook-monitor/link/file.txt", "bad")

    try:
        safe_extract_zip(zip_path, tmp_path / "staging")
    except ValueError as exc:
        assert str(exc) == "zip_member_path_unsafe"
    else:
        raise AssertionError("expected member under symlink to fail")


def test_apply_pending_update_rejects_zip_outside_updates_dir(tmp_path: Path) -> None:
    """pending update 不能指向 data updates 目錄外的 zip。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    zip_path = tmp_path / "update.zip"
    digest = make_update_zip(zip_path, exe_text="new")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message == "pending_update_zip_outside_updates_dir"
    assert (app_root / "facebook-monitor.exe").read_text(encoding="utf-8") == "old"


def test_safe_extract_zip_rejects_oversized_archive(tmp_path: Path) -> None:
    """解壓前會檢查展開後大小，避免異常 zip 耗盡磁碟。"""

    zip_path = tmp_path / "big.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("large.bin", "12345")

    try:
        safe_extract_zip(
            zip_path,
            tmp_path / "staging",
            max_uncompressed_bytes=4,
        )
    except ValueError as exc:
        assert str(exc) == "zip_uncompressed_too_large"
    else:
        raise AssertionError("expected oversized zip to fail")
