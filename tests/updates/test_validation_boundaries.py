"""Updater 共用 validation helper 邊界測試。"""

from __future__ import annotations

from pathlib import Path
from pathlib import PurePosixPath
import os
import struct

import pytest

from facebook_monitor.updates.validation import CPU_TYPE_ARM64
from facebook_monitor.updates.validation import FAT_MAGIC
from facebook_monitor.updates.validation import MACHO_MAGIC_64
from facebook_monitor.updates.validation import decode_zip_symlink_target
from facebook_monitor.updates.validation import is_macho_arm64
from facebook_monitor.updates.validation import resolve_zip_symlink_target
from facebook_monitor.updates.validation import validate_tree_links_stay_within_root
from facebook_monitor.updates.validation import validate_zip_member_path


def test_validate_zip_member_path_accepts_normal_nested_path() -> None:
    """正常 nested member path 應正規化為 POSIX path。"""

    assert validate_zip_member_path("facebook-monitor/_internal/app.py") == PurePosixPath(
        "facebook-monitor/_internal/app.py"
    )


@pytest.mark.parametrize(
    "filename",
    [
        "",
        "/absolute/path.txt",
        "../evil.txt",
        "facebook-monitor/../evil.txt",
        "facebook-monitor\\..\\evil.txt",
        "facebook-monitor/CON/file.txt",
        "facebook-monitor/aux.txt",
        "facebook-monitor/file:stream.txt",
        "facebook-monitor/trailing-dot./file.txt",
        "facebook-monitor/trailing-space /file.txt",
        "facebook-monitor/control-\x01.txt",
        "facebook-monitor/star*.txt",
        "facebook-monitor/question?.txt",
        "facebook-monitor/pipe|name.txt",
    ],
)
def test_validate_zip_member_path_rejects_cross_platform_unsafe_names(
    filename: str,
) -> None:
    """zip member path 不可在 POSIX 或 Windows 落地時產生危險語義。"""

    with pytest.raises(ValueError, match="zip_member_path_unsafe"):
        validate_zip_member_path(filename)


def test_decode_zip_symlink_target_accepts_relative_posix_path() -> None:
    """zip symlink target 只接受 UTF-8 POSIX 相對路徑文字。"""

    assert decode_zip_symlink_target(b"_internal/lib.dylib") == "_internal/lib.dylib"


@pytest.mark.parametrize(
    "target_data",
    [
        b"",
        b"_internal\\lib.dylib",
        b"_internal/lib.dylib\x00suffix",
        b"\xff\xfe",
    ],
)
def test_decode_zip_symlink_target_rejects_unsafe_targets(
    target_data: bytes,
) -> None:
    """空值、NUL、backslash 與非 UTF-8 symlink target 都不可接受。"""

    with pytest.raises(ValueError, match="zip_symlink_target_unsafe"):
        decode_zip_symlink_target(target_data)


def test_resolve_zip_symlink_target_accepts_in_root_relative_target() -> None:
    """相對 symlink target 經過 .. 解析後仍可留在 zip root 內。"""

    resolved = resolve_zip_symlink_target(
        PurePosixPath("facebook-monitor/lib/link"),
        "../_internal/lib.dylib",
    )

    assert resolved == PurePosixPath("facebook-monitor/_internal/lib.dylib")


@pytest.mark.parametrize("target", ["/tmp/outside", "../../outside"])
def test_resolve_zip_symlink_target_rejects_escaping_targets(target: str) -> None:
    """absolute 或逃出 archive root 的 symlink target 都要拒絕。"""

    assert (
        resolve_zip_symlink_target(PurePosixPath("facebook-monitor/link"), target)
        is None
    )


def test_is_macho_arm64_recognizes_thin_arm64_header() -> None:
    """thin Mach-O header 的 CPU type 為 arm64 時要被辨識。"""

    data = struct.pack("<Ii", MACHO_MAGIC_64, CPU_TYPE_ARM64)

    assert is_macho_arm64(data)


def test_is_macho_arm64_rejects_non_arm64_thin_header() -> None:
    """非 arm64 thin Mach-O 不可被誤判為 Apple Silicon binary。"""

    data = struct.pack("<Ii", MACHO_MAGIC_64, 7)

    assert not is_macho_arm64(data)


def test_is_macho_arm64_recognizes_fat_binary_containing_arm64() -> None:
    """universal binary 只要含 arm64 slice 就應通過檢查。"""

    data = struct.pack(
        ">IIiiIII",
        FAT_MAGIC,
        1,
        CPU_TYPE_ARM64,
        0,
        0,
        0,
        0,
    )

    assert is_macho_arm64(data)


def test_is_macho_arm64_rejects_short_or_truncated_data() -> None:
    """短資料或截斷 fat table 不可丟例外，也不可誤判通過。"""

    assert not is_macho_arm64(b"")
    assert not is_macho_arm64(struct.pack(">II", FAT_MAGIC, 1) + b"\x00" * 4)


def test_validate_tree_links_stay_within_root_accepts_safe_symlink(
    tmp_path: Path,
) -> None:
    """POSIX 相對 symlink 留在 root 內時，可保留於 app tree。"""

    if os.name == "nt":
        pytest.skip("Windows symlink permissions are environment-dependent")
    root = tmp_path / "app"
    root.mkdir()
    (root / "_internal").mkdir()
    (root / "_internal" / "lib.dylib").write_text("lib", encoding="utf-8")
    (root / "lib.dylib").symlink_to("_internal/lib.dylib")

    validate_tree_links_stay_within_root(
        root / "lib.dylib",
        root=root,
        reason="app_path_unsafe",
    )


def test_validate_tree_links_stay_within_root_rejects_sensitive_symlink_target(
    tmp_path: Path,
) -> None:
    """symlink 目標含 data/profiles 等敏感 component 時要拒絕。"""

    if os.name == "nt":
        pytest.skip("Windows symlink permissions are environment-dependent")
    root = tmp_path / "app"
    root.mkdir()
    (root / "data" / "profiles").mkdir(parents=True)
    (root / "profile-link").symlink_to("data/profiles")

    with pytest.raises(ValueError, match="app_path_unsafe"):
        validate_tree_links_stay_within_root(
            root / "profile-link",
            root=root,
            reason="app_path_unsafe",
            forbidden_target_parts=frozenset({"data", "profiles"}),
        )
