"""Profile setup CLI tests。"""

from __future__ import annotations

import builtins
from pathlib import Path

from facebook_monitor import profile_setup
from facebook_monitor.automation.browser_runtime import BrowserRuntimeOptions


def test_profile_setup_main_opens_runtime_profile_with_headed_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """profile setup CLI 使用 runtime resolver 的 automation profile 開 headed browser。"""

    context = _FakeSetupContext()
    launched_options: list[BrowserRuntimeOptions] = []

    def fake_launch(_playwright: object, options: BrowserRuntimeOptions) -> _FakeSetupContext:
        launched_options.append(options)
        return context

    monkeypatch.setattr(
        profile_setup,
        "sync_playwright",
        lambda: _FakePlaywrightContextManager(),
    )
    monkeypatch.setattr(profile_setup, "launch_persistent_context_sync", fake_launch)
    monkeypatch.setattr(builtins, "input", lambda _prompt: "")

    exit_code = profile_setup.main(
        [
            "--data-dir",
            str(tmp_path / "data"),
            "--start-url",
            "https://www.facebook.com/groups/test",
        ]
    )

    expected_profile_dir = tmp_path / "data" / "profiles" / "automation_default"
    assert exit_code == 0
    assert launched_options == [
        BrowserRuntimeOptions(profile_dir=expected_profile_dir, headless=False)
    ]
    assert context.page.goto_calls == [
        ("https://www.facebook.com/groups/test", "domcontentloaded")
    ]
    assert context.closed


class _FakeSetupPage:
    """測試用 setup page。"""

    def __init__(self) -> None:
        self.goto_calls: list[tuple[str, str]] = []

    def goto(self, url: str, *, wait_until: str) -> None:
        """記錄 profile setup 導頁。"""

        self.goto_calls.append((url, wait_until))


class _FakeSetupContext:
    """測試用 setup context。"""

    def __init__(self) -> None:
        self.page = _FakeSetupPage()
        self.closed = False

    def new_page(self) -> _FakeSetupPage:
        """回傳 fake page。"""

        return self.page

    def close(self) -> None:
        """記錄 context close。"""

        self.closed = True


class _FakePlaywrightContextManager:
    """測試用 sync_playwright context manager。"""

    def __enter__(self) -> object:
        return object()

    def __exit__(self, *args: object) -> None:
        return None
