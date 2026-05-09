"""正式日常入口：啟動本機 Web UI 與背景掃描服務。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.webapp.app import DEFAULT_DB_PATH
from facebook_monitor.webapp.app import DEFAULT_PROFILE_DIR
from facebook_monitor.application.services import DEFAULT_WEBUI_FIXED_REFRESH_SECONDS
from facebook_monitor.webapp.app import create_app


def parse_args() -> argparse.Namespace:
    """解析本機 web UI 啟動參數。"""

    parser = argparse.ArgumentParser(description="Run the local FastAPI web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR)
    parser.add_argument(
        "--scheduler-interval-seconds",
        type=float,
        default=DEFAULT_WEBUI_FIXED_REFRESH_SECONDS,
        help="Web UI background scheduler interval seconds.",
    )
    parser.add_argument(
        "--access-log",
        action="store_true",
        help="Print uvicorn HTTP access logs such as static file 304 responses.",
    )
    parser.add_argument(
        "--keep-runtime-data-on-startup",
        action="store_true",
        help=(
            "Keep previous scan/debug runtime data. By default Web UI startup clears "
            "scan_runs, latest_scan_items, match_history, notification_events and seen_items."
        ),
    )
    parser.add_argument(
        "--graceful-shutdown-timeout-seconds",
        type=int,
        default=5,
        help=(
            "Maximum seconds uvicorn waits for long-lived dashboard connections such as SSE "
            "when CTRL+C shuts down the local Web UI."
        ),
    )
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint：啟動 uvicorn server。"""

    args = parse_args()
    app = create_app(
        db_path=args.db_path,
        profile_dir=args.profile_dir,
        auto_start_scheduler=True,
        scheduler_interval_seconds=args.scheduler_interval_seconds,
        reset_targets_on_startup=True,
        reset_runtime_data_on_startup=not args.keep_runtime_data_on_startup,
    )
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        access_log=args.access_log,
        timeout_graceful_shutdown=args.graceful_shutdown_timeout_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
