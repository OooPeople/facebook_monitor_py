"""Standalone updater CLI entrypoint。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from facebook_monitor.runtime.paths import add_runtime_path_arguments
from facebook_monitor.runtime.paths import resolve_runtime_paths_from_args
from facebook_monitor.updates.apply import apply_pending_update_file
from facebook_monitor.updates.handoff import load_pending_update
from facebook_monitor.updates.handoff import pending_update_path
from facebook_monitor.updates.launcher import launch_restarted_app


def build_parser() -> argparse.ArgumentParser:
    """建立 updater CLI parser。"""

    parser = argparse.ArgumentParser(description="Apply a verified Facebook Monitor update.")
    add_runtime_path_arguments(parser)
    parser.add_argument(
        "--pending-update",
        type=Path,
        default=None,
        help="Path to pending_update.json. Defaults to <data-dir>/runtime/pending_update.json.",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=0,
        help="Seconds to wait for the main app lock to be released before applying.",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Restart facebook-monitor.exe after the update is applied.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint：套用 pending update。"""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        paths = resolve_runtime_paths_from_args(args)
    except ValueError as exc:
        parser.error(str(exc))
    pending_path = args.pending_update or pending_update_path(paths.runtime_dir)
    result = apply_pending_update_file(
        pending_path,
        wait_for_lock_seconds=float(args.wait_seconds),
        log_path=paths.logs_dir / "updater.log",
    )
    print(f"{result.status}: {result.message}")
    if result.backup_dir is not None:
        print(f"backup: {result.backup_dir}")
    if result.staging_dir is not None:
        print(f"staging: {result.staging_dir}")
    if result.applied and args.restart:
        try:
            pending = load_pending_update(pending_path)
        except (OSError, ValueError) as exc:
            print(f"restart: failed: {exc}")
        else:
            restart = launch_restarted_app(pending)
            print(f"restart: {restart.status}: {restart.message}")
    return 0 if result.applied else 2


if __name__ == "__main__":
    raise SystemExit(main())
