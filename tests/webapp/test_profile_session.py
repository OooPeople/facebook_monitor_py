"""Profile session manager tests。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from facebook_monitor.automation.browser_runtime import BrowserRuntimeOptions
from facebook_monitor.webapp.profile_session import ProfileSessionError
from facebook_monitor.webapp.profile_session import ProfileSessionManager
from facebook_monitor.webapp.profile_session import ProfileSessionOptions
from facebook_monitor.webapp.profile_session import _is_browser_closed_error


def test_profile_session_open_and_close_use_headed_profile_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """profile session 會用 headed persistent context，close 時釋放 context 並呼叫 callback。"""

    context = _FakeProfileContext()
    launched_options: list[BrowserRuntimeOptions] = []
    on_close_calls: list[str] = []
    _patch_profile_session_runtime(monkeypatch, context, launched_options)
    manager = ProfileSessionManager()

    manager.open(
        ProfileSessionOptions(
            profile_dir=tmp_path / "profile",
            start_url="https://www.facebook.com/groups/test",
            on_close=lambda: on_close_calls.append("closed"),
        )
    )

    assert manager.is_active()
    assert (tmp_path / "profile").is_dir()
    assert launched_options == [
        BrowserRuntimeOptions(profile_dir=tmp_path / "profile", headless=False)
    ]
    assert context.page.goto_calls == [
        ("https://www.facebook.com/groups/test", "domcontentloaded")
    ]

    manager.close()

    assert not manager.is_active()
    assert context.close_count == 1
    assert on_close_calls == ["closed"]


def test_profile_session_ignores_second_open_while_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已有 profile 視窗開啟時，第二次 open 不會再啟動另一個 context。"""

    context = _FakeProfileContext()
    launched_options: list[BrowserRuntimeOptions] = []
    _patch_profile_session_runtime(monkeypatch, context, launched_options)
    manager = ProfileSessionManager()

    manager.open(ProfileSessionOptions(profile_dir=tmp_path / "profile"))
    manager.open(
        ProfileSessionOptions(
            profile_dir=tmp_path / "profile",
            start_url="https://www.facebook.com/other",
        )
    )

    assert len(launched_options) == 1
    assert context.page.goto_calls == [
        ("https://www.facebook.com/groups/", "domcontentloaded")
    ]

    manager.close()


def test_profile_session_open_failure_clears_active_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Playwright 啟動失敗會轉成 ProfileSessionError，且不殘留 active session。"""

    context = _FakeProfileContext(goto_error=RuntimeError("launch failed"))
    launched_options: list[BrowserRuntimeOptions] = []
    _patch_profile_session_runtime(monkeypatch, context, launched_options)
    manager = ProfileSessionManager()

    with pytest.raises(ProfileSessionError, match="launch failed"):
        manager.open(ProfileSessionOptions(profile_dir=tmp_path / "profile"))

    assert not manager.is_active()
    assert context.close_count == 1


def test_profile_session_treats_browser_closed_before_ready_as_nonfatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """使用者在 ready 前關閉 browser 時，不把已關閉訊息回報成 profile 開啟錯誤。"""

    context = _FakeProfileContext(
        goto_error=RuntimeError("Target page, context or browser has been closed")
    )
    launched_options: list[BrowserRuntimeOptions] = []
    on_close_calls: list[str] = []
    _patch_profile_session_runtime(monkeypatch, context, launched_options)
    manager = ProfileSessionManager()

    manager.open(
        ProfileSessionOptions(
            profile_dir=tmp_path / "profile",
            on_close=lambda: on_close_calls.append("closed"),
        )
    )
    manager.close()

    assert not manager.is_active()
    assert context.close_count == 1
    assert on_close_calls == ["closed"]
    assert _is_browser_closed_error(RuntimeError("browser has been closed"))


class _FakeProfilePage:
    """測試用 profile page。"""

    def __init__(self, *, goto_error: Exception | None = None) -> None:
        self.goto_error = goto_error
        self.closed = False
        self.goto_calls: list[tuple[str, str]] = []

    def goto(self, url: str, *, wait_until: str) -> None:
        """記錄導頁或丟出指定錯誤。"""

        self.goto_calls.append((url, wait_until))
        if self.goto_error is not None:
            raise self.goto_error

    def is_closed(self) -> bool:
        """回傳 fake page 是否已關閉。"""

        return self.closed


class _FakeProfileContext:
    """測試用 persistent context。"""

    def __init__(self, *, goto_error: Exception | None = None) -> None:
        self.page = _FakeProfilePage(goto_error=goto_error)
        self.pages = [self.page]
        self.close_count = 0

    def close(self) -> None:
        """標記 context 已關閉。"""

        self.close_count += 1
        self.page.closed = True


class _FakePlaywrightContextManager:
    """測試用 sync_playwright context manager。"""

    def __enter__(self) -> object:
        return object()

    def __exit__(self, *args: object) -> None:
        return None


@contextmanager
def _fake_profile_lease(_profile_dir: Path, _owner: str) -> Iterator[None]:
    """測試用 profile lease，不建立 lock file。"""

    yield


def _patch_profile_session_runtime(
    monkeypatch: pytest.MonkeyPatch,
    context: _FakeProfileContext,
    launched_options: list[BrowserRuntimeOptions],
) -> None:
    """替換 profile session 的 Playwright/runtime 依賴。"""

    import facebook_monitor.webapp.profile_session as profile_session

    def fake_launch(_playwright: object, options: BrowserRuntimeOptions) -> _FakeProfileContext:
        launched_options.append(options)
        return context

    monkeypatch.setattr(profile_session, "acquire_profile_lease", _fake_profile_lease)
    monkeypatch.setattr(
        profile_session,
        "sync_playwright",
        lambda: _FakePlaywrightContextManager(),
    )
    monkeypatch.setattr(profile_session, "launch_persistent_context_sync", fake_launch)
    monkeypatch.setattr(profile_session, "get_start_page", lambda _context: context.page)
