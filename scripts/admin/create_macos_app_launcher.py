"""Admin tool：為 macOS onedir build 建立 Finder/Dock 用 .app 外殼。"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import plistlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.version import APP_VERSION


APP_ROOT_NAME = "facebook-monitor"
BUNDLE_NAME = "Facebook Monitor.app"
BUNDLE_DISPLAY_NAME = "Facebook Monitor"
BUNDLE_IDENTIFIER = "com.ooopeople.facebook-monitor"
LAUNCHER_EXECUTABLE_NAME = "facebook-monitor-launcher"
ICON_BASENAME = "facebook-monitor"
DEFAULT_ICON_SOURCE = ROOT / "packaging" / "assets" / "facebook-monitor.png"


def parse_args() -> argparse.Namespace:
    """解析 CLI 參數。"""

    parser = argparse.ArgumentParser(
        description="Create a macOS .app launcher beside/inside the frozen onedir app."
    )
    parser.add_argument(
        "--app-root",
        type=Path,
        default=ROOT / "dist" / APP_ROOT_NAME,
        help="Frozen onedir app root that contains the facebook-monitor executable.",
    )
    parser.add_argument(
        "--icon-source",
        type=Path,
        default=DEFAULT_ICON_SOURCE,
        help="PNG icon source used to generate the .app icns file.",
    )
    parser.add_argument(
        "--version",
        default=APP_VERSION,
        help="Bundle version. Defaults to facebook_monitor.version.APP_VERSION.",
    )
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint。"""

    args = parse_args()
    bundle = create_macos_app_launcher(
        app_root=args.app_root.resolve(),
        icon_source=args.icon_source.resolve(),
        version=str(args.version),
    )
    print(bundle)
    return 0


def create_macos_app_launcher(
    *,
    app_root: Path,
    icon_source: Path = DEFAULT_ICON_SOURCE,
    version: str = APP_VERSION,
    convert_icon: bool = True,
) -> Path:
    """在 frozen onedir 內建立 `Facebook Monitor.app` launcher bundle。"""

    app_root = app_root.resolve()
    app_entry = app_root / APP_ROOT_NAME
    if not app_entry.is_file():
        raise ValueError(f"missing macOS app executable: {app_entry}")
    bundle_dir = app_root / BUNDLE_NAME
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)

    contents_dir = bundle_dir / "Contents"
    macos_dir = contents_dir / "MacOS"
    resources_dir = contents_dir / "Resources"
    macos_dir.mkdir(parents=True)
    resources_dir.mkdir(parents=True)

    launcher_path = macos_dir / LAUNCHER_EXECUTABLE_NAME
    launcher_path.write_text(_launcher_script(), encoding="utf-8")
    launcher_path.chmod(0o755)
    (contents_dir / "Info.plist").write_bytes(
        plistlib.dumps(_info_plist(version=version), sort_keys=True)
    )
    if convert_icon:
        _create_icns(icon_source, resources_dir / f"{ICON_BASENAME}.icns")
    elif icon_source.is_file():
        shutil.copy2(icon_source, resources_dir / icon_source.name)
    return bundle_dir


def _launcher_script() -> str:
    """回傳 .app 內部 launcher script。"""

    return """#!/bin/sh
APP_BUNDLE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)
APP_ROOT=$(CDPATH= cd -- "$APP_BUNDLE_DIR/.." && pwd -P)
EXECUTABLE="$APP_ROOT/facebook-monitor"
if [ ! -x "$EXECUTABLE" ]; then
  /usr/bin/osascript -e 'display alert "Facebook Monitor" message "找不到 facebook-monitor executable。請確認 Facebook Monitor.app 仍在 facebook-monitor 資料夾內。" as critical' >/dev/null 2>&1 || true
  exit 127
fi
exec "$EXECUTABLE" "$@"
"""


def _info_plist(*, version: str) -> dict[str, object]:
    """建立 macOS launcher bundle 的 Info.plist。"""

    return {
        "CFBundleDevelopmentRegion": "zh_TW",
        "CFBundleDisplayName": BUNDLE_DISPLAY_NAME,
        "CFBundleExecutable": LAUNCHER_EXECUTABLE_NAME,
        "CFBundleIconFile": ICON_BASENAME,
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundleName": BUNDLE_DISPLAY_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHighResolutionCapable": True,
    }


def _create_icns(source_png: Path, destination: Path) -> None:
    """用 macOS 內建工具從 PNG 產生 .icns。"""

    if not source_png.is_file():
        raise ValueError(f"missing icon source: {source_png}")
    sips = shutil.which("sips")
    iconutil = shutil.which("iconutil")
    if not sips or not iconutil:
        raise ValueError("macOS icon tools not found: sips/iconutil")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="facebook-monitor-iconset-") as temp_dir:
        iconset = Path(temp_dir) / f"{ICON_BASENAME}.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            _run_sips_resize(
                sips,
                source_png,
                iconset / f"icon_{size}x{size}.png",
                size,
            )
            _run_sips_resize(
                sips,
                source_png,
                iconset / f"icon_{size}x{size}@2x.png",
                size * 2,
            )
        subprocess.run(
            [iconutil, "-c", "icns", str(iconset), "-o", str(destination)],
            check=True,
        )


def _run_sips_resize(
    sips: str,
    source_png: Path,
    destination: Path,
    size: int,
) -> None:
    """呼叫 sips 產生指定尺寸 icon PNG。"""

    subprocess.run(
        [
            sips,
            "-z",
            str(size),
            str(size),
            str(source_png),
            "--out",
            str(destination),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


if __name__ == "__main__":
    raise SystemExit(main())
