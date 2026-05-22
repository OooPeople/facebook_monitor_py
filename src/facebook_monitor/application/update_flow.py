"""Settings 更新流程 use case。

職責：把檢查 release、下載驗證、建立 handoff 與啟動 updater 的流程集中，
讓 Web route 只負責 HTTP redirect / JSON response 轉換。
"""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.core.user_messages import format_update_reason_message
from facebook_monitor.runtime.paths import RuntimePaths
from facebook_monitor.updates.capability import UpdateCapability
from facebook_monitor.updates.download import UpdateDownloadResult
from facebook_monitor.updates.handoff import pending_update_path
from facebook_monitor.updates.launcher import UpdaterLaunchResult
from facebook_monitor.updates.release_check import UpdateCheckResult


CheckUpdates = Callable[..., Awaitable[UpdateCheckResult]]
DownloadUpdate = Callable[..., Awaitable[UpdateDownloadResult]]
RevealFileManager = Callable[[Path], bool]
WritePendingUpdate = Callable[..., object]
LaunchUpdater = Callable[..., UpdaterLaunchResult]
RequestShutdown = Callable[[], bool]


@dataclass(frozen=True)
class UpdateFlowOutcome:
    """保存 settings 更新流程的產品結果。"""

    ok: bool
    stage: str
    message: str
    latest_version: str = ""
    shutdown_requested: bool = False


async def download_verified_update(
    *,
    current_version: str,
    paths: RuntimePaths,
    update_capability: UpdateCapability,
    allow_env_repository_override: bool,
    check_updates: CheckUpdates,
    download_update: DownloadUpdate,
    reveal_file_manager: RevealFileManager,
    write_pending_update: WritePendingUpdate,
    reveal_download: bool,
) -> UpdateFlowOutcome:
    """下載並驗證更新；支援平台會同步建立 pending handoff。"""

    if not update_capability.download_supported:
        return UpdateFlowOutcome(
            ok=False,
            stage="environment",
            message=update_capability.unsupported_reason,
        )
    update_check = await check_updates(
        current_version=current_version,
        channel="stable",
        allow_env_repository_override=allow_env_repository_override,
    )
    if not update_check.update_available:
        reason = format_update_reason_message(
            update_check.failure_reason or update_check.status
        )
        return UpdateFlowOutcome(
            ok=False,
            stage="check",
            message=f"沒有可下載的更新：{reason}",
        )
    result = await download_update(
        update_check=update_check,
        updates_dir=paths.updates_dir,
    )
    if not result.verified:
        return UpdateFlowOutcome(
            ok=False,
            stage="download",
            message=(
                "更新下載或驗證失敗："
                + format_update_reason_message(result.failure_reason)
            ),
            latest_version=update_check.latest_version,
        )
    opened = _reveal_download_if_requested(
        result.file_path,
        reveal_download=reveal_download,
        reveal_file_manager=reveal_file_manager,
    )
    suffix = "，已開啟下載資料夾" if opened else ""
    if not update_capability.apply_supported:
        return UpdateFlowOutcome(
            ok=True,
            stage="downloaded",
            message=(
                "更新下載完成並已驗證；此平台目前尚不支援自動套用，"
                f"請手動解壓新版 zip 後啟動{suffix}"
            ),
            latest_version=update_check.latest_version,
        )
    try:
        write_pending_update(
            update_check=update_check,
            download_result=result,
            paths=paths,
        )
    except ValueError as exc:
        return UpdateFlowOutcome(
            ok=False,
            stage="handoff",
            message="更新交接檔建立失敗：" + format_failure_message_text(str(exc)),
            latest_version=update_check.latest_version,
        )
    return UpdateFlowOutcome(
        ok=True,
        stage="downloaded",
        message=(
            "更新下載完成並已驗證；已建立交接檔："
            f"{pending_update_path(paths.runtime_dir)}{suffix}"
        ),
        latest_version=update_check.latest_version,
    )


def launch_verified_update(
    *,
    paths: RuntimePaths,
    update_capability: UpdateCapability,
    launch_updater: LaunchUpdater,
    request_shutdown: RequestShutdown,
) -> UpdateFlowOutcome:
    """啟動 temp updater，讓主程式退出後套用已驗證更新。"""

    if not update_capability.apply_supported:
        return UpdateFlowOutcome(
            ok=False,
            stage="environment",
            message=update_capability.unsupported_reason,
        )
    result = launch_updater(paths=paths)
    if not result.launched:
        return UpdateFlowOutcome(
            ok=False,
            stage="launch",
            message="無法啟動更新器：" + format_update_reason_message(result.message),
        )
    shutdown_requested = request_shutdown()
    if shutdown_requested:
        message = "更新器已啟動，程式即將關閉並套用更新"
    else:
        message = "更新器已啟動；請從右下角 tray 選單完整退出程式後套用更新"
    return UpdateFlowOutcome(
        ok=True,
        stage="launched",
        message=message,
        shutdown_requested=shutdown_requested,
    )


async def download_and_launch_verified_update(
    *,
    current_version: str,
    paths: RuntimePaths,
    update_capability: UpdateCapability,
    allow_env_repository_override: bool,
    check_updates: CheckUpdates,
    download_update: DownloadUpdate,
    write_pending_update: WritePendingUpdate,
    launch_updater: LaunchUpdater,
    request_shutdown: RequestShutdown,
) -> UpdateFlowOutcome:
    """下載、驗證、建立 handoff 並啟動 updater。"""

    if not update_capability.apply_supported:
        return UpdateFlowOutcome(
            ok=False,
            stage="environment",
            message=update_capability.unsupported_reason,
        )
    download_outcome = await download_verified_update(
        current_version=current_version,
        paths=paths,
        update_capability=update_capability,
        allow_env_repository_override=allow_env_repository_override,
        check_updates=check_updates,
        download_update=download_update,
        reveal_file_manager=lambda _path: False,
        write_pending_update=write_pending_update,
        reveal_download=False,
    )
    if not download_outcome.ok:
        return download_outcome
    launch_outcome = launch_verified_update(
        paths=paths,
        update_capability=update_capability,
        launch_updater=launch_updater,
        request_shutdown=request_shutdown,
    )
    if not launch_outcome.ok:
        return launch_outcome
    return UpdateFlowOutcome(
        ok=True,
        stage=launch_outcome.stage,
        message=launch_outcome.message,
        latest_version=download_outcome.latest_version,
        shutdown_requested=launch_outcome.shutdown_requested,
    )


def _reveal_download_if_requested(
    file_path: Path | None,
    *,
    reveal_download: bool,
    reveal_file_manager: RevealFileManager,
) -> bool:
    """需要時開啟下載資料夾；失敗不改變已驗證下載結果。"""

    if not reveal_download or file_path is None:
        return False
    return reveal_file_manager(file_path)
