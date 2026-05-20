"""Admin packaging helper：產生 Windows PyInstaller version resource。"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.version import APP_VERSION
from facebook_monitor.updates.platforms import WINDOWS_APP_ENTRY
from facebook_monitor.versioning import windows_file_version as _windows_file_version
from facebook_monitor.versioning import windows_version_tuple as _windows_version_tuple


DEFAULT_OUTPUT = (
    ROOT / "build" / "pyinstaller_generated" / "windows_app_version_info.txt"
)
DEFAULT_INTERNAL_NAME = Path(WINDOWS_APP_ENTRY).stem
DEFAULT_ORIGINAL_FILENAME = WINDOWS_APP_ENTRY


def parse_args() -> argparse.Namespace:
    """解析 CLI 參數。"""

    parser = argparse.ArgumentParser(
        description="Generate the Windows PyInstaller version resource."
    )
    parser.add_argument(
        "--version",
        default=APP_VERSION,
        help="Release version. Defaults to facebook_monitor.version.APP_VERSION.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Destination version resource file.",
    )
    return parser.parse_args()


def windows_file_version(version: str) -> str:
    """將 semver/rc 轉成 Windows FileVersion 字串。"""

    return _windows_file_version(version)


def windows_version_tuple(version: str) -> tuple[int, int, int, int]:
    """將 semver/rc 轉成 Windows FixedFileInfo tuple。"""

    try:
        return _windows_version_tuple(version)
    except ValueError as exc:
        raise ValueError(f"unsupported release version: {version}") from exc


def render_windows_version_info(version: str) -> str:
    """依 release version 產生 Windows PyInstaller version resource 內容。"""

    return render_windows_version_info_for_executable(
        version,
        internal_name=DEFAULT_INTERNAL_NAME,
        original_filename=DEFAULT_ORIGINAL_FILENAME,
    )


def render_windows_version_info_for_executable(
    version: str,
    *,
    internal_name: str,
    original_filename: str,
) -> str:
    """依 release version 與 EXE identity 產生 Windows version resource。"""

    version_tuple = windows_version_tuple(version)
    tuple_text = ", ".join(str(part) for part in version_tuple)
    file_version = windows_file_version(version)
    return f"""# UTF-8
#
# Generated Windows version resource for PyInstaller builds.
VSVersionInfo(
  ffi=FixedFileInfo(
  filevers=({tuple_text}),
  prodvers=({tuple_text}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
    ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'Facebook Monitor contributors'),
        StringStruct('FileDescription', 'Facebook Monitor'),
      StringStruct('FileVersion', '{file_version}'),
        StringStruct('InternalName', '{internal_name}'),
        StringStruct('LegalCopyright', 'Copyright 2026 Facebook Monitor contributors'),
        StringStruct('OriginalFilename', '{original_filename}'),
        StringStruct('ProductName', 'Facebook Monitor'),
      StringStruct('ProductVersion', '{version}')])
      ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def write_windows_version_info(
    path: Path,
    *,
    version: str = APP_VERSION,
    internal_name: str = DEFAULT_INTERNAL_NAME,
    original_filename: str = DEFAULT_ORIGINAL_FILENAME,
) -> Path:
    """把 Windows version resource 寫到指定位置。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_windows_version_info_for_executable(
            version,
            internal_name=internal_name,
            original_filename=original_filename,
        ),
        encoding="utf-8",
    )
    return path


def main() -> int:
    """CLI entrypoint。"""

    args = parse_args()
    output = write_windows_version_info(args.output.resolve(), version=str(args.version))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
