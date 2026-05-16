"""Admin smoke：驗證重新登入提示與 launcher login gate。

本腳本使用隔離的暫存 data-dir，不讀寫使用者真實 profile / cookies。
它不登入 Facebook；只驗證產品內部重新登入狀態流：

1. `profile_session_status=needs_login` 時，Web UI 右上角會顯示警告。
2. 下次 launcher 啟動前會先進 guided-login gate；本腳本以 fake guided-login
   模擬登入完成，避免碰真實帳號。
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from collections.abc import Callable

import httpx
from playwright.sync_api import sync_playwright
import uvicorn


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor import launcher
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.persistence.repositories.app_settings import ProfileSessionState
from facebook_monitor.webapp.app import create_app


def parse_args() -> argparse.Namespace:
    """解析 smoke test CLI 參數。"""

    parser = argparse.ArgumentParser(
        description="Smoke test relogin warning and launcher login gate with isolated data."
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Open a visible Chromium window while checking the Web UI warning.",
    )
    parser.add_argument(
        "--keep-data-dir",
        action="store_true",
        help="Keep the temporary data directory for inspection.",
    )
    return parser.parse_args()


def main() -> int:
    """執行隔離的 relogin smoke test。"""

    args = parse_args()
    with tempfile.TemporaryDirectory(prefix="facebook-monitor-relogin-smoke-") as temp_dir:
        data_dir = Path(temp_dir)
        db_path = data_dir / "app.db"
        profile_dir = data_dir / "profiles" / "automation_default"
        profile_dir.mkdir(parents=True, exist_ok=True)
        seed_needs_login_status(db_path)

        host = "127.0.0.1"
        port = choose_available_port(host)
        server = start_webui_server(db_path=db_path, profile_dir=profile_dir, host=host, port=port)
        try:
            warning_text = verify_webui_warning(
                url=f"http://{host}:{port}",
                headed=bool(args.headed),
                output_dir=ROOT / "output" / "playwright",
            )
        finally:
            stop_webui_server(server)

        gate_called = verify_launcher_login_gate(db_path=db_path, profile_dir=profile_dir)
        print("重新登入 UI 警告：OK")
        print(f"警告文案：{warning_text}")
        print(f"Launcher guided-login gate：OK ({gate_called})")
        if args.keep_data_dir:
            preserved_dir = ROOT / "output" / "relogin-smoke-data"
            if preserved_dir.exists():
                raise RuntimeError(f"Output data dir already exists: {preserved_dir}")
            data_dir.rename(preserved_dir)
            print(f"保留測試資料：{preserved_dir}")
            return 0
        return 0


def seed_needs_login_status(db_path: Path) -> None:
    """建立隔離 DB 並標記 profile 需要重新登入。"""

    with SqliteApplicationContext(db_path) as app:
        app.repositories.app_settings.mark_profile_needs_login(
            reason="login_required",
            source="smoke_relogin_flow",
        )


def start_webui_server(
    *,
    db_path: Path,
    profile_dir: Path,
    host: str,
    port: int,
) -> uvicorn.Server:
    """啟動隔離 Web UI server。"""

    app = create_app(
        db_path=db_path,
        profile_dir=profile_dir,
        auto_start_scheduler=False,
        reset_targets_on_startup=False,
        reset_runtime_data_on_startup=False,
    )
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="relogin-smoke-webui", daemon=True)
    thread.start()
    setattr(server, "_smoke_thread", thread)
    wait_until_healthy(f"http://{host}:{port}/health")
    return server


def stop_webui_server(server: uvicorn.Server) -> None:
    """停止 smoke test Web UI server。"""

    server.should_exit = True
    thread = getattr(server, "_smoke_thread", None)
    if isinstance(thread, threading.Thread):
        thread.join(timeout=5)


def verify_webui_warning(
    *,
    url: str,
    headed: bool,
    output_dir: Path,
) -> str:
    """用 Playwright 開 Web UI，確認重新登入警告可見。"""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded")
            warning = page.locator("[data-profile-session-warning]")
            warning.wait_for(state="visible", timeout=5000)
            text = warning.inner_text(timeout=2000).strip()
            if "Facebook 需要重新登入" not in text:
                raise AssertionError(f"Unexpected relogin warning text: {text}")
            output_dir.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=output_dir / "relogin_warning_smoke.png")
            return text
        finally:
            browser.close()


def verify_launcher_login_gate(*, db_path: Path, profile_dir: Path) -> str:
    """用 fake guided-login 驗證 launcher gate 會清掉 needs_login。"""

    calls: list[Path] = []
    original_guided_login = launcher.run_guided_facebook_login

    def fake_guided_login(
        options: launcher.GuidedLoginOptions,
        *,
        print_fn: Callable[[str], None] = print,
    ) -> bool:
        calls.append(Path(options.profile_dir))
        return True

    launcher.run_guided_facebook_login = fake_guided_login
    try:
        if not launcher._run_login_gate_if_needed(profile_dir, db_path):
            raise AssertionError("launcher login gate returned false")
    finally:
        launcher.run_guided_facebook_login = original_guided_login

    if calls != [profile_dir]:
        raise AssertionError(f"guided login was not called with profile dir: {calls}")
    with SqliteApplicationContext(db_path) as app:
        status = app.repositories.app_settings.get_profile_session_status()
    if status.state != ProfileSessionState.OK:
        raise AssertionError(f"profile status was not cleared to ok: {status.state}")
    return str(calls[0])


def wait_until_healthy(url: str, *, timeout_seconds: float = 8.0) -> None:
    """等待本機 Web UI health endpoint 可用。"""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=0.5)
            if response.status_code == 200 and response.json().get("status") == "ok":
                return
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(0.1)
    raise TimeoutError(f"Web UI did not become healthy: {url}")


def choose_available_port(host: str) -> int:
    """向 OS 取得暫時可用的本機 port。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


if __name__ == "__main__":
    raise SystemExit(main())
