"""Dashboard sidebar section read model 組裝。"""

from __future__ import annotations

from facebook_monitor.core.sidebar_models import SidebarGroup
from facebook_monitor.core.sidebar_models import SidebarGroupConfigTemplate
from facebook_monitor.core.sidebar_models import SidebarTargetPlacement
from facebook_monitor.webapp.dashboard_models import SidebarGroupSection
from facebook_monitor.webapp.dashboard_models import TargetRow


def build_sidebar_groups(
    *,
    rows: tuple[TargetRow, ...],
    placements_by_target: dict[str, SidebarTargetPlacement],
    groups: tuple[SidebarGroup, ...],
    templates_by_group: dict[str, SidebarGroupConfigTemplate],
) -> tuple[SidebarGroupSection, ...]:
    """依 rows 與 placement 建立 sidebar sections。"""

    rows_by_group: dict[str | None, list[TargetRow]] = {group.id: [] for group in groups}
    rows_by_group[None] = []
    known_group_ids = {group.id for group in groups}
    for row in rows:
        placement = placements_by_target.get(row.target_id)
        group_id = placement.sidebar_group_id if placement else None
        if group_id not in known_group_ids:
            group_id = None
        rows_by_group.setdefault(group_id, []).append(row)

    sections: list[SidebarGroupSection] = []
    for group in groups:
        sections.append(
            SidebarGroupSection(
                group_id=group.id,
                name=group.name,
                collapsed=group.collapsed,
                items=tuple(row.sidebar_item for row in rows_by_group.get(group.id, ())),
                template=templates_by_group.get(group.id),
            )
        )
    ungrouped_rows = rows_by_group.get(None, [])
    sections.append(
        SidebarGroupSection(
            group_id=None,
            name="未分組",
            items=tuple(row.sidebar_item for row in ungrouped_rows),
            is_system=True,
        )
    )
    return tuple(sections)
