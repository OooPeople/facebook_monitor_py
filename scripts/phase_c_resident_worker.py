"""Phase C resident worker CLI。

職責：啟動常駐瀏覽器 worker，重用同一個 Playwright context/page 掃描多個 target。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.worker.async_resident import run_async_resident_worker_loop_sync
from facebook_monitor.worker.resident import ResidentCycleSummary
from facebook_monitor.worker.resident import ResidentWorkerOptions


DEFAULT_PROFILE_DIR = ROOT / "data" / "profiles" / "phase0_default"
DEFAULT_DB_PATH = ROOT / "data" / "app.db"


def parse_args() -> argparse.Namespace:
    """解析 Phase C resident worker CLI 參數。"""

    parser = argparse.ArgumentParser(description="Run the resident group posts worker.")
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--interval-seconds", type=float, default=60)
    parser.add_argument("--scheduler-tick-seconds", type=float, default=2)
    parser.add_argument("--max-concurrent-scans", type=int, default=2)
    parser.add_argument("--scroll-rounds", type=int, default=3)
    parser.add_argument("--scroll-wait-ms", type=int, default=2500)
    parser.add_argument("--scan-timeout-seconds", type=float, default=120)
    parser.add_argument("--stale-running-after-seconds", type=float, default=180)
    parser.add_argument(
        "--headed-compat",
        action="store_true",
        help="Run the resident worker with a visible browser window.",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="Stop after N resident worker cycles. Omit to keep running.",
    )
    return parser.parse_args()


def print_cycle_summary(summary: ResidentCycleSummary) -> None:
    """輸出單輪 resident worker 摘要。"""

    print(
        "Resident worker cycle completed. "
        f"cycle={summary.cycle_index} "
        f"selected={summary.selected_count} "
        f"success={summary.success_count} "
        f"failure={summary.failure_count} "
        f"skipped={summary.skipped_count} "
        f"opened_pages={summary.opened_page_count} "
        f"reused_pages={summary.reused_page_count} "
        f"closed_pages={summary.closed_page_count}",
        flush=True,
    )


def main() -> int:
    """CLI entrypoint：啟動常駐瀏覽器 worker。"""

    args = parse_args()
    run_async_resident_worker_loop_sync(
        ResidentWorkerOptions(
            profile_dir=args.profile_dir,
            db_path=args.db_path,
            interval_seconds=args.interval_seconds,
            scheduler_tick_seconds=args.scheduler_tick_seconds,
            max_concurrent_scans=args.max_concurrent_scans,
            scroll_rounds=args.scroll_rounds,
            scroll_wait_ms=args.scroll_wait_ms,
            scan_timeout_seconds=args.scan_timeout_seconds,
            stale_running_after_seconds=args.stale_running_after_seconds,
            headed_compat=args.headed_compat,
            max_cycles=args.max_cycles,
        ),
        on_cycle=print_cycle_summary,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
