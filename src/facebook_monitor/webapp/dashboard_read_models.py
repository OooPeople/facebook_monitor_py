"""Dashboard read model shared types."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from facebook_monitor.persistence.repositories.app_settings import ProfileSessionStatus
from facebook_monitor.webapp.dashboard_models import SidebarGroupSection
from facebook_monitor.webapp.dashboard_models import SidebarTargetItem
from facebook_monitor.webapp.dashboard_models import TargetRow


@dataclass(frozen=True)
class DashboardRevision:
    """保存 dashboard 變更偵測用的輕量 revision。"""

    revision: str
    last_changed_at: str = ""


class DashboardRevisionUnavailable(RuntimeError):
    """表示 dashboard revision 暫時被 SQLite write lock 擋住。"""


class DashboardReadUnavailable(RuntimeError):
    """表示 dashboard read model 暫時被 SQLite write lock 擋住。"""


@dataclass(frozen=True)
class ProfileSessionWarning:
    """保存首頁右上角 Facebook 重新登入警告。"""

    needs_login: bool = False
    message: str = ""
    reason: str = ""


@dataclass(frozen=True)
class DatabaseInvariantWarning:
    """保存首頁資料 invariant 診斷警告。"""

    has_violations: bool = False
    message: str = ""
    violation_count: int = 0
    tables: tuple[str, ...] = ()


@dataclass(frozen=True)
class DashboardViewModel:
    """保存 dashboard template 所需 read model。"""

    rows: tuple[TargetRow, ...]
    sidebar_groups: tuple[SidebarGroupSection, ...] = ()
    profile_session_warning: ProfileSessionWarning = ProfileSessionWarning()
    database_invariant_warning: DatabaseInvariantWarning = DatabaseInvariantWarning()
    dashboard_degraded: bool = False

    @property
    def sidebar_items(self) -> tuple[SidebarTargetItem, ...]:
        """回傳 Phase 5 sidebar 使用的 target 摘要。"""

        return tuple(row.sidebar_item for row in self.rows)

    @property
    def sidebar_layout_signature(self) -> str:
        """回傳 sidebar group/order 結構簽章，供 partial update 判斷是否需 reload。"""

        payload = [
            {
                "group_id": group.dom_group_id,
                "name": group.name,
                "target_ids": [item.target_id for item in group.items],
            }
            for group in self.sidebar_groups
        ]
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DashboardReadResult:
    """保存 dashboard read 內部結果。"""

    rows: tuple[TargetRow, ...]
    sidebar_groups: tuple[SidebarGroupSection, ...]
    profile_session_status: ProfileSessionStatus
    database_invariant_warning: DatabaseInvariantWarning
    dashboard_degraded: bool = False
