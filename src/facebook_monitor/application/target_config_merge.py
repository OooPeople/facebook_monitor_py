"""Target config patch merge helpers。

職責：集中 TargetConfigPatch 到 TargetConfig 的欄位合併規則，避免建立、
更新與未來 DTO 轉換各自複製 target config 欄位清單。
"""

from __future__ import annotations

from dataclasses import fields
from dataclasses import replace
from typing import Any

from facebook_monitor.core.keyword_groups import flatten_include_keyword_groups
from facebook_monitor.core.keyword_groups import legacy_include_keyword_groups
from facebook_monitor.core.keyword_groups import normalize_include_keyword_groups
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UnsetConfigValue
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.scan_limits import clamp_target_post_count

TARGET_CONFIG_PATCH_FIELDS = tuple(field.name for field in fields(TargetConfigPatch))


def _normalize_config_field(field_name: str, value: Any) -> Any:
    """套用單一 target config 欄位的 domain normalize 規則。"""

    if field_name == "max_items_per_scan":
        return clamp_target_post_count(value)
    return value


def _patch_value_or_default(patch: TargetConfigPatch, field_name: str) -> Any:
    """未提供 patch 欄位時回傳正式 target config 預設值。"""

    value = getattr(patch, field_name)
    if isinstance(value, UnsetConfigValue):
        value = getattr(TargetConfig(target_id=""), field_name)
    return _normalize_config_field(field_name, value)


def _patch_value_or_existing(
    patch: TargetConfigPatch,
    existing_config: TargetConfig,
    field_name: str,
) -> Any:
    """未提供 patch 欄位時保留既有 target config 值。"""

    value = getattr(patch, field_name)
    if isinstance(value, UnsetConfigValue):
        value = getattr(existing_config, field_name)
    return _normalize_config_field(field_name, value)


def build_target_config_from_patch(target_id: str, patch: TargetConfigPatch) -> TargetConfig:
    """將 target config patch 轉成完整 target-scoped config。"""

    values = {
        field_name: _patch_value_or_default(patch, field_name)
        for field_name in TARGET_CONFIG_PATCH_FIELDS
    }
    values = _normalize_include_keyword_values(values, patch=patch)
    return TargetConfig(target_id=target_id, **values)


def merge_target_config_patch(
    existing_config: TargetConfig,
    patch: TargetConfigPatch,
) -> TargetConfig:
    """將 target config patch 合併到既有 target-scoped config。"""

    values = {
        field_name: _patch_value_or_existing(patch, existing_config, field_name)
        for field_name in TARGET_CONFIG_PATCH_FIELDS
    }
    values = _normalize_include_keyword_values(values, patch=patch)
    return replace(existing_config, **values)


def _normalize_include_keyword_values(
    values: dict[str, Any],
    *,
    patch: TargetConfigPatch,
) -> dict[str, Any]:
    """同步整理 include keyword groups 與 legacy flat projection。"""

    group_patch_provided = not isinstance(patch.include_keyword_groups, UnsetConfigValue)
    include_patch_provided = not isinstance(patch.include_keywords, UnsetConfigValue)
    if group_patch_provided:
        groups = normalize_include_keyword_groups(
            values["include_keyword_groups"],
            fill_empty_slots=True,
        )
        values["include_keyword_groups"] = groups
        values["include_keywords"] = flatten_include_keyword_groups(groups)
        return values
    if include_patch_provided:
        groups = legacy_include_keyword_groups(
            values["include_keywords"],
            fill_empty_slots=True,
        )
        values["include_keyword_groups"] = groups
    return values
