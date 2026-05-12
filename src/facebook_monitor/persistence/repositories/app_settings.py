"""SQLite app-level UI preference repository。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.sqlite_codec import encode_datetime


ALLOWED_THEMES = frozenset({"light", "dark"})
DEFAULT_THEME = "light"
DEFAULT_EXCLUDE_KEYWORDS_TEXT = ";".join(PYTHON_TARGET_CONFIG_DEFAULTS.exclude_keywords)
DEFAULT_EXCLUDE_IGNORE_PHRASES_TEXT = ";".join(
    PYTHON_TARGET_CONFIG_DEFAULTS.exclude_ignore_phrases
)
DEFAULT_EXCLUDE_KEYWORDS_KEY = "default_exclude_keywords"
DEFAULT_EXCLUDE_IGNORE_PHRASES_KEY = "default_exclude_ignore_phrases"


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
        """讀取目前 Web UI 主題，未設定或資料異常時回傳淺色。"""

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
