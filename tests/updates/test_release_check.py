"""GitHub Release 更新檢查測試。"""

from __future__ import annotations

from typing import Any

from pytest import MonkeyPatch
import pytest

from facebook_monitor.updates.release_check import configured_update_repository
from facebook_monitor.updates.release_check import DEFAULT_UPDATE_REPOSITORY
from facebook_monitor.updates.release_check import evaluate_release
from facebook_monitor.updates.release_check import find_portable_asset
from facebook_monitor.updates.release_check import parse_release_assets
from facebook_monitor.updates.release_check import UPDATE_REPOSITORY_ENV
from facebook_monitor.updates.artifacts import MACOS_ARM64_ONEDIR_POLICY
from facebook_monitor.updates.artifacts import UpdateRuntimePlatform
from facebook_monitor.updates.artifacts import WINDOWS_PORTABLE_POLICY
from facebook_monitor.updates.manifest import release_manifest_asset_name
from facebook_monitor.updates.manifest import release_manifest_signature_asset_name
from facebook_monitor.versioning import parse_version


def release_payload(
    *,
    tag_name: str,
    assets: list[dict[str, Any]],
) -> dict[str, Any]:
    """建立測試用 GitHub release payload。"""

    return {
        "tag_name": tag_name,
        "html_url": f"https://github.com/OooPeople/facebook_monitor_py/releases/{tag_name}",
        "assets": assets,
    }


def asset(name: str) -> dict[str, str]:
    """建立測試用 asset payload。"""

    return {
        "name": name,
        "browser_download_url": f"https://github.com/downloads/{name}",
    }


def manifest_assets(version: str) -> list[dict[str, str]]:
    """建立 signed manifest 與 detached signature asset payload。"""

    return [
        asset(release_manifest_asset_name(version)),
        asset(release_manifest_signature_asset_name(version)),
    ]


def test_parse_version_orders_release_candidate_before_stable() -> None:
    """同版本 stable 應視為比 rc 新。"""

    assert parse_version("0.1.0-rc1").sort_key() < parse_version("0.1.0").sort_key()
    assert parse_version("v0.1.1").sort_key() > parse_version("0.1.0").sort_key()


def test_configured_update_repository_can_disable_env_override(
    monkeypatch: MonkeyPatch,
) -> None:
    """正式 frozen updater 路徑不可被環境變數靜默切換 repository。"""

    monkeypatch.setenv(UPDATE_REPOSITORY_ENV, "OtherOwner/other_repo")

    assert configured_update_repository() == "OtherOwner/other_repo"
    assert configured_update_repository(allow_env_override=False) == DEFAULT_UPDATE_REPOSITORY


def test_evaluate_release_reports_available_windows_portable_asset() -> None:
    """新版 release 含 portable zip 時回報可更新。"""

    result = evaluate_release(
        current_version="0.1.0-rc1",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        artifact_policy=WINDOWS_PORTABLE_POLICY,
        release=release_payload(
            tag_name="v0.1.0",
            assets=[
                asset("facebook-monitor-0.1.0-windows-portable.zip"),
                asset("facebook-monitor-0.1.0-windows-portable.zip.sha256"),
                *manifest_assets("0.1.0"),
            ],
        ),
    )

    assert result.status == "available"
    assert result.update_available
    assert result.latest_version == "0.1.0"
    assert result.asset_name == "facebook-monitor-0.1.0-windows-portable.zip"
    assert result.sha256_asset_name == "facebook-monitor-0.1.0-windows-portable.zip.sha256"
    assert result.manifest_asset_name == "facebook-monitor-0.1.0-manifest.json"
    assert result.manifest_signature_asset_name == "facebook-monitor-0.1.0-manifest.json.sig"
    assert result.failure_reason == ""
    assert "只提供檢查" not in result.detail


def test_evaluate_release_reports_available_macos_arm64_asset() -> None:
    """macOS policy 應尋找 arm64 onedir zip，不影響 Windows asset 命名。"""

    result = evaluate_release(
        current_version="0.1.0-rc1",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        artifact_policy=MACOS_ARM64_ONEDIR_POLICY,
        release=release_payload(
            tag_name="v0.1.0",
            assets=[
                asset("facebook-monitor-0.1.0-macos-arm64-onedir.zip"),
                asset("facebook-monitor-0.1.0-macos-arm64-onedir.zip.sha256"),
                *manifest_assets("0.1.0"),
            ],
        ),
    )

    assert result.status == "available"
    assert result.update_available
    assert result.asset_name == "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    assert result.sha256_asset_name == "facebook-monitor-0.1.0-macos-arm64-onedir.zip.sha256"


@pytest.mark.parametrize(
    ("current_version", "tag_name", "expected_status", "expected_reason"),
    [
        ("0.1.0", "", "unavailable", "missing_tag_name"),
        ("not-a-version", "v0.2.0", "unavailable", "invalid_version"),
        ("0.1.0", "not-a-version", "unavailable", "invalid_version"),
    ],
)
def test_evaluate_release_preserves_version_failure_reasons(
    current_version: str,
    tag_name: str,
    expected_status: str,
    expected_reason: str,
) -> None:
    """release identity / version parse 失敗時不可進入 asset gate。"""

    result = evaluate_release(
        current_version=current_version,
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        artifact_policy=WINDOWS_PORTABLE_POLICY,
        release=release_payload(tag_name=tag_name, assets=[]),
    )

    assert result.checked is True
    assert result.status == expected_status
    assert result.failure_reason == expected_reason
    assert result.update_available is False
    assert result.asset_name == ""


def test_evaluate_release_artifact_policy_override_ignores_unsupported_runtime() -> None:
    """明確 artifact_policy override 不應被 unsupported runtime platform 擋住。"""

    unsupported_runtime = UpdateRuntimePlatform(
        platform_key="unsupported-linux-x86_64",
        artifact_policy=None,
        unsupported_reason="unsupported",
    )

    result = evaluate_release(
        current_version="0.1.0",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        artifact_policy=WINDOWS_PORTABLE_POLICY,
        runtime_platform=unsupported_runtime,
        release=release_payload(
            tag_name="v0.2.0",
            assets=[
                asset("facebook-monitor-0.2.0-windows-portable.zip"),
                asset("facebook-monitor-0.2.0-windows-portable.zip.sha256"),
                *manifest_assets("0.2.0"),
            ],
        ),
    )

    assert result.status == "available"
    assert result.update_available
    assert result.asset_name == "facebook-monitor-0.2.0-windows-portable.zip"


def test_evaluate_release_marks_missing_sha256_as_not_installable() -> None:
    """缺同名 SHA256 sidecar 時不可把 release 顯示成可下載更新。"""

    result = evaluate_release(
        current_version="0.1.0-rc1",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        artifact_policy=WINDOWS_PORTABLE_POLICY,
        release=release_payload(
            tag_name="v0.1.0",
            assets=[
                asset("facebook-monitor-0.1.0-windows-portable.zip"),
                *manifest_assets("0.1.0"),
            ],
        ),
    )

    assert result.status == "sha256_asset_missing"
    assert not result.update_available
    assert result.failure_reason == "sha256_asset_missing"
    assert result.asset_name == "facebook-monitor-0.1.0-windows-portable.zip"
    assert result.sha256_asset_name == ""


def test_evaluate_release_marks_missing_manifest_as_not_installable() -> None:
    """缺 signed manifest 時不可把 release 顯示成可下載更新。"""

    result = evaluate_release(
        current_version="0.1.0-rc1",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        artifact_policy=WINDOWS_PORTABLE_POLICY,
        release=release_payload(
            tag_name="v0.1.0",
            assets=[
                asset("facebook-monitor-0.1.0-windows-portable.zip"),
                asset("facebook-monitor-0.1.0-windows-portable.zip.sha256"),
            ],
        ),
    )

    assert result.status == "manifest_file_missing"
    assert not result.update_available
    assert result.failure_reason == "manifest_file_missing"
    assert result.asset_name == "facebook-monitor-0.1.0-windows-portable.zip"
    assert result.manifest_asset_name == ""


def test_evaluate_release_marks_missing_manifest_signature_as_not_installable() -> None:
    """缺 detached signature 時不可把 release 顯示成可下載更新。"""

    result = evaluate_release(
        current_version="0.1.0-rc1",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        artifact_policy=WINDOWS_PORTABLE_POLICY,
        release=release_payload(
            tag_name="v0.1.0",
            assets=[
                asset("facebook-monitor-0.1.0-windows-portable.zip"),
                asset("facebook-monitor-0.1.0-windows-portable.zip.sha256"),
                asset(release_manifest_asset_name("0.1.0")),
            ],
        ),
    )

    assert result.status == "manifest_signature_asset_missing"
    assert not result.update_available
    assert result.failure_reason == "manifest_signature_asset_missing"
    assert result.manifest_asset_name == "facebook-monitor-0.1.0-manifest.json"
    assert result.manifest_signature_asset_name == ""


def test_evaluate_release_manifest_failure_keeps_download_asset_metadata() -> None:
    """manifest gate 失敗時仍要保留 zip / sha256 欄位供 UI 與診斷顯示。"""

    result = evaluate_release(
        current_version="0.1.0-rc1",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        artifact_policy=WINDOWS_PORTABLE_POLICY,
        release=release_payload(
            tag_name="v0.1.0",
            assets=[
                asset("facebook-monitor-0.1.0-windows-portable.zip"),
                asset("facebook-monitor-0.1.0-windows-portable.zip.sha256"),
            ],
        ),
    )

    assert result.status == "manifest_file_missing"
    assert result.update_available is False
    assert result.asset_name == "facebook-monitor-0.1.0-windows-portable.zip"
    assert result.sha256_asset_name == "facebook-monitor-0.1.0-windows-portable.zip.sha256"
    assert result.manifest_asset_name == ""
    assert result.manifest_signature_asset_name == ""


def test_evaluate_release_reports_current_when_remote_is_not_newer() -> None:
    """遠端版本不高於目前版本時不提示可更新。"""

    result = evaluate_release(
        current_version="0.1.0",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        artifact_policy=WINDOWS_PORTABLE_POLICY,
        release=release_payload(
            tag_name="v0.1.0",
            assets=[asset("facebook-monitor-0.1.0-windows-portable.zip")],
        ),
    )

    assert result.status == "current"
    assert not result.update_available
    assert result.latest_version == "0.1.0"


def test_evaluate_release_does_not_show_older_rc_as_latest_version() -> None:
    """目前 stable 版本高於遠端 rc 時，不可把 rc 顯示成最新版本。"""

    result = evaluate_release(
        current_version="0.1.0",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        artifact_policy=WINDOWS_PORTABLE_POLICY,
        release=release_payload(
            tag_name="v0.1.0-rc1",
            assets=[asset("facebook-monitor-0.1.0-rc1-windows-portable.zip")],
        ),
    )

    assert result.status == "current"
    assert not result.update_available
    assert result.latest_version == "0.1.0"
    assert result.summary == "目前已是最新版本"


def test_evaluate_release_reports_asset_missing_for_newer_release_without_zip() -> None:
    """新版 release 缺 Windows portable zip 時要明確標示 asset_missing。"""

    result = evaluate_release(
        current_version="0.1.0-rc1",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        artifact_policy=WINDOWS_PORTABLE_POLICY,
        release=release_payload(tag_name="v0.1.0", assets=[asset("source.zip")]),
    )

    assert result.status == "asset_missing"
    assert not result.update_available
    assert result.failure_reason == "asset_missing"


def test_evaluate_release_rejects_portable_asset_for_different_version() -> None:
    """新版 release 不能使用其他版本的 portable zip。"""

    result = evaluate_release(
        current_version="0.1.0",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        artifact_policy=WINDOWS_PORTABLE_POLICY,
        release=release_payload(
            tag_name="v0.1.1",
            assets=[
                asset("facebook-monitor-0.1.0-windows-portable.zip"),
                asset("facebook-monitor-0.1.0-windows-portable.zip.sha256"),
            ],
        ),
    )

    assert result.status == "asset_version_mismatch"
    assert not result.update_available
    assert result.failure_reason == "asset_version_mismatch"


def test_find_portable_asset_prefers_exact_version_filename() -> None:
    """多個 portable zip 存在時先選與 release tag 相符的檔名。"""

    assets = parse_release_assets(
        [
            asset("facebook-monitor-0.1.0-rc1-windows-portable.zip"),
            asset("facebook-monitor-0.1.0-windows-portable.zip"),
        ]
    )

    selected = find_portable_asset(
        assets,
        latest_version="0.1.0",
        policy=WINDOWS_PORTABLE_POLICY,
    )

    assert selected is not None
    assert selected.name == "facebook-monitor-0.1.0-windows-portable.zip"


def test_find_portable_asset_returns_none_for_mismatched_version() -> None:
    """沒有精確版本檔名時不 fallback 到其他 portable zip。"""

    assets = parse_release_assets(
        [asset("facebook-monitor-0.1.0-windows-portable.zip")]
    )

    assert (
        find_portable_asset(
            assets,
            latest_version="0.1.1",
            policy=WINDOWS_PORTABLE_POLICY,
        )
        is None
    )
