"""Guided Facebook profile login tests。"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from pathlib import Path
import sqlite3

from facebook_monitor.profile_login import CHROMIUM_EPOCH
from facebook_monitor.profile_login import FACEBOOK_SESSION_COOKIE_NAMES
from facebook_monitor.profile_login import profile_has_facebook_session_cookies
from facebook_monitor.profile_login import snapshot_indicates_logged_in


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
