"""Internal tool：直接啟動 resident main worker，不作為日常入口。"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.worker.resident_main import run_resident_main_loop_sync
from facebook_monitor.worker.resident_shared import ResidentCycleSummary
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.runtime.paths import add_runtime_path_arguments
from facebook_monitor.runtime.paths import default_runtime_paths
from facebook_monitor.runtime.paths import resolve_runtime_paths_from_args


DEFAULT_RUNTIME_PATHS = default_runtime_paths()
DEFAULT_PROFILE_DIR = DEFAULT_RUNTIME_PATHS.profile_dir
DEFAULT_DB_PATH = DEFAULT_RUNTIME_PATHS.db_path


def parse_args() -> argparse.Namespace:
    """解析 internal resident main worker CLI 參數。"""

    parser = argparse.ArgumentParser(description="Run the resident main worker.")
    add_runtime_path_arguments(parser, include_unsafe_profile_dir=True)
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
        help="Run the resident main worker with a visible browser window.",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="Stop after N resident main worker cycles. Omit to keep running.",
    )
    return parser.parse_args()


def print_cycle_summary(summary: ResidentCycleSummary) -> None:
    """輸出單輪 resident main worker 摘要。"""

    print(
        "Resident main cycle completed. "
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
    paths = resolve_runtime_paths_from_args(args)
    run_resident_main_loop_sync(
        ResidentRuntimeOptions(
            profile_dir=paths.profile_dir,
            db_path=paths.db_path,
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
