"""Updater runtime capability resolution.

職責：依 frozen packaging mode、平台與 bundled updater 是否存在，決定 Web UI
可提供的更新操作，讓 route 只負責呈現與 orchestration。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import platform
import sys

from facebook_monitor.updates.artifacts import update_runtime_platform_for_system
from facebook_monitor.updates.launcher import find_bundled_updater


@dataclass(frozen=True)
class UpdateCapability:
    """描述目前 runtime 可提供的更新操作能力。"""

    download_supported: bool
    apply_supported: bool
    unsupported_reason: str


def resolve_update_capability(
    *,
    packaging_mode: str,
    frozen: bool,
    app_base_dir: object,
    data_dir: object | None = None,
    db_path: object | None = None,
    system: str | None = None,
    machine: str | None = None,
) -> UpdateCapability:
    """依 runtime 與平台決定可提供的更新能力。"""

    normalized = packaging_mode.strip().casefold()
    packaged = frozen or normalized.startswith("pyinstaller")
    if not packaged:
        return UpdateCapability(
            download_supported=False,
            apply_supported=False,
            unsupported_reason="Source mode 僅支援檢查更新",
        )
    runtime_platform = update_runtime_platform_for_system(
        system=system or sys.platform,
        machine=platform.machine() if machine is None else machine,
    )
    if runtime_platform.artifact_policy is None:
        return UpdateCapability(
            download_supported=False,
            apply_supported=False,
            unsupported_reason=runtime_platform.unsupported_reason,
        )
    platform_key = runtime_platform.artifact_policy.platform_key
    if platform_key == "macos-arm64":
        updater_available = find_bundled_updater(Path(str(app_base_dir))) is not None
        if not updater_available:
            return UpdateCapability(
                download_supported=True,
                apply_supported=False,
                unsupported_reason="macOS PyInstaller 打包版缺少 updater，僅支援下載並驗證",
            )
        return _apply_external_db_guard(
            UpdateCapability(
                download_supported=True,
                apply_supported=True,
                unsupported_reason="",
            ),
            data_dir=data_dir,
            db_path=db_path,
        )
    updater_available = find_bundled_updater(Path(str(app_base_dir))) is not None
    if not updater_available:
        return UpdateCapability(
            download_supported=False,
            apply_supported=False,
            unsupported_reason="Windows PyInstaller 打包版缺少 updater，僅支援檢查更新",
        )
    return _apply_external_db_guard(
        UpdateCapability(
            download_supported=True,
            apply_supported=True,
            unsupported_reason="",
        ),
        data_dir=data_dir,
        db_path=db_path,
    )


def _apply_external_db_guard(
    capability: UpdateCapability,
    *,
    data_dir: object | None,
    db_path: object | None,
) -> UpdateCapability:
    """外部 DB 可運作，但 updater handoff 只支援 data tree 內 DB。"""

    if not capability.apply_supported or data_dir is None or db_path is None:
        return capability
    resolved_data_dir = Path(str(data_dir)).resolve(strict=False)
    resolved_db_path = Path(str(db_path)).resolve(strict=False)
    if resolved_db_path.is_relative_to(resolved_data_dir):
        return capability
    return UpdateCapability(
        download_supported=capability.download_supported,
        apply_supported=False,
        unsupported_reason="外部 DB 路徑不支援自動套用更新，僅支援下載並驗證",
    )
