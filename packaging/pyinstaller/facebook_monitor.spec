# -*- mode: python ; coding: utf-8 -*-

"""Windows-only PyInstaller spec for the formal local Web UI launcher.

Build from the repository root after installing PyInstaller in the active
environment:

    pyinstaller packaging/pyinstaller/facebook_monitor.spec --clean
"""

import os
import subprocess
import sys
from datetime import datetime
from datetime import timezone
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.building.datastruct import TOC


ROOT_DIR = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from facebook_monitor.version import APP_VERSION
from facebook_monitor.updates.platforms import WINDOWS_APP_ENTRY
from facebook_monitor.updates.platforms import WINDOWS_UPDATER_ENTRY
from scripts.admin.windows_version_resource import write_windows_version_info

ENTRYPOINT = os.path.join(SRC_DIR, "facebook_monitor", "launcher.py")
UPDATER_ENTRYPOINT = os.path.join(SRC_DIR, "facebook_monitor", "updater.py")
GENERATED_DIR = os.path.join(ROOT_DIR, "build", "pyinstaller_generated")
BUILD_METADATA_HOOK = os.path.join(GENERATED_DIR, "facebook_monitor_build_metadata.py")
WINDOWS_APP_VERSION_INFO_FILE = os.path.join(
    GENERATED_DIR,
    "windows_app_version_info.txt",
)
WINDOWS_UPDATER_VERSION_INFO_FILE = os.path.join(
    GENERATED_DIR,
    "windows_updater_version_info.txt",
)
ICON_PATH = os.path.join(ROOT_DIR, "packaging", "assets", "facebook-monitor.ico")
TRAY_ICON_PATH = os.path.join(ROOT_DIR, "packaging", "assets", "facebook-monitor-tray.ico")


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


def bundled_chromium_dir():
    configured = os.environ.get("FACEBOOK_MONITOR_BUNDLED_CHROMIUM_DIR", "").strip()
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if (candidate / "chrome.exe").is_file():
            return str(candidate)
        raise SystemExit(
            "FACEBOOK_MONITOR_BUNDLED_CHROMIUM_DIR must point to a Chromium folder "
            "that contains chrome.exe"
        )
    browsers_root = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    candidates = [
        path
        for path in browsers_root.glob("chromium-*/chrome-win64")
        if (path / "chrome.exe").is_file()
    ]
    if not candidates:
        raise SystemExit(
            "No Playwright Chromium folder found. Run `playwright install chromium` "
            "or set FACEBOOK_MONITOR_BUNDLED_CHROMIUM_DIR."
        )
    return str(max(candidates, key=lambda path: path.stat().st_mtime))


os.makedirs(GENERATED_DIR, exist_ok=True)
write_windows_version_info(
    Path(WINDOWS_APP_VERSION_INFO_FILE),
    version=APP_VERSION,
    internal_name=Path(WINDOWS_APP_ENTRY).stem,
    original_filename=WINDOWS_APP_ENTRY,
)
write_windows_version_info(
    Path(WINDOWS_UPDATER_VERSION_INFO_FILE),
    version=APP_VERSION,
    internal_name=Path(WINDOWS_UPDATER_ENTRY).stem,
    original_filename=WINDOWS_UPDATER_ENTRY,
)
with open(BUILD_METADATA_HOOK, "w", encoding="utf-8") as hook_file:
    hook_file.write(
        "import os\n"
        f"os.environ['FACEBOOK_MONITOR_APP_VERSION'] = {APP_VERSION!r}\n"
        f"os.environ['FACEBOOK_MONITOR_BUILD_DATE'] = {os.environ.get('FACEBOOK_MONITOR_BUILD_DATE', build_date())!r}\n"
        f"os.environ['FACEBOOK_MONITOR_GIT_COMMIT'] = {os.environ.get('FACEBOOK_MONITOR_GIT_COMMIT', read_git_commit())!r}\n"
        f"os.environ['FACEBOOK_MONITOR_PACKAGING_MODE'] = {os.environ.get('FACEBOOK_MONITOR_PACKAGING_MODE', 'pyinstaller')!r}\n"
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
if os.path.exists(TRAY_ICON_PATH):
    datas.append((TRAY_ICON_PATH, "assets"))
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
    name=Path(WINDOWS_APP_ENTRY).stem,
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
    version=WINDOWS_APP_VERSION_INFO_FILE,
)
updater_exe = EXE(
    pyz,
    TOC(runtime_script_entries + [updater_script_entry]),
    [],
    exclude_binaries=True,
    name=Path(WINDOWS_UPDATER_ENTRY).stem,
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
    version=WINDOWS_UPDATER_VERSION_INFO_FILE,
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
