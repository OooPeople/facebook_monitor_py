"""Notification channel metadata drift tests。"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any

from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.core.models import GlobalNotificationSettings
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.notification_channels import NOTIFICATION_ENDPOINT_FIELDS
from facebook_monitor.core.notification_channels import NOTIFICATION_CHANNEL_DEFINITIONS
from facebook_monitor.core.notification_channels import NOTIFICATION_SETTING_FIELDS
from facebook_monitor.core.notification_channels import notification_channel_sort_key
from facebook_monitor.core.sidebar_models import SidebarGroupConfigTemplate
from facebook_monitor.persistence import secret_storage


def _field_names(model: type[Any]) -> set[str]:
    """回傳 dataclass 欄位名稱。"""

    return {field.name for field in fields(model)}


def test_notification_setting_fields_exist_on_config_models() -> None:
    """集中定義的 notification 欄位必須存在於所有 config DTO。"""

    for model in (
        TargetConfig,
        GlobalNotificationSettings,
        SidebarGroupConfigTemplate,
        TargetConfigPatch,
    ):
        assert set(NOTIFICATION_SETTING_FIELDS) <= _field_names(model)


def test_notification_channel_sort_key_follows_definition_order() -> None:
    """通道顯示排序需跟集中 metadata 順序一致。"""

    assert [
        notification_channel_sort_key(definition.channel)
        for definition in NOTIFICATION_CHANNEL_DEFINITIONS
    ] == list(range(len(NOTIFICATION_CHANNEL_DEFINITIONS)))


def test_notification_endpoint_fields_drive_secret_storage_columns() -> None:
    """DB-at-rest 加密欄位必須跟 notification endpoint 定義同步。"""

    expected_columns = {
        (table_name, endpoint_field)
        for table_name in secret_storage.NOTIFICATION_SECRET_TABLES
        for endpoint_field in NOTIFICATION_ENDPOINT_FIELDS
    }
    expected_columns.update(secret_storage.OUTBOX_SECRET_COLUMNS)

    assert set(secret_storage._notification_secret_columns()) == expected_columns


def test_notification_template_field_names_match_channel_metadata() -> None:
    """通知設定 partial 不可使用 metadata 以外的 notification 欄位名稱。"""

    template = Path(
        "src/facebook_monitor/webapp/templates/_notification_settings_fields.html"
    ).read_text(encoding="utf-8")

    for field in NOTIFICATION_SETTING_FIELDS:
        assert f'name="{field}"' in template
