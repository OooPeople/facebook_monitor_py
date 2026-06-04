"""Web UI form models。

職責：集中 HTML form 欄位解析與 application request 轉換，避免 route
各自手寫 keyword、checkbox 與 notification 欄位語義。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated
from typing import TypedDict

from fastapi import Form

from facebook_monitor.application.services import TargetConfigPatch
from facebook_monitor.application.services import UNSET_CONFIG_VALUE
from facebook_monitor.application.services import UnsetConfigValue
from facebook_monitor.application.services import UpsertCommentsTargetRequest
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.application.services import UpdateTargetConfigRequest
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.keyword_groups import flatten_include_keyword_groups
from facebook_monitor.core.keyword_groups import keyword_group_slots
from facebook_monitor.core.input_limits import normalize_notification_endpoint
from facebook_monitor.core.input_limits import normalize_ntfy_topic
from facebook_monitor.core.input_limits import parse_limited_keywords_text
from facebook_monitor.core.notification_channels import NOTIFICATION_CHANNEL_DEFINITIONS
from facebook_monitor.core.refresh_policy import MIN_REFRESH_SECONDS
from facebook_monitor.core.scan_limits import clamp_target_post_count
from facebook_monitor.core.models import IncludeKeywordGroup
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.sidebar_models import SidebarGroupConfigTemplate
from facebook_monitor.notifications.discord_url import validate_discord_webhook_url


FIXED_REFRESH_MODE = "fixed"
FLOATING_REFRESH_MODE = "floating"

_DISCORD_WEBHOOK_ERROR_MESSAGES = {
    "discord_webhook_url_too_long": "Discord webhook URL 過長",
    "discord_webhook_url_invalid": "Discord webhook URL 格式不正確",
    "discord_webhook_must_be_https": "Discord webhook 必須使用 HTTPS",
    "discord_webhook_host_not_allowed": "Discord webhook 必須是 Discord 官方 webhook URL",
    "discord_webhook_userinfo_not_allowed": "Discord webhook URL 格式不正確",
    "discord_webhook_port_not_allowed": "Discord webhook URL 格式不正確",
    "discord_webhook_path_invalid": "Discord webhook URL 格式不正確",
    "discord_webhook_extra_parts_not_allowed": "Discord webhook URL 格式不正確",
}


class NotificationSettingsKwargs(TypedDict):
    """通知設定欄位 kwargs；讓 dataclass 建構時保留精確型別。"""

    enable_desktop_notification: bool
    enable_ntfy: bool
    ntfy_topic: str
    enable_discord_notification: bool
    discord_webhook: str


class NotificationPatchKwargs(TypedDict):
    """target config patch 使用的通知欄位 kwargs。"""

    enable_desktop_notification: bool
    enable_ntfy: bool
    ntfy_topic: str | UnsetConfigValue
    enable_discord_notification: bool
    discord_webhook: str | UnsetConfigValue


def checkbox_checked(value: str | None) -> bool:
    """解析 HTML checkbox 欄位。"""

    return value == "on"


def checkbox_payload(value: object) -> str | None:
    """將 JSON payload 的 truthy 值轉成 HTML checkbox 表示。"""

    return "on" if bool(value) else None


def int_payload(value: object, fallback: int) -> int:
    """解析 JSON payload 整數欄位，失敗時回傳既有預設。"""

    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return fallback
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return fallback


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


def format_notification_form_error(exc: ValueError) -> str:
    """將通知表單驗證錯誤轉成安全、可顯示的繁中訊息。"""

    message = str(exc)
    return _DISCORD_WEBHOOK_ERROR_MESSAGES.get(message, message)


def _secret_string(value: str | UnsetConfigValue) -> str:
    """將不應出現在實際設定的 unset sentinel 轉成空字串。"""

    if isinstance(value, UnsetConfigValue):
        return ""
    return value


def _normalize_notification_endpoint(
    *,
    endpoint_field: str,
    value: object,
) -> str:
    """依通道 endpoint 欄位套用對應驗證。"""

    endpoint = str(value or "").strip()
    if endpoint_field == "discord_webhook":
        return validate_discord_webhook_url(endpoint)
    if endpoint_field == "ntfy_topic":
        return normalize_ntfy_topic(endpoint)
    return normalize_notification_endpoint(endpoint, field_label=endpoint_field)


def _notification_settings_kwargs_from_values(
    values: dict[str, object],
) -> NotificationSettingsKwargs:
    """依 notification channel definitions 取出設定欄位。"""

    kwargs: dict[str, object] = {}
    for definition in NOTIFICATION_CHANNEL_DEFINITIONS:
        kwargs[definition.enabled_field] = bool(values.get(definition.enabled_field))
        if definition.endpoint_field:
            kwargs[definition.endpoint_field] = _normalize_notification_endpoint(
                endpoint_field=definition.endpoint_field,
                value=values.get(definition.endpoint_field, ""),
            )
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
    ntfy_topic_keep: str | None = None
    clear_ntfy_topic: str | None = None
    enable_discord_notification: str | None = None
    discord_webhook: str = ""
    discord_webhook_keep: str | None = None
    clear_discord_webhook: str | None = None

    @classmethod
    def as_form(
        cls,
        enable_desktop_notification: Annotated[str | None, Form()] = None,
        enable_ntfy: Annotated[str | None, Form()] = None,
        ntfy_topic: Annotated[str, Form()] = "",
        ntfy_topic_keep: Annotated[str | None, Form()] = None,
        clear_ntfy_topic: Annotated[str | None, Form()] = None,
        enable_discord_notification: Annotated[str | None, Form()] = None,
        discord_webhook: Annotated[str, Form()] = "",
        discord_webhook_keep: Annotated[str | None, Form()] = None,
        clear_discord_webhook: Annotated[str | None, Form()] = None,
    ) -> NotificationConfigForm:
        """從 FastAPI HTML form dependency 建立通知設定表單。"""

        return cls(
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

    @classmethod
    def from_sidebar_template_payload(
        cls,
        payload: Mapping[str, object],
    ) -> NotificationConfigForm:
        """從 sidebar template JSON payload 建立通知表單。"""

        kwargs: dict[str, object] = {}
        for definition in NOTIFICATION_CHANNEL_DEFINITIONS:
            kwargs[definition.enabled_field] = checkbox_payload(
                payload.get(definition.enabled_field)
            )
            if definition.endpoint_field:
                kwargs[definition.endpoint_field] = str(
                    payload.get(definition.endpoint_field, "") or ""
                )
        kwargs["discord_webhook_keep"] = checkbox_payload(
            payload.get("discord_webhook_keep")
        )
        kwargs["clear_discord_webhook"] = checkbox_payload(
            payload.get("clear_discord_webhook")
        )
        kwargs["ntfy_topic_keep"] = checkbox_payload(payload.get("ntfy_topic_keep"))
        kwargs["clear_ntfy_topic"] = checkbox_payload(payload.get("clear_ntfy_topic"))
        return cls(
            enable_desktop_notification=checkbox_payload(
                kwargs.get("enable_desktop_notification")
            ),
            enable_ntfy=checkbox_payload(kwargs.get("enable_ntfy")),
            ntfy_topic=str(kwargs.get("ntfy_topic", "")),
            ntfy_topic_keep=checkbox_payload(kwargs.get("ntfy_topic_keep")),
            clear_ntfy_topic=checkbox_payload(kwargs.get("clear_ntfy_topic")),
            enable_discord_notification=checkbox_payload(
                kwargs.get("enable_discord_notification")
            ),
            discord_webhook=str(kwargs.get("discord_webhook", "")),
            discord_webhook_keep=checkbox_payload(kwargs.get("discord_webhook_keep")),
            clear_discord_webhook=checkbox_payload(kwargs.get("clear_discord_webhook")),
        )

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

    def to_target_config(
        self,
        *,
        target_id: str,
        existing_ntfy_topic: str = "",
        existing_discord_webhook: str = "",
    ) -> TargetConfig:
        """轉成 manual notification test 使用的 target config。"""

        return TargetConfig(
            target_id=target_id,
            **self.notification_settings_kwargs(
                existing_ntfy_topic=existing_ntfy_topic,
                existing_discord_webhook=existing_discord_webhook,
            ),
        )

    def notification_settings_kwargs(
        self,
        *,
        existing_ntfy_topic: str = "",
        existing_discord_webhook: str = "",
    ) -> NotificationSettingsKwargs:
        """回傳實際保存或測試通知使用的 notification 欄位。"""

        return build_notification_settings_kwargs(
            enable_desktop_notification=self.enable_desktop_notification,
            enable_ntfy=self.enable_ntfy,
            ntfy_topic=_secret_string(
                self.resolved_ntfy_topic(
                    existing_ntfy_topic=existing_ntfy_topic,
                    allow_unset=False,
                )
            ),
            enable_discord_notification=self.enable_discord_notification,
            discord_webhook=_secret_string(
                self.resolved_discord_webhook(
                    existing_discord_webhook=existing_discord_webhook,
                    allow_unset=False,
                )
            ),
        )

    def resolved_ntfy_topic(
        self,
        *,
        existing_ntfy_topic: str = "",
        allow_unset: bool,
    ) -> str | UnsetConfigValue:
        """依 masked form 欄位解析 ntfy topic 的更新語義。"""

        if checkbox_checked(self.clear_ntfy_topic):
            return ""
        raw_topic = self.ntfy_topic.strip()
        if raw_topic:
            return normalize_ntfy_topic(raw_topic)
        if checkbox_checked(self.ntfy_topic_keep):
            return UNSET_CONFIG_VALUE if allow_unset else str(existing_ntfy_topic or "")
        return ""

    def resolved_discord_webhook(
        self,
        *,
        existing_discord_webhook: str = "",
        allow_unset: bool,
    ) -> str | UnsetConfigValue:
        """依 masked form 欄位解析 Discord webhook 的更新語義。"""

        if checkbox_checked(self.clear_discord_webhook):
            return ""
        raw_webhook = self.discord_webhook.strip()
        if raw_webhook:
            return validate_discord_webhook_url(raw_webhook)
        if checkbox_checked(self.discord_webhook_keep):
            return UNSET_CONFIG_VALUE if allow_unset else str(existing_discord_webhook or "")
        return ""


@dataclass(frozen=True)
class TargetConfigForm:
    """保存 target create/update 共用設定欄位。"""

    include_keywords: str = ""
    include_keywords_2: str = ""
    include_keywords_3: str = ""
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
        exclude_keywords: Annotated[str, Form()] = "",
        exclude_ignore_phrases: Annotated[str, Form()] = "",
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
    ) -> TargetConfigForm:
        """從 FastAPI HTML form dependency 建立 target config form。"""

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

    @classmethod
    def from_sidebar_template_payload(
        cls,
        payload: Mapping[str, object],
    ) -> TargetConfigForm:
        """從 sidebar template JSON payload 建立 target config form。"""

        notification_form = NotificationConfigForm.from_sidebar_template_payload(payload)
        return cls(
            include_keywords=str(payload.get("include_keywords", "")),
            include_keywords_2=str(payload.get("include_keywords_2", "")),
            include_keywords_3=str(payload.get("include_keywords_3", "")),
            exclude_keywords=str(payload.get("exclude_keywords", "")),
            exclude_ignore_phrases=str(payload.get("exclude_ignore_phrases", "")),
            refresh_mode=str(payload.get("refresh_mode", FLOATING_REFRESH_MODE)),
            fixed_refresh_sec=int_payload(
                payload.get("fixed_refresh_sec"),
                PYTHON_TARGET_CONFIG_DEFAULTS.default_fixed_refresh_sec,
            ),
            min_refresh_sec=int_payload(
                payload.get("min_refresh_sec"),
                PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec,
            ),
            max_refresh_sec=int_payload(
                payload.get("max_refresh_sec"),
                PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec,
            ),
            max_items_per_scan=int_payload(
                payload.get("max_items_per_scan"),
                PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan,
            ),
            auto_load_more=checkbox_payload(payload.get("auto_load_more")),
            auto_adjust_sort=checkbox_payload(payload.get("auto_adjust_sort")),
            enable_desktop_notification=notification_form.enable_desktop_notification,
            enable_ntfy=notification_form.enable_ntfy,
            ntfy_topic=notification_form.ntfy_topic,
            ntfy_topic_keep=notification_form.ntfy_topic_keep,
            clear_ntfy_topic=notification_form.clear_ntfy_topic,
            enable_discord_notification=notification_form.enable_discord_notification,
            discord_webhook=notification_form.discord_webhook,
            discord_webhook_keep=notification_form.discord_webhook_keep,
            clear_discord_webhook=notification_form.clear_discord_webhook,
        )

    @property
    def include_keyword_tuple(self) -> tuple[str, ...]:
        """回傳已解析 include keywords。"""

        return flatten_include_keyword_groups(self.include_keyword_groups)

    @property
    def include_keyword_groups(self) -> tuple[IncludeKeywordGroup, ...]:
        """回傳已解析 include keyword groups。"""

        return keyword_group_slots(
            (
                parse_limited_keywords_text(
                    self.include_keywords,
                    field_label="包含關鍵字 1",
                ),
                parse_limited_keywords_text(
                    self.include_keywords_2,
                    field_label="包含關鍵字 2",
                ),
                parse_limited_keywords_text(
                    self.include_keywords_3,
                    field_label="包含關鍵字 3",
                ),
            )
        )

    @property
    def exclude_keyword_tuple(self) -> tuple[str, ...]:
        """回傳已解析 exclude keywords。"""

        return parse_limited_keywords_text(self.exclude_keywords, field_label="排除關鍵字")

    @property
    def exclude_ignore_phrase_tuple(self) -> tuple[str, ...]:
        """回傳已解析排除字忽略片語。"""

        return parse_limited_keywords_text(
            self.exclude_ignore_phrases,
            field_label="排除字忽略片語",
        )

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

    def to_config_patch(
        self,
        *,
        existing_ntfy_topic: str = "",
        existing_discord_webhook: str = "",
        preserve_secret_fields_as_unset: bool = True,
    ) -> TargetConfigPatch:
        """轉成 target config patch，供 create/update request 共用。"""

        self.validate_refresh_range()
        notification_kwargs = self.notification_patch_kwargs(
            existing_ntfy_topic=existing_ntfy_topic,
            existing_discord_webhook=existing_discord_webhook,
            allow_unset=preserve_secret_fields_as_unset,
        )
        return TargetConfigPatch(
            include_keywords=self.include_keyword_tuple,
            include_keyword_groups=self.include_keyword_groups,
            exclude_keywords=self.exclude_keyword_tuple,
            exclude_ignore_phrases=self.exclude_ignore_phrase_tuple,
            fixed_refresh_sec=self.refresh_fixed_value,
            min_refresh_sec=self.normalized_min_refresh_sec,
            max_refresh_sec=self.normalized_max_refresh_sec,
            jitter_enabled=self.refresh_jitter_enabled,
            max_items_per_scan=self.normalized_max_items_per_scan,
            auto_load_more=checkbox_checked(self.auto_load_more),
            auto_adjust_sort=checkbox_checked(self.auto_adjust_sort),
            enable_desktop_notification=notification_kwargs["enable_desktop_notification"],
            enable_ntfy=notification_kwargs["enable_ntfy"],
            ntfy_topic=notification_kwargs["ntfy_topic"],
            enable_discord_notification=notification_kwargs["enable_discord_notification"],
            discord_webhook=notification_kwargs["discord_webhook"],
        )

    def to_update_request(self, *, target_id: str) -> UpdateTargetConfigRequest:
        """轉成更新 target-scoped config 的 application request。"""

        return UpdateTargetConfigRequest(
            target_id=target_id,
            config=self.to_config_patch(),
        )

    def to_sidebar_group_template(
        self,
        *,
        sidebar_group_id: str,
        existing_ntfy_topic: str = "",
        existing_discord_webhook: str = "",
    ) -> SidebarGroupConfigTemplate:
        """轉成 sidebar group config template；不是 target config owner。"""

        self.validate_refresh_range()
        notification_kwargs = self.notification_settings_kwargs(
            existing_ntfy_topic=existing_ntfy_topic,
            existing_discord_webhook=existing_discord_webhook,
        )
        return SidebarGroupConfigTemplate(
            sidebar_group_id=sidebar_group_id,
            include_keywords=self.include_keyword_tuple,
            include_keyword_groups=self.include_keyword_groups,
            exclude_keywords=self.exclude_keyword_tuple,
            exclude_ignore_phrases=self.exclude_ignore_phrase_tuple,
            fixed_refresh_sec=self.refresh_fixed_value,
            min_refresh_sec=self.normalized_min_refresh_sec,
            max_refresh_sec=self.normalized_max_refresh_sec,
            jitter_enabled=self.refresh_jitter_enabled,
            max_items_per_scan=self.normalized_max_items_per_scan,
            auto_load_more=checkbox_checked(self.auto_load_more),
            auto_adjust_sort=checkbox_checked(self.auto_adjust_sort),
            **notification_kwargs,
        )

    def notification_settings_kwargs(
        self,
        *,
        existing_ntfy_topic: str = "",
        existing_discord_webhook: str = "",
    ) -> NotificationSettingsKwargs:
        """回傳 group template 實際保存用 notification 欄位。"""

        return build_notification_settings_kwargs(
            enable_desktop_notification=self.enable_desktop_notification,
            enable_ntfy=self.enable_ntfy,
            ntfy_topic=_secret_string(
                self.resolved_ntfy_topic(
                    existing_ntfy_topic=existing_ntfy_topic,
                    allow_unset=False,
                )
            ),
            enable_discord_notification=self.enable_discord_notification,
            discord_webhook=_secret_string(
                self.resolved_discord_webhook(
                    existing_discord_webhook=existing_discord_webhook,
                    allow_unset=False,
                )
            ),
        )

    def notification_patch_kwargs(
        self,
        *,
        existing_ntfy_topic: str = "",
        existing_discord_webhook: str = "",
        allow_unset: bool,
    ) -> NotificationPatchKwargs:
        """回傳 target config patch 用 notification 欄位。"""

        ntfy_topic = self.resolved_ntfy_topic(
            existing_ntfy_topic=existing_ntfy_topic,
            allow_unset=allow_unset,
        )
        discord_webhook = self.resolved_discord_webhook(
            existing_discord_webhook=existing_discord_webhook,
            allow_unset=allow_unset,
        )
        base = build_notification_settings_kwargs(
            enable_desktop_notification=self.enable_desktop_notification,
            enable_ntfy=self.enable_ntfy,
            ntfy_topic=_secret_string(ntfy_topic),
            enable_discord_notification=self.enable_discord_notification,
            discord_webhook=_secret_string(discord_webhook),
        )
        return {
            "enable_desktop_notification": base["enable_desktop_notification"],
            "enable_ntfy": base["enable_ntfy"],
            "ntfy_topic": ntfy_topic,
            "enable_discord_notification": base["enable_discord_notification"],
            "discord_webhook": discord_webhook,
        }

    def resolved_ntfy_topic(
        self,
        *,
        existing_ntfy_topic: str = "",
        allow_unset: bool,
    ) -> str | UnsetConfigValue:
        """依 masked form 欄位解析 ntfy topic 的更新語義。"""

        form = NotificationConfigForm(
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
        return form.resolved_ntfy_topic(
            existing_ntfy_topic=existing_ntfy_topic,
            allow_unset=allow_unset,
        )

    def resolved_discord_webhook(
        self,
        *,
        existing_discord_webhook: str = "",
        allow_unset: bool,
    ) -> str | UnsetConfigValue:
        """依 masked form 欄位解析 Discord webhook 的更新語義。"""

        form = NotificationConfigForm(
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
        return form.resolved_discord_webhook(
            existing_discord_webhook=existing_discord_webhook,
            allow_unset=allow_unset,
        )

    def to_group_posts_upsert_request(
        self,
        *,
        group_id: str,
        canonical_url: str,
        name: str,
        group_name: str,
        group_cover_image_url: str = "",
        existing_ntfy_topic: str = "",
        existing_discord_webhook: str = "",
    ) -> UpsertGroupPostsTargetRequest:
        """轉成 posts target upsert request。"""

        return UpsertGroupPostsTargetRequest(
            group_id=group_id,
            canonical_url=canonical_url,
            name=name,
            group_name=group_name,
            group_cover_image_url=group_cover_image_url,
            config=self.to_config_patch(
                existing_ntfy_topic=existing_ntfy_topic,
                existing_discord_webhook=existing_discord_webhook,
                preserve_secret_fields_as_unset=False,
            ),
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
        existing_ntfy_topic: str = "",
        existing_discord_webhook: str = "",
    ) -> UpsertCommentsTargetRequest:
        """轉成 comments target upsert request。"""

        return UpsertCommentsTargetRequest(
            group_id=group_id,
            parent_post_id=parent_post_id,
            canonical_url=canonical_url,
            name=name,
            group_name=group_name,
            group_cover_image_url=group_cover_image_url,
            config=self.to_config_patch(
                existing_ntfy_topic=existing_ntfy_topic,
                existing_discord_webhook=existing_discord_webhook,
                preserve_secret_fields_as_unset=False,
            ),
        )


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
