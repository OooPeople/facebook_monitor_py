"""Web UI read-side query helpers。

職責：集中整理首頁需要的 target/config/runtime/latest 資料，避免 FastAPI
route 直接散讀 repository，讓 UI route 專注處理 HTTP 流程。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.defaults import PYTHON_WEBUI_RUNTIME_DEFAULTS
from facebook_monitor.core.sidebar_models import SidebarGroup
from facebook_monitor.core.sidebar_models import SidebarGroupConfigTemplate
from facebook_monitor.core.sidebar_models import SidebarTargetPlacement
from facebook_monitor.persistence.repositories.app_settings import ProfileSessionStatus
from facebook_monitor.persistence.sqlite_retry import is_sqlite_lock_error
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import NotificationOutboxSummary
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.webapp.dashboard_models import SidebarTargetItem
from facebook_monitor.webapp.dashboard_models import SidebarGroupSection
from facebook_monitor.webapp.dashboard_models import TargetRow
from facebook_monitor.webapp.hit_record_models import FullHitRecordRow
from facebook_monitor.webapp.preview_models import HitRecordPreviewRow
from facebook_monitor.webapp.preview_models import LatestScanItemRow


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
class DashboardViewModel:
    """保存 dashboard template 所需 read model。"""

    rows: tuple[TargetRow, ...]
    sidebar_groups: tuple[SidebarGroupSection, ...] = ()
    profile_session_warning: ProfileSessionWarning = ProfileSessionWarning()

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


def list_target_rows(
    db_path: Path,
    *,
    session_started_at: datetime | None = None,
) -> list[TargetRow]:
    """讀取所有 target 與 config，整理為首頁 target row。"""

    return list(
        _read_dashboard_model(
            db_path,
            initialize_schema_on_enter=False,
            session_started_at=session_started_at,
        ).rows
    )


@dataclass(frozen=True)
class _DashboardReadResult:
    """保存 dashboard read 內部結果。"""

    rows: tuple[TargetRow, ...]
    sidebar_groups: tuple[SidebarGroupSection, ...]
    profile_session_status: ProfileSessionStatus


def _read_dashboard_model(
    db_path: Path,
    *,
    initialize_schema_on_enter: bool,
    session_started_at: datetime | None,
) -> _DashboardReadResult:
    """讀取 dashboard rows 與 sidebar group sections。"""

    try:
        return _list_target_rows(
            db_path,
            initialize_schema_on_enter=initialize_schema_on_enter,
            session_started_at=session_started_at,
        )
    except sqlite3.OperationalError as exc:
        if (
            not initialize_schema_on_enter
            and _is_missing_schema_error(exc)
        ):
            return _read_dashboard_model(
                db_path,
                initialize_schema_on_enter=True,
                session_started_at=session_started_at,
            )
        _raise_dashboard_read_unavailable_if_locked(exc)
        raise


def _list_target_rows(
    db_path: Path,
    *,
    initialize_schema_on_enter: bool,
    session_started_at: datetime | None,
) -> _DashboardReadResult:
    """執行 target row read；必要時供空 DB 首次讀取補 schema 後重試。"""

    with SqliteApplicationContext(
        db_path,
        initialize_schema_on_enter=initialize_schema_on_enter,
    ) as app_context:
        targets = app_context.repositories.targets.list_all()
        target_ids = [target.id for target in targets]
        placements_by_target = app_context.repositories.sidebar_layout.list_placements()
        groups = app_context.repositories.sidebar_layout.list_groups()
        targets = _order_targets_by_sidebar(targets, placements_by_target, groups)
        target_ids = [target.id for target in targets]
        configs_by_target = _configs_by_target(app_context, targets)
        runtime_by_target = app_context.repositories.runtime_states.list_by_targets(target_ids)
        latest_scan_runs = app_context.repositories.scan_runs.latest_by_targets(target_ids)
        latest_failed_scan_runs = app_context.repositories.scan_runs.latest_by_targets(
            target_ids,
            status=ScanStatus.FAILED,
        )
        outbox_summaries = app_context.repositories.notification_outbox.summarize_by_targets(
            target_ids
        )
        max_items_limit = max(
            (config.max_items_per_scan for config in configs_by_target.values()),
            default=1,
        )
        latest_scan_items = app_context.repositories.latest_scan_items.list_by_targets(
            target_ids,
            limit_per_target=max_items_limit,
        )
        hit_record_preview_items = app_context.repositories.match_history.list_by_targets(
            target_ids,
            limit_per_target=5,
            notified_since=session_started_at,
        )
        hit_record_counts = app_context.repositories.match_history.count_by_targets(
            target_ids,
            notified_since=session_started_at,
        )
        rows = tuple(
            _build_target_row(
                target=target,
                config=configs_by_target[target.id],
                runtime_state=runtime_by_target.get(
                    target.id,
                    TargetRuntimeState(target_id=target.id),
                ),
                latest_scan_items=latest_scan_items.get(target.id, ())[
                    : configs_by_target[target.id].max_items_per_scan
                ],
                hit_record_preview_items=hit_record_preview_items.get(target.id, ()),
                latest_scan_run=latest_scan_runs.get(target.id),
                latest_failed_scan_run=latest_failed_scan_runs.get(target.id),
                notification_outbox_summary=outbox_summaries.get(target.id),
                hit_record_total_count=hit_record_counts.get(target.id, 0),
            )
            for target in targets
        )
        sidebar_groups = _build_sidebar_groups(
            rows=rows,
            placements_by_target=placements_by_target,
            groups=groups,
            templates_by_group={
                group.id: (
                    app_context.repositories.sidebar_layout.get_template(group.id)
                    or SidebarGroupConfigTemplate(sidebar_group_id=group.id)
                )
                for group in groups
            },
        )
        return _DashboardReadResult(
            rows=rows,
            sidebar_groups=sidebar_groups,
            profile_session_status=app_context.repositories.app_settings
            .get_profile_session_status(),
        )


def get_dashboard_view(
    db_path: Path,
    *,
    session_started_at: datetime | None = None,
) -> DashboardViewModel:
    """讀取 dashboard read model。"""

    result = _read_dashboard_model(
        db_path,
        initialize_schema_on_enter=False,
        session_started_at=session_started_at,
    )
    return DashboardViewModel(
        rows=result.rows,
        sidebar_groups=result.sidebar_groups,
        profile_session_warning=_build_profile_session_warning(
            result.profile_session_status
        ),
    )


def _build_profile_session_warning(
    status: ProfileSessionStatus,
) -> ProfileSessionWarning:
    """將 repository 狀態轉成首頁顯示用警告文案。"""

    if not status.needs_login:
        return ProfileSessionWarning()
    return ProfileSessionWarning(
        needs_login=True,
        reason=status.reason,
        message=(
            "Facebook 需要重新登入。請關閉並重新開啟程式，"
            "系統會先開啟 Facebook 登入視窗；完成登入後會自動進入 Web UI。"
        ),
    )


def list_sidebar_items(
    db_path: Path,
    *,
    session_started_at: datetime | None = None,
) -> tuple[SidebarTargetItem, ...]:
    """讀取 sidebar partial update 所需的 target 摘要。"""

    return get_dashboard_view(db_path, session_started_at=session_started_at).sidebar_items


def get_target_card(
    db_path: Path,
    target_id: str,
    *,
    session_started_at: datetime | None = None,
) -> TargetRow | None:
    """讀取單一 target card read model，供 Phase 10 partial update 使用。"""

    try:
        with _read_application_context(db_path) as app_context:
            target = app_context.repositories.targets.get(target_id)
            if target is None:
                return None
            config = _configs_by_target(app_context, [target])[target.id]
            runtime_state = app_context.repositories.runtime_states.get(
                target.id
            ) or TargetRuntimeState(target_id=target.id)
            return _build_target_row(
                target=target,
                config=config,
                runtime_state=runtime_state,
                latest_scan_items=app_context.repositories.latest_scan_items.list_by_target(
                    target.id,
                    limit=config.max_items_per_scan,
                ),
                hit_record_preview_items=app_context.repositories.match_history.list_by_target(
                    target.id,
                    limit=5,
                    notified_since=session_started_at,
                ),
                latest_scan_run=app_context.repositories.scan_runs.latest_by_target(target.id),
                latest_failed_scan_run=app_context.repositories.scan_runs.latest_by_target(
                    target.id,
                    status=ScanStatus.FAILED,
                ),
                notification_outbox_summary=(
                    app_context.repositories.notification_outbox
                    .summarize_by_targets([target.id])
                    .get(target.id)
                ),
                hit_record_total_count=app_context.repositories.match_history.count_by_target(
                    target.id,
                    notified_since=session_started_at,
                ),
            )
    except sqlite3.OperationalError as exc:
        _raise_dashboard_read_unavailable_if_locked(exc)
        raise


def _order_targets_by_sidebar(
    targets: list[TargetDescriptor],
    placements_by_target: dict[str, SidebarTargetPlacement],
    groups: tuple[SidebarGroup, ...],
) -> list[TargetDescriptor]:
    """依 sidebar group/order 排列 dashboard rows，不改 target repository 語義。"""

    target_by_id = {target.id: target for target in targets}
    original_index = {target.id: index for index, target in enumerate(targets)}
    group_order = {group.id: index for index, group in enumerate(groups)}

    def sort_key(target: TargetDescriptor) -> tuple[int, int, int, int]:
        placement = placements_by_target.get(target.id)
        group_id = placement.sidebar_group_id if placement else None
        sort_order = placement.sort_order if placement else original_index[target.id]
        if group_id in group_order:
            return (0, group_order[group_id], sort_order, original_index[target.id])
        return (1, 0, sort_order, original_index[target.id])

    return sorted(target_by_id.values(), key=sort_key)


def _build_sidebar_groups(
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


def _configs_by_target(
    app_context: ApplicationContext,
    targets: list[TargetDescriptor],
) -> dict[str, TargetConfig]:
    """批次讀取 target-scoped config。"""

    target_configs = app_context.repositories.configs.list_for_targets([target.id for target in targets])
    configs: dict[str, TargetConfig] = {}
    for target in targets:
        config = target_configs.get(target.id)
        if config is None:
            config = app_context.services.targets.get_config_for_target(target)
        configs[target.id] = config
    return configs


def _build_target_row(
    *,
    target: TargetDescriptor,
    config: TargetConfig,
    runtime_state: TargetRuntimeState,
    latest_scan_items: list[LatestScanItem] | tuple[LatestScanItem, ...],
    hit_record_preview_items: list[MatchHistoryEntry] | tuple[MatchHistoryEntry, ...],
    latest_scan_run: ScanRun | None,
    latest_failed_scan_run: ScanRun | None,
    notification_outbox_summary: NotificationOutboxSummary | None,
    hit_record_total_count: int,
) -> TargetRow:
    """將 repository raw models 組成 dashboard target row。"""

    return TargetRow(
        target=target,
        config=config,
        runtime_state=runtime_state,
        latest_scan_run=latest_scan_run,
        latest_failed_scan_run=latest_failed_scan_run,
        notification_outbox_summary=notification_outbox_summary,
        latest_scan_items=tuple(LatestScanItemRow(item=item) for item in latest_scan_items),
        hit_record_preview_items=tuple(
            HitRecordPreviewRow(entry=entry) for entry in hit_record_preview_items
        ),
        hit_record_total_count=hit_record_total_count,
    )


def _read_application_context(db_path: Path) -> SqliteApplicationContext:
    """建立 Web UI read model 用 context，避免每輪 partial update 跑 schema init。"""

    return SqliteApplicationContext(db_path, initialize_schema_on_enter=False)


def _raise_dashboard_read_unavailable_if_locked(exc: sqlite3.OperationalError) -> None:
    """將 SQLite lock 轉成 route 可處理的 read model 暫不可用錯誤。"""

    if is_sqlite_lock_error(exc):
        raise DashboardReadUnavailable(str(exc)) from exc


def _is_missing_schema_error(exc: sqlite3.OperationalError) -> bool:
    """判斷 read path 是否遇到尚未初始化的空資料庫。"""

    return "no such table" in str(exc).lower()


def target_exists(db_path: Path, target_id: str) -> bool:
    """檢查 target 是否存在，供 API route 回傳明確 404。"""

    try:
        with _read_application_context(db_path) as app_context:
            return app_context.repositories.targets.get(target_id) is not None
    except sqlite3.OperationalError as exc:
        _raise_dashboard_read_unavailable_if_locked(exc)
        raise


def list_hit_record_preview_rows(
    db_path: Path,
    target_id: str,
    *,
    limit: int = PYTHON_WEBUI_RUNTIME_DEFAULTS.hit_record_preview_limit,
    session_started_at: datetime | None = None,
) -> tuple[HitRecordPreviewRow, ...]:
    """讀取單一 target 的命中紀錄 preview rows。"""

    try:
        with _read_application_context(db_path) as app_context:
            return tuple(
                HitRecordPreviewRow(entry=entry)
                for entry in app_context.repositories.match_history.list_by_target(
                    target_id,
                    limit=limit,
                    notified_since=session_started_at,
                )
            )
    except sqlite3.OperationalError as exc:
        _raise_dashboard_read_unavailable_if_locked(exc)
        raise


def list_full_hit_record_rows(
    db_path: Path,
    target_id: str,
    *,
    limit: int = PYTHON_WEBUI_RUNTIME_DEFAULTS.hit_record_full_limit,
    offset: int = 0,
) -> tuple[FullHitRecordRow, ...]:
    """讀取單一 target 的完整命中紀錄 rows。"""

    bounded_offset = max(int(offset), 0)
    try:
        with _read_application_context(db_path) as app_context:
            entries = app_context.repositories.match_history.list_by_target(
                target_id,
                limit=limit,
                offset=bounded_offset,
            )
            notification_events = (
                app_context.repositories.notification_events.latest_sent_by_target_item_keys(
                    target_id,
                    [entry.item_key for entry in entries],
                )
            )
    except sqlite3.OperationalError as exc:
        _raise_dashboard_read_unavailable_if_locked(exc)
        raise
    return tuple(
        FullHitRecordRow(
            entry=entry,
            sequence_number=bounded_offset + index + 1,
            notification_event=notification_events.get(entry.item_key),
        )
        for index, entry in enumerate(entries)
    )


def count_hit_records(
    db_path: Path,
    target_id: str,
    *,
    session_started_at: datetime | None = None,
) -> int:
    """計算單一 target 的命中紀錄筆數。"""

    try:
        with _read_application_context(db_path) as app_context:
            return app_context.repositories.match_history.count_by_target(
                target_id,
                notified_since=session_started_at,
            )
    except sqlite3.OperationalError as exc:
        _raise_dashboard_read_unavailable_if_locked(exc)
        raise


def get_dashboard_revision(db_path: Path) -> DashboardRevision:
    """用 read-only connection 讀取首頁 revision，避免 SSE 觸發 schema init。"""

    if not db_path.exists():
        return DashboardRevision(revision="0", last_changed_at="")
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.OperationalError as exc:
        if is_sqlite_lock_error(exc):
            raise DashboardRevisionUnavailable(str(exc)) from exc
        return DashboardRevision(revision="0", last_changed_at="")
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        row = connection.execute(
            "SELECT revision, updated_at FROM dashboard_revision WHERE id = 1"
        ).fetchone()
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if is_sqlite_lock_error(exc):
            raise DashboardRevisionUnavailable(str(exc)) from exc
        if "no such table" not in message:
            raise
        return DashboardRevision(revision="0", last_changed_at="")
    finally:
        connection.close()
    if row is None:
        return DashboardRevision(revision="0", last_changed_at="")
    return DashboardRevision(
        revision=str(row["revision"]),
        last_changed_at=row["updated_at"],
    )
