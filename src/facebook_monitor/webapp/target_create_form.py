"""Web UI create-target form model。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Form

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.webapp.form_refresh import FLOATING_REFRESH_MODE
from facebook_monitor.webapp.target_config_form import TargetConfigForm


@dataclass(frozen=True)
class CreateTargetConfigFormFields:
    """保存新增 target 表單欄位，保留缺欄套用關鍵字預設的語義。"""

    include_keywords: str = ""
    include_keywords_2: str = ""
    include_keywords_3: str = ""
    exclude_keywords: str | None = None
    exclude_ignore_phrases: str | None = None
    refresh_mode: str = FLOATING_REFRESH_MODE
    fixed_refresh_sec: int = PYTHON_TARGET_CONFIG_DEFAULTS.default_fixed_refresh_sec
    min_refresh_sec: int = PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec
    max_refresh_sec: int = PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec
    max_items_per_scan: int = PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan
    auto_load_more: str | None = None
    auto_adjust_sort: str | None = None
    enable_desktop_notification: str | None = None
    enable_ntfy: str | None = None
    ntfy_topic: str = ""
    ntfy_topic_keep: str | None = None
    clear_ntfy_topic: str | None = None
    enable_discord_notification: str | None = None
    discord_webhook: str = ""
    discord_webhook_keep: str | None = None
    clear_discord_webhook: str | None = None

    @classmethod
    def as_form(
        cls,
        include_keywords: Annotated[str, Form()] = "",
        include_keywords_2: Annotated[str, Form()] = "",
        include_keywords_3: Annotated[str, Form()] = "",
        exclude_keywords: Annotated[str | None, Form()] = None,
        exclude_ignore_phrases: Annotated[str | None, Form()] = None,
        refresh_mode: Annotated[str, Form()] = FLOATING_REFRESH_MODE,
        fixed_refresh_sec: Annotated[
            int,
            Form(),
        ] = PYTHON_TARGET_CONFIG_DEFAULTS.default_fixed_refresh_sec,
        min_refresh_sec: Annotated[
            int,
            Form(),
        ] = PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec,
        max_refresh_sec: Annotated[
            int,
            Form(),
        ] = PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec,
        max_items_per_scan: Annotated[
            int,
            Form(),
        ] = PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan,
        auto_load_more: Annotated[str | None, Form()] = None,
        auto_adjust_sort: Annotated[str | None, Form()] = None,
        enable_desktop_notification: Annotated[str | None, Form()] = None,
        enable_ntfy: Annotated[str | None, Form()] = None,
        ntfy_topic: Annotated[str, Form()] = "",
        ntfy_topic_keep: Annotated[str | None, Form()] = None,
        clear_ntfy_topic: Annotated[str | None, Form()] = None,
        enable_discord_notification: Annotated[str | None, Form()] = None,
        discord_webhook: Annotated[str, Form()] = "",
        discord_webhook_keep: Annotated[str | None, Form()] = None,
        clear_discord_webhook: Annotated[str | None, Form()] = None,
    ) -> CreateTargetConfigFormFields:
        """從新增 target HTML form 建立 raw fields。"""

        return cls(
            include_keywords=include_keywords,
            include_keywords_2=include_keywords_2,
            include_keywords_3=include_keywords_3,
            exclude_keywords=exclude_keywords,
            exclude_ignore_phrases=exclude_ignore_phrases,
            refresh_mode=refresh_mode,
            fixed_refresh_sec=fixed_refresh_sec,
            min_refresh_sec=min_refresh_sec,
            max_refresh_sec=max_refresh_sec,
            max_items_per_scan=max_items_per_scan,
            auto_load_more=auto_load_more,
            auto_adjust_sort=auto_adjust_sort,
            enable_desktop_notification=enable_desktop_notification,
            enable_ntfy=enable_ntfy,
            ntfy_topic=ntfy_topic,
            ntfy_topic_keep=ntfy_topic_keep,
            clear_ntfy_topic=clear_ntfy_topic,
            enable_discord_notification=enable_discord_notification,
            discord_webhook=discord_webhook,
            discord_webhook_keep=discord_webhook_keep,
            clear_discord_webhook=clear_discord_webhook,
        )

    def to_target_config_form(
        self,
        *,
        default_exclude_keywords: str,
        default_exclude_ignore_phrases: str,
    ) -> TargetConfigForm:
        """套用新增 target 專屬預設後轉成共用 target config form。"""

        return TargetConfigForm(
            include_keywords=self.include_keywords,
            include_keywords_2=self.include_keywords_2,
            include_keywords_3=self.include_keywords_3,
            exclude_keywords=(
                default_exclude_keywords
                if self.exclude_keywords is None
                else self.exclude_keywords
            ),
            exclude_ignore_phrases=(
                default_exclude_ignore_phrases
                if self.exclude_ignore_phrases is None
                else self.exclude_ignore_phrases
            ),
            refresh_mode=self.refresh_mode,
            fixed_refresh_sec=self.fixed_refresh_sec,
            min_refresh_sec=self.min_refresh_sec,
            max_refresh_sec=self.max_refresh_sec,
            max_items_per_scan=self.max_items_per_scan,
            auto_load_more=self.auto_load_more,
            auto_adjust_sort=self.auto_adjust_sort,
            enable_desktop_notification=self.enable_desktop_notification,
            enable_ntfy=self.enable_ntfy,
            ntfy_topic=self.ntfy_topic,
            ntfy_topic_keep=self.ntfy_topic_keep,
            clear_ntfy_topic=self.clear_ntfy_topic,
            enable_discord_notification=self.enable_discord_notification,
            discord_webhook=self.discord_webhook,
            discord_webhook_keep=self.discord_webhook_keep,
            clear_discord_webhook=self.clear_discord_webhook,
        )


__all__ = [
    "CreateTargetConfigFormFields",
]
