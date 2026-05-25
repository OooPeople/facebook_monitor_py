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
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
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

    return page.evaluate(
        """() => {
          const menu = document.querySelector("[data-sidebar-menu]");
          const panel = document.querySelector(".sidebar-menu-panel");
          const trigger = document.querySelector(".sidebar-menu-trigger");
          const rect = panel.getBoundingClientRect();
          const point = {
            x: Math.round(rect.left + rect.width / 2),
            y: Math.round(rect.top + rect.height / 2),
          };
          const topElement = document.elementFromPoint(point.x, point.y);
          return {
            activeCreateGroup: document.activeElement?.matches("[data-sidebar-create-group]"),
            activeTrigger: document.activeElement === trigger,
            containsTopElement: panel.contains(topElement),
            floating: panel.dataset.sidebarMenuFloating || "",
            menuOpen: menu.open,
            panelParentClass: panel.parentElement?.className || "",
            panelParentTag: panel.parentElement?.tagName || "",
            sorting: document.querySelector("[data-sidebar-layout]")?.classList.contains("sorting"),
          };
        }"""
    )


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
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto(f"{url}/", wait_until="load")
                page.click(".sidebar-menu-trigger")
                page.wait_for_function(
                    "document.activeElement?.matches('[data-sidebar-create-group]')"
                )

                opened = _sidebar_menu_state(page)
                assert opened["menuOpen"] is True
                assert opened["panelParentTag"] == "BODY"
                assert opened["floating"] == "1"
                assert opened["activeCreateGroup"] is True
                assert opened["containsTopElement"] is True

                page.keyboard.press("Escape")
                page.wait_for_function("!document.querySelector('[data-sidebar-menu]').open")
                escaped = _sidebar_menu_state(page)
                assert escaped["panelParentTag"] == "DETAILS"
                assert escaped["panelParentClass"] == "sidebar-menu"
                assert escaped["floating"] == ""
                assert escaped["activeTrigger"] is True

                page.click(".sidebar-menu-trigger")
                page.wait_for_function(
                    "document.querySelector('.sidebar-menu-panel').parentElement === document.body"
                )
                page.click("[data-sidebar-start-sort]")
                page.wait_for_function(
                    "document.querySelector('[data-sidebar-layout]').classList.contains('sorting')"
                )
                sorting = _sidebar_menu_state(page)
                assert sorting["menuOpen"] is False
                assert sorting["panelParentTag"] == "DETAILS"
                assert sorting["panelParentClass"] == "sidebar-menu"
                assert sorting["floating"] == ""
                assert sorting["sorting"] is True
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
