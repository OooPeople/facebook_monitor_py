"""新增 target 進階設定收合的瀏覽器互動 smoke tests。"""

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

from facebook_monitor.webapp.app import create_app


def _free_port() -> int:
    """取得測試 Web UI 可使用的 loopback port。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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


def _advanced_state(page) -> dict[str, object]:
    """讀取新增 target 進階設定的 disclosure 狀態。"""

    return page.evaluate(
        """
        () => {
          const details = document.querySelector("[data-new-target-advanced]");
          const summary = document.querySelector("[data-new-target-advanced-toggle]");
          const body = document.querySelector("[data-new-target-advanced-body]");
          return {
            ariaExpanded: summary?.getAttribute("aria-expanded") || "",
            bodyAnimating: Boolean(body?.hasAttribute("data-collapse-animating")),
            bodyHidden: Boolean(body?.hidden),
            detailsOpen: Boolean(details?.open),
            expandedClass: Boolean(details?.classList.contains("is-expanded")),
          };
        }
        """
    )


def _wait_for_advanced_state(page, *, expanded: bool) -> None:
    """等待收合動畫結束後狀態一致。"""

    page.wait_for_function(
        """
        (expanded) => {
          const details = document.querySelector("[data-new-target-advanced]");
          const summary = document.querySelector("[data-new-target-advanced-toggle]");
          const body = document.querySelector("[data-new-target-advanced-body]");
          return Boolean(details)
            && Boolean(summary)
            && Boolean(body)
            && details.open === expanded
            && summary.getAttribute("aria-expanded") === String(expanded)
            && body.hidden === !expanded
            && details.classList.contains("is-expanded") === expanded
            && !body.hasAttribute("data-collapse-animating");
        }
        """,
        arg=expanded,
    )


def test_new_target_advanced_collapse_browser_interaction(tmp_path: Path) -> None:
    """新增頁進階設定需支援滑鼠、鍵盤與快速切換後的穩定狀態。"""

    server, thread, url = _start_webapp(tmp_path / "app.db", tmp_path / "profile")
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True)
            except PlaywrightError as exc:
                pytest.skip(f"chromium browser is not installed: {exc}")
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                try:
                    page = context.new_page()
                    page.goto(f"{url}/targets/new", wait_until="load")
                    _wait_for_advanced_state(page, expanded=False)

                    summary = page.locator("[data-new-target-advanced-toggle]")
                    summary.click()
                    _wait_for_advanced_state(page, expanded=True)

                    summary.click()
                    closing = _advanced_state(page)
                    assert closing["ariaExpanded"] == "false"
                    assert closing["expandedClass"] is False
                    _wait_for_advanced_state(page, expanded=False)

                    summary.focus()
                    page.keyboard.press("Enter")
                    _wait_for_advanced_state(page, expanded=True)
                    page.keyboard.press("Space")
                    _wait_for_advanced_state(page, expanded=False)

                    summary.click()
                    summary.click()
                    summary.click()
                    _wait_for_advanced_state(page, expanded=True)
                    assert page.url == f"{url}/targets/new"
                finally:
                    context.close()
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
