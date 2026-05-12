"""Application context wiring。

職責：集中 SQLite connection、schema 初始化、repository 與 application service 的組裝。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from facebook_monitor.application.services import ScanApplicationService
from facebook_monitor.application.services import TargetApplicationService
from facebook_monitor.persistence.maintenance import RuntimeDataMaintenanceRepository
from facebook_monitor.persistence.repositories.app_settings import AppSettingsRepository
from facebook_monitor.persistence.repositories.dashboard_revision import DashboardRevisionRepository
from facebook_monitor.persistence.repositories.global_notification_settings import (
    GlobalNotificationSettingsRepository,
)
from facebook_monitor.persistence.repositories.latest_scan_items import LatestScanItemRepository
from facebook_monitor.persistence.repositories.match_history import MatchHistoryRepository
from facebook_monitor.persistence.repositories.notification_events import NotificationEventRepository
from facebook_monitor.persistence.repositories.notification_outbox import NotificationOutboxRepository
from facebook_monitor.persistence.repositories.scan_runs import ScanRunRepository
from facebook_monitor.persistence.repositories.seen_items import SeenItemRepository
from facebook_monitor.persistence.repositories.target_configs import TargetConfigRepository
from facebook_monitor.persistence.repositories.targets import TargetRepository
from facebook_monitor.persistence.repositories.target_runtime_state import (
    TargetRuntimeStateRepository,
)
from facebook_monitor.persistence.schema import initialize_schema
from facebook_monitor.persistence.secret_storage import PlaintextSecretCodec
from facebook_monitor.persistence.secret_storage import SecretCodec
from facebook_monitor.persistence.secret_storage import load_or_create_secret_codec
from facebook_monitor.persistence.sqlite_connection import SqliteConnection


logger = logging.getLogger(__name__)


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
    notification_outbox: NotificationOutboxRepository
    global_notification_settings: GlobalNotificationSettingsRepository
    app_settings: AppSettingsRepository
    maintenance: RuntimeDataMaintenanceRepository
    dashboard_revision: DashboardRevisionRepository


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
    after_commit_hooks: list[Callable[[], None]]
    after_commit_hook_keys: set[str]
    db_path: Path | None = None

    def run_after_commit(self, hook: Callable[[], None]) -> None:
        """註冊 DB commit 成功後才執行的副作用。"""

        self.after_commit_hooks.append(hook)

    def run_after_commit_once(self, key: str, hook: Callable[[], None]) -> None:
        """同一 application context 內以 key 去重註冊 after-commit hook。"""

        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError("after-commit hook key is required")
        if normalized_key in self.after_commit_hook_keys:
            return
        self.after_commit_hook_keys.add(normalized_key)
        self.after_commit_hooks.append(hook)


def build_repositories(
    connection: sqlite3.Connection,
    *,
    secret_codec: SecretCodec | PlaintextSecretCodec,
) -> RepositoryBundle:
    """用同一連線建立 repository bundle。"""

    return RepositoryBundle(
        targets=TargetRepository(connection),
        configs=TargetConfigRepository(connection, secret_codec=secret_codec),
        runtime_states=TargetRuntimeStateRepository(connection),
        seen_items=SeenItemRepository(connection),
        match_history=MatchHistoryRepository(connection),
        latest_scan_items=LatestScanItemRepository(connection),
        scan_runs=ScanRunRepository(connection),
        notification_events=NotificationEventRepository(connection),
        notification_outbox=NotificationOutboxRepository(
            connection,
            secret_codec=secret_codec,
        ),
        global_notification_settings=GlobalNotificationSettingsRepository(
            connection,
            secret_codec=secret_codec,
        ),
        app_settings=AppSettingsRepository(connection),
        maintenance=RuntimeDataMaintenanceRepository(connection),
        dashboard_revision=DashboardRevisionRepository(connection),
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


def build_application_context(
    connection: sqlite3.Connection,
    *,
    secret_codec: SecretCodec | PlaintextSecretCodec,
    db_path: Path | None = None,
) -> ApplicationContext:
    """建立 application context，供 CLI 或 worker 使用。"""

    repositories = build_repositories(connection, secret_codec=secret_codec)
    return ApplicationContext(
        repositories=repositories,
        services=build_services(repositories),
        after_commit_hooks=[],
        after_commit_hook_keys=set(),
        db_path=db_path,
    )


class SqliteApplicationContext:
    """以 context manager 管理 SQLite application context。"""

    def __init__(self, db_path: Path, *, initialize_schema_on_enter: bool = True) -> None:
        self.db_path = db_path
        self.initialize_schema_on_enter = initialize_schema_on_enter
        self.sqlite = SqliteConnection(db_path)
        self.context: ApplicationContext | None = None

    def __enter__(self) -> ApplicationContext:
        sqlite_context = self.sqlite.__enter__()
        connection = sqlite_context.require_connection()
        if self.initialize_schema_on_enter:
            initialize_schema(connection)
            connection.commit()
        self.context = build_application_context(
            connection,
            secret_codec=load_or_create_secret_codec(self.db_path),
            db_path=self.db_path,
        )
        return self.context

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        connection = self.sqlite.connection
        try:
            if connection is None:
                return
            if exc_type is None:
                connection.commit()
                if self.context is not None:
                    for hook in tuple(self.context.after_commit_hooks):
                        try:
                            hook()
                        except Exception:
                            logger.exception("after_commit_hook_failed")
                connection.commit()
            else:
                connection.rollback()
        finally:
            if connection is not None:
                connection.close()
            self.sqlite.connection = None
            self.context = None
