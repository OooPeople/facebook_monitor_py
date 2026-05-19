"""FastAPI Web UI tests。"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

from pytest import MonkeyPatch
from fastapi.testclient import TestClient

from facebook_monitor.application import context as application_context
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import TargetConfigPatch
from facebook_monitor.application.services import UpsertCommentsTargetRequest
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.application.services import RecordScanRequest
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.core.scan_failures import CONTENT_UNAVAILABLE_REASON
from facebook_monitor.facebook.group_metadata import GroupMetadata
from facebook_monitor.persistence.repositories.app_settings import ProfileSessionState
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.discord import DiscordResult
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult
from facebook_monitor.webapp.app import create_app as create_production_app
from facebook_monitor.webapp.app import parse_keywords_text
from facebook_monitor.webapp import query_service
from facebook_monitor.webapp.query_service import get_dashboard_view
from facebook_monitor.webapp.routes import dashboard as dashboard_routes
from facebook_monitor.webapp.routes.dashboard import _format_dashboard_revision_event
from facebook_monitor.runtime.paths import resolve_runtime_paths
from facebook_monitor.updates.download import UpdateDownloadResult
from facebook_monitor.updates.release_check import UpdateCheckResult
from facebook_monitor.webapp.scheduler_session import BackgroundSchedulerManager
from facebook_monitor.webapp.scheduler_session import SchedulerSessionOptions
from facebook_monitor.webapp.assets import ASSET_VERSION
from tests.helpers.webapp import FakeProfileManager
from tests.helpers.webapp import FakeSchedulerManager
from tests.helpers.notifications import NotificationRecorder


def create_app(**kwargs):
    """Web route tests 預設關閉 CSRF；CSRF 專門測試使用 production factory。"""

    kwargs.setdefault("enforce_csrf", False)
    return create_production_app(**kwargs)


def make_supported_update_paths(tmp_path: Path):
    """建立 settings 更新 route 測試用的 PyInstaller-like app root。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.app_base_dir.mkdir(parents=True, exist_ok=True)
    (paths.app_base_dir / "facebook-monitor-updater.exe").write_text(
        "updater",
        encoding="utf-8",
    )
    return paths


def make_supported_macos_update_paths(tmp_path: Path):
    """建立 settings 更新 route 測試用的 macOS onedir-like app root。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.app_base_dir.mkdir(parents=True, exist_ok=True)
    (paths.app_base_dir / "facebook-monitor-updater").write_text(
        "updater",
        encoding="utf-8",
    )
    return paths


def test_parse_keywords_text_dedupes_and_trims() -> None:
    """Web UI keyword parser 會去除空白與重複值。"""

    assert parse_keywords_text("票, 交換,票,,讓票") == ("票", "交換", "讓票")
    assert parse_keywords_text("徵;收;已售") == ("徵", "收", "已售")


def test_static_assets_revalidate_for_local_ui(tmp_path: Path) -> None:
    """Static JS/CSS 不保留本機快取，避免 sidebar module 沿用舊版。"""

    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    response = client.get("/static/dashboard/sidebar.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0, must-revalidate"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"


def test_create_app_uses_explicit_static_resource_dir(tmp_path: Path) -> None:
    """launcher 傳入的 static dir 應成為 Web UI 實際掛載資源路徑。"""

    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "resource-check.txt").write_text("custom static", encoding="utf-8")
    app = create_app(
        db_path=tmp_path / "app.db",
        profile_dir=tmp_path / "profile",
        static_dir=static_dir,
    )
    client = TestClient(app)

    response = client.get("/static/resource-check.txt")

    assert response.status_code == 200
    assert response.text == "custom static"
    assert app.state.static_dir == static_dir


def test_health_endpoint_returns_app_identity(tmp_path: Path) -> None:
    """Health endpoint 供 launcher 判斷既有 Web UI 是否存活。"""

    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "status": "ok",
        "app": "Facebook Monitor",
        "version": "0.3.1",
        "asset_version": ASSET_VERSION,
        "python_version": payload["python_version"],
        "packaging_mode": "source",
    }
    assert payload["python_version"]


def test_mutating_routes_require_csrf_token_for_loopback_host(tmp_path: Path) -> None:
    """CSRF middleware 驗 token 後，下游 Form route 仍要讀得到 body。"""

    db_path = tmp_path / "app.db"
    client = TestClient(
        create_production_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            csrf_token="known-token",
        )
    )
    missing_token_response = client.post(
        "/settings/target-keywords",
        data={"exclude_keywords": "售完"},
        headers={"host": "127.0.0.1:4818"},
        follow_redirects=False,
    )
    valid_token_response = client.post(
        "/settings/target-keywords",
        data={"csrf_token": "known-token", "exclude_keywords": "售完"},
        headers={"host": "127.0.0.1:4818"},
        follow_redirects=False,
    )

    assert missing_token_response.status_code == 403
    assert valid_token_response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        defaults = app_context.repositories.app_settings.get_target_keyword_defaults()
    assert defaults.exclude_keywords_text == "售完"


def test_mutating_routes_require_csrf_token_for_testserver_host(tmp_path: Path) -> None:
    """TestClient 預設 testserver host 也不能繞過 runtime CSRF 驗證。"""

    client = TestClient(
        create_production_app(
            db_path=tmp_path / "app.db",
            profile_dir=tmp_path / "profile",
            csrf_token="known-token",
        )
    )

    response = client.post(
        "/settings/target-keywords",
        data={"exclude_keywords": "售完"},
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_pages_render_csrf_token_for_forms_and_fetch_headers(tmp_path: Path) -> None:
    """HTML 會把同一個 CSRF token 提供給 form 與前端 fetch 使用。"""

    client = TestClient(
        create_app(
            db_path=tmp_path / "app.db",
            profile_dir=tmp_path / "profile",
            csrf_token="known-token",
        )
    )

    response = client.get("/settings")

    assert response.status_code == 200
    assert '<meta name="csrf-token" content="known-token">' in response.text
    assert response.text.count('name="csrf_token" value="known-token"') >= 3
    assert re.search(r'name="csrf_token" value="known-token"', response.text)


def test_settings_page_shows_runtime_diagnostics(tmp_path: Path) -> None:
    """設定頁顯示可複製的 runtime diagnostics。"""

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
    assert "Runtime diagnostics" in response.text
    assert response.text.index("通知預設值") < response.text.index("Runtime diagnostics")
    assert response.text.index("通知預設值") < response.text.index("程式更新")
    assert response.text.index("程式更新") < response.text.index("Runtime diagnostics")
    assert '<details class="target settings-card runtime-diagnostics-card">' in response.text
    assert '<summary class="runtime-diagnostics-summary">' in response.text
    assert str(paths.db_path) in response.text
    assert str(paths.profile_dir) in response.text
    assert str(paths.logs_dir) in response.text
    assert str(paths.updates_dir) in response.text
    assert "Browser mode" in response.text
    assert "playwright_chromium" in response.text
    assert "Asset version" in response.text
    assert "Python version" in response.text
    assert "Packaging mode" in response.text
    assert "Build date" in response.text
    assert "Git commit" in response.text
    assert "Reset targets on startup" in response.text
    assert "Resume active targets on startup" in response.text
    assert "Reset runtime data on startup" in response.text
    assert "複製診斷資訊" in response.text
    assert "runtime-diagnostics-copy-source" in response.text
    assert "data-secret-input" not in response.text
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
    ) -> UpdateCheckResult:
        assert current_version == "0.3.1"
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
            detail="目前只提供檢查，不會下載或套用更新。",
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


def test_settings_update_check_shows_macos_apply_action_when_updater_exists(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """macOS frozen onedir 含 updater 時顯示下載並套用入口。"""

    paths = make_supported_macos_update_paths(tmp_path)
    monkeypatch.setenv("FACEBOOK_MONITOR_PACKAGING_MODE", "pyinstaller-macos-arm64-onedir")
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_windows", lambda: False)
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_macos", lambda: True)

    async def fake_check_github_release_updates(
        *,
        current_version: str,
        channel: str = "stable",
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

    async def fake_check_github_release_updates(
        *,
        current_version: str,
        channel: str = "stable",
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
    ) -> UpdateCheckResult:
        assert current_version == "0.3.1"
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
        return UpdateDownloadResult(
            status="verified",
            downloaded=True,
            verified=True,
            file_path=file_path,
            sha256_path=file_path.with_name(file_path.name + ".sha256"),
            expected_sha256="a" * 64,
            actual_sha256="a" * 64,
            failure_reason="",
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
    checked_update: dict[str, object] = {}

    async def fake_check_github_release_updates(
        *,
        current_version: str,
        channel: str = "stable",
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
        return UpdateDownloadResult(
            status="verified",
            downloaded=True,
            verified=True,
            file_path=file_path,
            sha256_path=file_path.with_name(file_path.name + ".sha256"),
            expected_sha256="a" * 64,
            actual_sha256="a" * 64,
            failure_reason="",
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
    ) -> UpdateCheckResult:
        assert current_version == "0.3.1"
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
        return UpdateDownloadResult(
            status="verified",
            downloaded=True,
            verified=True,
            file_path=file_path,
            sha256_path=file_path.with_name(file_path.name + ".sha256"),
            expected_sha256="a" * 64,
            actual_sha256="a" * 64,
            failure_reason="",
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


def test_settings_macos_download_and_apply_update_creates_handoff(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """macOS frozen onedir 可走 settings 下載、handoff、啟動 updater 流程。"""

    paths = make_supported_macos_update_paths(tmp_path)
    monkeypatch.setenv("FACEBOOK_MONITOR_PACKAGING_MODE", "pyinstaller-macos-arm64-onedir")
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_windows", lambda: False)
    monkeypatch.setattr("facebook_monitor.webapp.routes.settings._is_macos", lambda: True)
    checked_update: dict[str, object] = {}

    async def fake_check_github_release_updates(
        *,
        current_version: str,
        channel: str = "stable",
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
        return UpdateDownloadResult(
            status="verified",
            downloaded=True,
            verified=True,
            file_path=file_path,
            sha256_path=file_path.with_name(file_path.name + ".sha256"),
            expected_sha256="a" * 64,
            actual_sha256="a" * 64,
            failure_reason="",
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


def test_settings_apply_update_rejects_source_mode(tmp_path: Path) -> None:
    """source mode 不可啟動 updater 套用流程。"""

    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    response = client.post("/settings/updates/apply", follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]


def test_settings_page_updates_target_keyword_defaults(tmp_path: Path) -> None:
    """設定頁可保存新增 target 使用的排除字預設值。"""

    db_path = tmp_path / "app.db"
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    initial_response = client.get("/settings")
    save_response = client.post(
        "/settings/target-keywords",
        data={
            "exclude_keywords": "售完,暫停",
            "exclude_ignore_phrases": "全收;回收",
        },
        follow_redirects=False,
    )
    settings_response = client.get("/settings")
    new_target_response = client.get("/targets/new")

    assert initial_response.status_code == 200
    assert "關鍵字預設值" in initial_response.text
    assert "徵;收;已售" in initial_response.text
    assert "全收;回收" in initial_response.text
    assert save_response.status_code == 303
    assert "message=" in save_response.headers["location"]
    assert "售完,暫停" in settings_response.text
    assert "全收;回收" in settings_response.text
    assert 'name="exclude_keywords" type="hidden" value="售完,暫停"' in new_target_response.text
    assert (
        'name="exclude_ignore_phrases" type="hidden" value="全收;回收"'
        in new_target_response.text
    )
    with SqliteApplicationContext(db_path) as app_context:
        defaults = app_context.repositories.app_settings.get_target_keyword_defaults()
    assert defaults.exclude_keywords_text == "售完,暫停"
    assert defaults.exclude_ignore_phrases_text == "全收;回收"


def test_create_target_route_uses_saved_keyword_defaults_when_fields_are_omitted(
    tmp_path: Path,
) -> None:
    """新增 target 表單沒有送關鍵字欄位時，route 會讀 DB 預設值。"""

    db_path = tmp_path / "app.db"
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=lambda _profile_dir, _url: "測試社團",
        )
    )
    client.post(
        "/settings/target-keywords",
        data={
            "exclude_keywords": "售完,暫停",
            "exclude_ignore_phrases": "全收,回收",
        },
        follow_redirects=False,
    )

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "include_keywords": "票",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
            "auto_load_more": "on",
            "auto_adjust_sort": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
        assert target is not None
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert config.exclude_keywords == ("售完", "暫停")
    assert config.exclude_ignore_phrases == ("全收", "回收")


def test_create_target_route_stores_and_renders_group_cover_thumbnail(tmp_path: Path) -> None:
    """新增 target 時 metadata resolver 會一併保存並顯示社團封面縮圖。"""

    db_path = tmp_path / "app.db"
    cover_url = "https://scontent.example.test/group-cover.jpg"
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=lambda _profile_dir, _url: GroupMetadata(
                group_name="測試社團",
                group_cover_image_url=cover_url,
            ),
        )
    )

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )
    index_response = client.get("/")

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
    assert target is not None
    assert target.group_cover_image_url == cover_url
    assert index_response.status_code == 200
    assert f'<img src="{cover_url}" alt=""' in index_response.text


def test_theme_preference_is_stored_in_database_for_all_pages(tmp_path: Path) -> None:
    """主題偏好必須存進 app DB，避免 auto-port 或瀏覽器狀態遺失。"""

    db_path = tmp_path / "app.db"
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    initial_response = client.get("/")
    save_response = client.post("/settings/theme", json={"theme": "dark"})
    index_response = client.get("/")
    settings_response = client.get("/settings")
    new_target_response = client.get("/targets/new")

    assert initial_response.status_code == 200
    assert 'let theme = "dark";' in initial_response.text
    assert save_response.status_code == 200
    assert save_response.json() == {"theme": "dark"}
    assert 'let theme = "dark";' in index_response.text
    assert 'let theme = "dark";' in settings_response.text
    assert 'let theme = "dark";' in new_target_response.text
    with SqliteApplicationContext(db_path) as app_context:
        assert app_context.repositories.app_settings.get_theme() == "dark"


def test_theme_preference_rejects_unknown_value(tmp_path: Path) -> None:
    """theme API 不接受未定義值。"""

    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    response = client.post("/settings/theme", json={"theme": "system"})

    assert response.status_code == 400


def test_index_and_partial_payload_show_profile_needs_login_warning(
    tmp_path: Path,
) -> None:
    """Facebook session 失效時，首頁與 partial update 都帶右上角警告。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        status = app_context.repositories.app_settings.mark_profile_needs_login(
            reason="login_required",
            source="resident_main",
        )
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    index_response = client.get("/")
    cards_response = client.get("/api/dashboard-cards")

    assert status.state == ProfileSessionState.NEEDS_LOGIN
    assert index_response.status_code == 200
    assert "Facebook 需要重新登入" in index_response.text
    payload = cards_response.json()
    warning = payload["profile_session_warning"]
    assert warning["needs_login"] is True
    assert warning["reason"] == "login_required"
    assert "重新開啟程式" in warning["message"]


def test_dashboard_import_map_versions_sidebar_module(tmp_path: Path) -> None:
    """Dashboard module graph 也要版本化，避免 Chrome 沿用舊 sidebar.js。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.get("/")

    assert response.status_code == 200
    assert '<script type="importmap">' in response.text
    assert '"/static/dashboard/sidebar.js"' in response.text
    assert f'"/static/dashboard/sidebar.js?v={ASSET_VERSION}"' in response.text


def test_target_card_panels_share_preview_height_contract() -> None:
    """Target card 左右 panel 必須共用高度約束，避免底部錯位回歸。"""

    styles = Path("src/facebook_monitor/webapp/static/styles/target-card.css").read_text(
        encoding="utf-8"
    )

    assert "grid-auto-rows: var(--preview-panel-height);" in styles
    assert ".target-settings" in styles
    assert ".match-panel" in styles
    assert ".section-title .form-status" in styles
    assert "overflow-y: auto;" in styles
    assert ".compact-config-form .keyword-rule-tabs" in styles
    assert ".keyword-field-header" in styles
    assert ".keyword-rule-tab-row" in styles
    assert ".compact-config-form .keyword-rule-tab" in styles
    assert ".keyword-help-button" in styles
    assert ".more-menu-trigger" in styles
    more_trigger_rule = styles.split(".more-menu-trigger {", 1)[1].split("}", 1)[0]
    assert "color: var(--text-soft);" in more_trigger_rule
    assert "list-style: none;" in more_trigger_rule
    for duplicated_button_property in ("border-radius:", "min-height:", "min-width:", "padding:"):
        assert duplicated_button_property not in more_trigger_rule
    assert ".menu-panel form" in styles
    assert ".menu-action" in styles
    assert ".compact-config-form .keyword-rule-panel[hidden]" in styles
    assert styles.count("height: var(--preview-panel-height);") >= 2
    assert styles.count("max-height: var(--preview-panel-height);") >= 2


def test_settings_keyword_defaults_use_compact_two_column_layout() -> None:
    """設定頁關鍵字預設值維持左右雙欄，textarea 不提供拖曳縮放。"""

    forms_css = Path("src/facebook_monitor/webapp/static/styles/forms.css").read_text(
        encoding="utf-8"
    )
    pages_css = Path("src/facebook_monitor/webapp/static/styles/pages.css").read_text(
        encoding="utf-8"
    )
    diagnostics_css = Path("src/facebook_monitor/webapp/static/styles/diagnostics.css").read_text(
        encoding="utf-8"
    )

    assert "textarea {\n  resize: none;\n}" in forms_css
    assert ".settings-form-grid--two {\n  grid-template-columns: repeat(2, minmax(0, 1fr));\n}" in forms_css
    assert ".settings-actions--right" in forms_css
    assert ".settings-actions--left" in forms_css
    assert "  .settings-form-grid--two {\n    grid-template-columns: 1fr;\n  }" in pages_css
    assert ".debug-copy-source {\n  min-height: 150px;\n  resize: none;" in diagnostics_css


def test_hit_records_modal_matches_preview_typography_and_link_style() -> None:
    """查看紀錄 modal 字級與連結樣式需對齊最近掃描 / 命中紀錄 preview。"""

    modal_styles = Path("src/facebook_monitor/webapp/static/styles/modals.css").read_text(
        encoding="utf-8"
    )
    hit_records_js = Path("src/facebook_monitor/webapp/static/dashboard/hit_records.js").read_text(
        encoding="utf-8"
    )

    assert ".hit-record-summary-list" in modal_styles
    assert ".hit-record-summary-item dt::after" in modal_styles
    assert 'content: "：";' in modal_styles
    assert "grid-template-columns: 5em minmax(0, 1fr);" in modal_styles
    assert "item.className = \"hit-record-summary-item\";" in hit_records_js
    assert "fields.className = \"hit-record-fields hit-record-summary-list\";" in hit_records_js
    assert modal_styles.count("font-size: 14px;") >= 4
    assert ".hit-record-row a" in modal_styles
    assert "border-radius: 999px;" in modal_styles
    assert "font-weight: 650;" in modal_styles
    assert "missing.className = \"missing-link\";" in hit_records_js


def test_index_renders_runtime_state_and_error(tmp_path: Path) -> None:
    """首頁會顯示 scheduler runtime state 與 last error。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        running_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="掃描測試社團",
            )
        )
        error_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
                group_name="錯誤社團",
            )
        )
        stopped_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="333",
                canonical_url="https://www.facebook.com/groups/333",
                group_name="停止社團",
            )
        )
        idle_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="444",
                canonical_url="https://www.facebook.com/groups/444",
                group_name="啟用等待社團",
            )
        )
        app_context.services.targets.restart_target_monitoring(running_target.id)
        app_context.services.targets.mark_target_running(running_target.id, "worker-1")
        app_context.services.targets.restart_target_monitoring(error_target.id)
        app_context.services.targets.mark_target_error(error_target.id, "login_required: 需要登入")
        app_context.services.targets.pause_target_monitoring(stopped_target.id)
        app_context.services.targets.restart_target_monitoring(idle_target.id)

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "已啟用" in response.text
    assert "掃描中" in response.text
    assert "錯誤" in response.text
    assert "login_required: 需要登入" in response.text
    assert "已停止" in response.text
    assert "閒置" not in response.text
    assert "執行中" not in response.text


def test_index_does_not_render_queue_position_runtime_note(tmp_path: Path) -> None:
    """排隊資訊不應以會推動 card 高度的 queue_position raw note 顯示。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="排隊測試社團",
            )
        )
        app_context.services.targets.restart_target_monitoring(target.id)
        app_context.services.targets.mark_target_queued(target.id, "due")

    scheduler_manager = FakeSchedulerManager()
    scheduler_manager.running = True
    scheduler_manager.queued_target_ids = (target.id,)
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.get("/")

    assert response.status_code == 200
    assert "排隊測試社團" in response.text
    assert "排隊中" in response.text
    assert "queue_position=" not in response.text


def test_hit_record_api_lists_counts_and_clears_only_target_history(tmp_path: Path) -> None:
    """查看紀錄 API 可查詢與清空單一 target，且不清其他 runtime/debug 資料。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="第一個社團",
            )
        )
        second_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
                group_name="第二個社團",
            )
        )
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=first_target.id,
                group_id=first_target.group_id,
                group_name="第一個社團",
                item_kind=ItemKind.POST,
                item_key="first-1",
                author="王小明",
                text="這是一筆有票券關鍵字的命中紀錄",
                permalink="https://www.facebook.com/groups/111/posts/1",
                include_rule="票券",
            )
        )
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=first_target.id,
                group_id=first_target.group_id,
                group_name="第一個社團",
                item_kind=ItemKind.COMMENT,
                item_key="first-2",
                author="陳小華",
                text="留言也有票券關鍵字",
                permalink="https://www.facebook.com/groups/111/posts/1?comment_id=2",
                include_rule="票券",
            )
        )
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=second_target.id,
                group_id=second_target.group_id,
                item_kind=ItemKind.POST,
                item_key="second-1",
                text="另一個 target 的命中紀錄",
                include_rule="票券",
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            first_target.id,
            [
                LatestScanItem(
                    target_id=first_target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="latest-1",
                    item_index=0,
                    text="最近掃描仍應保留",
                    matched_keyword="票券",
                    debug_metadata={"textSource": "primary"},
                )
            ],
        )
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=first_target.id,
                status=ScanStatus.SUCCESS,
                item_count=1,
                matched_count=1,
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=first_target.scope_id,
                item_key="seen-1",
                item_kind=ItemKind.POST,
            )
        )
        app_context.repositories.notification_events.add(
            NotificationEvent(
                target_id=first_target.id,
                item_key="first-1",
                channel=NotificationChannel.NTFY,
                status=NotificationStatus.SENT,
                message="sent",
            )
        )
        app_context.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{first_target.id}:first-1:ntfy",
                target_id=first_target.id,
                item_key="first-1",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )

    app = create_app(db_path=db_path, profile_dir=tmp_path / "profile")
    with SqliteApplicationContext(db_path) as app_context:
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=first_target.id,
                group_id=first_target.group_id,
                group_name="第一個社團",
                item_kind=ItemKind.POST,
                item_key="first-current",
                author="林本次",
                text="本次啟動期間的票券命中",
                permalink="https://www.facebook.com/groups/111/posts/current",
                include_rule="票券",
                notified_at=app.state.session_started_at + timedelta(seconds=1),
                created_at=app.state.session_started_at + timedelta(seconds=1),
            )
        )
    client = TestClient(app)
    preview_response = client.get(f"/api/targets/{first_target.id}/hit-records/preview")
    count_response = client.get(f"/api/targets/{first_target.id}/hit-records/count")
    full_response = client.get(
        f"/api/targets/{first_target.id}/hit-records",
        params={"limit": 1, "offset": 1},
    )
    sidebar_response = client.get("/api/sidebar")
    card_response = client.get(f"/api/targets/{first_target.id}/card")
    revision_before_clear = client.get("/api/dashboard-revision").json()["revision"]
    clear_response = client.delete(f"/api/targets/{first_target.id}/hit-records")
    revision_after_clear = client.get("/api/dashboard-revision").json()["revision"]

    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert preview_payload["total_count"] == 1
    assert preview_payload["items"][0]["author_name"] == "林本次"
    assert preview_payload["items"][0]["badge_text"] == "命中: 票券"
    assert preview_payload["items"][0]["content_segments"] == [
        {"text": "本次啟動期間的", "highlighted": False},
        {"text": "票券", "highlighted": True},
        {"text": "命中", "highlighted": False},
    ]
    assert count_response.json() == {"target_id": first_target.id, "total_count": 1}
    full_payload = full_response.json()
    assert full_payload["total_count"] == 3
    assert full_payload["items"][0]["sequence_number"] == 2
    assert full_payload["items"][0]["item_type"] == "留言"
    assert full_payload["items"][0]["notified_at"]
    assert "notification_summary" in full_payload["items"][0]
    assert {"text": "票券", "highlighted": True} in full_payload["items"][0]["content_segments"]
    hit_records_js = Path("src/facebook_monitor/webapp/static/dashboard/hit_records.js").read_text(
        encoding="utf-8"
    )
    assert "通知狀態" not in hit_records_js
    assert sidebar_response.status_code == 200
    sidebar_payload = sidebar_response.json()
    assert sidebar_payload["items"][0]["target_id"] == first_target.id
    assert sidebar_payload["items"][0]["hit_count"] == 1
    assert card_response.status_code == 200
    card_payload = card_response.json()
    assert card_payload["target_id"] == first_target.id
    assert card_payload["hit_record_total_count"] == 1
    assert "target-collapsed-summary-field" in card_payload["card_summary_html"]
    assert "關鍵字" in card_payload["card_summary_html"]
    assert "命中 1 筆" in card_payload["card_summary_html"]
    assert "preview-list" in card_payload["latest_scan_preview_html"]
    assert "preview-list" in card_payload["hit_record_preview_html"]
    assert "latest_scan_items:" in card_payload["latest_scan_diagnostics_text"]
    assert "textSource=primary" in card_payload["latest_scan_diagnostics_text"]
    assert "命中: 票券" in card_payload["hit_record_preview_html"]
    assert '<mark class="keyword-highlight">票券</mark>' in card_payload["hit_record_preview_html"]
    assert "開啟連結" in card_payload["hit_record_preview_html"]
    assert "latest_scan_preview_rows" not in card_payload
    assert "hit_record_preview_rows" not in card_payload
    assert "card_summary" not in card_payload
    assert clear_response.status_code == 200
    assert clear_response.json() == {
        "target_id": first_target.id,
        "deleted_count": 3,
        "total_count": 0,
    }
    assert revision_before_clear != revision_after_clear
    with SqliteApplicationContext(db_path) as app_context:
        assert app_context.repositories.match_history.count_by_target(first_target.id) == 0
        assert app_context.repositories.match_history.count_by_target(second_target.id) == 1
        assert app_context.repositories.latest_scan_items.list_by_target(first_target.id)
        assert app_context.repositories.scan_runs.latest_by_target(first_target.id) is not None
        assert app_context.repositories.seen_items.has_seen(first_target.scope_id, "seen-1")
        assert app_context.repositories.notification_events.list_by_target(first_target.id)
        assert (
            app_context.repositories.notification_outbox.get_by_idempotency_key(
                f"{first_target.id}:first-1:ntfy"
            )
            is not None
        )


def test_dashboard_card_payload_labels_content_unavailable_failure(
    tmp_path: Path,
) -> None:
    """dashboard card partial payload 會保留連結失效警示，避免刷新後退回泛用錯誤。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="測試社團",
            )
        )
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.FAILED,
                error_message=(
                    "content_unavailable: Facebook content is unavailable or no "
                    "longer visible."
                ),
                metadata={
                    "reason": CONTENT_UNAVAILABLE_REASON,
                    "worker": "resident_main",
                    "target_kind": "posts",
                    "retryable": False,
                },
            )
        )
        app_context.services.targets.restart_target_monitoring(target.id)
        app_context.services.targets.mark_target_error(
            target.id,
            "content_unavailable: Facebook content is unavailable or no longer visible.",
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/api/dashboard-cards")

    assert response.status_code == 200
    card_payload = response.json()["cards"][0]
    assert card_payload["has_latest_failed_scan"] is True
    assert card_payload["latest_error_indicator_label"] == "連結已失效"
    assert card_payload["latest_error_indicator_kind"] == "content-unavailable"
    assert card_payload["status_label"] == "錯誤"
    assert (
        card_payload["runtime_error"]
        == "連結已失效：Facebook 顯示目前無法查看此內容，可能已刪除或權限變更。"
    )
    assert card_payload["next_refresh_label"] == "下次刷新：未排程"
    assert "Facebook 顯示目前無法查看此內容" in card_payload["latest_error_indicator_title"]
    assert "status=failed · reason=連結已失效" in card_payload[
        "latest_scan_diagnostics_summary"
    ]
    assert "failure_reason=連結已失效" in card_payload["latest_scan_diagnostics_text"]
    assert "連結已失效" in card_payload["card_summary_html"]


def test_dashboard_card_payload_does_not_keep_content_unavailable_after_success(
    tmp_path: Path,
) -> None:
    """連結失效後若已有更新成功掃描，不應繼續顯示目前連結已失效。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="測試社團",
            )
        )
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.FAILED,
                error_message=(
                    "content_unavailable: Facebook content is unavailable or no "
                    "longer visible."
                ),
                metadata={
                    "reason": CONTENT_UNAVAILABLE_REASON,
                    "worker": "resident_main",
                    "target_kind": "posts",
                    "retryable": False,
                },
            )
        )
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
                item_count=1,
                matched_count=0,
                metadata={
                    "worker": "posts_scan",
                    "collection_strategy": "feed_visible_window",
                    "candidate_count": 1,
                    "round_count": 1,
                    "stop_reason": "target_count_reached",
                },
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/api/dashboard-cards")

    assert response.status_code == 200
    card_payload = response.json()["cards"][0]
    assert card_payload["has_latest_failed_scan"] is True
    assert card_payload["latest_error_indicator_label"] == "最近有錯誤"
    assert card_payload["latest_error_indicator_kind"] == "error"
    assert "曾偵測到連結失效" in card_payload["card_summary_html"]
    assert "status=failed · reason=連結已失效" not in card_payload[
        "latest_scan_diagnostics_summary"
    ]


def test_hit_record_preview_splits_multiple_matched_keyword_badges(tmp_path: Path) -> None:
    """命中紀錄 preview 會把多組命中 keyword 拆成多個 badge 並全部高亮。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
    app = create_app(db_path=db_path, profile_dir=tmp_path / "profile")
    with SqliteApplicationContext(db_path) as app_context:
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=target.id,
                group_id=target.group_id,
                group_name=target.group_name,
                item_kind=ItemKind.POST,
                item_key="multi-keyword",
                author="王小明",
                text="售6/5,6/6的票各一張",
                include_rule="6/5;6/6",
                notified_at=app.state.session_started_at + timedelta(seconds=1),
                created_at=app.state.session_started_at + timedelta(seconds=1),
            )
        )

    client = TestClient(app)
    response = client.get(f"/api/targets/{target.id}/hit-records/preview")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["badge_text"] == "命中: 6/5;6/6"
    assert item["badge_labels"] == ["命中: 6/5", "命中: 6/6"]
    assert {"text": "6/5", "highlighted": True} in item["content_segments"]
    assert {"text": "6/6", "highlighted": True} in item["content_segments"]


def test_webui_startup_keeps_full_history_but_resets_hit_preview(tmp_path: Path) -> None:
    """Web UI 重啟保留查看紀錄，但卡片命中 preview 只顯示本次 session。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="第一個社團",
            )
        )
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=target.id,
                group_id=target.group_id,
                group_name="第一個社團",
                item_kind=ItemKind.POST,
                item_key="persisted-1",
                text="重啟後仍保留的查看紀錄",
                include_rule="票券",
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="persisted-1",
                    item_index=0,
                )
            ],
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="persisted-1",
                item_kind=ItemKind.POST,
            )
        )
        app_context.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:persisted-1:ntfy",
                target_id=target.id,
                item_key="persisted-1",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )

    app = create_app(
        db_path=db_path,
        profile_dir=tmp_path / "profile",
        reset_runtime_data_on_startup=True,
    )
    with TestClient(app) as client:
        preview_payload = client.get(f"/api/targets/{target.id}/hit-records/preview").json()
        full_payload = client.get(f"/api/targets/{target.id}/hit-records").json()

    assert preview_payload["total_count"] == 0
    assert full_payload["total_count"] == 1
    with SqliteApplicationContext(db_path) as app_context:
        assert app_context.repositories.match_history.count_by_target(target.id) == 1
        assert not app_context.repositories.latest_scan_items.list_by_target(target.id)
        assert not app_context.repositories.seen_items.has_seen(target.scope_id, "persisted-1")
        assert (
            app_context.repositories.notification_outbox.get_by_idempotency_key(
                f"{target.id}:persisted-1:ntfy"
            )
            is not None
        )


def test_dashboard_view_model_includes_sidebar_preview_and_settings_summary(
    tmp_path: Path,
) -> None:
    """dashboard read model 會帶入 sidebar、hit preview 與設定摘要。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="測試社團",
                config=TargetConfigPatch(
                    fixed_refresh_sec=None,
                    min_refresh_sec=25,
                    max_refresh_sec=35,
                    jitter_enabled=True,
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="latest-1",
                    item_index=0,
                    author="王小明",
                    text="最近掃描內容",
                    matched_keyword="票券",
                ),
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="latest-2",
                    item_index=1,
                    author="林小美",
                    text="較新的最近掃描內容",
                    matched_keyword="票券",
                ),
            ],
        )
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=target.id,
                group_id=target.group_id,
                item_kind=ItemKind.POST,
                item_key="history-1",
                author="陳小華",
                text="歷史命中內容",
                include_rule="票券",
            )
        )

    dashboard = get_dashboard_view(db_path)
    row = dashboard.rows[0]
    latest_preview = row.latest_scan_preview_rows[0]
    hit_preview = row.hit_record_preview_rows[0]

    assert dashboard.sidebar_items[0].display_name == "測試社團"
    assert dashboard.sidebar_items[0].mode_label == "貼文"
    assert dashboard.sidebar_items[0].mode_class == "posts"
    assert dashboard.sidebar_items[0].hit_count == 1
    assert "命中 1 筆" in dashboard.sidebar_items[0].status_summary
    assert row.hit_record_total_count == 1
    assert row.hit_records_heading == "命中紀錄（1）"
    assert row.settings_summary.lines[0].icon_key == "refresh"
    assert row.settings_summary.lines[0].label == "刷新"
    assert row.settings_summary.lines[0].value == "浮動 25-35 秒"
    assert row.settings_summary.lines[-1].icon_key == "notification"
    assert row.settings_summary.lines[-1].label == "通知"
    assert row.settings_summary.lines[-1].value == "ntfy"
    assert latest_preview.author_name == "王小明"
    assert latest_preview.badge_kind == "hit"
    assert latest_preview.link_label == "開啟連結"
    assert not latest_preview.has_debug
    assert hit_preview.author_name == "陳小華"
    assert hit_preview.badge_text == "命中: 票券"
    assert hit_preview.link_label == "開啟連結"
    assert not hit_preview.has_debug

    response = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile")).get("/")
    assert response.status_code == 200
    assert "最近掃描" in response.text
    assert "命中紀錄 0" in response.text
    assert "最近掃描內容" in response.text


def test_index_renders_scan_guard_skip_reason(tmp_path: Path) -> None:
    """首頁會顯示同 target 重入被 guard 擋下的原因。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="重入測試社團",
            )
        )
        app_context.services.targets.mark_target_running(target.id, "worker-a")
        locked_state = app_context.services.targets.try_mark_target_running(
            target.id,
            "worker-b",
        )

    assert locked_state is None

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "重入測試社團" in response.text
    assert "scan_guard_skipped: target_already_running" in response.text
    assert "active_worker_id=worker-a" in response.text


def test_update_config_route_updates_target_config(tmp_path: Path) -> None:
    """設定表單送出後會更新 target config。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        f"/targets/{target.id}/config",
        data={
            "include_keywords": "票,交換",
            "exclude_keywords": "售完",
            "exclude_ignore_phrases": "全收,回收",
            "refresh_mode": "fixed",
            "fixed_refresh_sec": "90",
            "max_items_per_scan": "30",
            "auto_adjust_sort": "on",
            "enable_desktop_notification": "on",
            "enable_ntfy": "on",
            "ntfy_topic": "phase0test",
            "enable_discord_notification": "on",
            "discord_webhook": "https://discord.com/api/webhooks/example",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert config.include_keywords == ("票", "交換")
    assert config.exclude_keywords == ("售完",)
    assert config.exclude_ignore_phrases == ("全收", "回收")
    assert config.fixed_refresh_sec == 90
    assert config.max_items_per_scan == 10
    assert not config.auto_load_more
    assert config.auto_adjust_sort
    assert config.enable_desktop_notification
    assert config.enable_ntfy
    assert config.ntfy_topic == "phase0test"
    assert config.enable_discord_notification
    assert config.discord_webhook == "https://discord.com/api/webhooks/example"


def test_update_config_route_supports_fixed_and_floating_refresh_modes(
    tmp_path: Path,
) -> None:
    """Web UI 設定表單可保存固定與浮動刷新模式。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    floating_response = client.post(
        f"/targets/{target.id}/config",
        data={
            "refresh_mode": "floating",
            "fixed_refresh_sec": "90",
            "min_refresh_sec": "25",
            "max_refresh_sec": "35",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )
    index_response = client.get("/")
    with SqliteApplicationContext(db_path) as app_context:
        floating_config = app_context.repositories.configs.get_for_target(target)
    fixed_response = client.post(
        f"/targets/{target.id}/config",
        data={
            "refresh_mode": "fixed",
            "fixed_refresh_sec": "120",
            "min_refresh_sec": "20",
            "max_refresh_sec": "40",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert floating_response.status_code == 303
    assert "浮動 25-35 秒" in index_response.text
    assert floating_config is not None
    assert floating_config.fixed_refresh_sec is None
    assert floating_config.jitter_enabled
    assert floating_config.min_refresh_sec == 25
    assert floating_config.max_refresh_sec == 35
    assert fixed_response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        fixed_config = app_context.repositories.configs.get_for_target(target)
    assert fixed_config is not None
    assert fixed_config.fixed_refresh_sec == 120
    assert not fixed_config.jitter_enabled
    assert fixed_config.min_refresh_sec == 20
    assert fixed_config.max_refresh_sec == 40


def test_update_config_route_rejects_invalid_floating_refresh_range(
    tmp_path: Path,
) -> None:
    """浮動刷新最小秒數大於最大秒數時，Web UI 會拒絕保存。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        f"/targets/{target.id}/config",
        data={
            "refresh_mode": "floating",
            "fixed_refresh_sec": "60",
            "min_refresh_sec": "35",
            "max_refresh_sec": "25",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    with SqliteApplicationContext(db_path) as app_context:
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert config.fixed_refresh_sec is None
    assert config.jitter_enabled


def test_create_target_route_adds_group_posts_target(tmp_path: Path) -> None:
    """Web UI 會依 Facebook group URL 自動建立 posts target 並補社團名稱。"""

    db_path = tmp_path / "app.db"
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=lambda _profile_dir, _url: "測試社團",
        )
    )

    form_response = client.get("/targets/new")
    create_response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "include_keywords": "票",
            "exclude_keywords": "售完",
            "fixed_refresh_sec": "75",
            "max_items_per_scan": "25",
            "auto_load_more": "on",
            "auto_adjust_sort": "on",
            "enable_desktop_notification": "on",
            "enable_ntfy": "on",
            "ntfy_topic": "phase0test",
            "enable_discord_notification": "on",
            "discord_webhook": "https://discord.com/api/webhooks/example",
        },
        follow_redirects=False,
    )

    assert form_response.status_code == 200
    assert "Facebook group URL" in form_response.text
    assert (
        "https://www.facebook.com/groups/123456789 或 "
        "https://www.facebook.com/groups/123456789/posts/987654321"
    ) in form_response.text
    assert "自訂顯示名稱" in form_response.text
    assert "可留空，系統會嘗試使用社團名稱" in form_response.text
    assert "data-new-target-form" in form_response.text
    assert 'data-loading-text="建立中..."' in form_response.text
    assert "data-secret-input" not in form_response.text
    assert 'name="ntfy_topic" type="text"' in form_response.text
    assert 'name="discord_webhook" type="text"' in form_response.text
    assert (
        form_response.text.index('name="refresh_mode" type="radio" value="floating"')
        < form_response.text.index('name="refresh_mode" type="radio" value="fixed"')
    )
    assert re.search(
        r'name="refresh_mode" type="radio" value="floating"[^>]*checked',
        form_response.text,
    )
    assert f'value="{PYTHON_TARGET_CONFIG_DEFAULTS.default_fixed_refresh_sec}"' in (
        form_response.text
    )
    assert f'value="{PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan}"' in form_response.text
    assert 'name="auto_adjust_sort" type="hidden" value="on"' in form_response.text
    assert "Target kind" not in form_response.text
    assert create_response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
        assert target is not None
        config = app_context.repositories.configs.get_for_target(target)
    assert target.group_name == "測試社團"
    assert target.name == "測試社團"
    assert config is not None
    assert config.include_keywords == ("票",)
    assert config.exclude_keywords == ("售完",)
    assert config.exclude_ignore_phrases == ("全收", "回收")
    assert config.fixed_refresh_sec is None
    assert config.jitter_enabled
    assert config.min_refresh_sec == PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec
    assert config.max_refresh_sec == PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec
    assert config.max_items_per_scan == 10
    assert config.auto_load_more
    assert config.auto_adjust_sort
    assert config.enable_desktop_notification
    assert config.enable_ntfy
    assert config.ntfy_topic == "phase0test"
    assert config.enable_discord_notification
    assert config.discord_webhook == "https://discord.com/api/webhooks/example"


def test_create_target_route_preserves_form_body_after_csrf_validation(
    tmp_path: Path,
) -> None:
    """production CSRF middleware 讀 token 後，route 仍要讀得到 group_url。"""

    db_path = tmp_path / "app.db"
    client = TestClient(
        create_production_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            csrf_token="known-token",
            group_name_resolver=lambda _profile_dir, _url: "測試社團",
        )
    )

    response = client.post(
        "/targets",
        data={
            "csrf_token": "known-token",
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "display_name": "測試 target",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
            "auto_load_more": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" not in response.headers["location"]
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
    assert target is not None


def test_create_target_route_uses_custom_display_name_without_resolver(tmp_path: Path) -> None:
    """有填自訂顯示名稱時不需要自動解析 Facebook title。"""

    db_path = tmp_path / "app.db"

    def failing_resolver(_profile_dir: Path, _url: str) -> str:
        raise AssertionError("resolver should not be called")

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=failing_resolver,
        )
    )

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "display_name": "我的票券社團",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "20",
            "auto_load_more": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
    assert target is not None
    assert target.name == "我的票券社團"
    assert target.group_name == ""
    with SqliteApplicationContext(db_path) as app_context:
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert config.exclude_keywords == PYTHON_TARGET_CONFIG_DEFAULTS.exclude_keywords


def test_index_renders_target_rename_modal(tmp_path: Path) -> None:
    """target card 更多選單提供更改名稱 dialog，且輸入框預填目前名稱。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="我的票券社團",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "data-rename-target-button" in response.text
    assert "更改 target 名稱" in response.text
    assert 'name="display_name" type="text" value="我的票券社團"' in response.text


def test_index_hides_generated_fallback_name_until_metadata_refresh(tmp_path: Path) -> None:
    """metadata 尚未回填時，UI 顯示待抓取文案而不是系統 fallback id。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="204808657039646",
                parent_post_id="2155501991970293",
                canonical_url=(
                    "https://www.facebook.com/groups/204808657039646/posts/"
                    "2155501991970293"
                ),
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "抓取社團名稱中，請稍後" in response.text
    assert 'name="display_name" type="text" value=""' in response.text
    assert "group:204808657039646:post:2155501991970293:comments" not in response.text


def test_metadata_refresh_updates_rename_modal_display_name(tmp_path: Path) -> None:
    """metadata refresh 補名後，read model 同步提供卡片標題與更名 modal 預填值。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="204808657039646",
                parent_post_id="2155501991970293",
                canonical_url=(
                    "https://www.facebook.com/groups/204808657039646/posts/"
                    "2155501991970293"
                ),
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    pending_payload = client.get(f"/api/targets/{target.id}/card").json()
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.refresh_target_group_name(target.id, "票券測試社團")
    refreshed_payload = client.get(f"/api/targets/{target.id}/card").json()
    index_response = client.get("/")

    assert pending_payload["display_name"] == "抓取社團名稱中，請稍後"
    assert pending_payload["rename_display_name"] == ""
    assert refreshed_payload["display_name"] == "票券測試社團 / post:2155501991970293"
    assert refreshed_payload["rename_display_name"] == "票券測試社團 / post:2155501991970293"
    assert index_response.status_code == 200
    assert (
        'name="display_name" type="text" '
        'value="票券測試社團 / post:2155501991970293"'
    ) in index_response.text
    assert "group:204808657039646:post:2155501991970293:comments" not in index_response.text


def test_index_shows_metadata_failed_name_fallback(tmp_path: Path) -> None:
    """metadata 補名失敗時，UI 顯示手動改名提示並避免回填系統 fallback。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="204808657039646",
                parent_post_id="2155501991970293",
                canonical_url=(
                    "https://www.facebook.com/groups/204808657039646/posts/"
                    "2155501991970293"
                ),
            )
        )
        app_context.services.targets.mark_target_metadata_refresh_failed(
            target.id,
            "Facebook 尚未登入",
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "無法自動抓取名稱，請手動更改名稱" in response.text
    assert 'name="display_name" type="text" value=""' in response.text
    assert "group:204808657039646:post:2155501991970293:comments" not in response.text


def test_update_target_name_route_updates_display_name(tmp_path: Path) -> None:
    """更改名稱 route 會更新 target.name 並回到原 target card。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="原本名稱",
                group_name="測試社團",
            )
        )
        app_context.services.targets.mark_target_metadata_refresh_failed(
            target.id,
            "測試失敗",
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        f"/targets/{target.id}/name",
        data={
            "display_name": "(1) 新卡片名稱 | Facebook",
            "return_to": f"#target-{target.id}",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"#target-{target.id}")
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
    assert loaded is not None
    assert loaded.name == "新卡片名稱"
    assert loaded.group_name == "測試社團"
    assert loaded.metadata_status == TargetMetadataStatus.RESOLVED
    assert loaded.metadata_error == ""


def test_create_target_route_skips_name_resolver_when_scheduler_running(tmp_path: Path) -> None:
    """scheduler 正在跑時，新增 target 不應為了解析名稱而停止 scheduler。"""

    db_path = tmp_path / "app.db"
    resolver_calls: list[str] = []

    def failing_resolver(_profile_dir: Path, url: str) -> str:
        resolver_calls.append(url)
        raise AssertionError("resolver should not run while scheduler is running")

    scheduler_manager = BackgroundSchedulerManager(
        resident_main_runner=lambda _options, stop_event, _on_cycle, _sleep_fn=None: (
            stop_event.wait(timeout=2)
        )
    )
    scheduler_manager.start(
        SchedulerSessionOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
        )
    )
    try:
        client = TestClient(
            create_app(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                group_name_resolver=failing_resolver,
                scheduler_manager=scheduler_manager,
            )
        )

        response = client.post(
            "/targets",
            data={
                "group_url": "https://www.facebook.com/groups/222518561920110/",
                "fixed_refresh_sec": "60",
                "max_items_per_scan": "20",
                "auto_load_more": "on",
            },
            follow_redirects=False,
        )
    finally:
        scheduler_manager.stop(timeout_seconds=2)

    assert response.status_code == 303
    assert resolver_calls == []
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
    assert target is not None
    assert target.group_name == ""
    assert target.name == "group:222518561920110:posts"
    assert target.metadata_status == TargetMetadataStatus.PENDING
    assert target.metadata_error == ""
    assert scheduler_manager.take_metadata_refresh_requests() == (target.id,)


def test_create_permalink_comments_target_while_scheduler_running(tmp_path: Path) -> None:
    """scheduler 執行中仍可用 group permalink URL 建立 comments target。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = BackgroundSchedulerManager(
        resident_main_runner=lambda _options, stop_event, _on_cycle, _sleep_fn=None: (
            stop_event.wait(timeout=2)
        )
    )
    scheduler_manager.start(
        SchedulerSessionOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
        )
    )
    try:
        client = TestClient(
            create_app(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                scheduler_manager=scheduler_manager,
            )
        )

        response = client.post(
            "/targets",
            data={
                "group_url": (
                    "https://www.facebook.com/groups/204808657039646/"
                    "permalink/2155501991970293"
                ),
                "fixed_refresh_sec": "60",
                "max_items_per_scan": "5",
                "auto_load_more": "on",
            },
            follow_redirects=False,
        )
    finally:
        scheduler_manager.stop(timeout_seconds=2)

    assert response.status_code == 303
    assert "error=" not in response.headers["location"]
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.COMMENTS,
            scope_id="204808657039646:post:2155501991970293:comments",
        )
    assert target is not None
    assert target.canonical_url == (
        "https://www.facebook.com/groups/204808657039646/posts/2155501991970293"
    )
    assert target.metadata_status == TargetMetadataStatus.PENDING
    assert scheduler_manager.take_metadata_refresh_requests() == (target.id,)


def test_create_target_route_adds_comments_target_and_resolves_group_name(
    tmp_path: Path,
) -> None:
    """Web UI 會依單篇貼文 URL 自動建立 comments target 並補社團名稱。"""

    db_path = tmp_path / "app.db"
    resolver_calls: list[str] = []

    def fake_resolver(_profile_dir: Path, url: str) -> str:
        resolver_calls.append(url)
        return "留言測試社團"

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=fake_resolver,
        )
    )

    form_response = client.get("/targets/new")
    create_response = client.post(
        "/targets",
        data={
            "group_url": (
                "https://www.facebook.com/groups/222518561920110/posts/2187454285426518/"
                "?comment_id=123456789"
            ),
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
            "auto_load_more": "on",
        },
        follow_redirects=False,
    )

    assert form_response.status_code == 200
    assert "Target kind" not in form_response.text
    assert create_response.status_code == 303
    assert resolver_calls == ["https://www.facebook.com/groups/222518561920110"]
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.COMMENTS,
            scope_id="222518561920110:post:2187454285426518:comments",
        )
        assert target is not None
        config = app_context.repositories.configs.get_for_target(target)
        state = app_context.repositories.runtime_states.get(target.id)

    assert target.group_id == "222518561920110"
    assert target.parent_post_id == "2187454285426518"
    assert target.canonical_url == (
        "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"
    )
    assert target.name == "留言測試社團 / post:2187454285426518"
    assert target.group_name == "留言測試社團"
    assert target.paused
    assert config is not None
    assert config.exclude_keywords == PYTHON_TARGET_CONFIG_DEFAULTS.exclude_keywords
    assert state is not None

    index_response = client.get("/")
    assert index_response.status_code == 200
    assert "留言測試社團" in index_response.text
    assert "留言模式" in index_response.text
    assert "下次刷新：未排程" in index_response.text
    assert "comments · group=222518561920110" not in index_response.text
    assert "parent_post=2187454285426518" not in index_response.text
    assert "scope=222518561920110:post:2187454285426518:comments" not in index_response.text
    assert "target_kind=comments" in index_response.text
    assert "已停止" in index_response.text
    assert "開始" in index_response.text
    assert "comments D3 已建立 sort/load-more" not in index_response.text


def test_create_target_route_ignores_target_kind_form_field_and_detects_url(
    tmp_path: Path,
) -> None:
    """舊表單若仍送 target_kind，後端仍以 URL 自動判斷 target 類型。"""

    db_path = tmp_path / "app.db"
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=lambda _profile_dir, _url: "測試社團",
        )
    )

    response = client.post(
        "/targets",
        data={
            "target_kind": "comments",
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        posts_target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
        comments_target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.COMMENTS,
            scope_id="222518561920110:post::comments",
        )
    assert posts_target is not None
    assert comments_target is None


def test_settings_routes_control_profile_window(tmp_path: Path) -> None:
    """設定頁可開啟與關閉 Facebook automation profile 視窗。"""

    db_path = tmp_path / "app.db"
    profile_manager = FakeProfileManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            profile_manager=profile_manager,
        )
    )

    settings_response = client.get("/settings")
    open_response = client.post("/settings/facebook/open", follow_redirects=False)
    active_index_response = client.get("/")

    assert settings_response.status_code == 200
    assert "Facebook automation profile" in settings_response.text
    assert "未開啟" not in settings_response.text
    assert "視窗開啟中" not in settings_response.text
    assert "關閉視窗" not in settings_response.text
    assert open_response.status_code == 303
    assert profile_manager.active
    assert "設定 · 開啟中" not in active_index_response.text
    close_response = client.post("/settings/facebook/close", follow_redirects=False)
    assert close_response.status_code == 303
    assert not profile_manager.active


def test_settings_updates_tests_and_applies_global_notifications(tmp_path: Path) -> None:
    """設定頁可保存通知預設值；測試通知 route 保留給診斷使用。"""

    db_path = tmp_path / "app.db"
    notifications = NotificationRecorder()

    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            desktop_sender=notifications.desktop_sender,
            ntfy_sender=notifications.ntfy_sender,
            discord_sender=notifications.discord_sender,
        )
    )
    settings_page = client.get("/settings")
    save_response = client.post(
        "/settings/notifications",
        data={
            "enable_desktop_notification": "on",
            "enable_ntfy": "on",
            "ntfy_topic": "phase0test",
            "enable_discord_notification": "on",
            "discord_webhook": "https://discord.com/api/webhooks/example",
        },
        follow_redirects=False,
    )
    form_response = client.get("/targets/new")
    test_response = client.post(
        "/settings/notifications/test",
        data={
            "enable_desktop_notification": "on",
            "enable_ntfy": "on",
            "ntfy_topic": "phase0test",
            "enable_discord_notification": "on",
            "discord_webhook": "https://discord.com/api/webhooks/example",
        },
        follow_redirects=True,
    )
    apply_response = client.post(
        "/settings/notifications/apply-to-targets",
        follow_redirects=False,
    )

    assert save_response.status_code == 303
    assert "通知預設值" in settings_page.text
    assert "未填寫也不影響功能" in settings_page.text
    assert "批次套用來源" not in settings_page.text
    assert "套用到所有 target" not in settings_page.text
    assert "發送測試通知" not in settings_page.text
    assert form_response.status_code == 200
    assert "value=\"phase0test\"" in form_response.text
    assert "https://discord.com/api/webhooks/example" in form_response.text
    assert test_response.status_code == 200
    assert "desktop_sent / ntfy_sent / discord_sent" in test_response.text
    assert any(item.startswith("desktop:") for item in notifications.sent)
    assert any(item.startswith("ntfy:phase0test:") for item in notifications.sent)
    assert any(
        item.startswith("discord:https://discord.com/api/webhooks/example:")
        for item in notifications.sent
    )
    assert apply_response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert config.enable_desktop_notification
    assert config.enable_ntfy
    assert config.ntfy_topic == "phase0test"
    assert config.enable_discord_notification
    assert config.discord_webhook == "https://discord.com/api/webhooks/example"


def test_target_settings_modal_can_test_notifications_without_saving(
    tmp_path: Path,
) -> None:
    """target 設定 modal 的測試通知會使用表單值，但不保存 target 設定。"""

    db_path = tmp_path / "app.db"
    notifications = NotificationRecorder()

    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            desktop_sender=notifications.desktop_sender,
            ntfy_sender=notifications.ntfy_sender,
            discord_sender=notifications.discord_sender,
        )
    )
    index_response = client.get("/")
    test_response = client.post(
        f"/targets/{target.id}/notifications/test",
        data={
            "enable_desktop_notification": "on",
            "enable_ntfy": "on",
            "ntfy_topic": "modal-topic",
            "enable_discord_notification": "on",
            "discord_webhook": "https://discord.com/api/webhooks/modal",
        },
        follow_redirects=True,
    )
    json_test_response = client.post(
        f"/targets/{target.id}/notifications/test",
        data={
            "enable_desktop_notification": "on",
            "enable_ntfy": "on",
            "ntfy_topic": "modal-topic-json",
        },
        headers={"Accept": "application/json"},
    )

    assert index_response.status_code == 200
    assert "掃描設定" in index_response.text
    assert "刷新設定" in index_response.text
    assert "通知設定" in index_response.text
    assert "測試通知" in index_response.text
    assert f'data-notification-test-action="/targets/{target.id}/notifications/test"' in (
        index_response.text
    )
    assert f'data-notification-test-form-id="config-{target.id}"' in index_response.text
    assert f'formaction="/targets/{target.id}/notifications/test"' not in index_response.text
    assert f'data-dirty-status-for="config-{target.id}"' in index_response.text
    assert "data-notification-test" in index_response.text
    assert "data-notification-test-status" in index_response.text
    assert f'form="config-{target.id}"' in index_response.text
    assert (
        index_response.text.index(
            f'name="refresh_mode" type="radio" value="floating" form="config-{target.id}"'
        )
        < index_response.text.index(
            f'name="refresh_mode" type="radio" value="fixed" form="config-{target.id}"'
        )
    )
    assert re.search(
        rf'name="refresh_mode" type="radio" value="floating"[^>]*form="config-{target.id}"[^>]*checked',
        index_response.text,
    )
    assert f'name="refresh_mode" type="radio" value="fixed" form="config-{target.id}"' in (
        index_response.text
    )
    assert f'name="fixed_refresh_sec" type="number" min="5" value="60" form="config-{target.id}"' in (
        index_response.text
    )
    assert test_response.status_code == 200
    assert "desktop_sent / ntfy_sent / discord_sent" in test_response.text
    assert json_test_response.status_code == 200
    assert json_test_response.json()["ok"] is True
    assert json_test_response.json()["results"] == ["desktop_sent", "ntfy_sent"]
    assert any(item.startswith("desktop:") for item in notifications.sent)
    assert any(item.startswith("ntfy:modal-topic:") for item in notifications.sent)
    assert any(
        item.startswith("discord:https://discord.com/api/webhooks/modal:")
        for item in notifications.sent
    )
    with SqliteApplicationContext(db_path) as app_context:
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert not config.enable_desktop_notification
    assert not config.enable_ntfy
    assert config.ntfy_topic == ""
    assert not config.enable_discord_notification
    assert config.discord_webhook == ""


def test_notification_test_errors_are_sanitized(tmp_path: Path) -> None:
    """手動測試通知失敗時，UI 錯誤不得回填 webhook / topic。"""

    db_path = tmp_path / "app.db"

    def failing_ntfy_sender(config: NtfyConfig, _title: str, _message: str) -> NtfyResult:
        """模擬自訂 sender 例外內含 topic。"""

        raise RuntimeError(f"failed https://ntfy.sh/{config.topic}")

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            ntfy_sender=failing_ntfy_sender,
        )
    )

    response = client.post(
        "/settings/notifications/test",
        data={
            "enable_ntfy": "on",
            "ntfy_topic": "private-topic",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "notification_test_failed:RuntimeError" in response.text
    assert "private-topic" not in response.text


def test_target_notification_test_errors_are_sanitized(tmp_path: Path) -> None:
    """target 測試通知失敗時，UI 錯誤不得回填 webhook / topic。"""

    db_path = tmp_path / "app.db"

    def failing_discord_sender(
        config: DiscordConfig,
        _title: str,
        _message: str,
    ) -> DiscordResult:
        """模擬自訂 sender 例外內含 webhook。"""

        raise RuntimeError(f"failed {config.webhook_url}")

    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            discord_sender=failing_discord_sender,
        )
    )

    response = client.post(
        f"/targets/{target.id}/notifications/test",
        data={
            "enable_discord_notification": "on",
            "discord_webhook": "https://discord.com/api/webhooks/private-token",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "notification_test_failed:RuntimeError" in response.text
    assert "private-token" not in response.text


def test_settings_open_pauses_scheduler_until_profile_window_ends(tmp_path: Path) -> None:
    """設定頁開 profile 時暫停 scheduler；視窗自行結束後會自動恢復。"""

    db_path = tmp_path / "app.db"
    profile_manager = FakeProfileManager()
    scheduler_manager = FakeSchedulerManager()
    scheduler_manager.running = True
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            profile_manager=profile_manager,
            scheduler_manager=scheduler_manager,
        )
    )

    response = client.post("/settings/facebook/open", follow_redirects=False)

    assert response.status_code == 303
    assert profile_manager.active
    assert scheduler_manager.stopped_count == 1
    assert not scheduler_manager.running

    profile_manager.active = False
    assert profile_manager.options is not None
    assert profile_manager.options.on_close is not None
    profile_manager.options.on_close()

    assert scheduler_manager.started_count == 1
    assert scheduler_manager.running


def test_webui_shutdown_closes_active_profile_window(tmp_path: Path) -> None:
    """Web UI 關閉時會先收掉設定頁開出的 profile 視窗。"""

    db_path = tmp_path / "app.db"
    profile_manager = FakeProfileManager()
    scheduler_manager = FakeSchedulerManager()

    with TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            profile_manager=profile_manager,
            scheduler_manager=scheduler_manager,
        )
    ) as client:
        response = client.post("/settings/facebook/open", follow_redirects=False)
        assert response.status_code == 303
        assert profile_manager.active

    assert not profile_manager.active
    assert profile_manager.close_count == 1
    assert scheduler_manager.stopped_count == 1


def test_create_target_uses_fallback_name_when_scheduler_running(
    tmp_path: Path,
) -> None:
    """背景掃描執行中時，新增 target 不為了解析社團名稱暫停 scheduler。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    scheduler_manager.running = True
    resolver_calls: list[str] = []

    def fake_resolver(_profile_dir: Path, url: str) -> str:
        resolver_calls.append(url)
        return "測試社團"

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
            group_name_resolver=fake_resolver,
        )
    )

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
            "auto_load_more": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert resolver_calls == []
    assert scheduler_manager.stopped_count == 0
    assert scheduler_manager.started_count == 0
    assert scheduler_manager.running
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
    assert target is not None
    assert target.name == "group:222518561920110:posts"
    assert target.metadata_status == TargetMetadataStatus.PENDING
    assert scheduler_manager.metadata_refresh_target_ids == [target.id]


def test_manual_metadata_refresh_marks_pending_and_wakes_scheduler(
    tmp_path: Path,
) -> None:
    """設定 modal 的重新抓取會排入 resident metadata refresh，不直接搶 profile。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="舊名稱",
            )
        )
        app_context.services.targets.mark_target_metadata_refresh_failed(
            target.id,
            "Facebook 尚未登入",
        )

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.post(
        f"/targets/{target.id}/metadata/refresh",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (
        "%E5%B7%B2%E5%8A%A0%E5%85%A5%E6%8E%92%E7%A8%8B"
        in response.headers["location"]
    )
    assert scheduler_manager.metadata_refresh_target_ids == [target.id]
    assert scheduler_manager.started_count == 1
    assert scheduler_manager.woken_count == 1
    with SqliteApplicationContext(db_path) as app_context:
        updated = app_context.repositories.targets.get(target.id)
    assert updated is not None
    assert updated.metadata_status == TargetMetadataStatus.PENDING
    assert updated.metadata_error == ""


def test_settings_modal_keeps_metadata_refresh_entry_hidden_for_later_placement(
    tmp_path: Path,
) -> None:
    """metadata refresh 入口放在設定 modal footer，不佔用設定內容區塊。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app_context.services.targets.mark_target_metadata_refresh_failed(
            target.id,
            "Facebook 尚未登入",
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "Target 資訊" not in response.text
    assert "重新抓取名稱與封面" in response.text
    assert f"/targets/{target.id}/metadata/refresh" in response.text


def test_sidebar_status_shows_target_mode_chip(tmp_path: Path) -> None:
    """sidebar 副行在狀態與掃描摘要中間顯示貼文/留言 mode chip。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        comments = app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="1370511589953459",
                parent_post_id="2772468963091041",
                canonical_url=(
                    "https://www.facebook.com/groups/1370511589953459/"
                    "posts/2772468963091041"
                ),
                group_name="測試社團",
            )
        )
        posts = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="貼文社團",
            )
        )
        app_context.services.targets.pause_target_monitoring(comments.id)
        app_context.services.targets.pause_target_monitoring(posts.id)

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert 'data-sidebar-mode-label="留言"' in response.text
    assert 'data-sidebar-mode-class="comments"' in response.text
    assert (
        'class="sidebar-status-token target-mode-chip sidebar-mode-chip comments">留言</span>'
        in response.text
    )
    assert 'data-sidebar-mode-label="貼文"' in response.text
    assert 'data-sidebar-mode-class="posts"' in response.text
    assert (
        'class="sidebar-status-token target-mode-chip sidebar-mode-chip posts">貼文</span>'
        in response.text
    )


def test_scheduler_routes_are_not_public_daily_controls(tmp_path: Path) -> None:
    """Web UI 不再提供全域 scheduler 日常主開關 route。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )

    start_response = client.post("/scheduler/start", follow_redirects=False)
    index_response = client.get("/")
    stop_response = client.post("/scheduler/stop", follow_redirects=False)

    assert start_response.status_code == 404
    assert scheduler_manager.started_count == 0
    assert scheduler_manager.options is None
    assert "背景掃描服務" not in index_response.text
    assert "啟動自動掃描" not in index_response.text
    assert "停止自動掃描" not in index_response.text
    assert stop_response.status_code == 404
    assert scheduler_manager.stopped_count == 0
    assert not scheduler_manager.running


def test_webui_startup_resets_targets_to_stopped(tmp_path: Path) -> None:
    """正式 Web UI 啟動時會停止 target，但不覆蓋浮動刷新設定。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    fixed_refresh_sec=None,
                    min_refresh_sec=25,
                    max_refresh_sec=35,
                    jitter_enabled=True,
                ),
            )
        )

    with TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
            reset_targets_on_startup=True,
        )
    ) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "已停止" in response.text
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        state = app_context.repositories.runtime_states.get(target.id)
        config = app_context.repositories.configs.get_for_target(target)
    assert loaded is not None
    assert loaded.paused
    assert state is not None
    assert state.desired_state.value == "stopped"
    assert config is not None
    assert config.fixed_refresh_sec is None
    assert config.jitter_enabled
    assert config.min_refresh_sec == 25
    assert config.max_refresh_sec == 35


def test_webui_startup_can_clear_runtime_debug_data(tmp_path: Path) -> None:
    """Web UI 啟動時可清除上一輪 runtime/debug data，保留 target 設定。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="seen-before-startup",
                item_kind=ItemKind.POST,
            )
        )
        scan_run_id = app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
                item_count=1,
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=scan_run_id,
                    item_kind=ItemKind.POST,
                    item_key="seen-before-startup",
                    item_index=0,
                )
            ],
        )
        app_context.repositories.notification_events.add(
            NotificationEvent(
                target_id=target.id,
                item_key="seen-before-startup",
                channel=NotificationChannel.NTFY,
                status=NotificationStatus.SENT,
                message="sent",
            )
        )

    with TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
            reset_runtime_data_on_startup=True,
        )
    ) as client:
        response = client.get("/")

    assert response.status_code == 200
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        config = app_context.repositories.configs.get_for_target(target)
        latest_scan = app_context.repositories.scan_runs.latest_by_target(target.id)
        latest_items = app_context.repositories.latest_scan_items.list_by_target(target.id)
        notifications = app_context.repositories.notification_events.list_by_target(target.id)
        has_seen = app_context.repositories.seen_items.has_seen(
            target.scope_id,
            "seen-before-startup",
        )

    assert loaded is not None
    assert config is not None
    assert latest_scan is None
    assert latest_items == []
    assert notifications == []
    assert not has_seen


def test_start_and_stop_routes_update_target_status(tmp_path: Path) -> None:
    """Web UI 開始/停止 route 對齊 restart/pause 語義。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="seen-before-start",
                item_kind=ItemKind.POST,
            )
        )
        app_context.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:seen-before-start:ntfy",
                target_id=target.id,
                item_key="seen-before-start",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    stop_response = client.post(f"/targets/{target.id}/stop", follow_redirects=False)
    start_response = client.post(
        f"/targets/{target.id}/start",
        data={"return_to": f"#target-{target.id}"},
        follow_redirects=False,
    )

    assert stop_response.status_code == 303
    assert start_response.status_code == 303
    assert start_response.headers["location"].endswith(f"#target-{target.id}")
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        state = app_context.repositories.runtime_states.get(target.id)
        has_seen = app_context.repositories.seen_items.has_seen(
            target.scope_id,
            "seen-before-start",
        )
        outbox_entry = app_context.repositories.notification_outbox.get_by_idempotency_key(
            f"{target.id}:seen-before-start:ntfy",
        )
    assert loaded is not None
    assert loaded.enabled
    assert not loaded.paused
    assert state is not None
    assert state.scan_requested_at is not None
    assert not has_seen
    assert outbox_entry is None
    assert scheduler_manager.woken_count == 2


def test_start_route_supports_comments_target(tmp_path: Path) -> None:
    """Web UI comments target 的開始 route 會清 comments seen 並喚醒 scheduler。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="comment-before-start",
                item_kind=ItemKind.COMMENT,
            )
        )

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.post(
        f"/targets/{target.id}/start",
        data={"return_to": f"#target-{target.id}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        state = app_context.repositories.runtime_states.get(target.id)
        has_seen = app_context.repositories.seen_items.has_seen(
            target.scope_id,
            "comment-before-start",
        )
    assert loaded is not None
    assert not loaded.paused
    assert state is not None
    assert state.scan_requested_at is not None
    assert not has_seen
    assert scheduler_manager.woken_count == 1


def test_scan_once_requests_resident_scan_for_posts_and_comments(tmp_path: Path) -> None:
    """Web UI scan-once 只排入 resident scan request，不啟動 one-shot fallback。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        posts_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        comments_target = app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
            )
        )
        app_context.services.targets.restart_target_monitoring(posts_target.id)
        app_context.services.targets.restart_target_monitoring(comments_target.id)

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    posts_response = client.post(f"/targets/{posts_target.id}/scan-once", follow_redirects=False)
    comments_response = client.post(
        f"/targets/{comments_target.id}/scan-once",
        follow_redirects=False,
    )

    assert posts_response.status_code == 303
    assert comments_response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        posts_state = app_context.repositories.runtime_states.get(posts_target.id)
        comments_state = app_context.repositories.runtime_states.get(comments_target.id)
    assert posts_state is not None
    assert posts_state.scan_requested_at is not None
    assert comments_state is not None
    assert comments_state.scan_requested_at is not None
    assert scheduler_manager.started_count == 1
    assert scheduler_manager.woken_count == 2


def test_scan_once_requires_started_target(tmp_path: Path) -> None:
    """停止中的 target 不會被 scan-once 暗中送進 fallback worker。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
            )
        )

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.post(f"/targets/{target.id}/scan-once", follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert scheduler_manager.started_count == 0
    assert scheduler_manager.woken_count == 0


def test_dashboard_revision_endpoint_changes_after_target_update(tmp_path: Path) -> None:
    """dashboard revision endpoint 只在資料有變更時供前端刷新。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    first_revision = client.get("/api/dashboard-revision").json()["revision"]
    response = client.post(
        f"/targets/{target.id}/config",
        data={
            "return_to": f"#target-{target.id}",
            "include_keywords": "票券",
            "exclude_keywords": "",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )
    second_revision = client.get("/api/dashboard-revision").json()["revision"]

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"#target-{target.id}")
    assert first_revision != second_revision


def test_dashboard_revision_read_path_does_not_initialize_schema(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """SSE revision read path 不應建立 application context 或重跑 schema init。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("dashboard revision should use direct read-only connection")

    monkeypatch.setattr(query_service, "SqliteApplicationContext", fail_if_called)

    revision = query_service.get_dashboard_revision(db_path)

    assert int(revision.revision) > 0


def test_dashboard_sidebar_read_path_does_not_initialize_schema(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Sidebar partial update read path 不應在掃描寫入期間重跑 schema init。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("dashboard read path should not initialize schema")

    monkeypatch.setattr(application_context, "initialize_schema", fail_if_called)

    items = query_service.list_sidebar_items(db_path)

    assert len(items) == 1


def test_dashboard_revision_endpoint_ignores_temporary_sqlite_lock(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """dashboard polling endpoint 遇到短暫 DB lock 時回 503，前端可忽略該輪。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path):
        pass

    def raise_locked(*args: object, **kwargs: object) -> object:
        raise query_service.DashboardRevisionUnavailable("database is locked")

    monkeypatch.setattr(dashboard_routes, "get_dashboard_revision", raise_locked)
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.get("/api/dashboard-revision")

    assert response.status_code == 503


def test_dashboard_sidebar_endpoint_ignores_temporary_sqlite_lock(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Sidebar partial update 遇到短暫 DB lock 時回 503，避免 ASGI traceback。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path):
        pass

    def raise_locked(*args: object, **kwargs: object) -> object:
        raise query_service.DashboardReadUnavailable("database is locked")

    monkeypatch.setattr(dashboard_routes, "list_sidebar_items", raise_locked)
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.get("/api/sidebar")

    assert response.status_code == 503


def test_sidebar_layout_api_saves_group_order_and_placements_atomically(tmp_path: Path) -> None:
    """sidebar layout API 以單一請求保存 group order 與 placements。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        first_group = app_context.services.sidebar_layout.create_group("第一群")
        second_group = app_context.services.sidebar_layout.create_group("第二群")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        "/api/sidebar/layout",
        json={
            "group_ids": [second_group.id, first_group.id],
            "groups": [
                {"group_id": second_group.id, "target_ids": [second.id]},
                {"group_id": first_group.id, "target_ids": [first.id]},
                {"group_id": None, "target_ids": []},
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "updated_count": 2}
    with SqliteApplicationContext(db_path) as app_context:
        groups = app_context.repositories.sidebar_layout.list_groups()
        placements = app_context.repositories.sidebar_layout.list_placements()
    assert [group.id for group in groups] == [second_group.id, first_group.id]
    assert placements[first.id].sidebar_group_id == first_group.id
    assert placements[second.id].sidebar_group_id == second_group.id


def test_sidebar_group_order_api_rejects_duplicate_group_ids(tmp_path: Path) -> None:
    """sidebar group order API 不接受重複 group id。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first_group = app_context.services.sidebar_layout.create_group("第一群")
        second_group = app_context.services.sidebar_layout.create_group("第二群")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        "/api/sidebar/groups/order",
        json={"group_ids": [first_group.id, second_group.id, first_group.id]},
    )

    assert response.status_code == 400
    assert "重複群組" in response.json()["detail"]
    with SqliteApplicationContext(db_path) as app_context:
        groups = app_context.repositories.sidebar_layout.list_groups()
    assert [group.id for group in groups] == [first_group.id, second_group.id]


def test_sidebar_layout_api_rejects_duplicate_group_sections(tmp_path: Path) -> None:
    """sidebar layout API 不接受重複 group section。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        group = app_context.services.sidebar_layout.create_group("重複群組")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        "/api/sidebar/layout",
        json={
            "group_ids": [group.id],
            "groups": [
                {"group_id": group.id, "target_ids": [first.id]},
                {"group_id": group.id, "target_ids": [second.id]},
                {"group_id": None, "target_ids": []},
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "排序資料不可包含重複群組區塊"


def test_sidebar_placements_api_rejects_duplicate_ungrouped_sections(tmp_path: Path) -> None:
    """sidebar placements API 不接受多個未分組 section。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        "/api/sidebar/placements",
        json={
            "groups": [
                {"group_id": None, "target_ids": [first.id]},
                {"group_id": None, "target_ids": [second.id]},
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "排序資料不可包含重複群組區塊"


def test_flat_sidebar_order_api_rejects_when_targets_are_grouped(tmp_path: Path) -> None:
    """舊平面排序 API 不可在已有 group placement 時打平 sidebar 狀態。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        group = app_context.services.sidebar_layout.create_group("已分組")
        app_context.services.sidebar_layout.save_placements(
            [(group.id, [first.id]), (None, [second.id])]
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        "/api/sidebar/order",
        json={"target_ids": [second.id, first.id]},
    )

    assert response.status_code == 400
    assert "已有群組排序狀態" in response.json()["detail"]
    with SqliteApplicationContext(db_path) as app_context:
        placements = app_context.repositories.sidebar_layout.list_placements()
    assert placements[first.id].sidebar_group_id == group.id


def test_sidebar_api_errors_use_safe_traditional_chinese_messages(
    tmp_path: Path,
) -> None:
    """sidebar API 錯誤回應不得暴露英文內部錯誤或 repository 細節。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        group = app_context.services.sidebar_layout.create_group("已分組")
        app_context.services.sidebar_layout.save_placements(
            [(group.id, [first.id]), (None, [second.id])]
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    invalid_json = client.post(
        "/api/sidebar/groups",
        content="{",
        headers={"content-type": "application/json"},
    )
    grouped_order = client.post(
        "/api/sidebar/order",
        json={"target_ids": [second.id, first.id]},
    )
    missing_group = client.patch(
        "/api/sidebar/groups/missing",
        json={"name": "新名稱"},
    )

    assert invalid_json.status_code == 400
    assert invalid_json.json()["detail"] == "JSON 格式不正確"
    assert grouped_order.status_code == 400
    assert grouped_order.json()["detail"] == "已有群組排序狀態，請使用調整順序後的確認保存"
    assert missing_group.status_code == 404
    assert missing_group.json()["detail"] == "找不到指定的 sidebar 群組"


def test_dashboard_events_streams_revision_event(tmp_path: Path) -> None:
    """dashboard event stream endpoint 與 event 格式會提供 revision event。"""

    db_path = tmp_path / "app.db"
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    openapi = client.get("/openapi.json").json()
    event_text = _format_dashboard_revision_event(
        {"revision": "rev-1", "last_changed_at": "2026-05-08T00:00:00"}
    )

    assert "/api/dashboard-events" in openapi["paths"]
    assert event_text.startswith("event: dashboard_revision\n")
    assert event_text.endswith("\n\n")
    data_line = next(line for line in event_text.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload == {"revision": "rev-1", "last_changed_at": "2026-05-08T00:00:00"}


def test_index_shows_latest_items_up_to_target_max_items(tmp_path: Path) -> None:
    """右側最近掃描項目顯示上限會跟 target max_items_per_scan 對齊。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(max_items_per_scan=7),
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key=f"item-{index}",
                    item_index=index,
                    author=f"作者 {index}",
                    text=f"貼文 {index}",
                )
                for index in range(7)
            ],
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "作者 0" in response.text
    assert "作者 6" in response.text


def test_delete_route_removes_only_selected_target(tmp_path: Path) -> None:
    """Web UI 刪除 route 只刪除指定 target。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app_context.services.targets.pause_target_monitoring(second.id)

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(f"/targets/{first.id}/delete", follow_redirects=False)

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        assert app_context.repositories.targets.get(first.id) is None
        loaded_second = app_context.repositories.targets.get(second.id)
    assert loaded_second is not None
    assert loaded_second.enabled
    assert loaded_second.paused
