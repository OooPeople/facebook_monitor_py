"""Application context wiring。

職責：集中 SQLite connection、schema 初始化、repository 與 application service 的組裝。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from facebook_monitor.application.services import ScanApplicationService
from facebook_monitor.application.services import TargetApplicationService
from facebook_monitor.persistence.sqlite import LatestScanItemRepository
from facebook_monitor.persistence.sqlite import GlobalNotificationSettingsRepository
from facebook_monitor.persistence.sqlite import MatchHistoryRepository
from facebook_monitor.persistence.sqlite import NotificationEventRepository
from facebook_monitor.persistence.sqlite import ScanRunRepository
from facebook_monitor.persistence.sqlite import SeenItemRepository
from facebook_monitor.persistence.sqlite import SqliteConnection
from facebook_monitor.persistence.sqlite import TargetConfigRepository
from facebook_monitor.persistence.sqlite import TargetRepository
from facebook_monitor.persistence.sqlite import TargetRuntimeStateRepository
from facebook_monitor.persistence.sqlite import initialize_schema
from facebook_monitor.persistence.maintenance import RuntimeDataMaintenanceRepository


@dataclass(frozen=True)
class RepositoryBundle:
    """保存一組共享同一 SQLite connection 的 repositories。"""

    targets: TargetRepository
    configs: TargetConfigRepository
    runtime_states: TargetRuntimeStateRepository
    seen_items: SeenItemRepository
    match_history: MatchHistoryRepository
    latest_scan_items: LatestScanItemRepository
    scan_runs: ScanRunRepository
    notification_events: NotificationEventRepository
    global_notification_settings: GlobalNotificationSettingsRepository
    maintenance: RuntimeDataMaintenanceRepository


@dataclass(frozen=True)
class ServiceBundle:
    """保存 application service 入口。"""

    targets: TargetApplicationService
    scans: ScanApplicationService


@dataclass(frozen=True)
class ApplicationContext:
    """保存 application layer 的 repositories 與 services。"""

    repositories: RepositoryBundle
    services: ServiceBundle


def build_repositories(connection: sqlite3.Connection) -> RepositoryBundle:
    """用同一連線建立 repository bundle。"""

    return RepositoryBundle(
        targets=TargetRepository(connection),
        configs=TargetConfigRepository(connection),
        runtime_states=TargetRuntimeStateRepository(connection),
        seen_items=SeenItemRepository(connection),
        match_history=MatchHistoryRepository(connection),
        latest_scan_items=LatestScanItemRepository(connection),
        scan_runs=ScanRunRepository(connection),
        notification_events=NotificationEventRepository(connection),
        global_notification_settings=GlobalNotificationSettingsRepository(connection),
        maintenance=RuntimeDataMaintenanceRepository(connection),
    )


def build_services(repositories: RepositoryBundle) -> ServiceBundle:
    """用 repository bundle 建立 application service bundle。"""

    return ServiceBundle(
        targets=TargetApplicationService(
            targets=repositories.targets,
            configs=repositories.configs,
            runtime_states=repositories.runtime_states,
            seen_items=repositories.seen_items,
        ),
        scans=ScanApplicationService(scan_runs=repositories.scan_runs),
    )


def build_application_context(connection: sqlite3.Connection) -> ApplicationContext:
    """建立 application context，供 CLI 或 worker 使用。"""

    repositories = build_repositories(connection)
    return ApplicationContext(
        repositories=repositories,
        services=build_services(repositories),
    )


class SqliteApplicationContext:
    """以 context manager 管理 SQLite application context。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.sqlite = SqliteConnection(db_path)
        self.context: ApplicationContext | None = None

    def __enter__(self) -> ApplicationContext:
        sqlite_context = self.sqlite.__enter__()
        connection = sqlite_context.require_connection()
        initialize_schema(connection)
        self.context = build_application_context(connection)
        return self.context

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.sqlite.__exit__(exc_type, exc, traceback)
        self.context = None
