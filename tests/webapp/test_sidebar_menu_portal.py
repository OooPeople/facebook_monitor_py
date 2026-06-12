"""Sidebar menu portal browser smoke tests。"""

from __future__ import annotations

from pathlib import Path
import socket
import threading
import time

import httpx
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
import pytest
import uvicorn

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.webapp.app import create_app


def _free_port() -> int:
    """取得測試 Web UI 可使用的 loopback port。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _seed_sidebar_group(db_path: Path) -> None:
    """建立含 sidebar group 的 dashboard 測試資料。"""

    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="測試社團",
            )
        )
        group = app_context.services.sidebar_layout.create_group("測試群組")
        app_context.services.sidebar_layout.save_placements([(group.id, [target.id])])


def _start_webapp(db_path: Path, profile_dir: Path) -> tuple[uvicorn.Server, threading.Thread, str]:
    """在背景 thread 啟動正式 FastAPI app 供瀏覽器 smoke 使用。"""

    port = _free_port()
    app = create_app(db_path=db_path, profile_dir=profile_dir, enforce_csrf=False)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/health", timeout=0.3).status_code == 200:
                return server, thread, url
        except httpx.HTTPError:
            time.sleep(0.05)
    server.should_exit = True
    thread.join(timeout=5)
    raise RuntimeError("test Web UI did not start")


def _sidebar_menu_state(page) -> dict[str, object]:
    """回傳 sidebar menu portal 與焦點狀態。"""

    panel = page.locator(".sidebar-menu-panel").first
    panel_in_body = page.locator("body > .sidebar-menu-panel").count() > 0
    panel_in_details = page.locator("details.sidebar-menu > .sidebar-menu-panel").count() > 0
    parent_tag = "BODY" if panel_in_body else "DETAILS" if panel_in_details else ""
    parent_class = "sidebar-menu" if panel_in_details else ""
    return {
        "activeCreateGroup": page.locator("[data-sidebar-create-group]:focus").count() > 0,
        "activeTrigger": page.locator(".sidebar-menu-trigger:focus").count() > 0,
        "containsTopElement": page.locator(".sidebar-menu-panel [data-sidebar-create-group]")
        .first
        .is_visible(),
        "floating": panel.get_attribute("data-sidebar-menu-floating") or "",
        "menuOpen": page.locator("[data-sidebar-menu][open]").count() > 0,
        "panelParentClass": parent_class,
        "panelParentTag": parent_tag,
        "sorting": page.locator("[data-sidebar-layout].sorting").count() > 0,
    }


@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_sidebar_menu_portal_focus_and_restore(tmp_path: Path, browser_name: str) -> None:
    """漢堡選單浮到 body 後仍要可聚焦，並在關閉 / 排序入口還原 DOM。"""

    db_path = tmp_path / "app.db"
    _seed_sidebar_group(db_path)
    server, thread, url = _start_webapp(db_path, tmp_path / "profile")
    try:
        with sync_playwright() as playwright:
            browser_type = getattr(playwright, browser_name)
            try:
                browser = browser_type.launch(headless=True)
            except PlaywrightError as exc:
                pytest.skip(f"{browser_name} browser is not installed: {exc}")
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                try:
                    page = context.new_page()
                    page.goto(f"{url}/", wait_until="load")
                    page.click(".sidebar-menu-trigger")
                    page.locator("[data-sidebar-create-group]:focus").wait_for()

                    opened = _sidebar_menu_state(page)
                    assert opened["menuOpen"] is True
                    assert opened["panelParentTag"] == "BODY"
                    assert opened["floating"] == "1"
                    assert opened["activeCreateGroup"] is True
                    assert opened["containsTopElement"] is True

                    page.keyboard.press("Escape")
                    page.locator("[data-sidebar-menu]:not([open])").wait_for()
                    escaped = _sidebar_menu_state(page)
                    assert escaped["panelParentTag"] == "DETAILS"
                    assert escaped["panelParentClass"] == "sidebar-menu"
                    assert escaped["floating"] == ""
                    assert escaped["activeTrigger"] is True

                    page.click(".sidebar-menu-trigger")
                    page.locator("body > .sidebar-menu-panel").wait_for()
                    page.click("[data-sidebar-start-sort]")
                    page.locator("[data-sidebar-layout].sorting").wait_for()
                    sorting = _sidebar_menu_state(page)
                    assert sorting["menuOpen"] is False
                    assert sorting["panelParentTag"] == "DETAILS"
                    assert sorting["panelParentClass"] == "sidebar-menu"
                    assert sorting["floating"] == ""
                    assert sorting["sorting"] is True
                finally:
                    context.close()
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
