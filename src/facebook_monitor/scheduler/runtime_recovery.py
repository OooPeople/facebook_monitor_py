"""Target runtime recovery helpers。

職責：提供正式 resident main 與 one-shot fallback scheduler 共用的 runtime state
修復規則，避免正式 worker 從 fallback scheduler loop 匯入 helper。
"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext


RETRYABLE_IDLE_FAILURE_REASONS = frozenset({"extractor_empty"})


def recover_stale_running_targets(db_path: Path, stale_after_seconds: float) -> int:
    """修復過舊的 running runtime state，回傳修復筆數。"""

    with SqliteApplicationContext(db_path) as app:
        return len(
            app.services.targets.recover_stale_running_targets(
                stale_after_seconds=stale_after_seconds,
            )
        )


def recover_stale_queued_targets(db_path: Path, stale_after_seconds: float) -> int:
    """修復過舊的 queued runtime state，回傳修復筆數。"""

    with SqliteApplicationContext(db_path) as app:
        return len(
            app.services.targets.recover_stale_queued_targets(
                stale_after_seconds=stale_after_seconds,
            )
        )


def recover_stale_runtime_targets(db_path: Path, stale_after_seconds: float) -> int:
    """修復所有會讓 target 卡住的過舊 runtime state。"""

    with SqliteApplicationContext(db_path) as app:
        queued = app.services.targets.recover_stale_queued_targets(
            stale_after_seconds=stale_after_seconds,
        )
        running = app.services.targets.recover_stale_running_targets(
            stale_after_seconds=stale_after_seconds,
        )
        return len(queued) + len(running)
