# -*- mode: python ; coding: utf-8 -*-
# ruff: noqa: F821

"""macOS PyInstaller onedir spec for the formal local Web UI launcher.

Build this spec on macOS from the repository root after installing PyInstaller
in the active environment:

    pyinstaller packaging/pyinstaller/facebook_monitor_macos.spec --clean

This spec intentionally produces an onedir folder, not a signed `.app` bundle.
Signing / notarization is a later release step.
"""

import os
import subprocess
import sys
from datetime import datetime
from datetime import timezone
from pathlib import Path

from PyInstaller.building.datastruct import TOC
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules


if sys.platform != "darwin":
    raise SystemExit("facebook_monitor_macos.spec must be built on macOS.")


ROOT_DIR = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
ENTRYPOINT = os.path.join(SRC_DIR, "facebook_monitor", "launcher.py")
UPDATER_ENTRYPOINT = os.path.join(SRC_DIR, "facebook_monitor", "updater.py")
GENERATED_DIR = os.path.join(ROOT_DIR, "build", "pyinstaller_generated")
BUILD_METADATA_HOOK = os.path.join(GENERATED_DIR, "facebook_monitor_build_metadata.py")
ICON_PATH = os.path.join(ROOT_DIR, "packaging", "assets", "facebook-monitor.png")


def read_git_commit():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=ROOT_DIR,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def build_date():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def platform_packaging_mode():
    return "pyinstaller-macos-arm64-onedir"


def bundled_chromium_dir():
    configured = os.environ.get("FACEBOOK_MONITOR_BUNDLED_CHROMIUM_DIR", "").strip()
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if _contains_macos_chromium(candidate):
            return str(candidate)
        raise SystemExit(
            "FACEBOOK_MONITOR_BUNDLED_CHROMIUM_DIR must point to a macOS Chromium "
            "folder that contains Chromium.app"
        )
    browsers_root = Path.home() / "Library" / "Caches" / "ms-playwright"
    candidates = [
        path
        for path in browsers_root.glob("chromium-*/chrome-mac")
        if _contains_macos_chromium(path)
    ]
    if not candidates:
        raise SystemExit(
            "No Playwright macOS Chromium folder found. Run `playwright install chromium` "
            "or set FACEBOOK_MONITOR_BUNDLED_CHROMIUM_DIR."
        )
    return str(max(candidates, key=lambda path: path.stat().st_mtime))


def _contains_macos_chromium(path):
    return (path / "Chromium.app" / "Contents" / "MacOS" / "Chromium").is_file()


os.makedirs(GENERATED_DIR, exist_ok=True)
with open(BUILD_METADATA_HOOK, "w", encoding="utf-8") as hook_file:
    hook_file.write(
        "import os\n"
        f"os.environ['FACEBOOK_MONITOR_BUILD_DATE'] = {os.environ.get('FACEBOOK_MONITOR_BUILD_DATE', build_date())!r}\n"
        f"os.environ['FACEBOOK_MONITOR_GIT_COMMIT'] = {os.environ.get('FACEBOOK_MONITOR_GIT_COMMIT', read_git_commit())!r}\n"
        f"os.environ['FACEBOOK_MONITOR_PACKAGING_MODE'] = {os.environ.get('FACEBOOK_MONITOR_PACKAGING_MODE', platform_packaging_mode())!r}\n"
    )

datas = collect_data_files(
    "facebook_monitor",
    includes=[
        "webapp/templates/**/*.html",
        "webapp/static/**/*",
    ],
) + collect_data_files("playwright")
if os.path.exists(ICON_PATH):
    datas.append((ICON_PATH, "assets"))
browser_datas = Tree(bundled_chromium_dir(), prefix="browser")

hiddenimports = (
    ["facebook_monitor.launcher", "facebook_monitor.updater"]
    + collect_submodules("uvicorn.lifespan")
    + collect_submodules("uvicorn.loops")
    + collect_submodules("uvicorn.protocols")
    + collect_submodules("playwright")
)

a = Analysis(
    [ENTRYPOINT, UPDATER_ENTRYPOINT],
    pathex=[SRC_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[BUILD_METADATA_HOOK],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

runtime_script_entries = [
    entry
    for entry in a.scripts
    if entry[0].startswith("pyi_rth_") or entry[0] == "facebook_monitor_build_metadata"
]
launcher_script_entry = next(entry for entry in a.scripts if entry[0] == "launcher")
updater_script_entry = next(entry for entry in a.scripts if entry[0] == "updater")

exe = EXE(
    pyz,
    TOC(runtime_script_entries + [launcher_script_entry]),
    [],
    exclude_binaries=True,
    name="facebook-monitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_PATH if os.path.exists(ICON_PATH) else None,
)
updater_exe = EXE(
    pyz,
    TOC(runtime_script_entries + [updater_script_entry]),
    [],
    exclude_binaries=True,
    name="facebook-monitor-updater",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_PATH if os.path.exists(ICON_PATH) else None,
)
coll = COLLECT(
    exe,
    updater_exe,
    a.binaries,
    a.datas,
    browser_datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="facebook-monitor",
)
