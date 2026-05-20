"""Updater 平台決策測試。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.updates.artifacts import MACOS_ARM64_ONEDIR_POLICY
from facebook_monitor.updates.artifacts import WINDOWS_PORTABLE_POLICY
from facebook_monitor.updates.artifacts import update_artifact_policy_for_platform
from facebook_monitor.updates.artifacts import update_runtime_platform_for_system
from facebook_monitor.updates.capability import resolve_update_capability
from facebook_monitor.updates.release_check import evaluate_release


def release_payload() -> dict[str, object]:
    """建立同時含 Windows / macOS assets 的測試 release。"""

    return {
        "tag_name": "v0.2.0",
        "html_url": "https://github.com/OooPeople/facebook_monitor_py/releases/tag/v0.2.0",
        "assets": [
            {
                "name": "facebook-monitor-0.2.0-windows-portable.zip",
                "browser_download_url": "https://downloads.example.test/windows.zip",
            },
            {
                "name": "facebook-monitor-0.2.0-windows-portable.zip.sha256",
                "browser_download_url": "https://downloads.example.test/windows.zip.sha256",
            },
            {
                "name": "facebook-monitor-0.2.0-macos-arm64-onedir.zip",
                "browser_download_url": "https://downloads.example.test/macos.zip",
            },
            {
                "name": "facebook-monitor-0.2.0-macos-arm64-onedir.zip.sha256",
                "browser_download_url": "https://downloads.example.test/macos.zip.sha256",
            },
        ],
    }


def test_runtime_platform_resolves_supported_artifact_policies() -> None:
    """runtime 平台解析是 release check 與 capability 的共同來源。"""

    windows = update_runtime_platform_for_system(system="win32")
    macos = update_runtime_platform_for_system(system="darwin", machine="arm64")
    macos_aarch64 = update_runtime_platform_for_system(
        system="darwin",
        machine="aarch64",
    )
    macos_alias_policy = update_artifact_policy_for_platform(
        system="macOS",
        machine="arm64",
    )

    assert windows.artifact_policy is WINDOWS_PORTABLE_POLICY
    assert update_artifact_policy_for_platform(system="Windows-11") is WINDOWS_PORTABLE_POLICY
    assert macos.artifact_policy is MACOS_ARM64_ONEDIR_POLICY
    assert macos_aarch64.artifact_policy is MACOS_ARM64_ONEDIR_POLICY
    assert macos_alias_policy is MACOS_ARM64_ONEDIR_POLICY


def test_intel_macos_runtime_platform_is_not_supported() -> None:
    """Intel macOS 不可被誤判成 Apple Silicon updater artifact。"""

    runtime_platform = update_runtime_platform_for_system(
        system="darwin",
        machine="x86_64",
    )
    result = evaluate_release(
        current_version="0.1.0",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        release=release_payload(),
        runtime_platform=runtime_platform,
    )

    assert not runtime_platform.supported
    assert runtime_platform.platform_key == "unsupported-darwin-x86_64"
    assert runtime_platform.artifact_policy is None
    assert update_artifact_policy_for_platform(system="macOS", machine="x86_64") is None
    assert result.status == "platform_unsupported"
    assert not result.update_available
    assert result.asset_name == ""
    assert result.failure_reason == "platform_unsupported"


def test_unknown_runtime_platform_does_not_fallback_to_windows_artifact() -> None:
    """未知平台只能檢查版本，不可默默選 Windows release asset。"""

    runtime_platform = update_runtime_platform_for_system(
        system="linux",
        machine="x86_64",
    )
    result = evaluate_release(
        current_version="0.1.0",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        release=release_payload(),
        runtime_platform=runtime_platform,
    )

    assert not runtime_platform.supported
    assert runtime_platform.artifact_policy is None
    assert update_artifact_policy_for_platform(system="linux") is None
    assert result.status == "platform_unsupported"
    assert not result.update_available
    assert result.latest_version == "0.2.0"
    assert result.asset_name == ""
    assert result.failure_reason == "platform_unsupported"
    assert "Windows portable" not in result.summary


def test_unknown_packaged_runtime_capability_is_check_only(tmp_path: Path) -> None:
    """capability 與 release artifact policy 使用同一個未知平台語義。"""

    capability = resolve_update_capability(
        packaging_mode="pyinstaller-onedir-gui-tray",
        frozen=True,
        app_base_dir=tmp_path,
        system="linux",
    )

    assert not capability.download_supported
    assert not capability.apply_supported
    assert "沒有對應的更新檔" in capability.unsupported_reason


def test_intel_macos_packaged_runtime_capability_is_check_only(tmp_path: Path) -> None:
    """Intel macOS packaged runtime 只能檢查版本，不可下載 arm64 更新檔。"""

    capability = resolve_update_capability(
        packaging_mode="pyinstaller-macos-arm64-onedir",
        frozen=True,
        app_base_dir=tmp_path,
        system="darwin",
        machine="x86_64",
    )

    assert not capability.download_supported
    assert not capability.apply_supported
    assert "沒有對應的更新檔" in capability.unsupported_reason
