"""Web UI target config form model。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated

from fastapi import Form

from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UnsetConfigValue
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.application.target_requests import UpdateTargetConfigRequest
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.input_limits import parse_limited_keywords_text
from facebook_monitor.core.keyword_groups import flatten_include_keyword_groups
from facebook_monitor.core.keyword_groups import keyword_group_slots
from facebook_monitor.core.models import IncludeKeywordGroup
from facebook_monitor.core.scan_limits import clamp_target_post_count
from facebook_monitor.core.sidebar_models import SidebarGroupConfigTemplate
from facebook_monitor.webapp.form_parsing import checkbox_checked
from facebook_monitor.webapp.form_parsing import checkbox_payload
from facebook_monitor.webapp.form_parsing import int_payload
from facebook_monitor.webapp.form_refresh import FIXED_REFRESH_MODE
from facebook_monitor.webapp.form_refresh import FLOATING_REFRESH_MODE
from facebook_monitor.webapp.form_refresh import normalize_refresh_seconds
from facebook_monitor.webapp.notification_form_models import NotificationConfigForm
from facebook_monitor.webapp.notification_form_models import NotificationPatchKwargs
from facebook_monitor.webapp.notification_form_models import NotificationSettingsKwargs
from facebook_monitor.webapp.notification_form_models import build_notification_settings_kwargs
from facebook_monitor.webapp.notification_form_models import secret_value_or_empty


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
            ntfy_topic=secret_value_or_empty(ntfy_topic),
            enable_discord_notification=self.enable_discord_notification,
            discord_webhook=secret_value_or_empty(discord_webhook),
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



__all__ = [
    "TargetConfigForm",
]
