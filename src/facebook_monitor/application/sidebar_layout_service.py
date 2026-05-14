"""Sidebar layout application service。

職責：集中 sidebar 排序、分組與 group template 套用規則，避免 Web route
直接操作 SQL 或讓 group template 變成 target config fallback。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.sidebar_models import SidebarGroup
from facebook_monitor.core.sidebar_models import SidebarGroupConfigTemplate
from facebook_monitor.core.keyword_text import parse_keywords_text
from facebook_monitor.persistence.repositories.app_settings import AppSettingsRepository
from facebook_monitor.persistence.repositories.sidebar_layout import SidebarLayoutRepository
from facebook_monitor.persistence.repositories.target_configs import TargetConfigRepository
from facebook_monitor.persistence.repositories.targets import TargetRepository

SidebarTemplateSection = Literal["keywords", "scan", "notifications", "all"]


class SidebarLayoutService:
    """處理 sidebar UI layout 與 group template batch apply。"""

    def __init__(
        self,
        *,
        targets: TargetRepository,
        configs: TargetConfigRepository,
        app_settings: AppSettingsRepository,
        sidebar_layout: SidebarLayoutRepository,
    ) -> None:
        self.targets = targets
        self.configs = configs
        self.app_settings = app_settings
        self.sidebar_layout = sidebar_layout

    def create_group(self, name: str) -> SidebarGroup:
        """建立新的 sidebar UI group。"""

        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("群組名稱不可空白")
        group = self.sidebar_layout.save_group(
            SidebarGroup.create(
                name=normalized_name,
                sort_order=self.sidebar_layout.next_group_sort_order(),
            )
        )
        self.sidebar_layout.save_template(
            self._new_group_default_template(sidebar_group_id=group.id)
        )
        return group

    def rename_group(self, group_id: str, name: str) -> SidebarGroup:
        """更新 sidebar group 名稱。"""

        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("群組名稱不可空白")
        return self.sidebar_layout.rename_group(group_id, normalized_name)

    def set_group_collapsed(self, group_id: str, collapsed: bool) -> SidebarGroup:
        """更新 sidebar group 收合狀態。"""

        return self.sidebar_layout.set_group_collapsed(group_id, collapsed)

    def delete_empty_group(self, group_id: str) -> None:
        """刪除空 sidebar group；非空 group 不可直接刪除。"""

        if self.sidebar_layout.get_group(group_id) is None:
            raise ValueError("sidebar group not found")
        if self.sidebar_layout.count_targets_in_group(group_id) > 0:
            raise ValueError("群組內仍有 target，請先移出後再刪除")
        self.sidebar_layout.delete_group(group_id)

    def save_group_order(self, group_ids: list[str]) -> None:
        """保存 sidebar group 顯示順序。"""

        self._validate_group_order(group_ids)
        self.sidebar_layout.save_group_order(_unique_ids(group_ids))

    def save_target_order(self, target_ids: list[str]) -> int:
        """保存未分組平面 target 順序，供排序第一階段使用。"""

        if any(
            placement.sidebar_group_id is not None
            for placement in self.sidebar_layout.list_placements().values()
        ):
            raise ValueError("已有 sidebar group placement，請使用 grouped placement API")
        payload_ids = _unique_ids(target_ids)
        known_ids = self._all_target_ids()
        if set(payload_ids) != set(known_ids):
            raise ValueError("排序 payload 必須包含所有 target")
        self.sidebar_layout.save_group_placements([(None, payload_ids)])
        return len(payload_ids)

    def save_layout(
        self,
        *,
        group_ids: list[str],
        grouped_target_ids: list[tuple[str | None, list[str]]],
    ) -> int:
        """以單一 application transaction 保存 group order 與 target placements。"""

        self._validate_group_order(group_ids)
        updated_count = self._validate_placements(grouped_target_ids)
        self.sidebar_layout.save_group_order(_unique_ids(group_ids))
        self.sidebar_layout.save_group_placements(
            [
                (group_id, _unique_ids(target_ids))
                for group_id, target_ids in grouped_target_ids
            ]
        )
        return updated_count

    def save_placements(
        self,
        grouped_target_ids: list[tuple[str | None, list[str]]],
    ) -> int:
        """保存 group-scoped target placement。"""

        updated_count = self._validate_placements(grouped_target_ids)
        self.sidebar_layout.save_group_placements(
            [
                (group_id, _unique_ids(target_ids))
                for group_id, target_ids in grouped_target_ids
            ]
        )
        return updated_count

    def _validate_group_order(self, group_ids: list[str]) -> None:
        """驗證 group order payload 完整且不含未知 group。"""

        known_ids = {group.id for group in self.sidebar_layout.list_groups()}
        payload_ids = _unique_ids(group_ids)
        if set(payload_ids) != known_ids:
            raise ValueError("群組排序 payload 必須包含所有 sidebar group")

    def _validate_placements(
        self,
        grouped_target_ids: list[tuple[str | None, list[str]]],
    ) -> int:
        """驗證 placement payload 完整且沒有未知 group / target。"""

        known_group_ids = {group.id for group in self.sidebar_layout.list_groups()}
        payload_group_ids = [
            group_id
            for group_id, _target_ids in grouped_target_ids
            if group_id is not None
        ]
        if any(group_id not in known_group_ids for group_id in payload_group_ids):
            raise ValueError("placement payload contains unknown sidebar group")

        flattened: list[str] = []
        for _group_id, target_ids in grouped_target_ids:
            flattened.extend(target_ids)
        payload_target_ids = _unique_ids(flattened)
        known_target_ids = self._all_target_ids()
        if set(payload_target_ids) != set(known_target_ids):
            raise ValueError("placement payload 必須剛好包含所有 target")
        if len(payload_target_ids) != len(flattened):
            raise ValueError("placement payload 不可有重複 target")
        return len(payload_target_ids)

    def save_template(
        self,
        template: SidebarGroupConfigTemplate,
    ) -> SidebarGroupConfigTemplate:
        """保存 sidebar group config template。"""

        if self.sidebar_layout.get_group(template.sidebar_group_id) is None:
            raise ValueError("sidebar group not found")
        return self.sidebar_layout.save_template(template)

    def get_template_or_default(self, group_id: str) -> SidebarGroupConfigTemplate:
        """讀取 group template；未建立時回傳預設模板但不寫入 DB。"""

        if self.sidebar_layout.get_group(group_id) is None:
            raise ValueError("sidebar group not found")
        return (
            self.sidebar_layout.get_template(group_id)
            or SidebarGroupConfigTemplate(sidebar_group_id=group_id)
        )

    def apply_template(
        self,
        group_id: str,
        sections: list[SidebarTemplateSection],
    ) -> int:
        """將 group template 明確複製到 group 內每個 target config。"""

        normalized_sections = _normalize_sections(sections)
        template = self.get_template_or_default(group_id)
        target_ids = self.sidebar_layout.list_target_ids_for_group(group_id)
        for target_id in target_ids:
            current = self.configs.get_for_target_id(target_id) or TargetConfig(target_id=target_id)
            next_config = _merge_template_sections(
                current=current,
                template=template,
                sections=normalized_sections,
            )
            self.configs.save_for_target_id(target_id, next_config)
        return len(target_ids)

    def _all_target_ids(self) -> list[str]:
        """回傳所有 target ids，維持 repository 原本 created_at 排序。"""

        return [target.id for target in self.targets.list_all()]

    def _new_group_default_template(self, *, sidebar_group_id: str) -> SidebarGroupConfigTemplate:
        """建立 group 時 snapshot 當下全域關鍵字預設值。"""

        keyword_defaults = self.app_settings.get_target_keyword_defaults()
        return SidebarGroupConfigTemplate(
            sidebar_group_id=sidebar_group_id,
            exclude_keywords=parse_keywords_text(keyword_defaults.exclude_keywords_text),
            exclude_ignore_phrases=parse_keywords_text(
                keyword_defaults.exclude_ignore_phrases_text
            ),
        )


def _unique_ids(values: list[str]) -> list[str]:
    """整理 id 清單並拒絕空白 id。"""

    normalized = [str(value).strip() for value in values]
    if any(not value for value in normalized):
        raise ValueError("id 不可空白")
    return list(dict.fromkeys(normalized))


def _normalize_sections(
    sections: list[SidebarTemplateSection],
) -> tuple[SidebarTemplateSection, ...]:
    """整理 template 套用區段。"""

    normalized = tuple(dict.fromkeys(section for section in sections if section))
    if not normalized:
        raise ValueError("至少需要選擇一個套用區段")
    allowed = {"keywords", "scan", "notifications", "all"}
    if any(section not in allowed for section in normalized):
        raise ValueError("未知的群組模板套用區段")
    if "all" in normalized:
        return ("keywords", "scan", "notifications")
    return normalized


def _merge_template_sections(
    *,
    current: TargetConfig,
    template: SidebarGroupConfigTemplate,
    sections: tuple[SidebarTemplateSection, ...],
) -> TargetConfig:
    """依指定區段把 template 複製到 target-scoped config。"""

    next_config = current
    if "keywords" in sections:
        next_config = replace(
            next_config,
            include_keywords=template.include_keywords,
            exclude_keywords=template.exclude_keywords,
            exclude_ignore_phrases=template.exclude_ignore_phrases,
        )
    if "scan" in sections:
        next_config = replace(
            next_config,
            min_refresh_sec=template.min_refresh_sec,
            max_refresh_sec=template.max_refresh_sec,
            jitter_enabled=template.jitter_enabled,
            fixed_refresh_sec=template.fixed_refresh_sec,
            max_items_per_scan=template.max_items_per_scan,
            auto_load_more=template.auto_load_more,
            auto_adjust_sort=template.auto_adjust_sort,
        )
    if "notifications" in sections:
        next_config = replace(
            next_config,
            enable_desktop_notification=template.enable_desktop_notification,
            enable_ntfy=template.enable_ntfy,
            ntfy_topic=template.ntfy_topic,
            enable_discord_notification=template.enable_discord_notification,
            discord_webhook=template.discord_webhook,
        )
    return next_config
