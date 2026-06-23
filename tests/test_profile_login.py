"""Guided Facebook profile login tests。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from pathlib import Path
import sqlite3
from typing import Any

from facebook_monitor.profile_login import CHROMIUM_EPOCH
from facebook_monitor.profile_login import FACEBOOK_SESSION_COOKIE_NAMES
from facebook_monitor.profile_login import GuidedLoginError
from facebook_monitor.profile_login import GuidedLoginOptions
from facebook_monitor.profile_login import iter_chromium_cookie_db_paths
from facebook_monitor.profile_login import profile_has_facebook_session_cookies
from facebook_monitor.profile_login import run_guided_facebook_login
from facebook_monitor.profile_login import snapshot_indicates_logged_in
from facebook_monitor.automation.profile_lease import ProfileLeaseError


def test_snapshot_indicates_logged_in_requires_facebook_session_cookies() -> None:
    """引導登入只在 Facebook session cookie 存在且頁面非登入流程時完成。"""

    assert snapshot_indicates_logged_in(
        {
            "cookie_names": tuple(sorted(FACEBOOK_SESSION_COOKIE_NAMES)),
            "current_url": "https://www.facebook.com/",
            "body_text": "首頁",
        }
    )
    assert not snapshot_indicates_logged_in(
        {
            "cookie_names": ("c_user",),
            "current_url": "https://www.facebook.com/",
            "body_text": "首頁",
        }
    )
    assert not snapshot_indicates_logged_in(
        {
            "cookie_names": tuple(sorted(FACEBOOK_SESSION_COOKIE_NAMES)),
            "current_url": "https://www.facebook.com/login/",
            "body_text": "Log into Facebook",
        }
    )


def test_profile_has_facebook_session_cookies_reads_chromium_cookie_db(
    tmp_path: Path,
) -> None:
    """不開瀏覽器時，可從 Chromium Cookies DB 判斷 profile 是否已有登入資料。"""

    profile_dir = tmp_path / "profile"
    cookie_db_path = profile_dir / "Default" / "Network" / "Cookies"
    _write_cookie_db(
        cookie_db_path,
        [
            ("c_user", ".facebook.com", _chromium_expires_utc(3600)),
            ("xs", ".facebook.com", _chromium_expires_utc(3600)),
        ],
    )

    assert profile_has_facebook_session_cookies(profile_dir)


def test_profile_cookie_check_rejects_missing_or_expired_cookie(tmp_path: Path) -> None:
    """缺少必要 cookie 或 cookie 已過期時，不視為已有可用登入資料。"""

    profile_dir = tmp_path / "profile"
    cookie_db_path = profile_dir / "Default" / "Network" / "Cookies"
    _write_cookie_db(
        cookie_db_path,
        [
            ("c_user", ".facebook.com", _chromium_expires_utc(3600)),
            ("xs", ".facebook.com", _chromium_expires_utc(-3600)),
        ],
    )

    assert not profile_has_facebook_session_cookies(profile_dir)
    assert not profile_has_facebook_session_cookies(tmp_path / "missing-profile")


def test_profile_cookie_check_discovers_nested_cookie_db(tmp_path: Path) -> None:
    """Chromium profile 內非標準位置的 Cookies DB 仍會被登入 gate 掃到。"""

    profile_dir = tmp_path / "profile"
    nested_cookie_db = profile_dir / "Profile 1" / "Network" / "Cookies"
    _write_cookie_db(
        nested_cookie_db,
        [
            ("c_user", ".facebook.com", _chromium_expires_utc(3600)),
            ("xs", ".facebook.com", _chromium_expires_utc(3600)),
        ],
    )

    assert iter_chromium_cookie_db_paths(profile_dir) == (nested_cookie_db,)
    assert profile_has_facebook_session_cookies(profile_dir)


def test_profile_cookie_check_rejects_non_facebook_host(tmp_path: Path) -> None:
    """非 Facebook host 即使 cookie 名稱相同，也不能讓 profile gate 通過。"""

    cookie_db_path = tmp_path / "profile" / "Cookies"
    _write_cookie_db(
        cookie_db_path,
        [
            ("c_user", "notfacebook.com", _chromium_expires_utc(3600)),
            ("xs", "notfacebook.com", _chromium_expires_utc(3600)),
        ],
    )

    assert not profile_has_facebook_session_cookies(tmp_path / "profile")


def test_guided_login_returns_true_after_session_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """引導登入偵測到 Facebook session 後關閉 context 並回傳成功。"""

    context = _FakeLoginContext(open_pages=True)
    snapshots: list[dict[str, object]] = [
        {
            "cookie_names": (),
            "current_url": "https://www.facebook.com/login",
            "body_text": "Log into Facebook",
        },
        {
            "cookie_names": tuple(sorted(FACEBOOK_SESSION_COOKIE_NAMES)),
            "current_url": "https://www.facebook.com/",
            "body_text": "首頁",
        },
    ]
    _patch_guided_login_runtime(monkeypatch, context, snapshots=snapshots)

    result = run_guided_facebook_login(
        GuidedLoginOptions(profile_dir=tmp_path / "profile", poll_interval_seconds=0),
        print_fn=lambda _message: None,
    )

    assert result is True
    assert context.closed
    assert context.page.goto_calls == [("https://www.facebook.com/", "domcontentloaded")]


def test_guided_login_returns_false_when_user_closes_all_pages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """使用者手動關閉所有登入頁時，引導登入取消且不啟動 Web UI。"""

    context = _FakeLoginContext(open_pages=False)
    _patch_guided_login_runtime(monkeypatch, context, snapshots=[])

    result = run_guided_facebook_login(
        GuidedLoginOptions(profile_dir=tmp_path / "profile"),
        print_fn=lambda _message: None,
    )

    assert result is False
    assert context.closed


def test_guided_login_wraps_profile_lease_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """profile lease 被占用時，引導登入需回報穩定的 GuidedLoginError。"""

    import facebook_monitor.profile_login as profile_login

    @contextmanager
    def failing_lease(_profile_dir: Path, _owner: str) -> Iterator[None]:
        raise ProfileLeaseError("profile busy")
        yield

    monkeypatch.setattr(profile_login, "acquire_profile_lease", failing_lease)

    try:
        run_guided_facebook_login(
            GuidedLoginOptions(profile_dir=tmp_path / "profile"),
            print_fn=lambda _message: None,
        )
    except GuidedLoginError as exc:
        assert str(exc) == "profile busy"
    else:
        raise AssertionError("expected GuidedLoginError")


def _write_cookie_db(
    cookie_db_path: Path,
    rows: list[tuple[str, str, int]],
) -> None:
    """建立測試用 Chromium Cookies DB。"""

    cookie_db_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(cookie_db_path)
    try:
        connection.execute(
            """
            CREATE TABLE cookies (
                name TEXT NOT NULL,
                host_key TEXT NOT NULL,
                expires_utc INTEGER NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO cookies (name, host_key, expires_utc) VALUES (?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()


def _chromium_expires_utc(offset_seconds: int) -> int:
    """建立 Chromium epoch microseconds 格式 expiry。"""

    return int(
        (datetime.now(timezone.utc) - CHROMIUM_EPOCH).total_seconds() * 1_000_000
    ) + offset_seconds * 1_000_000


class _FakeLoginPage:
    """測試用登入 page。"""

    def __init__(self, *, open_page: bool) -> None:
        self._open = open_page
        self.goto_calls: list[tuple[str, str]] = []

    def goto(self, url: str, *, wait_until: str) -> None:
        """記錄導頁呼叫。"""

        self.goto_calls.append((url, wait_until))

    def is_closed(self) -> bool:
        """回傳 fake page 是否已被使用者關閉。"""

        return not self._open


class _FakeLoginContext:
    """測試用 guided login browser context。"""

    def __init__(self, *, open_pages: bool) -> None:
        self.page = _FakeLoginPage(open_page=open_pages)
        self.pages = [self.page]
        self.closed = False

    def close(self) -> None:
        """記錄 context close。"""

        self.closed = True


class _FakePlaywrightContextManager:
    """測試用 sync_playwright context manager。"""

    def __enter__(self) -> object:
        return object()

    def __exit__(self, *args: object) -> None:
        return None


@contextmanager
def _fake_profile_lease(_profile_dir: Path, _owner: str) -> Iterator[None]:
    """測試用 profile lease，不碰 filesystem lock。"""

    yield


def _patch_guided_login_runtime(
    monkeypatch: Any,
    context: _FakeLoginContext,
    *,
    snapshots: list[dict[str, object]],
) -> None:
    """替換 guided login 的 Playwright/runtime 依賴。"""

    import facebook_monitor.profile_login as profile_login

    snapshot_iter = iter(snapshots)
    monkeypatch.setattr(profile_login, "acquire_profile_lease", _fake_profile_lease)
    monkeypatch.setattr(
        profile_login,
        "sync_playwright",
        lambda: _FakePlaywrightContextManager(),
    )
    monkeypatch.setattr(
        profile_login,
        "launch_persistent_context_sync",
        lambda _playwright, _options: context,
    )
    monkeypatch.setattr(profile_login, "get_start_page", lambda _context: context.page)
    monkeypatch.setattr(profile_login.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        profile_login,
        "read_facebook_session_snapshot",
        lambda _context: next(snapshot_iter),
    )
