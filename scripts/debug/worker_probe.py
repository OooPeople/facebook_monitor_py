# ruff: noqa: E402
"""Debug tool：DB-free headless Facebook extractor probe。

本工具只用於快速分流 profile/session、page load 與 extractor 問題；不寫正式
DB、不保存 seen state、不執行 scheduler / notification pipeline。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.automation.browser_runtime import BrowserRuntimeOptions
from facebook_monitor.automation.browser_runtime import launch_persistent_context_sync
from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.facebook.feed_extractor import ExtractRoundStats
from facebook_monitor.facebook.feed_extractor import collect_items_with_diagnostics
from facebook_monitor.runtime.paths import add_runtime_path_arguments
from facebook_monitor.runtime.paths import default_runtime_paths
from facebook_monitor.runtime.paths import resolve_runtime_paths_from_args
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

DEFAULT_RUNTIME_PATHS = default_runtime_paths()
PROFILE_DIR = DEFAULT_RUNTIME_PATHS.profile_dir
LOG_PATH = DEFAULT_RUNTIME_PATHS.logs_dir / "worker_probe.log"


@dataclass(frozen=True)
class ProbeResult:
    """保存單輪 extractor probe 的可記錄結果。"""

    url: str
    body_chars: int
    scroll_rounds: int
    item_count: int
    round_stats: list[ExtractRoundStats]


class ProbeFailure(RuntimeError):
    """保存 probe 的失敗分類與可記錄訊息。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def log(message: str) -> None:
    """將 probe 結果寫到 console 與本機 runtime log。"""

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"{timestamp} {message}"
    print(line)
    with LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(line + "\n")


def parse_args() -> argparse.Namespace:
    """解析 DB-free extractor probe CLI 參數。"""

    global LOG_PATH, PROFILE_DIR
    parser = argparse.ArgumentParser(
        description=(
            "Run a DB-free headless Facebook extractor probe. "
            "This checks profile/session/page load/extractor only."
        )
    )
    add_runtime_path_arguments(parser, include_unsafe_profile_dir=True)
    parser.add_argument(
        "target_url",
        help="Facebook group URL to open in headless mode.",
    )
    parser.add_argument("--max-items", type=int, default=12, help="Maximum extracted items.")
    parser.add_argument(
        "--scroll-rounds",
        type=int,
        default=0,
        help="Number of feed scroll rounds before finalizing extracted items.",
    )
    parser.add_argument(
        "--scroll-wait-ms",
        type=int,
        default=PYTHON_SCHEDULER_RUNTIME_DEFAULTS.scroll_wait_ms,
        help="Milliseconds to wait after each scroll round.",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Log anonymous per-scroll DOM counts for extractor tuning.",
    )
    args = parser.parse_args()
    paths = resolve_runtime_paths_from_args(args)
    paths.ensure_writable_dirs()
    PROFILE_DIR = paths.profile_dir
    LOG_PATH = paths.logs_dir / "worker_probe.log"
    return args


def classify_playwright_exception(error: Exception) -> str:
    """將 Playwright 或環境例外轉成 probe 失敗分類。"""

    message = str(error).lower()
    if "user data directory is already in use" in message or "processsingleton" in message:
        return "profile_locked"
    if "timeout" in message:
        return "page_load_timeout"
    if "net::" in message or "navigation" in message:
        return "page_load_timeout"
    return "unknown"


def run_probe(args: argparse.Namespace) -> ProbeResult:
    """執行一次 DB-free headless extractor probe。"""

    if not PROFILE_DIR.exists():
        raise ProbeFailure(
            "profile_missing",
            "Profile does not exist yet. Run facebook-monitor-login first: "
            f"{PROFILE_DIR}",
        )

    try:
        with acquire_profile_lease(PROFILE_DIR, "debug extractor probe"):
            with sync_playwright() as playwright:
                context = launch_persistent_context_sync(
                    playwright,
                    BrowserRuntimeOptions(
                        profile_dir=PROFILE_DIR,
                        headless=True,
                    ),
                )
                try:
                    page = context.new_page()
                    page.goto(args.target_url, wait_until="domcontentloaded")
                    page.wait_for_timeout(5000)
                    body_text = page.locator("body").inner_text(timeout=10000)
                    if "log into facebook" in body_text.lower() or "登入 facebook" in body_text.lower():
                        raise ProbeFailure("login_required", "Facebook login is required.")

                    items, round_stats, _collection_meta = collect_items_with_diagnostics(
                        page=page,
                        max_items=args.max_items,
                        scroll_rounds=args.scroll_rounds,
                        scroll_wait_ms=args.scroll_wait_ms,
                    )
                    if not items:
                        raise ProbeFailure("extractor_empty", "No post-like items were extracted.")

                    return ProbeResult(
                        url=page.url,
                        body_chars=len(body_text),
                        scroll_rounds=max(args.scroll_rounds, 0),
                        item_count=len(items),
                        round_stats=round_stats,
                    )
                finally:
                    context.close()
    except ProfileLeaseError as exc:
        raise ProbeFailure("profile_locked", str(exc)) from exc


def log_probe_result(result: ProbeResult, diagnostics: bool) -> None:
    """將單輪 probe 結果寫入 log。"""

    log(
        "opened "
        f"url={result.url!r} "
        f"body_chars={result.body_chars} "
        f"scroll_rounds={result.scroll_rounds} "
        f"item_count={result.item_count}"
    )
    if not diagnostics:
        return
    for stat in result.round_stats:
        log(
            "extractor_round "
            f"round={stat.round_index} "
            f"raw_item_count={stat.raw_item_count} "
            f"unique_item_count={stat.unique_item_count} "
            f"scroll_y={stat.scroll_y} "
            f"scroll_height={stat.scroll_height}"
        )


def run_probe_with_logging(args: argparse.Namespace) -> bool:
    """執行單輪 probe，將成功或失敗寫入 log。"""

    try:
        result = run_probe(args)
        log_probe_result(result, args.diagnostics)
    except ProbeFailure as error:
        log(f"failed reason={error.reason} message={str(error)!r}")
        return False
    except (PlaywrightTimeoutError, PlaywrightError) as error:
        reason = classify_playwright_exception(error)
        log(f"failed reason={reason} message={str(error)!r}")
        return False
    except Exception as error:
        log(f"failed reason=unknown message={str(error)!r}")
        return False
    return True


def main() -> int:
    """CLI entrypoint：執行單輪 DB-free extractor probe。"""

    args = parse_args()
    return 0 if run_probe_with_logging(args) else 2


if __name__ == "__main__":
    raise SystemExit(main())
