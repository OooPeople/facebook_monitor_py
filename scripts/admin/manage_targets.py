"""Admin tool：用互動式選單編輯 target 設定與啟停狀態。"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpdateTargetConfigRequest
from facebook_monitor.core.keyword_text import parse_keywords_text
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.runtime.paths import add_runtime_path_arguments
from facebook_monitor.runtime.paths import resolve_runtime_paths_from_args


def parse_args() -> argparse.Namespace:
    """解析互動式設定管理入口的最小參數。"""

    parser = argparse.ArgumentParser(description="Open the interactive target settings manager.")
    add_runtime_path_arguments(parser)
    return parser.parse_args()


def format_keywords(keywords: tuple[str, ...]) -> str:
    """將 keyword tuple 格式化成適合選單顯示的字串。"""

    return ", ".join(keywords) if keywords else "(未設定)"


def parse_yes_no(text: str, current: bool) -> bool:
    """解析 y/n 類型輸入；空白時保留目前值。"""

    normalized = text.strip().lower()
    if not normalized:
        return current
    if normalized in {"y", "yes", "true", "1", "是", "開", "啟用"}:
        return True
    if normalized in {"n", "no", "false", "0", "否", "關", "停用"}:
        return False
    raise ValueError("請輸入 y 或 n")


def prompt_keywords(label: str, current: tuple[str, ...]) -> tuple[str, ...]:
    """提示使用者輸入 keyword list。"""

    print(f"目前{label}: {format_keywords(current)}")
    print("輸入逗號分隔內容；直接 Enter 保留；輸入 - 清空。")
    raw_value = input(f"{label}> ").strip()
    if not raw_value:
        return current
    if raw_value == "-":
        return ()
    return parse_keywords_text(raw_value)


def prompt_int(label: str, current: int, minimum: int = 1) -> int:
    """提示使用者輸入整數設定。"""

    raw_value = input(f"{label} [{current}]> ").strip()
    if not raw_value:
        return current
    value = int(raw_value)
    if value < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    return value


def prompt_ntfy_enabled(current: bool) -> bool:
    """提示使用者輸入 ntfy 啟用狀態。"""

    raw_value = input(f"啟用 ntfy? [{'y' if current else 'n'}]> ")
    return parse_yes_no(raw_value, current)


def prompt_ntfy_topic(current: str) -> str:
    """提示使用者輸入 ntfy topic。"""

    raw_value = input(f"ntfy topic [{current or '未設定'}]> ").strip()
    return raw_value or current


def prompt_text(label: str, current: str) -> str:
    """提示使用者輸入文字設定；空白時保留目前值。"""

    raw_value = input(f"{label} [{current or '未設定'}]> ").strip()
    return raw_value or current


def redact_secret(value: str) -> str:
    """CLI 預設不完整印出 webhook 這類 token-like 設定。"""

    text = value.strip()
    if not text:
        return "(未設定)"
    if len(text) <= 12:
        return "***"
    return f"{text[:6]}...{text[-4:]}"


def print_targets(targets: list[TargetDescriptor], app: ApplicationContext) -> None:
    """列出目前所有 target 與摘要設定。"""

    print("\nTargets")
    print("=======")
    for index, target in enumerate(targets, start=1):
        config = app.repositories.configs.get_for_target(target) or TargetConfig(
            target_id=target.id
        )
        status = "paused" if target.paused else "enabled" if target.enabled else "disabled"
        print(f"{index}. {target.group_name or target.name}")
        print(f"   group_id={target.group_id} status={status}")
        print(f"   include={format_keywords(config.include_keywords)}")
        print(f"   exclude={format_keywords(config.exclude_keywords)}")
        print(
            "   "
            f"auto_load_more={'on' if config.auto_load_more else 'off'} "
            f"auto_adjust_sort={'on' if config.auto_adjust_sort else 'off'}"
        )
        print(
            "   "
            f"desktop={'on' if config.enable_desktop_notification else 'off'} "
            f"ntfy={'on' if config.enable_ntfy else 'off'} "
            f"discord={'on' if config.enable_discord_notification else 'off'}"
        )
        print(
            "   "
            f"ntfy_topic={redact_secret(config.ntfy_topic)} "
            f"discord_webhook={redact_secret(config.discord_webhook)}"
        )


def choose_target(targets: list[TargetDescriptor]) -> TargetDescriptor | None:
    """讓使用者用編號選擇 target。"""

    raw_value = input("\n選擇 target 編號，或 q 離開> ").strip().lower()
    if raw_value in {"q", "quit", "exit"}:
        return None
    selected_index = int(raw_value)
    if selected_index < 1 or selected_index > len(targets):
        raise ValueError("target 編號超出範圍")
    return targets[selected_index - 1]


def choose_target_action() -> str:
    """讓使用者選擇單一 target 的設定或啟停操作。"""

    print("\nTarget actions")
    print("1. 編輯設定")
    print("2. 啟動監視")
    print("3. 停止監視")
    print("b. 返回 target 清單")
    return input("選擇操作> ").strip().lower()


def run_target_action(app: ApplicationContext, target: TargetDescriptor, action: str) -> None:
    """執行單一 target 的互動操作。"""

    if action == "1":
        edit_target_config(app, target)
        return
    if action == "2":
        updated_target = app.services.targets.restart_target_monitoring(target.id)
        print(f"已啟動: {updated_target.group_name or updated_target.name}")
        return
    if action == "3":
        updated_target = app.services.targets.pause_target_monitoring(target.id)
        print(f"已停止: {updated_target.group_name or updated_target.name}")
        return
    if action in {"b", "back", ""}:
        return
    raise ValueError("請輸入 1、2、3 或 b")


def edit_target_config(app: ApplicationContext, target: TargetDescriptor) -> TargetConfig:
    """互動式編輯單一 target config。"""

    current = app.repositories.configs.get_for_target(target) or TargetConfig(
        target_id=target.id
    )
    print(f"\n正在編輯: {target.group_name or target.name}")
    include_keywords = prompt_keywords("include keywords", current.include_keywords)
    exclude_keywords = prompt_keywords("exclude keywords", current.exclude_keywords)
    fixed_refresh_sec = prompt_int("scan interval seconds", current.fixed_refresh_sec or 60)
    max_items_per_scan = prompt_int("max items per scan", current.max_items_per_scan)
    auto_load_more = parse_yes_no(
        input(f"自動載入更多? [{'y' if current.auto_load_more else 'n'}]> "),
        current.auto_load_more,
    )
    auto_adjust_sort = parse_yes_no(
        input(f"自動調整最新排序? [{'y' if current.auto_adjust_sort else 'n'}]> "),
        current.auto_adjust_sort,
    )
    enable_desktop_notification = parse_yes_no(
        input(f"啟用桌面通知? [{'y' if current.enable_desktop_notification else 'n'}]> "),
        current.enable_desktop_notification,
    )
    enable_ntfy = prompt_ntfy_enabled(current.enable_ntfy)
    ntfy_topic = prompt_ntfy_topic(current.ntfy_topic)
    enable_discord_notification = parse_yes_no(
        input(f"啟用 Discord 通知? [{'y' if current.enable_discord_notification else 'n'}]> "),
        current.enable_discord_notification,
    )
    discord_webhook = prompt_text("Discord webhook URL", current.discord_webhook)

    config = app.services.targets.update_target_config(
        UpdateTargetConfigRequest(
            target_id=target.id,
            config=TargetConfigPatch(
                include_keywords=include_keywords,
                exclude_keywords=exclude_keywords,
                fixed_refresh_sec=fixed_refresh_sec,
                max_items_per_scan=max_items_per_scan,
                auto_load_more=auto_load_more,
                auto_adjust_sort=auto_adjust_sort,
                enable_desktop_notification=enable_desktop_notification,
                enable_ntfy=enable_ntfy,
                ntfy_topic=ntfy_topic,
                enable_discord_notification=enable_discord_notification,
                discord_webhook=discord_webhook,
            ),
        )
    )
    print("設定已保存。")
    return config


def run_manager(db_path: Path) -> int:
    """執行互動式設定管理 loop。"""

    with SqliteApplicationContext(db_path) as app:
        while True:
            targets = app.repositories.targets.list_all()
            if not targets:
                print("目前沒有 target。請先使用 Web UI 或 debug/capture_posts_target.py 新增社團。")
                return 0
            print_targets(targets, app)
            try:
                target = choose_target(targets)
                if target is None:
                    return 0
                run_target_action(app, target, choose_target_action())
            except ValueError as exc:
                print(f"ERROR: {exc}")


def main() -> int:
    """CLI entrypoint：開啟互動式設定管理。"""

    args = parse_args()
    paths = resolve_runtime_paths_from_args(args)
    return run_manager(paths.db_path)


if __name__ == "__main__":
    raise SystemExit(main())
