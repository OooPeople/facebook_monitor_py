"""Release artifact 平台策略。

職責：集中定義不同平台的 GitHub Release asset 命名與更新能力，
避免 Windows / macOS 判斷散落在 release check、Web UI 與 validation。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import platform
import re
import sys


RELEASE_ASSET_PREFIX = "facebook-monitor"
RELEASE_ARCHIVE_ROOT_NAME = RELEASE_ASSET_PREFIX
SHA256_SIDECAR_SUFFIX = ".sha256"
WINDOWS_PORTABLE_SUFFIX = "-windows-portable.zip"
MACOS_ARM64_ONEDIR_SUFFIX = "-macos-arm64-onedir.zip"
MACOS_ARM64_MACHINE_ALIASES = frozenset({"arm64", "arm64e", "aarch64"})
UNSUPPORTED_PLATFORM_REASON = "目前平台沒有對應的更新檔，只支援檢查版本資訊"


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

        return f"{RELEASE_ASSET_PREFIX}-{version}{self.asset_suffix}"

    def sha256_asset_name(self, version: str) -> str:
        """回傳指定 version 的 SHA256 sidecar release asset 檔名。"""

        return release_sha256_asset_name(self.asset_name(version))


@dataclass(frozen=True)
class UpdateRuntimePlatform:
    """描述目前 runtime 平台對應的 release artifact 能力。"""

    platform_key: str
    artifact_policy: UpdateArtifactPolicy | None
    unsupported_reason: str = ""

    @property
    def supported(self) -> bool:
        """回傳目前平台是否有對應 release artifact。"""

        return self.artifact_policy is not None


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
    apply_supported=True,
)
UPDATE_ARTIFACT_POLICIES = (
    WINDOWS_PORTABLE_POLICY,
    MACOS_ARM64_ONEDIR_POLICY,
)
UPDATE_ARTIFACT_PLATFORM_ALIASES = {
    "windows": WINDOWS_PORTABLE_POLICY,
    "win32": WINDOWS_PORTABLE_POLICY,
    "macos-arm64": MACOS_ARM64_ONEDIR_POLICY,
    "darwin-arm64": MACOS_ARM64_ONEDIR_POLICY,
}


def current_update_artifact_policy() -> UpdateArtifactPolicy:
    """依目前執行平台取得 release artifact 策略；不支援平台會丟出錯誤。"""

    current_platform = current_update_runtime_platform()
    if current_platform.artifact_policy is None:
        raise ValueError(f"unsupported artifact platform: {current_platform.platform_key}")
    return current_platform.artifact_policy


def current_update_runtime_platform() -> UpdateRuntimePlatform:
    """依目前執行平台取得 release artifact runtime 平台資訊。"""

    return update_runtime_platform_for_system(
        system=sys.platform,
        machine=platform.machine(),
    )


def update_artifact_policy_for_platform(
    *,
    system: str,
    machine: str = "",
) -> UpdateArtifactPolicy | None:
    """依平台字串回傳 release artifact 策略；未知平台回傳 None。"""

    return update_runtime_platform_for_system(
        system=system,
        machine=machine,
    ).artifact_policy


def update_runtime_platform_for_system(
    *,
    system: str,
    machine: str = "",
) -> UpdateRuntimePlatform:
    """依平台字串回傳 updater runtime 平台資訊。"""

    normalized_system = system.strip().casefold()
    normalized_machine = machine.strip().casefold()
    if normalized_system == "win32" or normalized_system.startswith("windows"):
        return UpdateRuntimePlatform(
            platform_key=WINDOWS_PORTABLE_POLICY.platform_key,
            artifact_policy=WINDOWS_PORTABLE_POLICY,
        )
    if normalized_system == "darwin" or normalized_system.startswith("macos"):
        if normalized_machine in MACOS_ARM64_MACHINE_ALIASES:
            return UpdateRuntimePlatform(
                platform_key=MACOS_ARM64_ONEDIR_POLICY.platform_key,
                artifact_policy=MACOS_ARM64_ONEDIR_POLICY,
            )
        suffix = f"-{normalized_machine}" if normalized_machine else ""
        return UpdateRuntimePlatform(
            platform_key=f"unsupported-{normalized_system or 'unknown'}{suffix}",
            artifact_policy=None,
            unsupported_reason=UNSUPPORTED_PLATFORM_REASON,
        )
    suffix = f"-{normalized_machine}" if normalized_machine else ""
    return UpdateRuntimePlatform(
        platform_key=f"unsupported-{normalized_system or 'unknown'}{suffix}",
        artifact_policy=None,
        unsupported_reason=UNSUPPORTED_PLATFORM_REASON,
    )


def update_artifact_policy_for_key(platform_name: str) -> UpdateArtifactPolicy:
    """依 CLI / validation 使用的平台 key 回傳 release artifact 策略。"""

    normalized = platform_name.strip().casefold()
    try:
        return UPDATE_ARTIFACT_PLATFORM_ALIASES[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported artifact platform: {platform_name}") from exc


def release_sha256_asset_name(asset_name: str) -> str:
    """回傳 release zip 對應的 `.sha256` asset 名稱。"""

    return asset_name + SHA256_SIDECAR_SUFFIX


def sanitize_release_asset_name(value: str) -> str:
    """限制 release artifact 名稱，避免 runtime path 由外部字串跳出工作目錄。"""

    name = Path(value).name.strip()
    if name != value.strip() or not name:
        raise ValueError("invalid_asset_name")
    if name in {".", ".."}:
        raise ValueError("invalid_asset_name")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError("invalid_asset_name")
    return name


def is_release_asset_name_for_policy(
    asset_name: str,
    *,
    policy: UpdateArtifactPolicy,
) -> bool:
    """判斷 asset name 是否像指定平台的 release zip。"""

    return (
        asset_name.startswith(f"{RELEASE_ASSET_PREFIX}-")
        and asset_name.endswith(policy.asset_suffix)
    )


def release_artifact_policy_for_asset_name(asset_name: str) -> UpdateArtifactPolicy | None:
    """依 release asset 檔名回傳對應平台 policy。"""

    for policy in UPDATE_ARTIFACT_POLICIES:
        if is_release_asset_name_for_policy(asset_name, policy=policy):
            return policy
    return None


def release_asset_name(*, version: str, policy: UpdateArtifactPolicy | None = None) -> str:
    """回傳目前平台或指定 policy 的 release asset 檔名。"""

    resolved_policy = policy or current_update_artifact_policy()
    return resolved_policy.asset_name(version)
