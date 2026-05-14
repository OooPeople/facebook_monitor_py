"""SQLite repository for sidebar layout and group templates。

職責：保存 Web UI sidebar 分組、target placement 與 group config template。
template 只供 application service 明確套用，不參與 target config fallback。
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime

from facebook_monitor.core.sidebar_models import SidebarGroup
from facebook_monitor.core.sidebar_models import SidebarGroupConfigTemplate
from facebook_monitor.core.sidebar_models import SidebarTargetPlacement
from facebook_monitor.persistence.secret_storage import PlaintextSecretCodec
from facebook_monitor.persistence.secret_storage import SecretCodec
from facebook_monitor.persistence.sqlite_codec import decode_datetime
from facebook_monitor.persistence.sqlite_codec import decode_keywords
from facebook_monitor.persistence.sqlite_codec import encode_datetime
from facebook_monitor.persistence.sqlite_codec import encode_keywords


class SidebarLayoutRepository:
    """保存 sidebar layout 與 group template。"""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        secret_codec: SecretCodec | PlaintextSecretCodec,
    ) -> None:
        self.connection = connection
        self.secret_codec = secret_codec

    def list_groups(self) -> tuple[SidebarGroup, ...]:
        """依 sidebar sort order 列出使用者建立的 group。"""

        rows = self.connection.execute(
            "SELECT * FROM sidebar_groups ORDER BY sort_order, created_at, id"
        ).fetchall()
        return tuple(_group_from_row(row) for row in rows)

    def get_group(self, group_id: str) -> SidebarGroup | None:
        """依 id 讀取 sidebar group。"""

        row = self.connection.execute(
            "SELECT * FROM sidebar_groups WHERE id = ?",
            (group_id,),
        ).fetchone()
        return _group_from_row(row) if row else None

    def save_group(self, group: SidebarGroup) -> SidebarGroup:
        """新增或更新 sidebar group。"""

        self.connection.execute(
            """
            INSERT INTO sidebar_groups (
                id, name, sort_order, collapsed, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                sort_order=excluded.sort_order,
                collapsed=excluded.collapsed,
                updated_at=excluded.updated_at
            """,
            (
                group.id,
                group.name,
                group.sort_order,
                int(group.collapsed),
                encode_datetime(group.created_at),
                encode_datetime(group.updated_at),
            ),
        )
        return group

    def next_group_sort_order(self) -> int:
        """回傳新 group 應使用的尾端 sort_order。"""

        row = self.connection.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM sidebar_groups"
        ).fetchone()
        return int(row["next_order"]) if row else 0

    def rename_group(self, group_id: str, name: str) -> SidebarGroup:
        """更新 group 名稱。"""

        group = self.get_group(group_id)
        if group is None:
            raise ValueError("sidebar group not found")
        updated = replace(group, name=name.strip(), updated_at=_now())
        return self.save_group(updated)

    def set_group_collapsed(self, group_id: str, collapsed: bool) -> SidebarGroup:
        """保存 group sidebar 收合狀態。"""

        group = self.get_group(group_id)
        if group is None:
            raise ValueError("sidebar group not found")
        updated = replace(group, collapsed=collapsed, updated_at=_now())
        return self.save_group(updated)

    def save_group_order(self, group_ids: list[str]) -> None:
        """依傳入順序保存 group 排序。"""

        for index, group_id in enumerate(group_ids):
            self.connection.execute(
                """
                UPDATE sidebar_groups
                SET sort_order = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (index, group_id),
            )

    def delete_group(self, group_id: str) -> bool:
        """刪除空 sidebar group。"""

        cursor = self.connection.execute("DELETE FROM sidebar_groups WHERE id = ?", (group_id,))
        return cursor.rowcount > 0

    def count_targets_in_group(self, group_id: str) -> int:
        """計算 group 內 target 數量。"""

        row = self.connection.execute(
            """
            SELECT COUNT(1) AS count
            FROM sidebar_target_placements
            WHERE sidebar_group_id = ?
            """,
            (group_id,),
        ).fetchone()
        return int(row["count"]) if row else 0

    def list_placements(self) -> dict[str, SidebarTargetPlacement]:
        """列出所有 target placement，以 target_id 索引。"""

        rows = self.connection.execute("SELECT * FROM sidebar_target_placements").fetchall()
        return {
            row["target_id"]: _placement_from_row(row)
            for row in rows
        }

    def list_target_ids_for_group(self, group_id: str) -> tuple[str, ...]:
        """列出指定 group 內 target ids。"""

        rows = self.connection.execute(
            """
            SELECT target_id
            FROM sidebar_target_placements
            WHERE sidebar_group_id = ?
            ORDER BY sort_order, updated_at, target_id
            """,
            (group_id,),
        ).fetchall()
        return tuple(str(row["target_id"]) for row in rows)

    def ensure_default_placements(self, target_ids: list[str]) -> None:
        """為尚未有 placement 的 target 建立未分組尾端位置。"""

        normalized_ids = [target_id for target_id in dict.fromkeys(target_ids) if target_id]
        if not normalized_ids:
            return
        existing = set(self.list_placements())
        missing = [target_id for target_id in normalized_ids if target_id not in existing]
        if not missing:
            return
        row = self.connection.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM sidebar_target_placements"
        ).fetchone()
        next_order = int(row["next_order"]) if row else 0
        for offset, target_id in enumerate(missing):
            self.save_placement(
                SidebarTargetPlacement(
                    target_id=target_id,
                    sidebar_group_id=None,
                    sort_order=next_order + offset,
                )
            )

    def save_placement(self, placement: SidebarTargetPlacement) -> SidebarTargetPlacement:
        """新增或更新單一 target placement。"""

        self.connection.execute(
            """
            INSERT INTO sidebar_target_placements (
                target_id, sidebar_group_id, sort_order, updated_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET
                sidebar_group_id=excluded.sidebar_group_id,
                sort_order=excluded.sort_order,
                updated_at=excluded.updated_at
            """,
            (
                placement.target_id,
                placement.sidebar_group_id,
                placement.sort_order,
                encode_datetime(placement.updated_at),
            ),
        )
        return placement

    def save_group_placements(
        self,
        grouped_target_ids: list[tuple[str | None, list[str]]],
    ) -> None:
        """保存每個 sidebar group 內的 target 順序。"""

        for group_id, target_ids in grouped_target_ids:
            for index, target_id in enumerate(target_ids):
                self.save_placement(
                    SidebarTargetPlacement(
                        target_id=target_id,
                        sidebar_group_id=group_id,
                        sort_order=index,
                    )
                )

    def get_template(self, group_id: str) -> SidebarGroupConfigTemplate | None:
        """讀取 group config template。"""

        row = self.connection.execute(
            "SELECT * FROM sidebar_group_config_templates WHERE sidebar_group_id = ?",
            (group_id,),
        ).fetchone()
        if row is None:
            return None
        return self._decrypt_template(_template_from_row(row))

    def save_template(
        self,
        template: SidebarGroupConfigTemplate,
    ) -> SidebarGroupConfigTemplate:
        """新增或更新 group config template。"""

        encrypted = replace(
            template,
            ntfy_topic=self.secret_codec.encrypt(template.ntfy_topic),
            discord_webhook=self.secret_codec.encrypt(template.discord_webhook),
        )
        self.connection.execute(
            """
            INSERT INTO sidebar_group_config_templates (
                sidebar_group_id, include_keywords, exclude_keywords, exclude_ignore_phrases,
                min_refresh_sec, max_refresh_sec, jitter_enabled, fixed_refresh_sec,
                max_items_per_scan, auto_load_more, auto_adjust_sort,
                enable_desktop_notification, enable_ntfy, ntfy_topic,
                enable_discord_notification, discord_webhook, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sidebar_group_id) DO UPDATE SET
                include_keywords=excluded.include_keywords,
                exclude_keywords=excluded.exclude_keywords,
                exclude_ignore_phrases=excluded.exclude_ignore_phrases,
                min_refresh_sec=excluded.min_refresh_sec,
                max_refresh_sec=excluded.max_refresh_sec,
                jitter_enabled=excluded.jitter_enabled,
                fixed_refresh_sec=excluded.fixed_refresh_sec,
                max_items_per_scan=excluded.max_items_per_scan,
                auto_load_more=excluded.auto_load_more,
                auto_adjust_sort=excluded.auto_adjust_sort,
                enable_desktop_notification=excluded.enable_desktop_notification,
                enable_ntfy=excluded.enable_ntfy,
                ntfy_topic=excluded.ntfy_topic,
                enable_discord_notification=excluded.enable_discord_notification,
                discord_webhook=excluded.discord_webhook,
                updated_at=excluded.updated_at
            """,
            (
                encrypted.sidebar_group_id,
                encode_keywords(encrypted.include_keywords),
                encode_keywords(encrypted.exclude_keywords),
                encode_keywords(encrypted.exclude_ignore_phrases),
                encrypted.min_refresh_sec,
                encrypted.max_refresh_sec,
                int(encrypted.jitter_enabled),
                encrypted.fixed_refresh_sec,
                encrypted.max_items_per_scan,
                int(encrypted.auto_load_more),
                int(encrypted.auto_adjust_sort),
                int(encrypted.enable_desktop_notification),
                int(encrypted.enable_ntfy),
                encrypted.ntfy_topic,
                int(encrypted.enable_discord_notification),
                encrypted.discord_webhook,
                encode_datetime(encrypted.updated_at),
            ),
        )
        return template

    def _decrypt_template(
        self,
        template: SidebarGroupConfigTemplate,
    ) -> SidebarGroupConfigTemplate:
        """還原 group template 內的 notification secrets。"""

        return replace(
            template,
            ntfy_topic=self.secret_codec.decrypt(template.ntfy_topic),
            discord_webhook=self.secret_codec.decrypt(template.discord_webhook),
        )


def _now() -> datetime:
    """回傳 UTC datetime；避免 repository 呼叫端處理 updated_at。"""

    from facebook_monitor.core.models import utc_now

    return utc_now()


def _group_from_row(row: sqlite3.Row) -> SidebarGroup:
    """將 SQLite row 轉為 SidebarGroup。"""

    created_at = decode_datetime(row["created_at"])
    updated_at = decode_datetime(row["updated_at"])
    if created_at is None or updated_at is None:
        raise ValueError("sidebar group row has invalid datetime fields")
    return SidebarGroup(
        id=row["id"],
        name=row["name"],
        sort_order=int(row["sort_order"]),
        collapsed=bool(row["collapsed"]),
        created_at=created_at,
        updated_at=updated_at,
    )


def _placement_from_row(row: sqlite3.Row) -> SidebarTargetPlacement:
    """將 SQLite row 轉為 SidebarTargetPlacement。"""

    updated_at = decode_datetime(row["updated_at"])
    if updated_at is None:
        raise ValueError("sidebar target placement row has invalid updated_at")
    return SidebarTargetPlacement(
        target_id=row["target_id"],
        sidebar_group_id=row["sidebar_group_id"],
        sort_order=int(row["sort_order"]),
        updated_at=updated_at,
    )


def _template_from_row(row: sqlite3.Row) -> SidebarGroupConfigTemplate:
    """將 SQLite row 轉為 SidebarGroupConfigTemplate。"""

    updated_at = decode_datetime(row["updated_at"])
    if updated_at is None:
        raise ValueError("sidebar group template row has invalid updated_at")
    return SidebarGroupConfigTemplate(
        sidebar_group_id=row["sidebar_group_id"],
        include_keywords=decode_keywords(row["include_keywords"]),
        exclude_keywords=decode_keywords(row["exclude_keywords"]),
        exclude_ignore_phrases=decode_keywords(row["exclude_ignore_phrases"]),
        min_refresh_sec=int(row["min_refresh_sec"]),
        max_refresh_sec=int(row["max_refresh_sec"]),
        jitter_enabled=bool(row["jitter_enabled"]),
        fixed_refresh_sec=row["fixed_refresh_sec"],
        max_items_per_scan=int(row["max_items_per_scan"]),
        auto_load_more=bool(row["auto_load_more"]),
        auto_adjust_sort=bool(row["auto_adjust_sort"]),
        enable_desktop_notification=bool(row["enable_desktop_notification"]),
        enable_ntfy=bool(row["enable_ntfy"]),
        ntfy_topic=row["ntfy_topic"],
        enable_discord_notification=bool(row["enable_discord_notification"]),
        discord_webhook=row["discord_webhook"],
        updated_at=updated_at,
    )
