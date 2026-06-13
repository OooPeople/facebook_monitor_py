# ruff: noqa: E402
"""Debug tool：使用專用 profile 執行背景掃描可行性 probe。"""

from __future__ import annotations

import argparse
import json
import sys
import time
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
from facebook_monitor.core.keyword_rules import evaluate_keyword_rules
from facebook_monitor.facebook.extracted_item import make_item_key
from facebook_monitor.facebook.feed_extractor import ExtractRoundStats
from facebook_monitor.facebook.feed_extractor import collect_items_with_diagnostics
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.runtime.paths import add_runtime_path_arguments
from facebook_monitor.runtime.paths import default_runtime_paths
from facebook_monitor.runtime.paths import resolve_runtime_paths_from_args
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

DEFAULT_RUNTIME_PATHS = default_runtime_paths()
PROFILE_DIR = DEFAULT_RUNTIME_PATHS.profile_dir
LOG_PATH = DEFAULT_RUNTIME_PATHS.logs_dir / "worker_probe.log"
SEEN_PATH = DEFAULT_RUNTIME_PATHS.runtime_dir / "worker_probe_seen_keys.json"


@dataclass(frozen=True)
class ScanResult:
    """保存單輪 worker scan 的可記錄結果。"""

    url: str
    body_chars: int
    scroll_rounds: int
    item_count: int
    new_count: int
    matched_count: int
    include_count: int
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
    """解析 worker probe 的最小 CLI 參數。"""

    global LOG_PATH, PROFILE_DIR, SEEN_PATH
    parser = argparse.ArgumentParser(description="Run a minimal headless Facebook probe.")
    add_runtime_path_arguments(parser, include_unsafe_profile_dir=True)
    parser.add_argument(
        "target_url",
        nargs="?",
        help="Facebook group URL to open in headless mode.",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="Include keyword. Can be repeated or comma-separated.",
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
        "--reset-seen",
        action="store_true",
        help="Clear the local probe seen-key store before scanning.",
    )
    parser.add_argument(
        "--duration-minutes",
        type=float,
        default=0,
        help="Run repeatedly for this many minutes, then stop automatically.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=PYTHON_SCHEDULER_RUNTIME_DEFAULTS.one_shot_interval_seconds,
        help="Seconds to wait between scans in duration mode.",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Log anonymous per-scroll DOM counts for extractor tuning.",
    )
    parser.add_argument(
        "--ntfy-topic",
        default="",
        help="ntfy topic for probe test notifications.",
    )
    parser.add_argument(
        "--ntfy-server",
        default="https://ntfy.sh",
        help="ntfy server base URL.",
    )
    parser.add_argument(
        "--notify-test",
        action="store_true",
        help="Send a standalone ntfy test notification and exit.",
    )
    parser.add_argument(
        "--notify-on-new",
        action="store_true",
        help="Send ntfy summary when new item hashes are detected.",
    )
    args = parser.parse_args()
    paths = resolve_runtime_paths_from_args(args)
    paths.ensure_writable_dirs()
    PROFILE_DIR = paths.profile_dir
    LOG_PATH = paths.logs_dir / "worker_probe.log"
    SEEN_PATH = paths.runtime_dir / "worker_probe_seen_keys.json"
    return args


def normalize_keywords(raw_keywords: list[str]) -> list[str]:
    """將重複或逗號分隔的 include keyword 正規化。"""

    keywords: list[str] = []
    for raw_value in raw_keywords:
        for part in raw_value.split(","):
            keyword = part.strip().lower()
            if keyword:
                keywords.append(keyword)
    return keywords


def load_seen_keys(reset: bool) -> set[str]:
    """載入本機 seen key；只保存 hash，不保存貼文原文。"""

    if reset or not SEEN_PATH.exists():
        return set()
    with SEEN_PATH.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return set(payload.get("keys", []))


def save_seen_keys(keys: set[str]) -> None:
    """保存本輪更新後的 seen key。"""

    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"keys": sorted(keys), "updated_at": datetime.now(timezone.utc).isoformat()}
    with SEEN_PATH.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


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


def run_probe(args: argparse.Namespace) -> ScanResult:
    """執行一次 headless worker probe。"""

    if not args.target_url:
        raise ProbeFailure("unknown", "target_url is required unless --notify-test is used.")

    if not PROFILE_DIR.exists():
        raise ProbeFailure(
            "profile_missing",
            "Profile does not exist yet. Run facebook-monitor-login first: "
            f"{PROFILE_DIR}",
        )

    include_keywords = normalize_keywords(args.include)
    seen_keys = load_seen_keys(args.reset_seen)

    try:
        with acquire_profile_lease(PROFILE_DIR, "debug worker probe"):
            with sync_playwright() as p:
                context = launch_persistent_context_sync(
                    p,
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

                    item_keys = {make_item_key(item) for item in items}
                    new_keys = item_keys - seen_keys
                    seen_keys.update(item_keys)
                    save_seen_keys(seen_keys)
                    matched_count = sum(
                        1
                        for item in items
                        if evaluate_keyword_rules(item.text, include_keywords).eligible
                    )

                    return ScanResult(
                        url=page.url,
                        body_chars=len(body_text),
                        scroll_rounds=max(args.scroll_rounds, 0),
                        item_count=len(items),
                        new_count=len(new_keys),
                        matched_count=matched_count,
                        include_count=len(include_keywords),
                        round_stats=round_stats,
                    )
                finally:
                    context.close()
    except ProfileLeaseError as exc:
        raise ProbeFailure("profile_locked", str(exc)) from exc


def log_scan_result(result: ScanResult, diagnostics: bool) -> None:
    """將單輪 scan 結果寫入 log。"""

    log(
        "opened "
        f"url={result.url!r} "
        f"body_chars={result.body_chars} "
        f"scroll_rounds={result.scroll_rounds} "
        f"item_count={result.item_count} "
        f"new_count={result.new_count} "
        f"matched_count={result.matched_count} "
        f"include_count={result.include_count}"
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


def dispatch_scan_notification(args: argparse.Namespace, result: ScanResult) -> None:
    """在有命中或新項目時送出最小 ntfy 摘要，不包含貼文原文。"""

    if not args.ntfy_topic:
        return
    reasons: list[str] = []
    if result.matched_count > 0:
        reasons.append("matched")
    if args.notify_on_new and result.new_count > 0:
        reasons.append("new")
    if not reasons:
        return

    ntfy_result = send_ntfy_notification(
        config=NtfyConfig(server=args.ntfy_server, topic=args.ntfy_topic),
        title="Facebook probe scan",
        message=(
            "Worker probe detected items.\n"
            f"reason={','.join(reasons)}\n"
            f"matched_count={result.matched_count}\n"
            f"new_count={result.new_count}\n"
            f"item_count={result.item_count}"
        ),
    )
    status = "ok" if ntfy_result.ok else "failed"
    log(
        "notification "
        f"channel='ntfy' status={status!r} "
        f"reason={','.join(reasons)!r} "
        f"status_code={ntfy_result.status_code} "
        f"message={ntfy_result.message!r}"
    )


def run_probe_with_logging(args: argparse.Namespace) -> bool:
    """執行單輪 probe，將成功或失敗寫入 log。"""

    try:
        result = run_probe(args)
        log_scan_result(result, args.diagnostics)
        dispatch_scan_notification(args, result)
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


def send_test_notification(args: argparse.Namespace) -> int:
    """送出一則獨立 ntfy 測試通知。"""

    ntfy_result = send_ntfy_notification(
        config=NtfyConfig(server=args.ntfy_server, topic=args.ntfy_topic),
        title="Facebook probe test",
        message="Worker probe ntfy test notification.",
    )
    status = "ok" if ntfy_result.ok else "failed"
    log(
        "notification_test "
        f"channel='ntfy' status={status!r} "
        f"status_code={ntfy_result.status_code} "
        f"message={ntfy_result.message!r}"
    )
    return 0 if ntfy_result.ok else 2


def run_duration_mode(args: argparse.Namespace) -> int:
    """依指定時長反覆執行 probe，時間到自動停止。"""

    duration_seconds = max(args.duration_minutes, 0) * 60
    interval_seconds = max(args.interval_seconds, 1)
    deadline = time.monotonic() + duration_seconds
    started_at = datetime.now(timezone.utc).isoformat()
    scan_count = 0
    success_count = 0
    failure_count = 0

    log(
        "long_run_start "
        f"started_at={started_at!r} "
        f"duration_minutes={args.duration_minutes} "
        f"interval_seconds={interval_seconds} "
        f"scroll_rounds={max(args.scroll_rounds, 0)} "
        f"max_items={args.max_items}"
    )

    while True:
        scan_count += 1
        log(f"long_run_scan_start scan={scan_count}")
        success = run_probe_with_logging(args)
        if success:
            success_count += 1
        else:
            failure_count += 1
        args.reset_seen = False

        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            break
        sleep_seconds = min(interval_seconds, remaining_seconds)
        log(f"long_run_sleep scan={scan_count} seconds={round(sleep_seconds, 1)}")
        time.sleep(sleep_seconds)

    log(
        "long_run_finish "
        f"scan_count={scan_count} "
        f"success_count={success_count} "
        f"failure_count={failure_count}"
    )
    return 0 if success_count > 0 else 2


def main() -> int:
    """CLI entrypoint：執行單輪或自動計時長測 probe。"""

    args = parse_args()
    if args.notify_test:
        return send_test_notification(args)
    if args.duration_minutes > 0:
        return run_duration_mode(args)
    return 0 if run_probe_with_logging(args) else 2


if __name__ == "__main__":
    raise SystemExit(main())
