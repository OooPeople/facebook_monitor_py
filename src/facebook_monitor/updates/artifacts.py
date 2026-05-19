"""Release artifact 平台策略。

職責：集中定義不同平台的 GitHub Release asset 命名與更新能力，
避免 Windows / macOS 判斷散落在 release check、Web UI 與 validation。
"""

from __future__ import annotations

from dataclasses import dataclass
import platform
import sys


WINDOWS_PORTABLE_SUFFIX = "-windows-portable.zip"
MACOS_ARM64_ONEDIR_SUFFIX = "-macos-arm64-onedir.zip"


@dataclass(frozen=True)
class UpdateArtifactPolicy:
    """描述單一平台 release artifact 命名與能力。"""

    platform_key: str
    asset_suffix: str
    display_label: str
    download_supported: bool
    apply_supported: bool

    def asset_name(self, version: str) -> str:
        """回傳指定 version 的完整 release asset 檔名。"""

        return f"facebook-monitor-{version}{self.asset_suffix}"


WINDOWS_PORTABLE_POLICY = UpdateArtifactPolicy(
    platform_key="windows",
    asset_suffix=WINDOWS_PORTABLE_SUFFIX,
    display_label="Windows portable",
    download_supported=True,
    apply_supported=True,
)
MACOS_ARM64_ONEDIR_POLICY = UpdateArtifactPolicy(
    platform_key="macos-arm64",
    asset_suffix=MACOS_ARM64_ONEDIR_SUFFIX,
    display_label="macOS arm64 onedir",
    download_supported=True,
    apply_supported=False,
)
def current_update_artifact_policy() -> UpdateArtifactPolicy:
    """依目前執行平台取得 release artifact 策略。"""

    return update_artifact_policy_for_platform(
        system=sys.platform,
        machine=platform.machine(),
    )


def update_artifact_policy_for_platform(
    *,
    system: str,
    machine: str = "",
) -> UpdateArtifactPolicy:
    """依平台字串回傳 release artifact 策略；未知平台退回 Windows 既有語義。"""

    normalized_system = system.casefold()
    if normalized_system == "win32" or normalized_system.startswith("windows"):
        return WINDOWS_PORTABLE_POLICY
    if normalized_system == "darwin" or normalized_system.startswith("macos"):
        return MACOS_ARM64_ONEDIR_POLICY
    return WINDOWS_PORTABLE_POLICY


def release_asset_name(*, version: str, policy: UpdateArtifactPolicy | None = None) -> str:
    """回傳目前平台或指定 policy 的 release asset 檔名。"""

    resolved_policy = policy or current_update_artifact_policy()
    return resolved_policy.asset_name(version)
