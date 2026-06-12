"""Settings update flow use cases."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from facebook_monitor.application.update_flow import CheckUpdates
from facebook_monitor.application.update_flow import DownloadUpdate
from facebook_monitor.application.update_flow import download_and_launch_verified_update
from facebook_monitor.application.update_flow import download_verified_update
from facebook_monitor.application.update_flow import LaunchUpdater
from facebook_monitor.application.update_flow import launch_verified_update
from facebook_monitor.application.update_flow import RevealFileManager
from facebook_monitor.application.update_flow import UpdateFlowOutcome
from facebook_monitor.application.update_flow import WritePendingUpdate
from facebook_monitor.runtime.build_metadata import BuildMetadata
from facebook_monitor.runtime.build_metadata import collect_build_metadata
from facebook_monitor.runtime.paths import RuntimePaths
from facebook_monitor.runtime.update_operation_lock import acquire_update_operation_lock
from facebook_monitor.runtime.update_operation_lock import UpdateOperationLockError
from facebook_monitor.updates.capability import resolve_update_capability
from facebook_monitor.updates.capability import UpdateCapability
from facebook_monitor.updates.release_check import build_idle_update_check
from facebook_monitor.updates.release_check import UpdateCheckResult
from facebook_monitor.webapp.assets import ASSET_VERSION
from facebook_monitor.webapp.dependencies import get_runtime_paths

SystemNameProvider = Callable[[], str]
MachineNameProvider = Callable[[], str]
RequestShutdown = Callable[[], bool]


@dataclass(frozen=True)
class SettingsUpdateContext:
    """Settings route 執行更新流程時共用的 runtime context。"""

    metadata: BuildMetadata
    paths: RuntimePaths
    update_capability: UpdateCapability

    @property
    def allow_env_repository_override(self) -> bool:
        """source mode 保留測試與 debug repository override。"""

        return not self.metadata.frozen


def build_settings_update_context(
    request: Request,
    *,
    current_system: SystemNameProvider,
    current_machine: MachineNameProvider,
) -> SettingsUpdateContext:
    """收集 settings 頁所有更新流程共用的 build、path 與 capability。"""

    metadata = collect_build_metadata(asset_version=ASSET_VERSION)
    paths = get_runtime_paths(request)
    return SettingsUpdateContext(
        metadata=metadata,
        paths=paths,
        update_capability=resolve_update_capability(
            packaging_mode=metadata.packaging_mode,
            frozen=metadata.frozen,
            app_base_dir=paths.app_base_dir,
            data_dir=paths.data_dir,
            db_path=paths.db_path,
            system=current_system(),
            machine=current_machine(),
        ),
    )


async def load_settings_update_check(
    request: Request,
    update_context: SettingsUpdateContext,
    *,
    check_updates: CheckUpdates,
) -> UpdateCheckResult:
    """依 query 決定 settings 頁只顯示 idle 狀態或實際檢查 GitHub release。"""

    update_check = build_idle_update_check(
        current_version=update_context.metadata.app_version,
        channel="stable",
        allow_env_repository_override=update_context.allow_env_repository_override,
    )
    if request.query_params.get("update_check") != "1":
        return update_check
    try:
        with acquire_update_operation_lock(
            update_context.paths.runtime_dir,
            "settings-check",
        ):
            return await check_updates(
                current_version=update_context.metadata.app_version,
                channel="stable",
                allow_env_repository_override=update_context.allow_env_repository_override,
            )
    except UpdateOperationLockError:
        return update_check


async def download_update_for_settings(
    update_context: SettingsUpdateContext,
    *,
    check_updates: CheckUpdates,
    download_update: DownloadUpdate,
    reveal_file_manager: RevealFileManager,
    write_pending_update: WritePendingUpdate,
) -> UpdateFlowOutcome:
    """下載並驗證更新包；不支援自動套用的平台只保留下載結果。"""

    return await download_verified_update(
        current_version=update_context.metadata.app_version,
        paths=update_context.paths,
        update_capability=update_context.update_capability,
        allow_env_repository_override=update_context.allow_env_repository_override,
        check_updates=check_updates,
        download_update=download_update,
        reveal_file_manager=reveal_file_manager,
        write_pending_update=write_pending_update,
        reveal_download=True,
    )


def apply_update_for_settings(
    update_context: SettingsUpdateContext,
    *,
    launch_updater: LaunchUpdater,
    request_shutdown: RequestShutdown,
) -> UpdateFlowOutcome:
    """啟動 temp updater，讓它等待主程式退出後套用已驗證更新。"""

    return launch_verified_update(
        paths=update_context.paths,
        update_capability=update_context.update_capability,
        launch_updater=launch_updater,
        request_shutdown=request_shutdown,
    )


async def download_and_apply_update_for_settings(
    update_context: SettingsUpdateContext,
    *,
    check_updates: CheckUpdates,
    download_update: DownloadUpdate,
    write_pending_update: WritePendingUpdate,
    launch_updater: LaunchUpdater,
    request_shutdown: RequestShutdown,
) -> UpdateFlowOutcome:
    """下載、驗證並啟動 updater；供 settings 頁 modal 流程使用。"""

    return await download_and_launch_verified_update(
        current_version=update_context.metadata.app_version,
        paths=update_context.paths,
        update_capability=update_context.update_capability,
        allow_env_repository_override=update_context.allow_env_repository_override,
        check_updates=check_updates,
        download_update=download_update,
        write_pending_update=write_pending_update,
        launch_updater=launch_updater,
        request_shutdown=request_shutdown,
    )
