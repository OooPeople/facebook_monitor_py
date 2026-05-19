"""Build metadata tests。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.runtime.build_metadata import BUILD_DATE_ENV
from facebook_monitor.runtime.build_metadata import GIT_COMMIT_ENV
from facebook_monitor.runtime.build_metadata import PACKAGING_MODE_ENV
from facebook_monitor.runtime.build_metadata import collect_build_metadata


def test_collect_build_metadata_uses_source_defaults() -> None:
    """未注入 build 環境變數時，metadata 保留 source mode 預設值。"""

    metadata = collect_build_metadata(asset_version="asset-test")

    assert metadata.app_name == "Facebook Monitor"
    assert metadata.app_version == "0.3.0"
    assert metadata.asset_version == "asset-test"
    assert metadata.python_version
    assert metadata.executable.exists()
    assert metadata.packaging_mode in {"source", "frozen"}
    assert metadata.build_date == "unknown"
    assert metadata.git_commit == "unknown"


def test_collect_build_metadata_reads_packaging_env(monkeypatch) -> None:
    """打包流程可用環境變數注入 build metadata。"""

    monkeypatch.setenv(BUILD_DATE_ENV, "2026-05-10T00:00:00Z")
    monkeypatch.setenv(GIT_COMMIT_ENV, "abc1234")
    monkeypatch.setenv(PACKAGING_MODE_ENV, "portable")

    metadata = collect_build_metadata(asset_version="asset-test")

    assert metadata.packaging_mode == "portable"
    assert metadata.build_date == "2026-05-10T00:00:00Z"
    assert metadata.git_commit == "abc1234"


def test_pyinstaller_spec_uses_formal_launcher_and_web_assets() -> None:
    """EXE spec 必須走正式 launcher，並收進 Web UI/Playwright runtime 資料。"""

    spec_text = Path("packaging/pyinstaller/facebook_monitor.spec").read_text(
        encoding="utf-8"
    )

    assert "Windows-only PyInstaller spec" in spec_text
    assert '"launcher.py"' in spec_text
    assert '"updater.py"' in spec_text
    assert "webapp/templates/**/*.html" in spec_text
    assert "webapp/static/**/*" in spec_text
    assert "FACEBOOK_MONITOR_BUILD_DATE" in spec_text
    assert "FACEBOOK_MONITOR_GIT_COMMIT" in spec_text
    assert "FACEBOOK_MONITOR_PACKAGING_MODE" in spec_text
    assert "os.environ.setdefault" not in spec_text
    assert "runtime_hooks=[BUILD_METADATA_HOOK]" in spec_text
    assert 'collect_data_files("playwright")' in spec_text
    assert 'collect_submodules("playwright")' in spec_text
    assert 'name="facebook-monitor"' in spec_text
    assert 'name="facebook-monitor-updater"' in spec_text
    assert 'datas.append((ICON_PATH, "assets"))' in spec_text
    assert 'datas.append((TRAY_ICON_PATH, "assets"))' in spec_text
    assert "console=False" in spec_text
    assert "upx=False" in spec_text
    assert "webapp.app:app" not in spec_text


def test_macos_pyinstaller_spec_supports_apple_silicon_playwright_browser() -> None:
    """macOS spec 必須支援 Apple Silicon Playwright browser bundle 名稱。"""

    spec_text = Path("packaging/pyinstaller/facebook_monitor_macos.spec").read_text(
        encoding="utf-8"
    )

    assert "chromium-*/*" in spec_text
    assert "Google Chrome for Testing.app" in spec_text
    assert "Google Chrome for Testing" in spec_text
    assert "Chromium.app" in spec_text
    assert "FACEBOOK_MONITOR_BUNDLED_CHROMIUM_DIR" in spec_text
    assert "create_macos_app_launcher" in spec_text
