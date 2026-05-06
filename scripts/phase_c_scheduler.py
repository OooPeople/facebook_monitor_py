"""Phase C minimal scheduler CLI。

職責：啟動最小長駐 scheduler loop，依 target 啟停狀態順序掃描多個社團。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.scheduler.loop import SchedulerOptions
from facebook_monitor.scheduler.loop import run_scheduler_loop


DEFAULT_PROFILE_DIR = ROOT / "data" / "profiles" / "phase0_default"
DEFAULT_DB_PATH = ROOT / "data" / "app.db"


def parse_args() -> argparse.Namespace:
    """解析 Phase C scheduler CLI 參數。"""

    parser = argparse.ArgumentParser(description="Run the minimal target scheduler loop.")
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--interval-seconds", type=float, default=300)
    parser.add_argument("--scheduler-tick-seconds", type=float, default=2)
    parser.add_argument("--max-concurrent-scans", type=int, default=2)
    parser.add_argument("--scroll-rounds", type=int, default=3)
    parser.add_argument("--scroll-wait-ms", type=int, default=2500)
    parser.add_argument("--scan-timeout-seconds", type=float, default=120)
    parser.add_argument("--stale-running-after-seconds", type=float, default=180)
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="Stop after N scheduler cycles. Omit to keep running.",
    )
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint：啟動最小 scheduler loop。"""

    args = parse_args()
    summaries = run_scheduler_loop(
        SchedulerOptions(
            profile_dir=args.profile_dir,
            db_path=args.db_path,
            interval_seconds=args.interval_seconds,
            scheduler_tick_seconds=args.scheduler_tick_seconds,
            max_concurrent_scans=args.max_concurrent_scans,
            scroll_rounds=args.scroll_rounds,
            scroll_wait_ms=args.scroll_wait_ms,
            scan_timeout_seconds=args.scan_timeout_seconds,
            stale_running_after_seconds=args.stale_running_after_seconds,
            max_cycles=args.max_cycles,
        )
    )
    for summary in summaries:
        print(
            "Scheduler cycle completed. "
            f"cycle={summary.cycle_index} "
            f"selected={summary.selected_count} "
            f"success={summary.success_count} "
            f"failure={summary.failure_count}"
            f" skipped={summary.skipped_count}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
