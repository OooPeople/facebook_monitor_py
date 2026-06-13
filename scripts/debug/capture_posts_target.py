"""Debug tool：開啟瀏覽器並擷取目前社團頁作為 posts target。"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.application.target_requests import UNSET_CONFIG_VALUE
from facebook_monitor.automation.browser_runtime import BrowserRuntimeOptions
from facebook_monitor.automation.browser_runtime import launch_persistent_context_sync
from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.facebook.browser_capture import get_start_page
from facebook_monitor.facebook.browser_capture import select_capture_route
from facebook_monitor.facebook.browser_capture import snapshot_browser_pages
from facebook_monitor.facebook.route_detection import RouteDetectionError
from facebook_monitor.runtime.paths import add_runtime_path_arguments
from facebook_monitor.runtime.paths import default_runtime_paths
from facebook_monitor.runtime.paths import resolve_runtime_paths_from_args


DEFAULT_RUNTIME_PATHS = default_runtime_paths()
DEFAULT_PROFILE_DIR = DEFAULT_RUNTIME_PATHS.profile_dir
DEFAULT_DB_PATH = DEFAULT_RUNTIME_PATHS.db_path
DEFAULT_START_URL = "https://www.facebook.com/groups/"


@dataclass(frozen=True)
class CaptureOptions:
    """保存 group posts capture 執行選項。"""

    profile_dir: Path = DEFAULT_PROFILE_DIR
    db_path: Path = DEFAULT_DB_PATH
    start_url: str = DEFAULT_START_URL
    include_keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    fixed_refresh_sec: int | None = None
    max_items_per_scan: int | None = None
    enable_desktop_notification: bool = False
    enable_ntfy: bool = False
    ntfy_topic: str = ""
    enable_discord_notification: bool = False
    discord_webhook: str = ""


def parse_keyword_args(values: list[str]) -> tuple[str, ...]:
    """將多次或逗號分隔的 CLI keyword 參數整理成 tuple。"""

    keywords: list[str] = []
    for value in values:
        for keyword in value.split(","):
            cleaned = keyword.strip()
            if cleaned:
                keywords.append(cleaned)
    return tuple(dict.fromkeys(keywords))


def parse_args() -> argparse.Namespace:
    """解析 group posts capture CLI 參數。"""

    parser = argparse.ArgumentParser(
        description="Capture the current Facebook group feed as a persisted target.",
    )
    add_runtime_path_arguments(parser, include_unsafe_profile_dir=True)
    parser.add_argument(
        "--start-url",
        default=DEFAULT_START_URL,
        help=(
            "Initial URL opened in the headed browser. Defaults to the neutral "
            "Facebook groups entry so the target group is chosen manually."
        ),
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="Include keyword. Can be repeated or comma-separated.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Exclude keyword. Can be repeated or comma-separated.",
    )
    parser.add_argument(
        "--scan-interval-seconds",
        type=int,
        default=None,
        help=(
            "Fixed scan interval seconds. Omit to keep existing config, or use "
            "the project default for a new target."
        ),
    )
    parser.add_argument(
        "--max-items-per-scan",
        type=int,
        default=None,
        help=(
            "Maximum extracted items kept per scan. Omit to keep existing config, "
            "or use the project default for a new target."
        ),
    )
    parser.add_argument(
        "--enable-desktop-notification",
        action="store_true",
        help="Enable local desktop notification for this target.",
    )
    parser.add_argument(
        "--enable-ntfy",
        action="store_true",
        help="Enable ntfy notification for this target.",
    )
    parser.add_argument(
        "--ntfy-topic",
        default="",
        help="ntfy topic name.",
    )
    parser.add_argument(
        "--enable-discord",
        action="store_true",
        help="Enable Discord webhook notification for this target.",
    )
    parser.add_argument(
        "--discord-webhook",
        default="",
        help="Discord webhook URL.",
    )
    return parser.parse_args()


def run_capture(options: CaptureOptions) -> int:
    """執行 headed capture，成功時回傳 0。"""

    if options.enable_ntfy and not options.ntfy_topic.strip():
        print("ERROR: --enable-ntfy requires --ntfy-topic")
        return 2
    if options.enable_discord_notification and not options.discord_webhook.strip():
        print("ERROR: --enable-discord requires --discord-webhook")
        return 2

    options.profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        with acquire_profile_lease(options.profile_dir, "capture group posts"):
            with sync_playwright() as playwright:
                context = launch_persistent_context_sync(
                    playwright,
                    BrowserRuntimeOptions(
                        profile_dir=options.profile_dir,
                        headless=False,
                    ),
                )
                page = get_start_page(context)
                page.goto(options.start_url, wait_until="domcontentloaded")

                print("Browser is open at the Facebook groups entry.")
                print("Choose any group you want to monitor, then open that group's feed.")
                print("The address bar must be https://www.facebook.com/groups/<group_id>.")
                print("Do not capture /groups/feed or a single post permalink.")
                input("Press Enter here after the target group feed is visible...")

                snapshots = snapshot_browser_pages(context.pages)
                context.close()
    except ProfileLeaseError as exc:
        print(f"ERROR: {exc}")
        return 2

    try:
        selection = select_capture_route(snapshots)
    except RouteDetectionError as exc:
        print(f"ERROR: {exc}")
        return 1
    route = selection.route

    with SqliteApplicationContext(options.db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id=route.group_id,
                canonical_url=route.canonical_url,
                group_name=route.group_name,
                config=TargetConfigPatch(
                    include_keywords=(
                        options.include_keywords
                        if options.include_keywords
                        else UNSET_CONFIG_VALUE
                    ),
                    exclude_keywords=(
                        options.exclude_keywords
                        if options.exclude_keywords
                        else UNSET_CONFIG_VALUE
                    ),
                    fixed_refresh_sec=(
                        options.fixed_refresh_sec
                        if options.fixed_refresh_sec is not None
                        else UNSET_CONFIG_VALUE
                    ),
                    max_items_per_scan=(
                        options.max_items_per_scan
                        if options.max_items_per_scan is not None
                        else UNSET_CONFIG_VALUE
                    ),
                    enable_desktop_notification=(
                        True if options.enable_desktop_notification else UNSET_CONFIG_VALUE
                    ),
                    enable_ntfy=True if options.enable_ntfy else UNSET_CONFIG_VALUE,
                    ntfy_topic=options.ntfy_topic.strip()
                    if options.ntfy_topic.strip()
                    else UNSET_CONFIG_VALUE,
                    enable_discord_notification=(
                        True if options.enable_discord_notification else UNSET_CONFIG_VALUE
                    ),
                    discord_webhook=options.discord_webhook.strip()
                    if options.discord_webhook.strip()
                    else UNSET_CONFIG_VALUE,
                ),
            )
        )
        config = app.repositories.configs.get_for_target(target)

    print("Captured group posts target.")
    print(f"Captured from page {selection.snapshot.page_index}: {selection.source_url}")
    if selection.valid_count > 1:
        print(f"Warning: {selection.valid_count} valid group pages were detected; selected the last one.")
    print(f"Target id: {target.id}")
    print(f"Group id: {target.group_id}")
    print(f"Canonical URL: {target.canonical_url}")
    if route.group_name:
        print(f"Group name: {route.group_name}")
    if config:
        print(f"Include keywords: {', '.join(config.include_keywords) or '(none)'}")
        print(f"desktop notification enabled: {config.enable_desktop_notification}")
        print(f"ntfy enabled: {config.enable_ntfy}")
        print(f"discord enabled: {config.enable_discord_notification}")
    print(f"Database: {options.db_path}")
    return 0


def main() -> int:
    """CLI entrypoint：解析參數後執行 headed capture。"""

    args = parse_args()
    paths = resolve_runtime_paths_from_args(args)
    return run_capture(
        CaptureOptions(
            profile_dir=paths.profile_dir,
            db_path=paths.db_path,
            start_url=args.start_url,
            include_keywords=parse_keyword_args(args.include),
            exclude_keywords=parse_keyword_args(args.exclude),
            fixed_refresh_sec=args.scan_interval_seconds,
            max_items_per_scan=args.max_items_per_scan,
            enable_desktop_notification=args.enable_desktop_notification,
            enable_ntfy=args.enable_ntfy,
            ntfy_topic=args.ntfy_topic,
            enable_discord_notification=args.enable_discord,
            discord_webhook=args.discord_webhook,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
