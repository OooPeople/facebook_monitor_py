"""Build metadata tests。"""

from __future__ import annotations

from pathlib import Path
import tomllib

from facebook_monitor.runtime.build_metadata import BUILD_DATE_ENV
from facebook_monitor.runtime.build_metadata import GIT_COMMIT_ENV
from facebook_monitor.runtime.build_metadata import PACKAGING_MODE_ENV
from facebook_monitor.runtime.build_metadata import collect_build_metadata
from facebook_monitor.runtime.bundled_browser import MACOS_BROWSER_APP_EXECUTABLES
from facebook_monitor.version import APP_VERSION
from facebook_monitor import version as version_module


def test_collect_build_metadata_uses_source_defaults() -> None:
    """未注入 build 環境變數時，metadata 保留 source mode 預設值。"""

    metadata = collect_build_metadata(asset_version="asset-test")

    assert metadata.app_name == "Facebook Monitor"
    assert metadata.app_version == APP_VERSION
    assert metadata.asset_version == "asset-test"
    assert metadata.python_version
    assert metadata.executable.exists()
    assert metadata.packaging_mode in {"source", "frozen"}
    assert metadata.build_date == "unknown"
    assert metadata.git_commit == "unknown"


def test_app_version_comes_from_pyproject() -> None:
    """runtime APP_VERSION 必須跟 pyproject.toml 的單一版本來源一致。"""

    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert APP_VERSION == pyproject["project"]["version"]


def test_resolve_app_version_reads_frozen_build_env(monkeypatch) -> None:
    """frozen runtime 由 PyInstaller runtime hook 注入版本，不依賴 source tree。"""

    monkeypatch.setattr(version_module.sys, "frozen", True, raising=False)
    monkeypatch.setenv(version_module.APP_VERSION_ENV, "9.8.7")

    assert version_module._resolve_app_version() == "9.8.7"


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
    assert "APP_VERSION" in spec_text
    assert "write_windows_version_info" in spec_text
    assert "windows_app_version_info.txt" in spec_text
    assert "windows_updater_version_info.txt" in spec_text
    assert '"launcher.py"' in spec_text
    assert '"updater.py"' in spec_text
    assert "webapp/templates/**/*.html" in spec_text
    assert "webapp/static/**/*" in spec_text
    assert "FACEBOOK_MONITOR_BUILD_DATE" in spec_text
    assert "FACEBOOK_MONITOR_GIT_COMMIT" in spec_text
    assert "FACEBOOK_MONITOR_PACKAGING_MODE" in spec_text
    assert "FACEBOOK_MONITOR_APP_VERSION" in spec_text
    assert "os.environ.setdefault" not in spec_text
    assert "runtime_hooks=[BUILD_METADATA_HOOK]" in spec_text
    assert 'collect_data_files("playwright")' in spec_text
    assert 'collect_submodules("playwright")' in spec_text
    assert "WINDOWS_APP_ENTRY" in spec_text
    assert "WINDOWS_UPDATER_ENTRY" in spec_text
    assert "name=Path(WINDOWS_APP_ENTRY).stem" in spec_text
    assert "name=Path(WINDOWS_UPDATER_ENTRY).stem" in spec_text
    assert "original_filename=WINDOWS_APP_ENTRY" in spec_text
    assert "original_filename=WINDOWS_UPDATER_ENTRY" in spec_text
    assert 'datas.append((ICON_PATH, "assets"))' in spec_text
    assert 'datas.append((TRAY_ICON_PATH, "assets"))' in spec_text
    assert "console=False" in spec_text
    assert "upx=False" in spec_text
    assert "webapp.app:app" not in spec_text
    assert 'os.path.join(SPECPATH, "version_info.txt")' not in spec_text


def test_macos_pyinstaller_spec_supports_apple_silicon_playwright_browser() -> None:
    """macOS spec 必須支援 Apple Silicon Playwright browser bundle 名稱。"""

    spec_text = Path("packaging/pyinstaller/facebook_monitor_macos.spec").read_text(
        encoding="utf-8"
    )

    assert "chromium-*/*" in spec_text
    assert 'target_arch="arm64"' in spec_text
    assert "MACOS_BROWSER_APP_EXECUTABLES" in spec_text
    assert any("Google Chrome for Testing.app" in path for path in MACOS_BROWSER_APP_EXECUTABLES)
    assert any("Chromium.app" in path for path in MACOS_BROWSER_APP_EXECUTABLES)
    assert "FACEBOOK_MONITOR_BUNDLED_CHROMIUM_DIR" in spec_text
    assert "FACEBOOK_MONITOR_APP_VERSION" in spec_text
    assert "create_macos_app_launcher" in spec_text
