"""macOS onedir / `.app` 測試 fixture helpers。"""

from __future__ import annotations

from pathlib import Path
import plistlib
import struct
import zipfile

from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_ICON
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_INFO_PLIST
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER


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


def macos_app_plist(*, version: str = "0.1.0") -> bytes:
    """建立 Dock-visible `.app` 測試用 Info.plist bytes。"""

    return plistlib.dumps(
        {
            "CFBundleExecutable": Path(MACOS_APP_BUNDLE_LAUNCHER).name,
            "CFBundleIconFile": "facebook-monitor",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
        }
    )


def write_macos_app_bundle(
    root: Path,
    *,
    version: str = "0.1.0",
    launcher_content: bytes = MACHO_ARM64_BYTES,
) -> None:
    """在 onedir root 寫入測試用 Finder/Dock `.app` bundle。"""

    launcher = root / MACOS_APP_BUNDLE_LAUNCHER
    icon = root / MACOS_APP_BUNDLE_ICON
    launcher.parent.mkdir(parents=True, exist_ok=True)
    icon.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_bytes(launcher_content)
    launcher.chmod(0o755)
    icon.write_text("icon", encoding="utf-8")
    (root / MACOS_APP_BUNDLE_INFO_PLIST).write_bytes(macos_app_plist(version=version))


def write_macos_app_bundle_to_zip(
    archive: zipfile.ZipFile,
    *,
    root_prefix: str = "facebook-monitor",
    version: str = "0.1.0",
    launcher_content: bytes = MACHO_ARM64_BYTES,
) -> None:
    """在 zip 內寫入測試用 Finder/Dock `.app` bundle。"""

    archive.writestr(
        f"{root_prefix}/{MACOS_APP_BUNDLE_INFO_PLIST}",
        macos_app_plist(version=version),
    )
    _writestr_with_mode(
        archive,
        f"{root_prefix}/{MACOS_APP_BUNDLE_LAUNCHER}",
        launcher_content,
        0o755,
    )
    archive.writestr(f"{root_prefix}/{MACOS_APP_BUNDLE_ICON}", "icon")


def _writestr_with_mode(
    archive: zipfile.ZipFile,
    name: str,
    content: bytes,
    mode: int,
) -> None:
    """寫入帶 POSIX mode 的 zip member。"""

    info = zipfile.ZipInfo(name)
    info.external_attr = (mode & 0o777) << 16
    archive.writestr(info, content)
