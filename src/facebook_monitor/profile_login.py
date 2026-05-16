"""Facebook profile guided login flow。

職責：在 launcher 判定既有 automation profile 需要重新登入時，開啟
headed Playwright persistent context，等偵測到 Facebook session cookie 後自動關閉。
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from datetime import timedelta
from pathlib import Path
import sqlite3
import time
from typing import Any

from playwright.sync_api import sync_playwright

from facebook_monitor.automation.browser_runtime import BrowserRuntimeOptions
from facebook_monitor.automation.browser_runtime import launch_persistent_context_sync
from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.facebook.browser_capture import get_start_page
from facebook_monitor.worker.scan_orchestration import classify_facebook_session_failure


FACEBOOK_HOME_URL = "https://www.facebook.com/"
FACEBOOK_SESSION_COOKIE_NAMES = frozenset({"c_user", "xs"})
CHROMIUM_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


class GuidedLoginError(RuntimeError):
    """表示引導登入流程無法開啟或已被中止。"""


@dataclass(frozen=True)
class GuidedLoginOptions:
    """保存 launcher 引導登入需要的參數。"""

    profile_dir: Path
    start_url: str = FACEBOOK_HOME_URL
    poll_interval_seconds: float = 1.0


def run_guided_facebook_login(
    options: GuidedLoginOptions,
    *,
    print_fn: Callable[[str], None] = print,
) -> bool:
    """開啟 Facebook 登入視窗，偵測登入完成後自動關閉。"""

    poll_interval = max(float(options.poll_interval_seconds), 0.2)
    try:
        with acquire_profile_lease(options.profile_dir, "launcher guided login"):
            with sync_playwright() as playwright:
                context = launch_persistent_context_sync(
                    playwright,
                    BrowserRuntimeOptions(
                        profile_dir=options.profile_dir,
                        headless=False,
                    ),
                )
                try:
                    page = get_start_page(context)
                    page.goto(options.start_url, wait_until="domcontentloaded")
                    print_fn("Facebook 需要重新登入，已開啟登入視窗。")
                    print_fn("登入完成後視窗會自動關閉，接著啟動 Web UI。")
                    while True:
                        if _context_has_no_open_pages(context):
                            return False
                        snapshot = read_facebook_session_snapshot(context)
                        if snapshot_indicates_logged_in(snapshot):
                            return True
                        time.sleep(poll_interval)
                finally:
                    context.close()
    except ProfileLeaseError as exc:
        raise GuidedLoginError(str(exc)) from exc


def read_facebook_session_snapshot(context: Any) -> dict[str, object]:
    """讀取目前 context 的 cookie 與第一個可用 Facebook 頁面狀態。"""

    cookie_names = set()
    try:
        cookie_names = {str(cookie.get("name") or "") for cookie in context.cookies()}
    except Exception:
        cookie_names = set()

    current_url = ""
    body_text = ""
    for page in getattr(context, "pages", []) or []:
        try:
            if page.is_closed():
                continue
        except Exception:
            continue
        current_url = str(getattr(page, "url", "") or "")
        try:
            body_text = str(page.locator("body").inner_text(timeout=1000) or "")
        except Exception:
            body_text = ""
        break
    return {
        "cookie_names": tuple(sorted(cookie_names)),
        "current_url": current_url,
        "body_text": body_text,
    }


def snapshot_indicates_logged_in(snapshot: dict[str, object]) -> bool:
    """判斷 snapshot 是否足以代表 Facebook session 已可用。"""

    raw_cookie_names = snapshot.get("cookie_names", ())
    cookie_names = (
        {str(name) for name in raw_cookie_names}
        if isinstance(raw_cookie_names, Iterable)
        else set()
    )
    if not FACEBOOK_SESSION_COOKIE_NAMES.issubset(cookie_names):
        return False
    body_text = str(snapshot.get("body_text") or "")
    current_url = str(snapshot.get("current_url") or "")
    return classify_facebook_session_failure(body_text, current_url) is None


def profile_has_facebook_session_cookies(profile_dir: Path) -> bool:
    """不啟動瀏覽器，從 Chromium profile cookie DB 判斷是否已有登入資料。"""

    cookie_names: set[str] = set()
    for cookie_db_path in iter_chromium_cookie_db_paths(profile_dir):
        cookie_names.update(read_facebook_cookie_names(cookie_db_path))
        if FACEBOOK_SESSION_COOKIE_NAMES.issubset(cookie_names):
            return True
    return False


def iter_chromium_cookie_db_paths(profile_dir: Path) -> tuple[Path, ...]:
    """列出 Chromium persistent profile 可能存放 Cookies DB 的位置。"""

    if not profile_dir.exists():
        return ()
    candidates = [
        profile_dir / "Default" / "Network" / "Cookies",
        profile_dir / "Default" / "Cookies",
        profile_dir / "Network" / "Cookies",
        profile_dir / "Cookies",
    ]
    discovered = [
        path
        for path in profile_dir.rglob("Cookies")
        if path.is_file() and path not in candidates
    ]
    return tuple(path for path in [*candidates, *discovered] if path.is_file())


def read_facebook_cookie_names(cookie_db_path: Path) -> set[str]:
    """讀取單一 Chromium Cookies SQLite 內未過期的 Facebook cookie 名稱。"""

    try:
        connection = sqlite3.connect(
            f"{cookie_db_path.resolve().as_uri()}?mode=ro",
            uri=True,
        )
    except sqlite3.Error:
        return set()
    try:
        rows = connection.execute(
            """
            SELECT name, expires_utc
            FROM cookies
            WHERE host_key LIKE ?
            """,
            ("%facebook.com",),
        ).fetchall()
    except sqlite3.Error:
        return set()
    finally:
        connection.close()
    return {
        str(name)
        for name, expires_utc in rows
        if str(name) in FACEBOOK_SESSION_COOKIE_NAMES
        and _chromium_cookie_expiry_is_valid(int(expires_utc or 0))
    }


def _chromium_cookie_expiry_is_valid(expires_utc: int) -> bool:
    """判斷 Chromium cookie expiry 是否仍有效；0 視為 session cookie。"""

    if expires_utc <= 0:
        return True
    expires_at = CHROMIUM_EPOCH + timedelta(microseconds=expires_utc)
    return expires_at > datetime.now(timezone.utc)


def _context_has_no_open_pages(context: Any) -> bool:
    """判斷使用者是否已手動關閉所有登入視窗。"""

    for page in getattr(context, "pages", []) or []:
        try:
            if not page.is_closed():
                return False
        except Exception:
            continue
    return True
