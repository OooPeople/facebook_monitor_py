"""Shared scan finalize test helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_finalize import UNGUARDED_SCAN_COMMIT
from facebook_monitor.worker.scan_finalize import finalize_scan_items as _finalize_scan_items
from facebook_monitor.worker.scan_finalize import record_guarded_skipped_scan
from facebook_monitor.worker.scan_finalize import record_unguarded_skipped_scan_for_one_shot
from facebook_monitor.worker.scan_finalize import scan_commit_guard_from_runtime_state


def finalize_scan_items(**kwargs: Any) -> Any:
    """測試預設走明確 unguarded finalize；guard 案例可覆寫 commit_guard。"""

    kwargs.setdefault("commit_guard", UNGUARDED_SCAN_COMMIT)
    return _finalize_scan_items(**kwargs)


def record_protective_skip_for_test(**kwargs: Any) -> Any:
    """測試 helper：依 commit_guard 轉到明確 guarded / unguarded skip API。"""

    kwargs.setdefault("commit_guard", UNGUARDED_SCAN_COMMIT)
    commit_guard = kwargs.pop("commit_guard")
    if commit_guard is None:
        return record_unguarded_skipped_scan_for_one_shot(**kwargs)
    return record_guarded_skipped_scan(commit_guard=commit_guard, **kwargs)


def _activate_target(
    app: ApplicationContext,
    target: TargetDescriptor,
) -> TargetDescriptor:
    """讓 finalize 測試明確模擬正式 worker 正在處理 active target。"""

    activated = app.services.targets.restart_target_monitoring(target.id)
    app.repositories.scan_scope_state.mark_initialized(activated.scope_id)
    return activated


@dataclass(frozen=True)
class RunningTargetFixture:
    """保存已取得 scan admission 的 target 測試資料。"""

    target: TargetDescriptor
    config: TargetConfig
    commit_guard: ScanCommitGuard


def _stub_outbox_dispatch(monkeypatch: Any) -> list[Path]:
    """攔截 after-commit outbox dispatch，避免測試打到外部通知服務。"""

    dispatch_calls: list[Path] = []

    def fake_dispatch(**kwargs: object) -> int:
        db_path = kwargs["db_path"]
        assert isinstance(db_path, Path)
        dispatch_calls.append(db_path)
        return 1

    monkeypatch.setattr(
        "facebook_monitor.notifications.outbox_service.dispatch_new_pending_notification_outbox_for_db",
        fake_dispatch,
    )
    return dispatch_calls


def _create_running_target_with_guard(
    app: ApplicationContext,
    *,
    include_keywords: tuple[str, ...] = (),
) -> RunningTargetFixture:
    """建立 active target、初始化 scope，並回傳目前 running attempt guard。"""

    target = app.services.targets.upsert_group_posts_target(
        UpsertGroupPostsTargetRequest(
            group_id="123",
            canonical_url="https://www.facebook.com/groups/123",
            group_name="測試社團",
            config=TargetConfigPatch(include_keywords=include_keywords),
        )
    )
    target = _activate_target(app, target)
    config = app.services.targets.get_config_for_target(target)
    app.repositories.scan_scope_state.mark_initialized(target.scope_id)
    running_state = app.services.targets.mark_target_running(
        target.id,
        "worker-a",
        page_id="page-a",
    )
    return RunningTargetFixture(
        target=target,
        config=config,
        commit_guard=scan_commit_guard_from_runtime_state(running_state),
    )
