"""macOS `.app` launcher bundle 建立測試。"""

from __future__ import annotations

import plistlib
from pathlib import Path

from scripts.admin.create_macos_app_launcher import BUNDLE_NAME
from scripts.admin.create_macos_app_launcher import create_macos_app_launcher


def test_create_macos_app_launcher_builds_dock_visible_bundle(tmp_path: Path) -> None:
    """launcher bundle 應保留 Dock 顯示能力，並啟動 onedir 內的 executable。"""

    app_root = tmp_path / "facebook-monitor"
    app_root.mkdir()
    executable = app_root / "facebook-monitor"
    executable.write_text("binary", encoding="utf-8")
    executable.chmod(0o755)
    icon_source = tmp_path / "facebook-monitor.png"
    icon_source.write_text("png", encoding="utf-8")

    bundle = create_macos_app_launcher(
        app_root=app_root,
        icon_source=icon_source,
        version="0.1.0",
        convert_icon=False,
    )

    assert bundle == app_root / BUNDLE_NAME
    launcher = bundle / "Contents" / "MacOS" / "facebook-monitor-launcher"
    assert launcher.is_file()
    assert launcher.stat().st_mode & 0o111
    assert 'EXECUTABLE="$APP_ROOT/facebook-monitor"' in launcher.read_text(
        encoding="utf-8"
    )
    plist = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
    assert plist["CFBundleExecutable"] == "facebook-monitor-launcher"
    assert plist["CFBundleIconFile"] == "facebook-monitor"
    assert plist["CFBundlePackageType"] == "APPL"
    assert plist["CFBundleShortVersionString"] == "0.1.0"
    assert "LSUIElement" not in plist
    assert "LSBackgroundOnly" not in plist
