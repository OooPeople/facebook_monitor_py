"""Browser runtime abstraction tests。"""

from __future__ import annotations

from pathlib import Path

import pytest

from facebook_monitor.automation.browser_runtime import BrowserMode
from facebook_monitor.automation.browser_runtime import BROWSER_EXECUTABLE_ENV
from facebook_monitor.automation.browser_runtime import BrowserRuntimeError
from facebook_monitor.automation.browser_runtime import BrowserRuntimeOptions
from facebook_monitor.automation.browser_runtime import build_persistent_context_kwargs
from facebook_monitor.automation.browser_runtime import launch_persistent_context_sync


class FakeBrowserType:
    """測試用 sync browser type。"""

    def __init__(self) -> None:
        self.kwargs: dict[str, object] | None = None

    def launch_persistent_context(self, **kwargs: object) -> object:
        """保存 launch kwargs 並回傳 fake context。"""

        self.kwargs = kwargs
        return object()


class FakePlaywright:
    """測試用 sync Playwright root。"""

    def __init__(self) -> None:
        self.chromium = FakeBrowserType()


def test_build_persistent_context_kwargs_uses_shared_defaults(tmp_path: Path) -> None:
    """BrowserRuntime 集中 profile、headless、viewport 與 timeout 預設值。"""

    options = BrowserRuntimeOptions(profile_dir=tmp_path / "profile")

    kwargs = build_persistent_context_kwargs(options)

    assert kwargs == {
        "user_data_dir": str(tmp_path / "profile"),
        "headless": True,
        "viewport": {"width": 1366, "height": 900},
        "timeout": 120000.0,
    }


def test_launch_persistent_context_sync_uses_chromium(tmp_path: Path) -> None:
    """目前正式 backend 是 Playwright Chromium。"""

    playwright = FakePlaywright()
    options = BrowserRuntimeOptions(profile_dir=tmp_path / "profile", headless=False)

    context = launch_persistent_context_sync(playwright, options)

    assert context is not None
    assert playwright.chromium.kwargs is not None
    assert playwright.chromium.kwargs["headless"] is False
    assert playwright.chromium.kwargs["user_data_dir"] == str(tmp_path / "profile")


def test_build_persistent_context_kwargs_accepts_executable_path(tmp_path: Path) -> None:
    """EXE 可指定外部或隨附 Chromium executable。"""

    browser_exe = tmp_path / "chrome.exe"
    browser_exe.write_text("", encoding="utf-8")
    options = BrowserRuntimeOptions(
        profile_dir=tmp_path / "profile",
        executable_path=browser_exe,
    )

    kwargs = build_persistent_context_kwargs(options)

    assert kwargs["executable_path"] == str(browser_exe.resolve())


def test_build_persistent_context_kwargs_reads_browser_executable_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未直接傳入 executable_path 時可由環境變數提供。"""

    browser_exe = tmp_path / "chrome.exe"
    browser_exe.write_text("", encoding="utf-8")
    monkeypatch.setenv(BROWSER_EXECUTABLE_ENV, str(browser_exe))
    options = BrowserRuntimeOptions(profile_dir=tmp_path / "profile")

    kwargs = build_persistent_context_kwargs(options)

    assert kwargs["executable_path"] == str(browser_exe.resolve())


def test_frozen_runtime_uses_bundled_browser_when_env_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """frozen portable folder 可自動使用 EXE 旁的 bundled Chromium。"""

    app_dir = tmp_path / "app"
    app_dir.mkdir()
    exe = app_dir / "facebook-monitor.exe"
    exe.write_text("", encoding="utf-8")
    browser_exe = app_dir / "browser" / "chrome.exe"
    browser_exe.parent.mkdir()
    browser_exe.write_text("", encoding="utf-8")
    monkeypatch.delenv(BROWSER_EXECUTABLE_ENV, raising=False)
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", str(exe))
    options = BrowserRuntimeOptions(profile_dir=tmp_path / "profile")

    kwargs = build_persistent_context_kwargs(options)

    assert kwargs["executable_path"] == str(browser_exe.resolve())


def test_missing_browser_executable_path_fails_explicitly(tmp_path: Path) -> None:
    """browser executable path 指到不存在檔案時先給明確錯誤。"""

    options = BrowserRuntimeOptions(
        profile_dir=tmp_path / "profile",
        executable_path=tmp_path / "missing.exe",
    )

    with pytest.raises(BrowserRuntimeError, match="Browser executable does not exist"):
        build_persistent_context_kwargs(options)


def test_unsupported_browser_mode_fails_explicitly(tmp_path: Path) -> None:
    """Chrome/Edge/custom 只預留介面，尚未偽裝成已支援。"""

    options = BrowserRuntimeOptions(
        profile_dir=tmp_path / "profile",
        mode=BrowserMode.CHROME,
    )

    with pytest.raises(BrowserRuntimeError):
        build_persistent_context_kwargs(options)
