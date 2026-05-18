"""GitHub Release 更新檢查測試。"""

from __future__ import annotations

from typing import Any

from facebook_monitor.updates.release_check import evaluate_release
from facebook_monitor.updates.release_check import find_windows_portable_asset
from facebook_monitor.updates.release_check import parse_release_assets
from facebook_monitor.updates.release_check import parse_version
from facebook_monitor.updates.artifacts import MACOS_ARM64_ONEDIR_POLICY


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


def test_parse_version_orders_release_candidate_before_stable() -> None:
    """同版本 stable 應視為比 rc 新。"""

    assert parse_version("0.1.0-rc1").sort_key() < parse_version("0.1.0").sort_key()
    assert parse_version("v0.1.1").sort_key() > parse_version("0.1.0").sort_key()


def test_evaluate_release_reports_available_windows_portable_asset() -> None:
    """新版 release 含 portable zip 時回報可更新。"""

    result = evaluate_release(
        current_version="0.1.0-rc1",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        release=release_payload(
            tag_name="v0.1.0",
            assets=[
                asset("facebook-monitor-0.1.0-windows-portable.zip"),
                asset("facebook-monitor-0.1.0-windows-portable.zip.sha256"),
            ],
        ),
    )

    assert result.status == "available"
    assert result.update_available
    assert result.latest_version == "0.1.0"
    assert result.asset_name == "facebook-monitor-0.1.0-windows-portable.zip"
    assert result.sha256_asset_name == "facebook-monitor-0.1.0-windows-portable.zip.sha256"
    assert result.failure_reason == ""


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
            ],
        ),
    )

    assert result.status == "available"
    assert result.update_available
    assert result.asset_name == "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    assert result.sha256_asset_name == "facebook-monitor-0.1.0-macos-arm64-onedir.zip.sha256"


def test_evaluate_release_keeps_missing_sha256_as_non_blocking_phase_one_reason() -> None:
    """Phase 1 只查 metadata，缺 SHA256 asset 先保留 reason 但不阻擋更新提示。"""

    result = evaluate_release(
        current_version="0.1.0-rc1",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
        release=release_payload(
            tag_name="v0.1.0",
            assets=[asset("facebook-monitor-0.1.0-windows-portable.zip")],
        ),
    )

    assert result.status == "available"
    assert result.update_available
    assert result.failure_reason == "sha256_asset_missing"
    assert result.sha256_asset_name == ""


def test_evaluate_release_reports_current_when_remote_is_not_newer() -> None:
    """遠端版本不高於目前版本時不提示可更新。"""

    result = evaluate_release(
        current_version="0.1.0",
        channel="stable",
        repository="OooPeople/facebook_monitor_py",
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


def test_find_windows_portable_asset_prefers_exact_version_filename() -> None:
    """多個 portable zip 存在時先選與 release tag 相符的檔名。"""

    assets = parse_release_assets(
        [
            asset("facebook-monitor-0.1.0-rc1-windows-portable.zip"),
            asset("facebook-monitor-0.1.0-windows-portable.zip"),
        ]
    )

    selected = find_windows_portable_asset(assets, latest_version="0.1.0")

    assert selected is not None
    assert selected.name == "facebook-monitor-0.1.0-windows-portable.zip"


def test_find_windows_portable_asset_returns_none_for_mismatched_version() -> None:
    """沒有精確版本檔名時不 fallback 到其他 portable zip。"""

    assets = parse_release_assets(
        [asset("facebook-monitor-0.1.0-windows-portable.zip")]
    )

    assert find_windows_portable_asset(assets, latest_version="0.1.1") is None
