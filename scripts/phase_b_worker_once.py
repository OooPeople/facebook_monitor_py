"""Phase B one-shot group posts worker CLI。

職責：解析 CLI 參數，呼叫正式 worker runner 執行一次已保存 target 掃描。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.worker.group_posts import WorkerFailure
from facebook_monitor.worker.runner import WorkerOnceOptions
from facebook_monitor.worker.runner import run_worker_once


DEFAULT_PROFILE_DIR = ROOT / "data" / "profiles" / "phase0_default"
DEFAULT_DB_PATH = ROOT / "data" / "app.db"


def parse_args() -> argparse.Namespace:
    """解析 Phase B one-shot worker CLI 參數。"""

    parser = argparse.ArgumentParser(description="Run one persisted group posts target scan.")
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--target-id",
        default="",
        help="Target id to scan. Defaults to first enabled posts target.",
    )
    parser.add_argument(
        "--group-id",
        default="",
        help="Facebook group id to scan. Useful when multiple group targets are saved.",
    )
    parser.add_argument("--scroll-rounds", type=int, default=3)
    parser.add_argument("--scroll-wait-ms", type=int, default=2500)
    parser.add_argument("--scan-timeout-seconds", type=float, default=120)
    parser.add_argument("--headed-compat", action="store_true", help="Run with a visible browser window.")
    return parser.parse_args()


def run_worker_once_cli(options: WorkerOnceOptions) -> int:
    """執行 one-shot worker 並輸出 CLI 摘要。"""

    try:
        summary = run_worker_once(options)
    except WorkerFailure as error:
        print(f"ERROR: {error.reason}: {error}")
        return 2

    print("Worker scan completed.")
    print(f"Target id: {summary.target_id}")
    print(f"URL: {summary.url}")
    print(f"item_count={summary.item_count}")
    print(f"new_count={summary.new_count}")
    print(f"matched_count={summary.matched_count}")
    print(f"scan_run_id={summary.scan_run_id}")
    return 0


def main() -> int:
    """CLI entrypoint：解析參數後執行一次已保存 target 掃描。"""

    args = parse_args()
    return run_worker_once_cli(
        WorkerOnceOptions(
            profile_dir=args.profile_dir,
            db_path=args.db_path,
            target_id=args.target_id,
            group_id=args.group_id,
            scroll_rounds=args.scroll_rounds,
            scroll_wait_ms=args.scroll_wait_ms,
            headed_compat=args.headed_compat,
            scan_timeout_seconds=args.scan_timeout_seconds,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
