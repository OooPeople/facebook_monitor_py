"""macOS onedir / `.app` 測試 fixture helpers。"""

from __future__ import annotations

from pathlib import Path
import os
import plistlib
import stat
import struct
import zipfile

from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_ICON
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_INFO_PLIST
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER
from facebook_monitor.updates.validation import has_posix_executable_bit
from facebook_monitor.updates.validation import zip_member_has_executable_bit


MACHO_ARM64_BYTES = struct.pack(
    "<IiiIIIII",
    0xFEEDFACF,
    0x0100000C,
    0,
    2,
    0,
    0,
    0,
    0,
)


def macos_app_plist(
    *,
    version: str = "0.1.0",
    extra_values: dict[str, object] | None = None,
) -> bytes:
    """建立 Dock-visible `.app` 測試用 Info.plist bytes。"""

    values: dict[str, object] = {
        "CFBundleExecutable": Path(MACOS_APP_BUNDLE_LAUNCHER).name,
        "CFBundleIconFile": "facebook-monitor",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
    }
    if extra_values:
        values.update(extra_values)
    return plistlib.dumps(
        values
    )


def write_macos_app_bundle(
    root: Path,
    *,
    version: str = "0.1.0",
    launcher_content: bytes = MACHO_ARM64_BYTES,
    extra_plist_values: dict[str, object] | None = None,
) -> None:
    """在 onedir root 寫入測試用 Finder/Dock `.app` bundle。"""

    launcher = root / MACOS_APP_BUNDLE_LAUNCHER
    icon = root / MACOS_APP_BUNDLE_ICON
    launcher.parent.mkdir(parents=True, exist_ok=True)
    icon.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_bytes(launcher_content)
    launcher.chmod(0o755)
    icon.write_text("icon", encoding="utf-8")
    (root / MACOS_APP_BUNDLE_INFO_PLIST).write_bytes(
        macos_app_plist(version=version, extra_values=extra_plist_values)
    )


def write_macos_app_bundle_to_zip(
    archive: zipfile.ZipFile,
    *,
    root_prefix: str = "facebook-monitor",
    version: str = "0.1.0",
    launcher_content: bytes = MACHO_ARM64_BYTES,
    extra_plist_values: dict[str, object] | None = None,
) -> None:
    """在 zip 內寫入測試用 Finder/Dock `.app` bundle。"""

    archive.writestr(
        f"{root_prefix}/{MACOS_APP_BUNDLE_INFO_PLIST}",
        macos_app_plist(version=version, extra_values=extra_plist_values),
    )
    writestr_with_mode(
        archive,
        f"{root_prefix}/{MACOS_APP_BUNDLE_LAUNCHER}",
        launcher_content,
        0o755,
    )
    archive.writestr(f"{root_prefix}/{MACOS_APP_BUNDLE_ICON}", "icon")


def assert_posix_executable_when_supported(path: Path) -> None:
    """在支援 POSIX mode 的平台確認 executable bit。"""

    assert path.is_file()
    if os.name != "nt":
        assert has_posix_executable_bit(path)


def writestr_with_mode(
    archive: zipfile.ZipFile,
    name: str,
    content: str | bytes,
    mode: int,
) -> None:
    """寫入帶 POSIX mode 的 zip member。"""

    info = zipfile.ZipInfo(name)
    info.external_attr = (mode & 0o777) << 16
    archive.writestr(info, content)


def writestr_symlink(
    archive: zipfile.ZipFile,
    name: str,
    target: str,
) -> None:
    """寫入 POSIX symlink zip member。"""

    info = zipfile.ZipInfo(name)
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    archive.writestr(info, target)


def write_path_to_zip_with_mode(
    archive: zipfile.ZipFile,
    path: Path,
    arcname: str | Path,
    mode: int,
) -> None:
    """將檔案寫入 zip，並明確指定 POSIX mode metadata。"""

    writestr_with_mode(archive, str(arcname), path.read_bytes(), mode)


def assert_zip_member_executable(archive: zipfile.ZipFile, name: str) -> None:
    """在任何平台檢查 zip metadata 是否含 executable bit。"""

    assert zip_member_has_executable_bit(archive.getinfo(name))
