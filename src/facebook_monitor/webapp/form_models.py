"""Web UI form models。

職責：集中 HTML form 欄位解析與 application request 轉換，避免 route
各自手寫 keyword、checkbox 與 notification 欄位語義。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from facebook_monitor.application.services import TargetConfigPatch
from facebook_monitor.application.services import UpsertCommentsTargetRequest
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.application.services import UpdateTargetConfigRequest
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.keyword_text import parse_keywords_text
from facebook_monitor.core.notification_channels import NOTIFICATION_CHANNEL_DEFINITIONS
from facebook_monitor.core.refresh_policy import MIN_REFRESH_SECONDS
from facebook_monitor.core.scan_limits import clamp_target_post_count
from facebook_monitor.core.models import GlobalNotificationSettings
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.sidebar_models import SidebarGroupConfigTemplate


FIXED_REFRESH_MODE = "fixed"
FLOATING_REFRESH_MODE = "floating"


class NotificationSettingsKwargs(TypedDict):
    """通知設定欄位 kwargs；讓 dataclass 建構時保留精確型別。"""

    enable_desktop_notification: bool
    enable_ntfy: bool
    ntfy_topic: str
    enable_discord_notification: bool
    discord_webhook: str


class NotificationFormKwargs(TypedDict):
    """通知表單欄位 kwargs；供動態 payload 轉 TargetConfigForm 使用。"""

    enable_desktop_notification: str | None
    enable_ntfy: str | None
    ntfy_topic: str
    enable_discord_notification: str | None
    discord_webhook: str


def checkbox_checked(value: str | None) -> bool:
    """解析 HTML checkbox 欄位。"""

    return value == "on"


def normalize_refresh_seconds(value: int, fallback: int) -> int:
    """整理 refresh 秒數，套用至少 5 秒的保護。"""

    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = int(fallback)
    return max(seconds, MIN_REFRESH_SECONDS)


def build_notification_settings_kwargs(
    *,
    enable_desktop_notification: str | None,
    enable_ntfy: str | None,
    ntfy_topic: str,
    enable_discord_notification: str | None,
    discord_webhook: str,
) -> NotificationSettingsKwargs:
    """將通知表單欄位整理成 config / settings 共用 kwargs。"""

    form_values = {
        "enable_desktop_notification": checkbox_checked(enable_desktop_notification),
        "enable_ntfy": checkbox_checked(enable_ntfy),
        "ntfy_topic": ntfy_topic.strip(),
        "enable_discord_notification": checkbox_checked(enable_discord_notification),
        "discord_webhook": discord_webhook.strip(),
    }
    return _notification_settings_kwargs_from_values(form_values)


def _notification_settings_kwargs_from_values(
    values: dict[str, object],
) -> NotificationSettingsKwargs:
    """依 notification channel definitions 取出設定欄位。"""

    kwargs: dict[str, object] = {}
    for definition in NOTIFICATION_CHANNEL_DEFINITIONS:
        kwargs[definition.enabled_field] = bool(values.get(definition.enabled_field))
        if definition.endpoint_field:
            kwargs[definition.endpoint_field] = str(
                values.get(definition.endpoint_field, "") or ""
            ).strip()
    return NotificationSettingsKwargs(
        enable_desktop_notification=bool(kwargs.get("enable_desktop_notification")),
        enable_ntfy=bool(kwargs.get("enable_ntfy")),
        ntfy_topic=str(kwargs.get("ntfy_topic", "")),
        enable_discord_notification=bool(kwargs.get("enable_discord_notification")),
        discord_webhook=str(kwargs.get("discord_webhook", "")),
    )


@dataclass(frozen=True)
class NotificationConfigForm:
    """保存通知表單欄位，集中轉換成設定或測試通知 config。"""

    enable_desktop_notification: str | None = None
    enable_ntfy: str | None = None
    ntfy_topic: str = ""
    enable_discord_notification: str | None = None
    discord_webhook: str = ""

    @property
    def desktop_enabled(self) -> bool:
        """回傳桌面通知 checkbox 狀態。"""

        return checkbox_checked(self.enable_desktop_notification)

    @property
    def ntfy_enabled(self) -> bool:
        """回傳 ntfy checkbox 狀態。"""

        return checkbox_checked(self.enable_ntfy)

    @property
    def discord_enabled(self) -> bool:
        """回傳 Discord checkbox 狀態。"""

        return checkbox_checked(self.enable_discord_notification)

    def to_global_settings(self) -> GlobalNotificationSettings:
        """轉成全域通知預設值。"""

        return GlobalNotificationSettings(**self.notification_kwargs)

    def to_target_config(self, *, target_id: str) -> TargetConfig:
        """轉成 manual notification test 使用的 target config。"""

        return TargetConfig(target_id=target_id, **self.notification_kwargs)

    @property
    def notification_kwargs(self) -> NotificationSettingsKwargs:
        """回傳 notification config/settings 欄位。"""

        return build_notification_settings_kwargs(
            enable_desktop_notification=self.enable_desktop_notification,
            enable_ntfy=self.enable_ntfy,
            ntfy_topic=self.ntfy_topic,
            enable_discord_notification=self.enable_discord_notification,
            discord_webhook=self.discord_webhook,
        )


@dataclass(frozen=True)
class TargetConfigForm:
    """保存 target create/update 共用設定欄位。"""

    include_keywords: str = ""
    exclude_keywords: str = ""
    exclude_ignore_phrases: str = ""
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
    enable_discord_notification: str | None = None
    discord_webhook: str = ""

    @property
    def include_keyword_tuple(self) -> tuple[str, ...]:
        """回傳已解析 include keywords。"""

        return parse_keywords_text(self.include_keywords)

    @property
    def exclude_keyword_tuple(self) -> tuple[str, ...]:
        """回傳已解析 exclude keywords。"""

        return parse_keywords_text(self.exclude_keywords)

    @property
    def exclude_ignore_phrase_tuple(self) -> tuple[str, ...]:
        """回傳已解析排除字忽略片語。"""

        return parse_keywords_text(self.exclude_ignore_phrases)

    @property
    def normalized_refresh_mode(self) -> str:
        """回傳目前使用的 refresh mode。"""

        mode = self.refresh_mode.strip().lower()
        if mode in {FIXED_REFRESH_MODE, FLOATING_REFRESH_MODE}:
            return mode
        raise ValueError("刷新模式必須是 fixed 或 floating")

    @property
    def normalized_fixed_refresh_sec(self) -> int:
        """回傳至少 5 秒的固定掃描間隔。"""

        return normalize_refresh_seconds(
            self.fixed_refresh_sec,
            PYTHON_TARGET_CONFIG_DEFAULTS.default_fixed_refresh_sec,
        )

    @property
    def normalized_min_refresh_sec(self) -> int:
        """回傳至少 5 秒的浮動最小掃描間隔。"""

        return normalize_refresh_seconds(
            self.min_refresh_sec,
            PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec,
        )

    @property
    def normalized_max_refresh_sec(self) -> int:
        """回傳至少 5 秒的浮動最大掃描間隔。"""

        return normalize_refresh_seconds(
            self.max_refresh_sec,
            PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec,
        )

    @property
    def refresh_fixed_value(self) -> int | None:
        """依 refresh mode 回傳 fixed_refresh_sec 寫入值。"""

        if self.normalized_refresh_mode == FLOATING_REFRESH_MODE:
            return None
        return self.normalized_fixed_refresh_sec

    @property
    def refresh_jitter_enabled(self) -> bool:
        """依 refresh mode 回傳 jitter_enabled 寫入值。"""

        return self.normalized_refresh_mode == FLOATING_REFRESH_MODE

    def validate_refresh_range(self) -> None:
        """確認浮動刷新範圍合法。"""

        if self.normalized_refresh_mode != FLOATING_REFRESH_MODE:
            return
        if self.normalized_min_refresh_sec > self.normalized_max_refresh_sec:
            raise ValueError("浮動刷新最小秒數不可大於最大秒數")

    @property
    def normalized_max_items_per_scan(self) -> int:
        """回傳符合 domain policy 的每輪掃描上限。"""

        return clamp_target_post_count(self.max_items_per_scan)

    def to_config_patch(self) -> TargetConfigPatch:
        """轉成 target config patch，供 create/update request 共用。"""

        self.validate_refresh_range()
        return TargetConfigPatch(
            include_keywords=self.include_keyword_tuple,
            exclude_keywords=self.exclude_keyword_tuple,
            exclude_ignore_phrases=self.exclude_ignore_phrase_tuple,
            fixed_refresh_sec=self.refresh_fixed_value,
            min_refresh_sec=self.normalized_min_refresh_sec,
            max_refresh_sec=self.normalized_max_refresh_sec,
            jitter_enabled=self.refresh_jitter_enabled,
            max_items_per_scan=self.normalized_max_items_per_scan,
            auto_load_more=checkbox_checked(self.auto_load_more),
            auto_adjust_sort=checkbox_checked(self.auto_adjust_sort),
            **self.notification_kwargs,
        )

    def to_update_request(self, *, target_id: str) -> UpdateTargetConfigRequest:
        """轉成更新 target-scoped config 的 application request。"""

        return UpdateTargetConfigRequest(
            target_id=target_id,
            config=self.to_config_patch(),
        )

    def to_sidebar_group_template(self, *, sidebar_group_id: str) -> SidebarGroupConfigTemplate:
        """轉成 sidebar group config template；不是 target config owner。"""

        self.validate_refresh_range()
        return SidebarGroupConfigTemplate(
            sidebar_group_id=sidebar_group_id,
            include_keywords=self.include_keyword_tuple,
            exclude_keywords=self.exclude_keyword_tuple,
            exclude_ignore_phrases=self.exclude_ignore_phrase_tuple,
            fixed_refresh_sec=self.refresh_fixed_value,
            min_refresh_sec=self.normalized_min_refresh_sec,
            max_refresh_sec=self.normalized_max_refresh_sec,
            jitter_enabled=self.refresh_jitter_enabled,
            max_items_per_scan=self.normalized_max_items_per_scan,
            auto_load_more=checkbox_checked(self.auto_load_more),
            auto_adjust_sort=checkbox_checked(self.auto_adjust_sort),
            **self.notification_kwargs,
        )

    @property
    def notification_kwargs(self) -> NotificationSettingsKwargs:
        """回傳 target config / group template 共用 notification 欄位。"""

        return build_notification_settings_kwargs(
            enable_desktop_notification=self.enable_desktop_notification,
            enable_ntfy=self.enable_ntfy,
            ntfy_topic=self.ntfy_topic,
            enable_discord_notification=self.enable_discord_notification,
            discord_webhook=self.discord_webhook,
        )

    def to_group_posts_upsert_request(
        self,
        *,
        group_id: str,
        canonical_url: str,
        name: str,
        group_name: str,
        group_cover_image_url: str = "",
    ) -> UpsertGroupPostsTargetRequest:
        """轉成 posts target upsert request。"""

        return UpsertGroupPostsTargetRequest(
            group_id=group_id,
            canonical_url=canonical_url,
            name=name,
            group_name=group_name,
            group_cover_image_url=group_cover_image_url,
            config=self.to_config_patch(),
        )

    def to_comments_upsert_request(
        self,
        *,
        group_id: str,
        parent_post_id: str,
        canonical_url: str,
        name: str,
        group_name: str,
        group_cover_image_url: str = "",
    ) -> UpsertCommentsTargetRequest:
        """轉成 comments target upsert request。"""

        return UpsertCommentsTargetRequest(
            group_id=group_id,
            parent_post_id=parent_post_id,
            canonical_url=canonical_url,
            name=name,
            group_name=group_name,
            group_cover_image_url=group_cover_image_url,
            config=self.to_config_patch(),
        )
