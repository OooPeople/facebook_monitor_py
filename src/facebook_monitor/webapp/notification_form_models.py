"""Web UI notification form models。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated
from typing import TypedDict

from fastapi import Form

from facebook_monitor.application.target_requests import UNSET_CONFIG_VALUE
from facebook_monitor.application.target_requests import UnsetConfigValue
from facebook_monitor.core.input_limits import normalize_notification_endpoint
from facebook_monitor.core.input_limits import normalize_ntfy_topic
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.notification_channels import NOTIFICATION_CHANNEL_DEFINITIONS
from facebook_monitor.notifications.discord_url import validate_discord_webhook_url
from facebook_monitor.webapp.form_parsing import checkbox_checked
from facebook_monitor.webapp.form_parsing import checkbox_payload


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


def secret_value_or_empty(value: str | UnsetConfigValue) -> str:
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
            ntfy_topic=secret_value_or_empty(
                self.resolved_ntfy_topic(
                    existing_ntfy_topic=existing_ntfy_topic,
                    allow_unset=False,
                )
            ),
            enable_discord_notification=self.enable_discord_notification,
            discord_webhook=secret_value_or_empty(
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


__all__ = [
    "NotificationConfigForm",
    "NotificationPatchKwargs",
    "NotificationSettingsKwargs",
    "build_notification_settings_kwargs",
    "format_notification_form_error",
    "secret_value_or_empty",
]
