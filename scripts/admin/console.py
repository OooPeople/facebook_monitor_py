"""Admin tool：提供低頻互動式 target 管理與一次性掃描選單。"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.debug.capture_posts_target import CaptureOptions
from scripts.debug.capture_posts_target import DEFAULT_START_URL
from scripts.debug.capture_posts_target import run_capture
from scripts.admin.manage_targets import run_manager
from scripts.debug.one_shot_scan import run_one_shot_scan_cli
from facebook_monitor.worker.one_shot_dispatch import OneShotScanOptions
from facebook_monitor.runtime.paths import add_runtime_path_arguments
from facebook_monitor.runtime.paths import default_runtime_paths
from facebook_monitor.runtime.paths import resolve_runtime_paths_from_args


DEFAULT_RUNTIME_PATHS = default_runtime_paths()
DEFAULT_PROFILE_DIR = DEFAULT_RUNTIME_PATHS.profile_dir
DEFAULT_DB_PATH = DEFAULT_RUNTIME_PATHS.db_path


def parse_args() -> argparse.Namespace:
    """解析單一 console 入口的共用參數。"""

    parser = argparse.ArgumentParser(description="Open the Facebook monitor admin console.")
    add_runtime_path_arguments(parser)
    parser.add_argument("--start-url", default=DEFAULT_START_URL)
    return parser.parse_args()


def print_menu() -> None:
    """印出 admin console 主選單。"""

    print("\nAdmin Console")
    print("=============")
    print("1. 新增/保存社團 target")
    print("2. 編輯/啟停 target")
    print("3. 執行一次掃描")
    print("q. 離開")


def prompt_scan_group_id() -> str:
    """提示使用者輸入要掃描的 group id；空白時使用預設 target。"""

    return input("要掃描的 group id（直接 Enter 使用第一個有效 target）> ").strip()


def run_console(profile_dir: Path, db_path: Path, start_url: str) -> int:
    """執行 admin console 主迴圈。"""

    while True:
        print_menu()
        choice = input("選擇操作> ").strip().lower()
        if choice in {"q", "quit", "exit"}:
            return 0
        if choice == "1":
            run_capture(
                CaptureOptions(
                    profile_dir=profile_dir,
                    db_path=db_path,
                    start_url=start_url,
                )
            )
            continue
        if choice == "2":
            run_manager(db_path)
            continue
        if choice == "3":
            group_id = prompt_scan_group_id()
            run_one_shot_scan_cli(
                OneShotScanOptions(
                    profile_dir=profile_dir,
                    db_path=db_path,
                    group_id=group_id,
                )
            )
            continue
        print("ERROR: 請輸入 1、2、3 或 q")


def main() -> int:
    """CLI entrypoint：開啟低頻管理用互動入口。"""

    args = parse_args()
    paths = resolve_runtime_paths_from_args(args)
    return run_console(paths.profile_dir, paths.db_path, args.start_url)


if __name__ == "__main__":
    raise SystemExit(main())
