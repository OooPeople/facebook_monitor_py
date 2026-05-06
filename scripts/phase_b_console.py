"""Phase B single-command console。

職責：提供單一互動入口，把新增 target、編輯設定與執行一次掃描串在同一個選單中。
既有細分 script 仍保留供除錯與自動化使用。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.phase_b_capture_group_posts import CaptureOptions
from scripts.phase_b_capture_group_posts import DEFAULT_START_URL
from scripts.phase_b_capture_group_posts import run_capture
from scripts.phase_b_manage_targets import run_manager
from scripts.phase_b_worker_once import run_worker_once_cli
from facebook_monitor.worker.runner import WorkerOnceOptions


DEFAULT_PROFILE_DIR = ROOT / "data" / "profiles" / "phase0_default"
DEFAULT_DB_PATH = ROOT / "data" / "app.db"


def parse_args() -> argparse.Namespace:
    """解析單一 console 入口的共用參數。"""

    parser = argparse.ArgumentParser(description="Open the Phase B Facebook monitor console.")
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--start-url", default=DEFAULT_START_URL)
    return parser.parse_args()


def print_menu() -> None:
    """印出 Phase B console 主選單。"""

    print("\nPhase B Console")
    print("===============")
    print("1. 新增/保存社團 target")
    print("2. 編輯/啟停 target")
    print("3. 執行一次掃描")
    print("q. 離開")


def prompt_scan_group_id() -> str:
    """提示使用者輸入要掃描的 group id；空白時使用預設 target。"""

    return input("要掃描的 group id（直接 Enter 使用第一個有效 target）> ").strip()


def run_console(profile_dir: Path, db_path: Path, start_url: str) -> int:
    """執行 Phase B console 主迴圈。"""

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
            run_worker_once_cli(
                WorkerOnceOptions(
                    profile_dir=profile_dir,
                    db_path=db_path,
                    group_id=group_id,
                )
            )
            continue
        print("ERROR: 請輸入 1、2、3 或 q")


def main() -> int:
    """CLI entrypoint：開啟 Phase B 單一互動入口。"""

    args = parse_args()
    return run_console(args.profile_dir, args.db_path, args.start_url)


if __name__ == "__main__":
    raise SystemExit(main())
