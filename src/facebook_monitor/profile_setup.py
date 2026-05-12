"""正式 profile setup 入口。

職責：使用與 Web UI 相同的 runtime path resolver 開啟 headed Playwright
persistent profile，供使用者登入 Facebook 或檢查 session。
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from playwright.sync_api import sync_playwright

from facebook_monitor.automation.browser_runtime import BrowserRuntimeOptions
from facebook_monitor.automation.browser_runtime import launch_persistent_context_sync
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.runtime.paths import RuntimePaths
from facebook_monitor.runtime.paths import add_runtime_path_arguments
from facebook_monitor.runtime.paths import resolve_runtime_paths_from_args


START_URL = "https://www.facebook.com/groups/"


def build_parser() -> argparse.ArgumentParser:
    """建立 profile setup CLI parser。"""

    parser = argparse.ArgumentParser(description="Open the dedicated Facebook Monitor profile.")
    add_runtime_path_arguments(parser)
    parser.add_argument(
        "--start-url",
        default=START_URL,
        help="URL opened after the headed browser starts.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint：開啟共用 automation profile 供登入檢查。"""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        paths = resolve_runtime_paths_from_args(args)
    except ValueError as exc:
        parser.error(str(exc))
    paths.ensure_writable_dirs()
    _print_setup_summary(paths)
    with acquire_profile_lease(paths.profile_dir, "setup login"):
        with sync_playwright() as playwright:
            context = launch_persistent_context_sync(
                playwright,
                BrowserRuntimeOptions(
                    profile_dir=paths.profile_dir,
                    headless=False,
                ),
            )
            page = context.new_page()
            page.goto(args.start_url, wait_until="domcontentloaded")
            print("登入視窗已開啟。請登入 Facebook，並確認可以看到目標社團或貼文。")
            print("目前使用的 profile：", paths.profile_dir)
            input("設定完成後回到這裡按 Enter，瀏覽器會關閉...")
            context.close()
    return 0


def _print_setup_summary(paths: RuntimePaths) -> None:
    """列印登入工具使用的核心路徑，避免 profile 與 Web UI 不一致。"""

    print("Facebook Monitor 登入設定")
    print(f"資料目錄：{paths.data_dir}")
    print(f"Profile 目錄：{paths.profile_dir}")


if __name__ == "__main__":
    raise SystemExit(main())
