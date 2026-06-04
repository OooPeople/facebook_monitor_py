"""FastAPI Web UI tests。"""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch
from fastapi.testclient import TestClient

from facebook_monitor.runtime.paths import resolve_runtime_paths
from facebook_monitor.runtime.update_operation_lock import acquire_update_operation_lock
from facebook_monitor.updates.download import UpdateDownloadResult
from facebook_monitor.updates.manifest import release_manifest_asset_name
from facebook_monitor.updates.manifest import release_manifest_signature_asset_name
from facebook_monitor.updates.release_check import UpdateCheckResult
from facebook_monitor.version import APP_VERSION
from tests.helpers.webapp import FakeSchedulerManager


from tests.webapp.app_test_helpers import create_app
from tests.webapp.app_test_helpers import make_supported_macos_update_paths
from tests.webapp.app_test_helpers import make_supported_update_paths
from tests.webapp.app_test_helpers import verified_update_download_result


def signed_manifest_update_fields(version: str) -> dict[str, str]:
    """建立可安裝 release 需要的 signed manifest 欄位。"""

    manifest_name = release_manifest_asset_name(version)
    signature_name = release_manifest_signature_asset_name(version)
    return {
        "manifest_asset_name": manifest_name,
        "manifest_asset_download_url": f"https://downloads.example.test/{manifest_name}",
        "manifest_signature_asset_name": signature_name,
        "manifest_signature_asset_download_url": (
            f"https://downloads.example.test/{signature_name}"
        ),
    }


def test_settings_page_shows_problem_report_diagnostics(tmp_path: Path) -> None:
    """設定頁只保留問題回報診斷入口，不顯示 runtime 明細。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data")
    app = create_app(
        db_path=paths.db_path,
        profile_dir=paths.profile_dir,
        scheduler_manager=FakeSchedulerManager(),
    )
    app.state.runtime_paths = paths
    client = TestClient(app)

    response = client.get("/settings")

    assert response.status_code == 200
    assert "問題回報與診斷" in response.text
    assert "下載支援診斷包" in response.text
    assert "Runtime diagnostics" not in response.text
    assert "複製診斷資訊" not in response.text
    assert "通知預設值" not in response.text
    assert "通知 outbox" not in response.text
    assert "通知發送失敗" not in response.text
    assert "清除失敗通知" not in response.text
    assert str(paths.db_path) not in response.text
    assert str(paths.logs_dir) not in response.text
    assert "data-dirty-submit" in response.text


def test_settings_page_shows_idle_update_check(tmp_path: Path) -> None:
    """設定頁預設只顯示更新檢查入口，不主動查 GitHub。"""

    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    response = client.get("/settings")

    assert response.status_code == 200
    assert "程式更新" in response.text
    assert "尚未檢查更新" in response.text
    assert 'name="update_check" value="1"' in response.text
    assert "data-update-section" in response.text
    assert "data-update-check-form" in response.text
    assert "data-update-progress-help" in response.text
    assert "當自動跳出新頁面時，這個分頁就可以關閉。" in response.text
    assert "下載完成後會自動套用更新並重啟程式" not in response.text
    assert "請不要手動關閉程式" not in response.text
    assert "GitHub repo" not in response.text
    assert "Preview" not in response.text


def test_settings_update_check_uses_github_release_presenter(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """按下更新檢查時，設定頁顯示 release metadata，不下載 asset。"""

    async def fake_check_github_release_updates(
        *,
        current_version: str,
        channel: str = "stable",
        allow_env_repository_override: bool = True,
    ) -> UpdateCheckResult:
        assert current_version == APP_VERSION
        assert channel == "stable"
        return UpdateCheckResult(
            checked=True,
            status="available",
            channel=channel,
            repository="OooPeople/facebook_monitor_py",
            current_version=current_version,
            latest_version="0.1.1",
            update_available=True,
            summary="有新版 0.1.1",
            detail="下載與套用能力會依目前 runtime 支援與 SHA256 asset 狀態決定。",
            release_url="https://github.com/OooPeople/facebook_monitor_py/releases/tag/v0.1.1",
            asset_name="facebook-monitor-0.1.1-windows-portable.zip",
            asset_download_url=(
                "https://github.com/OooPeople/facebook_monitor_py/releases/download/"
                "v0.1.1/facebook-monitor-0.1.1-windows-portable.zip"
            ),
            sha256_asset_name="facebook-monitor-0.1.1-windows-portable.zip.sha256",
            sha256_asset_download_url=(
                "https://github.com/OooPeople/facebook_monitor_py/releases/download/"
                "v0.1.1/facebook-monitor-0.1.1-windows-portable.zip.sha256"
            ),
            failure_reason="",
        )

    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.check_github_release_updates",
        fake_check_github_release_updates,
    )
    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    response = client.get("/settings?update_check=1")

    assert response.status_code == 200
    assert "有新版 0.1.1" in response.text
    assert "最新版本" in response.text
    assert "0.1.1" in response.text
    assert "Release asset" not in response.text
    assert "SHA256" not in response.text
    assert "開啟 Release" not in response.text
    assert "下載更新" not in response.text


def test_settings_update_check_skips_github_when_operation_locked(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """已有更新流程時，手動檢查更新不應再進入 GitHub release check。"""

    paths = make_supported_update_paths(tmp_path)
    checked = False

    async def fake_check_github_release_updates(**kwargs) -> UpdateCheckResult:
        nonlocal checked
        checked = True
        raise AssertionError("check should not run while operation lock is busy")

    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.check_github_release_updates",
        fake_check_github_release_updates,
    )
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    client = TestClient(app)

    with acquire_update_operation_lock(paths.runtime_dir, "external"):
        response = client.get("/settings?update_check=1")

    assert response.status_code == 200
    assert "尚未檢查更新" in response.text
    assert checked is False


def test_settings_update_check_shows_download_action_when_sha256_asset_exists(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """有新版且有 SHA256 asset 時才顯示下載更新入口。"""

    paths = make_supported_update_paths(tmp_path)
    monkeypatch.setenv("FACEBOOK_MONITOR_PACKAGING_MODE", "pyinstaller-onedir-gui-tray")
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_windows", lambda: True)

    async def fake_check_github_release_updates(
        *,
        current_version: str,
        channel: str = "stable",
        allow_env_repository_override: bool = True,
    ) -> UpdateCheckResult:
        return UpdateCheckResult(
            checked=True,
            status="available",
            channel=channel,
            repository="OooPeople/facebook_monitor_py",
            current_version=current_version,
            latest_version="0.1.1",
            update_available=True,
            summary="有新版 0.1.1",
            detail="",
            release_url="https://github.com/OooPeople/facebook_monitor_py/releases/tag/v0.1.1",
            asset_name="facebook-monitor-0.1.1-windows-portable.zip",
            asset_download_url="https://downloads.example.test/app.zip",
            sha256_asset_name="facebook-monitor-0.1.1-windows-portable.zip.sha256",
            sha256_asset_download_url="https://downloads.example.test/app.zip.sha256",
            failure_reason="",
            **signed_manifest_update_fields("0.1.1"),
        )

    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.check_github_release_updates",
        fake_check_github_release_updates,
    )
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    client = TestClient(app)

    response = client.get("/settings?update_check=1")

    assert response.status_code == 200
    assert 'action="/settings/updates/download-and-apply"' in response.text
    assert "下載新版並套用更新" in response.text


def test_settings_update_check_hides_action_without_signed_manifest(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """即使 release metadata 標示有新版，缺 signed manifest 也不可顯示更新入口。"""

    paths = make_supported_update_paths(tmp_path)
    monkeypatch.setenv("FACEBOOK_MONITOR_PACKAGING_MODE", "pyinstaller-onedir-gui-tray")
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_windows", lambda: True)

    async def fake_check_github_release_updates(
        *,
        current_version: str,
        channel: str = "stable",
        allow_env_repository_override: bool = True,
    ) -> UpdateCheckResult:
        return UpdateCheckResult(
            checked=True,
            status="manifest_file_missing",
            channel=channel,
            repository="OooPeople/facebook_monitor_py",
            current_version=current_version,
            latest_version="0.1.1",
            update_available=False,
            summary="找到新版 0.1.1，但缺少 signed manifest",
            detail="此版本缺少 signed manifest，無法下載或套用。",
            release_url="https://github.com/OooPeople/facebook_monitor_py/releases/tag/v0.1.1",
            asset_name="facebook-monitor-0.1.1-windows-portable.zip",
            asset_download_url="https://downloads.example.test/app.zip",
            sha256_asset_name="facebook-monitor-0.1.1-windows-portable.zip.sha256",
            sha256_asset_download_url="https://downloads.example.test/app.zip.sha256",
            failure_reason="manifest_file_missing",
        )

    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.check_github_release_updates",
        fake_check_github_release_updates,
    )
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    client = TestClient(app)

    response = client.get("/settings?update_check=1")

    assert response.status_code == 200
    assert 'action="/settings/updates/download"' not in response.text
    assert 'action="/settings/updates/download-and-apply"' not in response.text


def test_settings_download_update_does_not_download_without_signed_manifest(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """缺 signed manifest 時 route 層不可呼叫下載流程。"""

    paths = make_supported_update_paths(tmp_path)
    monkeypatch.setenv("FACEBOOK_MONITOR_PACKAGING_MODE", "pyinstaller-onedir-gui-tray")
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_windows", lambda: True)
    download_called = False

    async def fake_check_github_release_updates(
        *,
        current_version: str,
        channel: str = "stable",
        allow_env_repository_override: bool = True,
    ) -> UpdateCheckResult:
        return UpdateCheckResult(
            checked=True,
            status="manifest_file_missing",
            channel=channel,
            repository="OooPeople/facebook_monitor_py",
            current_version=current_version,
            latest_version="0.1.1",
            update_available=False,
            summary="找到新版 0.1.1，但缺少 signed manifest",
            detail="此版本缺少 signed manifest，無法下載或套用。",
            release_url="https://github.com/OooPeople/facebook_monitor_py/releases/tag/v0.1.1",
            asset_name="facebook-monitor-0.1.1-windows-portable.zip",
            asset_download_url="https://downloads.example.test/app.zip",
            sha256_asset_name="facebook-monitor-0.1.1-windows-portable.zip.sha256",
            sha256_asset_download_url="https://downloads.example.test/app.zip.sha256",
            failure_reason="manifest_file_missing",
        )

    async def fake_download_and_verify_update(*args, **kwargs):
        nonlocal download_called
        download_called = True
        raise AssertionError("download should not be called without signed manifest")

    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.check_github_release_updates",
        fake_check_github_release_updates,
    )
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.download_and_verify_update",
        fake_download_and_verify_update,
    )
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    client = TestClient(app)

    response = client.post("/settings/updates/download", follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert not download_called


def test_settings_update_check_shows_macos_apply_action_when_updater_exists(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """macOS frozen onedir 含 updater 時顯示下載並套用入口。"""

    paths = make_supported_macos_update_paths(tmp_path)
    monkeypatch.setenv("FACEBOOK_MONITOR_PACKAGING_MODE", "pyinstaller-macos-arm64-onedir")
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_windows", lambda: False)
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_macos", lambda: True)
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings._current_update_machine",
        lambda: "arm64",
    )

    async def fake_check_github_release_updates(
        *,
        current_version: str,
        channel: str = "stable",
        allow_env_repository_override: bool = True,
    ) -> UpdateCheckResult:
        return UpdateCheckResult(
            checked=True,
            status="available",
            channel=channel,
            repository="OooPeople/facebook_monitor_py",
            current_version=current_version,
            latest_version="0.1.1",
            update_available=True,
            summary="有新版 0.1.1",
            detail="",
            release_url="https://github.com/OooPeople/facebook_monitor_py/releases/tag/v0.1.1",
            asset_name="facebook-monitor-0.1.1-macos-arm64-onedir.zip",
            asset_download_url="https://downloads.example.test/app.zip",
            sha256_asset_name="facebook-monitor-0.1.1-macos-arm64-onedir.zip.sha256",
            sha256_asset_download_url="https://downloads.example.test/app.zip.sha256",
            failure_reason="",
            **signed_manifest_update_fields("0.1.1"),
        )

    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.check_github_release_updates",
        fake_check_github_release_updates,
    )
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    client = TestClient(app)

    response = client.get("/settings?update_check=1")

    assert response.status_code == 200
    assert 'action="/settings/updates/download-and-apply"' in response.text
    assert "下載新版並套用更新" in response.text


def test_settings_update_check_shows_macos_download_only_when_updater_missing(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """macOS frozen onedir 缺 updater 時保留下載驗證但不允許套用。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.app_base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FACEBOOK_MONITOR_PACKAGING_MODE", "pyinstaller-macos-arm64-onedir")
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_windows", lambda: False)
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_macos", lambda: True)
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings._current_update_machine",
        lambda: "arm64",
    )

    async def fake_check_github_release_updates(
        *,
        current_version: str,
        channel: str = "stable",
        allow_env_repository_override: bool = True,
    ) -> UpdateCheckResult:
        return UpdateCheckResult(
            checked=True,
            status="available",
            channel=channel,
            repository="OooPeople/facebook_monitor_py",
            current_version=current_version,
            latest_version="0.1.1",
            update_available=True,
            summary="有新版 0.1.1",
            detail="",
            release_url="https://github.com/OooPeople/facebook_monitor_py/releases/tag/v0.1.1",
            asset_name="facebook-monitor-0.1.1-macos-arm64-onedir.zip",
            asset_download_url="https://downloads.example.test/app.zip",
            sha256_asset_name="facebook-monitor-0.1.1-macos-arm64-onedir.zip.sha256",
            sha256_asset_download_url="https://downloads.example.test/app.zip.sha256",
            failure_reason="",
            **signed_manifest_update_fields("0.1.1"),
        )

    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.check_github_release_updates",
        fake_check_github_release_updates,
    )
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    client = TestClient(app)

    response = client.get("/settings?update_check=1")

    assert response.status_code == 200
    assert 'action="/settings/updates/download"' in response.text
    assert "下載並驗證更新" in response.text
    assert 'action="/settings/updates/download-and-apply"' not in response.text


def test_settings_update_check_shows_download_only_for_external_db_path(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """外部 DB runtime 可檢查與下載更新，但不可顯示自動套用入口。"""

    paths = resolve_runtime_paths(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "external" / "app.db",
        app_base_dir=tmp_path / "app",
    )
    paths.app_base_dir.mkdir(parents=True, exist_ok=True)
    (paths.app_base_dir / "facebook-monitor-updater.exe").write_text(
        "updater",
        encoding="utf-8",
    )
    monkeypatch.setenv("FACEBOOK_MONITOR_PACKAGING_MODE", "pyinstaller-onedir-gui-tray")
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_windows", lambda: True)

    async def fake_check_github_release_updates(
        *,
        current_version: str,
        channel: str = "stable",
        allow_env_repository_override: bool = True,
    ) -> UpdateCheckResult:
        return UpdateCheckResult(
            checked=True,
            status="available",
            channel=channel,
            repository="OooPeople/facebook_monitor_py",
            current_version=current_version,
            latest_version="0.1.1",
            update_available=True,
            summary="有新版 0.1.1",
            detail="",
            release_url="https://github.com/OooPeople/facebook_monitor_py/releases/tag/v0.1.1",
            asset_name="facebook-monitor-0.1.1-windows-portable.zip",
            asset_download_url="https://downloads.example.test/app.zip",
            sha256_asset_name="facebook-monitor-0.1.1-windows-portable.zip.sha256",
            sha256_asset_download_url="https://downloads.example.test/app.zip.sha256",
            failure_reason="",
            **signed_manifest_update_fields("0.1.1"),
        )

    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.check_github_release_updates",
        fake_check_github_release_updates,
    )
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    client = TestClient(app)

    response = client.get("/settings?update_check=1")

    assert response.status_code == 200
    assert 'action="/settings/updates/download"' in response.text
    assert 'action="/settings/updates/download-and-apply"' not in response.text
    assert "外部 DB 路徑不支援自動套用更新" in response.text


def test_settings_update_check_hides_download_action_on_intel_macos(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Intel macOS 不顯示 Apple Silicon 更新檔下載或套用入口。"""

    paths = make_supported_macos_update_paths(tmp_path)
    monkeypatch.setenv("FACEBOOK_MONITOR_PACKAGING_MODE", "pyinstaller-macos-arm64-onedir")
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_windows", lambda: False)
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_macos", lambda: True)
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings._current_update_machine",
        lambda: "x86_64",
    )

    async def fake_check_github_release_updates(
        *,
        current_version: str,
        channel: str = "stable",
        allow_env_repository_override: bool = True,
    ) -> UpdateCheckResult:
        return UpdateCheckResult(
            checked=True,
            status="available",
            channel=channel,
            repository="OooPeople/facebook_monitor_py",
            current_version=current_version,
            latest_version="0.1.1",
            update_available=True,
            summary="有新版 0.1.1",
            detail="",
            release_url="https://github.com/OooPeople/facebook_monitor_py/releases/tag/v0.1.1",
            asset_name="facebook-monitor-0.1.1-macos-arm64-onedir.zip",
            asset_download_url="https://downloads.example.test/app.zip",
            sha256_asset_name="facebook-monitor-0.1.1-macos-arm64-onedir.zip.sha256",
            sha256_asset_download_url="https://downloads.example.test/app.zip.sha256",
            failure_reason="",
            **signed_manifest_update_fields("0.1.1"),
        )

    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.check_github_release_updates",
        fake_check_github_release_updates,
    )
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    client = TestClient(app)

    response = client.get("/settings?update_check=1")

    assert response.status_code == 200
    assert "目前平台沒有對應的更新檔" in response.text
    assert 'action="/settings/updates/download"' not in response.text
    assert 'action="/settings/updates/download-and-apply"' not in response.text


def test_settings_download_update_verifies_asset_and_opens_folder(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """下載更新 route 重新查 metadata，下載到 runtime updates dir，驗證後開資料夾。"""

    paths = make_supported_update_paths(tmp_path)
    monkeypatch.setenv("FACEBOOK_MONITOR_PACKAGING_MODE", "pyinstaller-onedir-gui-tray")
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_windows", lambda: True)
    checked_update: dict[str, object] = {}

    async def fake_check_github_release_updates(
        *,
        current_version: str,
        channel: str = "stable",
        allow_env_repository_override: bool = True,
    ) -> UpdateCheckResult:
        assert current_version == APP_VERSION
        assert channel == "stable"
        return UpdateCheckResult(
            checked=True,
            status="available",
            channel=channel,
            repository="OooPeople/facebook_monitor_py",
            current_version=current_version,
            latest_version="0.1.1",
            update_available=True,
            summary="有新版 0.1.1",
            detail="",
            release_url="https://github.com/OooPeople/facebook_monitor_py/releases/tag/v0.1.1",
            asset_name="facebook-monitor-0.1.1-windows-portable.zip",
            asset_download_url="https://downloads.example.test/app.zip",
            sha256_asset_name="facebook-monitor-0.1.1-windows-portable.zip.sha256",
            sha256_asset_download_url="https://downloads.example.test/app.zip.sha256",
            failure_reason="",
            **signed_manifest_update_fields("0.1.1"),
        )

    async def fake_download_and_verify_update(
        *,
        update_check: UpdateCheckResult,
        updates_dir: Path,
    ) -> UpdateDownloadResult:
        checked_update["asset_name"] = update_check.asset_name
        checked_update["updates_dir"] = updates_dir
        file_path = updates_dir / "0.1.1" / "facebook-monitor-0.1.1-windows-portable.zip"
        file_path.parent.mkdir(parents=True)
        file_path.write_bytes(b"verified zip")
        return verified_update_download_result(
            update_check=update_check,
            file_path=file_path,
        )

    opened_paths: list[Path] = []

    def fake_reveal_in_file_manager(path: Path) -> bool:
        opened_paths.append(path)
        return True

    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.check_github_release_updates",
        fake_check_github_release_updates,
    )
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.download_and_verify_update",
        fake_download_and_verify_update,
    )
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.reveal_in_file_manager",
        fake_reveal_in_file_manager,
    )
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    client = TestClient(app)

    response = client.post(
        "/settings/updates/download",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "message=" in response.headers["location"]
    assert checked_update["asset_name"] == "facebook-monitor-0.1.1-windows-portable.zip"
    assert checked_update["updates_dir"] == paths.updates_dir
    assert (paths.runtime_dir / "pending_update.json").is_file()
    assert opened_paths == [
        paths.updates_dir / "0.1.1" / "facebook-monitor-0.1.1-windows-portable.zip"
    ]


def test_settings_download_update_returns_busy_when_operation_locked(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """下載更新 route 在 operation lock busy 時不應查 release 或下載。"""

    paths = make_supported_update_paths(tmp_path)
    monkeypatch.setenv("FACEBOOK_MONITOR_PACKAGING_MODE", "pyinstaller-onedir-gui-tray")
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_windows", lambda: True)
    checked = False

    async def fake_check_github_release_updates(**kwargs) -> UpdateCheckResult:
        nonlocal checked
        checked = True
        raise AssertionError("check should not run while operation lock is busy")

    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.check_github_release_updates",
        fake_check_github_release_updates,
    )
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    client = TestClient(app)

    with acquire_update_operation_lock(paths.runtime_dir, "external"):
        response = client.post("/settings/updates/download", follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert checked is False


def test_settings_macos_download_update_does_not_create_pending_update(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """macOS 打包缺 updater 時只下載驗證，不建立 updater handoff。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.app_base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FACEBOOK_MONITOR_PACKAGING_MODE", "pyinstaller-macos-arm64-onedir")
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_windows", lambda: False)
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_macos", lambda: True)
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings._current_update_machine",
        lambda: "arm64",
    )
    checked_update: dict[str, object] = {}

    async def fake_check_github_release_updates(
        *,
        current_version: str,
        channel: str = "stable",
        allow_env_repository_override: bool = True,
    ) -> UpdateCheckResult:
        return UpdateCheckResult(
            checked=True,
            status="available",
            channel=channel,
            repository="OooPeople/facebook_monitor_py",
            current_version=current_version,
            latest_version="0.1.1",
            update_available=True,
            summary="有新版 0.1.1",
            detail="",
            release_url="https://github.com/OooPeople/facebook_monitor_py/releases/tag/v0.1.1",
            asset_name="facebook-monitor-0.1.1-macos-arm64-onedir.zip",
            asset_download_url="https://downloads.example.test/app.zip",
            sha256_asset_name="facebook-monitor-0.1.1-macos-arm64-onedir.zip.sha256",
            sha256_asset_download_url="https://downloads.example.test/app.zip.sha256",
            failure_reason="",
            **signed_manifest_update_fields("0.1.1"),
        )

    async def fake_download_and_verify_update(
        *,
        update_check: UpdateCheckResult,
        updates_dir: Path,
    ) -> UpdateDownloadResult:
        checked_update["asset_name"] = update_check.asset_name
        file_path = updates_dir / "0.1.1" / "facebook-monitor-0.1.1-macos-arm64-onedir.zip"
        file_path.parent.mkdir(parents=True)
        file_path.write_bytes(b"verified zip")
        return verified_update_download_result(
            update_check=update_check,
            file_path=file_path,
        )

    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.check_github_release_updates",
        fake_check_github_release_updates,
    )
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.download_and_verify_update",
        fake_download_and_verify_update,
    )
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.reveal_in_file_manager",
        lambda path: True,
    )
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    client = TestClient(app)

    response = client.post("/settings/updates/download", follow_redirects=False)

    assert response.status_code == 303
    assert "message=" in response.headers["location"]
    assert checked_update["asset_name"] == "facebook-monitor-0.1.1-macos-arm64-onedir.zip"
    assert not (paths.runtime_dir / "pending_update.json").exists()


def test_settings_download_and_apply_update_returns_modal_json_and_requests_shutdown(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """設定頁主流程會下載驗證、建立 handoff、啟動 updater，並回 JSON 給 modal。"""

    paths = make_supported_update_paths(tmp_path)
    monkeypatch.setenv("FACEBOOK_MONITOR_PACKAGING_MODE", "pyinstaller-onedir-gui-tray")
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_windows", lambda: True)
    checked_update: dict[str, object] = {}

    async def fake_check_github_release_updates(
        *,
        current_version: str,
        channel: str = "stable",
        allow_env_repository_override: bool = True,
    ) -> UpdateCheckResult:
        assert current_version == APP_VERSION
        assert channel == "stable"
        return UpdateCheckResult(
            checked=True,
            status="available",
            channel=channel,
            repository="OooPeople/facebook_monitor_py",
            current_version=current_version,
            latest_version="0.1.1",
            update_available=True,
            summary="有新版 0.1.1",
            detail="",
            release_url="https://github.com/OooPeople/facebook_monitor_py/releases/tag/v0.1.1",
            asset_name="facebook-monitor-0.1.1-windows-portable.zip",
            asset_download_url="https://downloads.example.test/app.zip",
            sha256_asset_name="facebook-monitor-0.1.1-windows-portable.zip.sha256",
            sha256_asset_download_url="https://downloads.example.test/app.zip.sha256",
            failure_reason="",
            **signed_manifest_update_fields("0.1.1"),
        )

    async def fake_download_and_verify_update(
        *,
        update_check: UpdateCheckResult,
        updates_dir: Path,
    ) -> UpdateDownloadResult:
        checked_update["asset_name"] = update_check.asset_name
        checked_update["updates_dir"] = updates_dir
        file_path = updates_dir / "0.1.1" / "facebook-monitor-0.1.1-windows-portable.zip"
        file_path.parent.mkdir(parents=True)
        file_path.write_bytes(b"verified zip")
        return verified_update_download_result(
            update_check=update_check,
            file_path=file_path,
        )

    launched_paths: list[Path] = []

    def fake_launch_temp_updater(*, paths, wait_seconds=300):
        launched_paths.append(paths.runtime_dir)
        from facebook_monitor.updates.launcher import UpdaterLaunchResult

        return UpdaterLaunchResult(
            launched=True,
            status="launched",
            message="updater launched",
            pid=123,
        )

    shutdown_requested: list[bool] = []
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.check_github_release_updates",
        fake_check_github_release_updates,
    )
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.download_and_verify_update",
        fake_download_and_verify_update,
    )
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.launch_temp_updater",
        fake_launch_temp_updater,
    )
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    app.state.request_shutdown = lambda: shutdown_requested.append(True)
    client = TestClient(app)

    response = client.post("/settings/updates/download-and-apply")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "stage": "launched",
        "message": "更新器已啟動，程式即將關閉並套用更新",
        "latest_version": "0.1.1",
        "shutdown_requested": True,
    }
    assert checked_update["asset_name"] == "facebook-monitor-0.1.1-windows-portable.zip"
    assert checked_update["updates_dir"] == paths.updates_dir
    assert (paths.runtime_dir / "pending_update.json").is_file()
    assert launched_paths == [paths.runtime_dir]
    assert shutdown_requested == [True]


def test_settings_download_and_apply_update_returns_busy_when_operation_locked(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """更新流程已在執行時，modal route 應回既有錯誤 JSON 且不下載。"""

    paths = make_supported_update_paths(tmp_path)
    monkeypatch.setenv("FACEBOOK_MONITOR_PACKAGING_MODE", "pyinstaller-onedir-gui-tray")
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_windows", lambda: True)
    checked = False

    async def fake_check_github_release_updates(**kwargs) -> UpdateCheckResult:
        nonlocal checked
        checked = True
        raise AssertionError("check should not run while operation lock is busy")

    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.check_github_release_updates",
        fake_check_github_release_updates,
    )
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    client = TestClient(app)

    with acquire_update_operation_lock(paths.runtime_dir, "external"):
        response = client.post("/settings/updates/download-and-apply")

    assert response.status_code == 400
    assert response.json() == {
        "ok": False,
        "stage": "operation_lock",
        "error": "更新流程正在執行中，請稍後再試。",
    }
    assert checked is False


def test_settings_macos_download_and_apply_update_creates_handoff(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """macOS frozen onedir 可走 settings 下載、handoff、啟動 updater 流程。"""

    paths = make_supported_macos_update_paths(tmp_path)
    monkeypatch.setenv("FACEBOOK_MONITOR_PACKAGING_MODE", "pyinstaller-macos-arm64-onedir")
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_windows", lambda: False)
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_macos", lambda: True)
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings._current_update_machine",
        lambda: "arm64",
    )
    checked_update: dict[str, object] = {}

    async def fake_check_github_release_updates(
        *,
        current_version: str,
        channel: str = "stable",
        allow_env_repository_override: bool = True,
    ) -> UpdateCheckResult:
        return UpdateCheckResult(
            checked=True,
            status="available",
            channel=channel,
            repository="OooPeople/facebook_monitor_py",
            current_version=current_version,
            latest_version="0.1.1",
            update_available=True,
            summary="有新版 0.1.1",
            detail="",
            release_url="https://github.com/OooPeople/facebook_monitor_py/releases/tag/v0.1.1",
            asset_name="facebook-monitor-0.1.1-macos-arm64-onedir.zip",
            asset_download_url="https://downloads.example.test/app.zip",
            sha256_asset_name="facebook-monitor-0.1.1-macos-arm64-onedir.zip.sha256",
            sha256_asset_download_url="https://downloads.example.test/app.zip.sha256",
            failure_reason="",
            **signed_manifest_update_fields("0.1.1"),
        )

    async def fake_download_and_verify_update(
        *,
        update_check: UpdateCheckResult,
        updates_dir: Path,
    ) -> UpdateDownloadResult:
        checked_update["asset_name"] = update_check.asset_name
        checked_update["updates_dir"] = updates_dir
        file_path = updates_dir / "0.1.1" / "facebook-monitor-0.1.1-macos-arm64-onedir.zip"
        file_path.parent.mkdir(parents=True)
        file_path.write_bytes(b"verified zip")
        return verified_update_download_result(
            update_check=update_check,
            file_path=file_path,
        )

    launched_paths: list[Path] = []

    def fake_launch_temp_updater(*, paths, wait_seconds=300):
        launched_paths.append(paths.runtime_dir)
        from facebook_monitor.updates.launcher import UpdaterLaunchResult

        return UpdaterLaunchResult(
            launched=True,
            status="launched",
            message="updater launched",
            pid=123,
        )

    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.check_github_release_updates",
        fake_check_github_release_updates,
    )
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.download_and_verify_update",
        fake_download_and_verify_update,
    )
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.launch_temp_updater",
        fake_launch_temp_updater,
    )
    shutdown_requested: list[bool] = []
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    app.state.request_shutdown = lambda: shutdown_requested.append(True)
    client = TestClient(app)

    response = client.post("/settings/updates/download-and-apply")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert checked_update["asset_name"] == "facebook-monitor-0.1.1-macos-arm64-onedir.zip"
    assert checked_update["updates_dir"] == paths.updates_dir
    assert (paths.runtime_dir / "pending_update.json").is_file()
    assert launched_paths == [paths.runtime_dir]
    assert shutdown_requested == [True]


def test_settings_macos_download_and_apply_uses_macos_release_asset_policy(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """macOS settings 更新流程應從同一個 release 中選 macOS artifact。"""

    paths = make_supported_macos_update_paths(tmp_path)
    monkeypatch.setenv("FACEBOOK_MONITOR_PACKAGING_MODE", "pyinstaller-macos-arm64-onedir")
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_windows", lambda: False)
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_macos", lambda: True)
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings._current_update_machine",
        lambda: "arm64",
    )
    monkeypatch.setattr("facebook_monitor.updates.artifacts.sys.platform", "darwin")
    monkeypatch.setattr("facebook_monitor.updates.artifacts.platform.machine", lambda: "arm64")
    checked_update: dict[str, object] = {}

    async def fake_fetch_release(*, client, repository: str, channel: str):
        del client
        assert repository == "OooPeople/facebook_monitor_py"
        assert channel == "stable"
        return {
            "tag_name": "v9.9.9",
            "html_url": "https://github.com/OooPeople/facebook_monitor_py/releases/tag/v9.9.9",
            "assets": [
                {
                    "name": "facebook-monitor-9.9.9-windows-portable.zip",
                    "browser_download_url": "https://downloads.example.test/windows.zip",
                },
                {
                    "name": "facebook-monitor-9.9.9-windows-portable.zip.sha256",
                    "browser_download_url": "https://downloads.example.test/windows.zip.sha256",
                },
                {
                    "name": "facebook-monitor-9.9.9-macos-arm64-onedir.zip",
                    "browser_download_url": "https://downloads.example.test/macos.zip",
                },
                {
                    "name": "facebook-monitor-9.9.9-macos-arm64-onedir.zip.sha256",
                    "browser_download_url": "https://downloads.example.test/macos.zip.sha256",
                },
                {
                    "name": release_manifest_asset_name("9.9.9"),
                    "browser_download_url": "https://downloads.example.test/manifest.json",
                },
                {
                    "name": release_manifest_signature_asset_name("9.9.9"),
                    "browser_download_url": "https://downloads.example.test/manifest.json.sig",
                },
            ],
        }

    async def fake_download_and_verify_update(
        *,
        update_check: UpdateCheckResult,
        updates_dir: Path,
    ) -> UpdateDownloadResult:
        checked_update["asset_name"] = update_check.asset_name
        checked_update["asset_download_url"] = update_check.asset_download_url
        file_path = updates_dir / "9.9.9" / update_check.asset_name
        file_path.parent.mkdir(parents=True)
        file_path.write_bytes(b"verified zip")
        return verified_update_download_result(
            update_check=update_check,
            file_path=file_path,
        )

    def fake_launch_temp_updater(*, paths, wait_seconds=300):
        del paths, wait_seconds
        from facebook_monitor.updates.launcher import UpdaterLaunchResult

        return UpdaterLaunchResult(
            launched=True,
            status="launched",
            message="updater launched",
            pid=123,
        )

    monkeypatch.setattr(
        "facebook_monitor.updates.release_check._fetch_release",
        fake_fetch_release,
    )
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.download_and_verify_update",
        fake_download_and_verify_update,
    )
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.launch_temp_updater",
        fake_launch_temp_updater,
    )
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    app.state.request_shutdown = lambda: None
    client = TestClient(app)

    response = client.post("/settings/updates/download-and-apply")

    assert response.status_code == 200
    assert checked_update == {
        "asset_name": "facebook-monitor-9.9.9-macos-arm64-onedir.zip",
        "asset_download_url": "https://downloads.example.test/macos.zip",
    }


def test_settings_download_update_rejects_source_mode(tmp_path: Path) -> None:
    """source mode 不可建立會指向 repo app_base_dir 的 pending update。"""

    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    response = client.post(
        "/settings/updates/download",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]


def test_settings_apply_update_launches_updater_and_requests_shutdown(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """套用更新 route 啟動 temp updater，並呼叫 launcher 提供的 shutdown hook。"""

    paths = make_supported_update_paths(tmp_path)
    monkeypatch.setenv("FACEBOOK_MONITOR_PACKAGING_MODE", "pyinstaller-onedir-gui-tray")
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_windows", lambda: True)
    launched_paths: list[Path] = []

    def fake_launch_temp_updater(*, paths, wait_seconds=300):
        launched_paths.append(paths.runtime_dir)
        from facebook_monitor.updates.launcher import UpdaterLaunchResult

        return UpdaterLaunchResult(
            launched=True,
            status="launched",
            message="updater launched",
            pid=123,
        )

    shutdown_requested: list[bool] = []
    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.launch_temp_updater",
        fake_launch_temp_updater,
    )
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    app.state.request_shutdown = lambda: shutdown_requested.append(True)
    client = TestClient(app)

    response = client.post("/settings/updates/apply", follow_redirects=False)

    assert response.status_code == 303
    assert "message=" in response.headers["location"]
    assert launched_paths == [paths.runtime_dir]
    assert shutdown_requested == [True]


def test_settings_apply_update_returns_busy_when_operation_locked(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """套用更新 route 在 operation lock busy 時不應啟動 updater。"""

    paths = make_supported_update_paths(tmp_path)
    monkeypatch.setenv("FACEBOOK_MONITOR_PACKAGING_MODE", "pyinstaller-onedir-gui-tray")
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_windows", lambda: True)
    launched = False

    def fake_launch_temp_updater(*, paths, wait_seconds=300):
        nonlocal launched
        launched = True
        raise AssertionError("launch should not run while operation lock is busy")

    monkeypatch.setattr(
        "facebook_monitor.webapp.routes.settings.launch_temp_updater",
        fake_launch_temp_updater,
    )
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    client = TestClient(app)

    with acquire_update_operation_lock(paths.runtime_dir, "external"):
        response = client.post("/settings/updates/apply", follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert launched is False


def test_settings_apply_update_rejects_source_mode(tmp_path: Path) -> None:
    """source mode 不可啟動 updater 套用流程。"""

    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    response = client.post("/settings/updates/apply", follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
