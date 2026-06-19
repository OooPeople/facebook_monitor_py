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
from facebook_monitor.notifications.desktop import DesktopNotificationResult
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.discord import DiscordResult
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult
from facebook_monitor.notifications.outbox_dispatch_service import (
    dispatch_new_pending_notification_outbox,
)
from facebook_monitor.notifications.senders import DesktopSender
from facebook_monitor.notifications.senders import DiscordSender
from facebook_monitor.notifications.senders import NtfySender
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


def dispatch_pending_notifications_for_test(
    *,
    app: ApplicationContext,
    ntfy_sender: NtfySender | None = None,
    desktop_sender: DesktopSender | None = None,
    discord_sender: DiscordSender | None = None,
) -> int:
    """測試用 outbox drain；預設 sender 不觸發任何外部通知。"""

    return dispatch_new_pending_notification_outbox(
        app=app,
        ntfy_sender=ntfy_sender or _fake_success_ntfy_sender,
        desktop_sender=desktop_sender or _fake_success_desktop_sender,
        discord_sender=discord_sender or _fake_success_discord_sender,
    )


def _fake_success_ntfy_sender(
    config: NtfyConfig,
    title: str,
    message: str,
) -> NtfyResult:
    """測試預設 ntfy sender：只回傳成功，不連外。"""

    return NtfyResult(ok=True, status_code=200, message="ntfy_sent")


def _fake_success_desktop_sender(
    title: str,
    message: str,
) -> DesktopNotificationResult:
    """測試預設 desktop sender：只回傳成功，不呼叫 OS 通知。"""

    return DesktopNotificationResult(ok=True, status_code=None, message="desktop_sent")


def _fake_success_discord_sender(
    config: DiscordConfig,
    title: str,
    message: str,
) -> DiscordResult:
    """測試預設 Discord sender：只回傳成功，不送 webhook。"""

    return DiscordResult(ok=True, status_code=204, message="discord_sent")


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
    """攔截 after-commit outbox dispatcher wake，避免測試打到外部通知服務。"""

    dispatch_calls: list[Path] = []

    def fake_wake(db_path: Path) -> bool:
        assert isinstance(db_path, Path)
        dispatch_calls.append(db_path)
        return True

    monkeypatch.setattr(
        "facebook_monitor.notifications.outbox_enqueue_service.wake_notification_outbox_dispatcher_for_db",
        fake_wake,
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
