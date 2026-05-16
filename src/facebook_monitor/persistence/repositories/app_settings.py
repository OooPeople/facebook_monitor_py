"""SQLite app-level UI preference repository。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import json

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.sqlite_codec import decode_datetime
from facebook_monitor.persistence.sqlite_codec import encode_datetime


ALLOWED_THEMES = frozenset({"light", "dark"})
DEFAULT_THEME = "dark"
DEFAULT_EXCLUDE_KEYWORDS_TEXT = ";".join(PYTHON_TARGET_CONFIG_DEFAULTS.exclude_keywords)
DEFAULT_EXCLUDE_IGNORE_PHRASES_TEXT = ";".join(
    PYTHON_TARGET_CONFIG_DEFAULTS.exclude_ignore_phrases
)
DEFAULT_EXCLUDE_KEYWORDS_KEY = "default_exclude_keywords"
DEFAULT_EXCLUDE_IGNORE_PHRASES_KEY = "default_exclude_ignore_phrases"
PROFILE_SESSION_STATUS_KEY = "profile_session_status"


class ProfileSessionState(StrEnum):
    """保存 Facebook automation profile 的全域 session 狀態。"""

    UNKNOWN = "unknown"
    OK = "ok"
    NEEDS_LOGIN = "needs_login"


@dataclass(frozen=True)
class ProfileSessionStatus:
    """保存登入 profile 是否需要使用者重新登入。"""

    state: ProfileSessionState = ProfileSessionState.UNKNOWN
    reason: str = ""
    source: str = ""
    updated_at: datetime | None = None

    @property
    def needs_login(self) -> bool:
        """回傳 Web UI 與 launcher 是否應啟動重新登入流程。"""

        return self.state == ProfileSessionState.NEEDS_LOGIN


@dataclass(frozen=True)
class TargetKeywordDefaultSettings:
    """保存新增 target 時使用的關鍵字預設值文字。"""

    exclude_keywords_text: str = DEFAULT_EXCLUDE_KEYWORDS_TEXT
    exclude_ignore_phrases_text: str = DEFAULT_EXCLUDE_IGNORE_PHRASES_TEXT


class AppSettingsRepository:
    """保存不屬於 target config 的本機 Web UI 偏好。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get_theme(self) -> str:
        """讀取目前 Web UI 主題，未設定或資料異常時回傳深色。"""

        theme = self._get_value("theme", fallback="").strip()
        return theme if theme in ALLOWED_THEMES else DEFAULT_THEME

    def save_theme(self, theme: str) -> str:
        """保存 Web UI 主題並回傳正規化後的值。"""

        normalized_theme = theme.strip()
        if normalized_theme not in ALLOWED_THEMES:
            raise ValueError("theme must be light or dark")
        self._save_value("theme", normalized_theme)
        return normalized_theme

    def get_target_keyword_defaults(self) -> TargetKeywordDefaultSettings:
        """讀取新增 target 表單使用的關鍵字預設值。"""

        return TargetKeywordDefaultSettings(
            exclude_keywords_text=self._get_value(
                DEFAULT_EXCLUDE_KEYWORDS_KEY,
                fallback=DEFAULT_EXCLUDE_KEYWORDS_TEXT,
            ).strip(),
            exclude_ignore_phrases_text=self._get_value(
                DEFAULT_EXCLUDE_IGNORE_PHRASES_KEY,
                fallback=DEFAULT_EXCLUDE_IGNORE_PHRASES_TEXT,
            ).strip(),
        )

    def save_target_keyword_defaults(
        self,
        settings: TargetKeywordDefaultSettings,
    ) -> TargetKeywordDefaultSettings:
        """保存新增 target 表單使用的關鍵字預設值。"""

        normalized = TargetKeywordDefaultSettings(
            exclude_keywords_text=settings.exclude_keywords_text.strip(),
            exclude_ignore_phrases_text=settings.exclude_ignore_phrases_text.strip(),
        )
        self._save_value(
            DEFAULT_EXCLUDE_KEYWORDS_KEY,
            normalized.exclude_keywords_text,
        )
        self._save_value(
            DEFAULT_EXCLUDE_IGNORE_PHRASES_KEY,
            normalized.exclude_ignore_phrases_text,
        )
        return normalized

    def get_profile_session_status(self) -> ProfileSessionStatus:
        """讀取 automation profile 登入狀態；資料異常時回到 unknown。"""

        raw_value = self._get_value(PROFILE_SESSION_STATUS_KEY, fallback="")
        if not raw_value:
            return ProfileSessionStatus()
        try:
            payload = json.loads(raw_value)
        except json.JSONDecodeError:
            return ProfileSessionStatus()
        if not isinstance(payload, dict):
            return ProfileSessionStatus()
        try:
            state = ProfileSessionState(str(payload.get("state") or ""))
        except ValueError:
            return ProfileSessionStatus()
        updated_at = None
        updated_at_value = payload.get("updated_at")
        if isinstance(updated_at_value, str):
            try:
                updated_at = decode_datetime(updated_at_value)
            except ValueError:
                updated_at = None
        return ProfileSessionStatus(
            state=state,
            reason=str(payload.get("reason") or ""),
            source=str(payload.get("source") or ""),
            updated_at=updated_at,
        )

    def mark_profile_needs_login(
        self,
        *,
        reason: str,
        source: str,
    ) -> ProfileSessionStatus:
        """標記 profile session 已失效，需要下次 launcher 先引導登入。"""

        normalized_reason = reason.strip()
        normalized_source = source.strip()
        current = self.get_profile_session_status()
        if (
            current.state == ProfileSessionState.NEEDS_LOGIN
            and current.reason == normalized_reason
            and current.source == normalized_source
        ):
            return current
        return self._save_profile_session_status(
            ProfileSessionStatus(
                state=ProfileSessionState.NEEDS_LOGIN,
                reason=normalized_reason,
                source=normalized_source,
                updated_at=utc_now(),
            )
        )

    def mark_profile_ok(self, *, source: str) -> ProfileSessionStatus:
        """標記 profile session 已可用；已是 ok 時不重複寫入避免 revision churn。"""

        current = self.get_profile_session_status()
        if current.state == ProfileSessionState.OK:
            return current
        return self._save_profile_session_status(
            ProfileSessionStatus(
                state=ProfileSessionState.OK,
                source=source.strip(),
                updated_at=utc_now(),
            )
        )

    def _get_value(self, key: str, *, fallback: str) -> str:
        """讀取單一 app setting；未寫入時回傳 fallback。"""

        row = self.connection.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return fallback
        return str(row["value"])

    def _save_value(self, key: str, value: str) -> None:
        """寫入單一 app setting。"""

        self.connection.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """,
            (key, value, encode_datetime(utc_now())),
        )

    def _save_profile_session_status(
        self,
        status: ProfileSessionStatus,
    ) -> ProfileSessionStatus:
        """以 JSON 保存 profile session 狀態。"""

        payload = {
            "state": status.state.value,
            "reason": status.reason,
            "source": status.source,
            "updated_at": encode_datetime(status.updated_at),
        }
        self._save_value(
            PROFILE_SESSION_STATUS_KEY,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
        return status
