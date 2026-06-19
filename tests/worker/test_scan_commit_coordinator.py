"""Scan commit coordinator tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import TargetApplicationService
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationEventKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.scan_failure_policy import ScanFailureSource
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_STOPPING_REASON
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.core.scan_failures import TARGET_STOPPED_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult
from facebook_monitor.notifications.outbox_enqueue_service import (
    build_notification_idempotency_key,
)
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.scan_commit_coordinator import commit_failure_request_for_db_async
from facebook_monitor.worker.scan_commit_coordinator import commit_guarded_protective_skip
from facebook_monitor.worker.scan_commit_coordinator import commit_success
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcome
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcomeKind
from facebook_monitor.worker.scan_commit_requests import FailureScanCommitRequest
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_finalize import NormalizedScanItem
from facebook_monitor.worker.scan_finalize import scan_commit_guard_from_runtime_state
from facebook_monitor.worker.scan_failure_finalize import record_guarded_scan_failure_result
from facebook_monitor.worker.scan_pipeline_results import ProtectiveSkipScanResult
from facebook_monitor.worker.scan_pipeline_results import SuccessScanResult

from tests.worker.scan_finalize_test_helpers import _activate_target
from tests.worker.scan_finalize_test_helpers import _create_running_target_with_guard
from tests.worker.scan_finalize_test_helpers import _stub_outbox_dispatch


async def _commit_failure_request_for_test(
    *,
    db_path: Path,
    target_id: str,
    reason: str,
    message: str,
    source: ScanFailureSource,
    worker_path: str,
    commit_guard: ScanCommitGuard,
    exception_class: str = "",
) -> ScanCommitOutcome:
    """測試用薄 helper：讓案例都走 typed request commit path。"""

    return await commit_failure_request_for_db_async(
        FailureScanCommitRequest(
            db_path=db_path,
            target_id=target_id,
            reason=reason,
            message=message,
            source=source,
            worker_path=worker_path,
            commit_guard=commit_guard,
            exception_class=exception_class,
        )
    )


def _assert_no_visible_scan_writes(app: ApplicationContext, target_id: str) -> None:
    """確認 validation / guard rejection 沒寫 visible scan state 或 outbox。"""

    latest_scan = app.repositories.scan_runs.latest_by_target(target_id)
    latest_items = app.repositories.latest_scan_items.list_by_target(target_id)
    history = app.repositories.match_history.list_by_target(target_id)
    pending_outbox = app.repositories.notification_outbox.list_pending()

    assert latest_scan is None
    assert latest_items == []
    assert history == []
    assert pending_outbox == []


def test_scan_commit_coordinator_commits_success_and_idle(
    tmp_path: Path,
) -> None:
    """success coordinator 擁有 finalize writes 與 guarded idle commit。"""

    db_path = tmp_path / "app.db"
    sent_messages: list[str] = []

    def fake_ntfy_sender(_config: NtfyConfig, _title: str, message: str) -> NtfyResult:
        """記錄 success coordinator after-commit 通知。"""

        sent_messages.append(message)
        return NtfyResult(ok=True, status_code=200, message="sent")

    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app, include_keywords=("票券",))
        config = replace(
            fixture.config,
            enable_ntfy=True,
            ntfy_topic="phase6-success",
        )
        outcome = commit_success(
            app=app,
            target=fixture.target,
            config=config,
            result=SuccessScanResult(
                target_id=fixture.target.id,
                url=fixture.target.canonical_url,
                items=(
                    NormalizedScanItem(
                        item_kind=ItemKind.POST,
                        item_key="post:coordinator-success",
                        alias_keys=("post:coordinator-success",),
                        group_id=fixture.target.group_id,
                        author="作者",
                        text="這是一篇票券貼文",
                        permalink=f"{fixture.target.canonical_url}/posts/1",
                    ),
                ),
                item_count=1,
                metadata={"worker": "phase6"},
            ),
            commit_guard=fixture.commit_guard,
            notification_sender=fake_ntfy_sender,
        )
        state = app.repositories.runtime_states.get(fixture.target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(fixture.target.id)
        history = app.repositories.match_history.list_by_target(fixture.target.id)
        outbox_entry = app.repositories.notification_outbox.get_by_idempotency_key(
            build_notification_idempotency_key(
                target_id=fixture.target.id,
                item_key="post:coordinator-success",
                channel=NotificationChannel.NTFY,
            )
        )

    assert outcome.kind == ScanCommitOutcomeKind.SUCCESS_COMMITTED
    assert outcome.committed_visible_scan_state is True
    assert outcome.scan_run_id > 0
    assert outcome.new_count == 1
    assert outcome.matched_count == 1
    assert outcome.side_effects.wrote_scan_run is True
    assert outcome.side_effects.wrote_latest_scan is True
    assert outcome.side_effects.cleared_latest_scan is False
    assert outcome.side_effects.wrote_match_history is True
    assert outcome.side_effects.enqueued_match_notification_outbox is True
    assert outcome.side_effects.enqueued_runtime_failure_notification_outbox is False
    assert outcome.side_effects.updated_scope_state is True
    assert outcome.side_effects.updated_runtime_state is True
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.active_worker_id == ""
    assert state.active_page_id == ""
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.SUCCESS
    assert latest_scan.metadata["worker"] == "phase6"
    assert len(latest_items) == 1
    assert latest_items[0].item_key == "post:coordinator-success"
    assert len(history) == 1
    assert outbox_entry is not None
    assert outbox_entry.source_scan_run_id is None


def test_scan_commit_coordinator_success_reports_guard_mismatch_without_writes(
    tmp_path: Path,
) -> None:
    """success coordinator 遇 stale guard 時不得寫 visible scan state。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app, include_keywords=("票券",))
        old_state = app.services.targets.ensure_runtime_state(fixture.target.id)
        app.repositories.runtime_states.save(
            replace(old_state, active_worker_id="worker-b", active_page_id="page-b")
        )
        outcome = commit_success(
            app=app,
            target=fixture.target,
            config=fixture.config,
            result=SuccessScanResult(
                target_id=fixture.target.id,
                url=fixture.target.canonical_url,
                items=(
                    NormalizedScanItem(
                        item_kind=ItemKind.POST,
                        item_key="post:stale-success",
                        alias_keys=("post:stale-success",),
                        group_id=fixture.target.group_id,
                        text="票券",
                    ),
                ),
                item_count=1,
                metadata={"worker": "phase6"},
            ),
            commit_guard=fixture.commit_guard,
        )
        state = app.repositories.runtime_states.get(fixture.target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(fixture.target.id)
        history = app.repositories.match_history.list_by_target(fixture.target.id)
        pending_outbox = app.repositories.notification_outbox.list_pending()
        seen_count = app.repositories.seen_items.connection.execute(
            "SELECT COUNT(*) FROM seen_items WHERE scope_id = ?",
            (fixture.target.scope_id,),
        ).fetchone()[0]

    assert outcome.kind == ScanCommitOutcomeKind.GUARD_MISMATCH
    assert outcome.committed_visible_scan_state is False
    assert outcome.side_effects.any is False
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert state.active_worker_id == "worker-b"
    assert state.active_page_id == "page-b"
    assert latest_scan is None
    assert latest_items == []
    assert history == []
    assert pending_outbox == []
    assert seen_count == 0


def test_scan_commit_coordinator_success_post_write_idle_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """success 寫入後若 idle guard 失敗，整筆 scan commit 必須 rollback。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app, include_keywords=("票券",))
    config = replace(
        fixture.config,
        enable_ntfy=True,
        ntfy_topic="phase6-success-rollback",
    )

    def reject_idle(*_args: Any, **_kwargs: Any) -> None:
        return None

    with pytest.raises(WorkerFailure) as exc_info:
        with SqliteApplicationContext(db_path) as app:
            monkeypatch.setattr(
                app.services.targets,
                "guarded_mark_target_idle",
                reject_idle,
            )
            commit_success(
                app=app,
                target=fixture.target,
                config=config,
                result=SuccessScanResult(
                    target_id=fixture.target.id,
                    url=fixture.target.canonical_url,
                    items=(
                        NormalizedScanItem(
                            item_kind=ItemKind.POST,
                            item_key="post:success-idle-rollback",
                            alias_keys=("post:success-idle-rollback",),
                            group_id=fixture.target.group_id,
                            text="票券",
                        ),
                    ),
                    item_count=1,
                    metadata={"worker": "phase6"},
                ),
                commit_guard=fixture.commit_guard,
            )

    assert exc_info.value.reason == TARGET_STOPPED_REASON
    with SqliteApplicationContext(db_path) as app:
        _assert_no_visible_scan_writes(app, fixture.target.id)
        state = app.repositories.runtime_states.get(fixture.target.id)
        seen_count = app.repositories.seen_items.connection.execute(
            "SELECT COUNT(*) FROM seen_items WHERE scope_id = ?",
            (fixture.target.scope_id,),
        ).fetchone()[0]

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert state.active_worker_id == fixture.commit_guard.worker_id
    assert state.active_page_id == fixture.commit_guard.page_id
    assert seen_count == 0


def test_scan_commit_coordinator_success_reports_target_inactive_without_writes(
    tmp_path: Path,
) -> None:
    """success coordinator 遇 target inactive 時不得寫 visible scan state。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app, include_keywords=("票券",))
        app.services.targets.pause_target_monitoring(fixture.target.id)
        outcome = commit_success(
            app=app,
            target=fixture.target,
            config=fixture.config,
            result=SuccessScanResult(
                target_id=fixture.target.id,
                url=fixture.target.canonical_url,
                items=(
                    NormalizedScanItem(
                        item_kind=ItemKind.POST,
                        item_key="post:inactive-success",
                        alias_keys=("post:inactive-success",),
                        group_id=fixture.target.group_id,
                        text="票券",
                    ),
                ),
                item_count=1,
                metadata={"worker": "phase6"},
            ),
            commit_guard=fixture.commit_guard,
        )
        state = app.repositories.runtime_states.get(fixture.target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(fixture.target.id)
        history = app.repositories.match_history.list_by_target(fixture.target.id)
        pending_outbox = app.repositories.notification_outbox.list_pending()

    assert outcome.kind == ScanCommitOutcomeKind.TARGET_INACTIVE
    assert outcome.reason == "target_inactive_before_commit"
    assert outcome.committed_visible_scan_state is False
    assert outcome.side_effects.any is False
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert latest_scan is None
    assert latest_items == []
    assert history == []
    assert pending_outbox == []


def test_scan_commit_coordinator_success_reports_target_missing_without_writes(
    tmp_path: Path,
) -> None:
    """target deleted before commit 以 target inactive outcome 表示且不寫入。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app, include_keywords=("票券",))
        target_id = fixture.target.id
        app.services.targets.delete_target(target_id)
        outcome = commit_success(
            app=app,
            target=fixture.target,
            config=fixture.config,
            result=SuccessScanResult(
                target_id=target_id,
                url=fixture.target.canonical_url,
                items=(),
                item_count=0,
                metadata={"worker": "phase6"},
            ),
            commit_guard=fixture.commit_guard,
        )

    assert outcome.kind == ScanCommitOutcomeKind.TARGET_INACTIVE
    assert outcome.reason == "target_missing_before_commit"
    assert outcome.side_effects.any is False


def test_scan_commit_coordinator_success_reports_runtime_missing_without_writes(
    tmp_path: Path,
) -> None:
    """runtime state missing before commit 以 guard mismatch 表示且不寫入。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app, include_keywords=("票券",))
        app.repositories.runtime_states.connection.execute(
            "DELETE FROM target_runtime_state WHERE target_id = ?",
            (fixture.target.id,),
        )
        outcome = commit_success(
            app=app,
            target=fixture.target,
            config=fixture.config,
            result=SuccessScanResult(
                target_id=fixture.target.id,
                url=fixture.target.canonical_url,
                items=(),
                item_count=0,
                metadata={"worker": "phase6"},
            ),
            commit_guard=fixture.commit_guard,
        )
        _assert_no_visible_scan_writes(app, fixture.target.id)

    assert outcome.kind == ScanCommitOutcomeKind.GUARD_MISMATCH
    assert outcome.reason == "runtime_state_missing_before_commit"
    assert outcome.side_effects.any is False


def test_scan_commit_coordinator_success_reports_runtime_not_running_without_writes(
    tmp_path: Path,
) -> None:
    """runtime no longer running before commit 以 guard mismatch 表示且不寫入。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app, include_keywords=("票券",))
        app.services.targets.guarded_mark_target_idle(
            fixture.target.id,
            worker_id=fixture.commit_guard.worker_id,
            started_at=fixture.commit_guard.started_at,
            page_id=fixture.commit_guard.page_id,
        )
        outcome = commit_success(
            app=app,
            target=fixture.target,
            config=fixture.config,
            result=SuccessScanResult(
                target_id=fixture.target.id,
                url=fixture.target.canonical_url,
                items=(),
                item_count=0,
                metadata={"worker": "phase6"},
            ),
            commit_guard=fixture.commit_guard,
        )
        _assert_no_visible_scan_writes(app, fixture.target.id)

    assert outcome.kind == ScanCommitOutcomeKind.GUARD_MISMATCH
    assert outcome.reason == "runtime_not_running_before_commit"
    assert outcome.side_effects.any is False


def test_scan_commit_coordinator_success_reports_started_at_mismatch_without_writes(
    tmp_path: Path,
) -> None:
    """same worker with newer started_at before commit must not accept stale result。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app, include_keywords=("票券",))
        app.services.targets.mark_target_running(
            fixture.target.id,
            fixture.commit_guard.worker_id,
            page_id=fixture.commit_guard.page_id,
        )
        outcome = commit_success(
            app=app,
            target=fixture.target,
            config=fixture.config,
            result=SuccessScanResult(
                target_id=fixture.target.id,
                url=fixture.target.canonical_url,
                items=(),
                item_count=0,
                metadata={"worker": "phase6"},
            ),
            commit_guard=fixture.commit_guard,
        )
        _assert_no_visible_scan_writes(app, fixture.target.id)

    assert outcome.kind == ScanCommitOutcomeKind.GUARD_MISMATCH
    assert outcome.reason == "scan_started_at_changed_before_commit"
    assert outcome.side_effects.any is False


def test_scan_commit_coordinator_success_reports_page_mismatch_without_writes(
    tmp_path: Path,
) -> None:
    """same worker/start with different page before commit must not write result。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app, include_keywords=("票券",))
        old_state = app.services.targets.ensure_runtime_state(fixture.target.id)
        app.repositories.runtime_states.save(replace(old_state, active_page_id="page-b"))
        outcome = commit_success(
            app=app,
            target=fixture.target,
            config=fixture.config,
            result=SuccessScanResult(
                target_id=fixture.target.id,
                url=fixture.target.canonical_url,
                items=(),
                item_count=0,
                metadata={"worker": "phase6"},
            ),
            commit_guard=fixture.commit_guard,
        )
        _assert_no_visible_scan_writes(app, fixture.target.id)

    assert outcome.kind == ScanCommitOutcomeKind.GUARD_MISMATCH
    assert outcome.reason == "page_owner_changed_before_commit"
    assert outcome.side_effects.any is False


def test_scan_commit_coordinator_rejects_success_result_target_mismatch(
    tmp_path: Path,
) -> None:
    """success result target_id 不符時 fail closed 且不寫 visible state。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        with pytest.raises(WorkerFailure) as exc_info:
            commit_success(
                app=app,
                target=fixture.target,
                config=fixture.config,
                result=SuccessScanResult(
                    target_id="other-target",
                    url=fixture.target.canonical_url,
                    items=(),
                    item_count=0,
                    metadata={"worker": "phase6"},
                ),
                commit_guard=fixture.commit_guard,
            )
        assert exc_info.value.reason == "scan_result_target_mismatch"
        _assert_no_visible_scan_writes(app, fixture.target.id)


def test_scan_commit_coordinator_rejects_success_item_group_mismatch(
    tmp_path: Path,
) -> None:
    """success item group_id 不符時 fail closed 且不寫 visible state。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        with pytest.raises(WorkerFailure) as exc_info:
            commit_success(
                app=app,
                target=fixture.target,
                config=fixture.config,
                result=SuccessScanResult(
                    target_id=fixture.target.id,
                    url=fixture.target.canonical_url,
                    items=(
                        NormalizedScanItem(
                            item_kind=ItemKind.POST,
                            item_key="post:wrong-group",
                            alias_keys=("post:wrong-group",),
                            group_id="other-group",
                            text="票券",
                        ),
                    ),
                    item_count=1,
                    metadata={"worker": "phase6"},
                ),
                commit_guard=fixture.commit_guard,
            )
        assert exc_info.value.reason == "scan_result_target_mismatch"
        _assert_no_visible_scan_writes(app, fixture.target.id)


def test_scan_commit_coordinator_rejects_success_item_kind_mismatch(
    tmp_path: Path,
) -> None:
    """posts target 收到 comment item 時 fail closed 且不寫 visible state。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        with pytest.raises(WorkerFailure) as exc_info:
            commit_success(
                app=app,
                target=fixture.target,
                config=fixture.config,
                result=SuccessScanResult(
                    target_id=fixture.target.id,
                    url=fixture.target.canonical_url,
                    items=(
                        NormalizedScanItem(
                            item_kind=ItemKind.COMMENT,
                            item_key="comment:wrong-kind",
                            alias_keys=("comment:wrong-kind",),
                            group_id=fixture.target.group_id,
                            parent_post_id="post-1",
                            comment_id="comment-1",
                            text="票券",
                        ),
                    ),
                    item_count=1,
                    metadata={"worker": "phase6"},
                ),
                commit_guard=fixture.commit_guard,
            )
        assert exc_info.value.reason == "scan_result_target_mismatch"
        _assert_no_visible_scan_writes(app, fixture.target.id)


def test_scan_commit_coordinator_rejects_raw_target_kind_mismatch(
    tmp_path: Path,
) -> None:
    """item raw_target_kind 不符時 fail closed 且不寫 visible state。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        with pytest.raises(WorkerFailure) as exc_info:
            commit_success(
                app=app,
                target=fixture.target,
                config=fixture.config,
                result=SuccessScanResult(
                    target_id=fixture.target.id,
                    url=fixture.target.canonical_url,
                    items=(
                        NormalizedScanItem(
                            item_kind=ItemKind.POST,
                            item_key="post:wrong-raw-kind",
                            alias_keys=("post:wrong-raw-kind",),
                            group_id=fixture.target.group_id,
                            text="票券",
                            raw_target_kind="comments",
                        ),
                    ),
                    item_count=1,
                    metadata={"worker": "phase6"},
                ),
                commit_guard=fixture.commit_guard,
            )
        assert exc_info.value.reason == "scan_result_target_mismatch"
        _assert_no_visible_scan_writes(app, fixture.target.id)


def test_scan_commit_coordinator_rejects_comments_parent_mismatch(
    tmp_path: Path,
) -> None:
    """comments item parent_post_id 不符時 fail closed 且不寫 visible state。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="123",
                parent_post_id="post-1",
                canonical_url="https://www.facebook.com/groups/123/posts/post-1",
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)
        running_state = app.services.targets.mark_target_running(
            target.id,
            "worker-a",
            page_id="page-a",
        )
        commit_guard = scan_commit_guard_from_runtime_state(running_state)

        with pytest.raises(WorkerFailure) as exc_info:
            commit_success(
                app=app,
                target=target,
                config=config,
                result=SuccessScanResult(
                    target_id=target.id,
                    url=target.canonical_url,
                    items=(
                        NormalizedScanItem(
                            item_kind=ItemKind.COMMENT,
                            item_key="comment:wrong-parent",
                            alias_keys=("comment:wrong-parent",),
                            group_id=target.group_id,
                            parent_post_id="post-2",
                            comment_id="comment-1",
                            text="票券",
                            raw_target_kind=target.target_kind.value,
                        ),
                    ),
                    item_count=1,
                    metadata={"worker": "phase6"},
                ),
                commit_guard=commit_guard,
            )
        assert exc_info.value.reason == "scan_result_target_mismatch"
        _assert_no_visible_scan_writes(app, target.id)


def test_scan_commit_coordinator_rejects_protective_skip_target_mismatch(
    tmp_path: Path,
) -> None:
    """protective skip result target_id 不符時 fail closed 且不寫 visible state。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        with pytest.raises(WorkerFailure) as exc_info:
            commit_guarded_protective_skip(
                app=app,
                target=fixture.target,
                result=ProtectiveSkipScanResult(
                    target_id="other-target",
                    url=fixture.target.canonical_url,
                    metadata={
                        "worker": "resident_main",
                        "skip_reason": SORT_ADJUST_UNCONFIRMED_REASON,
                    },
                ),
                commit_guard=fixture.commit_guard,
            )
        assert exc_info.value.reason == "scan_result_target_mismatch"
        _assert_no_visible_scan_writes(app, fixture.target.id)


def test_scan_commit_coordinator_commits_guarded_failure(
    tmp_path: Path,
) -> None:
    """failure wrapper 回傳 existing failure decision 與 typed outcome。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)

    async def run_test() -> None:
        outcome = await _commit_failure_request_for_test(
            db_path=db_path,
            target_id=fixture.target.id,
            reason=UNKNOWN_REASON,
            message="boom",
            source="unknown_exception",
            worker_path="resident_main",
            commit_guard=fixture.commit_guard,
            exception_class="RuntimeError",
        )
        assert outcome.kind == ScanCommitOutcomeKind.FAILURE_COMMITTED
        assert outcome.committed_visible_scan_state is True
        assert outcome.scan_run_id > 0
        assert outcome.runtime_failure_notification_count == 0
        assert outcome.side_effects.wrote_scan_run is True
        assert outcome.side_effects.enqueued_runtime_failure_notification_outbox is False
        assert outcome.side_effects.updated_runtime_state is True
        assert outcome.failure_decision is not None
        assert outcome.reason == outcome.failure_decision.reason
        assert outcome.discard_page == outcome.failure_decision.discard_page

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(fixture.target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.consecutive_failure_count == 1
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.FAILED
    assert latest_scan.metadata["reason"] == UNKNOWN_REASON


def test_scan_commit_coordinator_commits_failure_request(
    tmp_path: Path,
) -> None:
    """typed failure request path 保留 existing guarded failure finalize 語義。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)

    async def run_test() -> None:
        outcome = await commit_failure_request_for_db_async(
            FailureScanCommitRequest(
                db_path=db_path,
                target_id=fixture.target.id,
                reason=UNKNOWN_REASON,
                message="boom",
                source="unknown_exception",
                worker_path="resident_main",
                commit_guard=fixture.commit_guard,
                exception_class="RuntimeError",
                page_reused=False,
            )
        )
        assert outcome.kind == ScanCommitOutcomeKind.FAILURE_COMMITTED
        assert outcome.scan_run_id > 0
        assert outcome.side_effects.wrote_scan_run is True
        assert outcome.side_effects.updated_runtime_state is True
        assert outcome.failure_decision is not None
        assert outcome.reason == UNKNOWN_REASON

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)

    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.FAILED
    assert latest_scan.metadata["page_reused"] is False


def test_scan_commit_coordinator_failure_duplicate_reports_no_scan_run_write(
    tmp_path: Path,
) -> None:
    """duplicate non-terminal failure 不應被標成新增 visible failure scan。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)

    async def run_test() -> tuple[int, int, bool, int, bool, bool]:
        first = await _commit_failure_request_for_test(
            db_path=db_path,
            target_id=fixture.target.id,
            reason=SCHEDULER_STOPPING_REASON,
            message="resident scheduler is stopping",
            source="scheduler_cancel",
            worker_path="resident_main",
            commit_guard=fixture.commit_guard,
            exception_class="CancelledError",
        )
        with SqliteApplicationContext(db_path) as app:
            running_state = app.services.targets.mark_target_running(
                fixture.target.id,
                "worker-2",
                page_id="page-2",
            )
            second_guard = scan_commit_guard_from_runtime_state(running_state)
        second = await _commit_failure_request_for_test(
            db_path=db_path,
            target_id=fixture.target.id,
            reason=SCHEDULER_STOPPING_REASON,
            message="resident scheduler is stopping",
            source="scheduler_cancel",
            worker_path="resident_main",
            commit_guard=second_guard,
            exception_class="CancelledError",
        )
        return (
            first.scan_run_id,
            second.scan_run_id,
            second.committed_visible_scan_state,
            second.runtime_failure_notification_count,
            second.side_effects.wrote_scan_run,
            second.side_effects.updated_runtime_state,
        )

    (
        first_scan_run_id,
        second_scan_run_id,
        committed,
        outbox_count,
        duplicate_wrote_scan_run,
        duplicate_updated_runtime,
    ) = asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        failed_scan_count = app.repositories.scan_runs.connection.execute(
            "SELECT COUNT(*) FROM scan_runs WHERE target_id = ? AND status = ?",
            (fixture.target.id, ScanStatus.FAILED.value),
        ).fetchone()[0]

    assert first_scan_run_id > 0
    assert second_scan_run_id == 0
    assert committed is False
    assert outbox_count == 0
    assert duplicate_wrote_scan_run is False
    assert duplicate_updated_runtime is True
    assert failed_scan_count == 1


def test_scan_commit_coordinator_failure_reports_runtime_outbox_count(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """terminal runtime failure outcome 要帶出實際 queued outbox count。"""

    db_path = tmp_path / "app.db"
    _stub_outbox_dispatch(monkeypatch)
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="terminal-runtime",
                canonical_url="https://www.facebook.com/groups/terminal-runtime",
                group_name="Terminal runtime",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="runtime-topic",
                ),
            )
        )
        target = _activate_target(app, target)

    async def run_test() -> tuple[int, int]:
        latest_scan_run_id = 0
        latest_outbox_count = 0
        for index in range(3):
            with SqliteApplicationContext(db_path) as app:
                running_state = app.services.targets.mark_target_running(
                    target.id,
                    f"worker-{index}",
                    page_id=f"page-{index}",
                )
                commit_guard = scan_commit_guard_from_runtime_state(running_state)
            outcome = await _commit_failure_request_for_test(
                db_path=db_path,
                target_id=target.id,
                reason=SCHEDULER_RUNTIME_REASON,
                message="Target page, context or browser has been closed",
                source="unknown_exception",
                worker_path="resident_main",
                commit_guard=commit_guard,
                exception_class="RuntimeError",
            )
            latest_scan_run_id = outcome.scan_run_id
            latest_outbox_count = outcome.runtime_failure_notification_count
            assert outcome.side_effects.wrote_scan_run is True
            assert outcome.side_effects.enqueued_runtime_failure_notification_outbox is (
                outcome.runtime_failure_notification_count > 0
            )
            assert outcome.side_effects.updated_runtime_state is True
        return latest_scan_run_id, latest_outbox_count

    terminal_scan_run_id, terminal_outbox_count = asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        latest_scan_row_id = app.repositories.scan_runs.connection.execute(
            "SELECT id FROM scan_runs WHERE target_id = ? ORDER BY id DESC LIMIT 1",
            (target.id,),
        ).fetchone()[0]
        state = app.repositories.runtime_states.get(target.id)
        entries = app.repositories.notification_outbox.list_pending()

    assert terminal_scan_run_id > 0
    assert terminal_outbox_count == 1
    assert latest_scan is not None
    assert latest_scan_row_id == terminal_scan_run_id
    assert latest_scan.metadata["reason"] == SCHEDULER_RUNTIME_REASON
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.ERROR
    assert len(entries) == 1
    entry = entries[0]
    assert entry.event_kind == NotificationEventKind.RUNTIME_FAILURE
    assert entry.source_scan_run_id == terminal_scan_run_id
    assert entry.failure_reason == SCHEDULER_RUNTIME_REASON
    assert entry.failure_count == 3


def test_scan_commit_coordinator_commits_existing_protective_skip(
    tmp_path: Path,
) -> None:
    """skip coordinator 只包 guarded protective finalize。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        outcome = commit_guarded_protective_skip(
            app=app,
            target=fixture.target,
            result=ProtectiveSkipScanResult(
                target_id=fixture.target.id,
                url=fixture.target.canonical_url,
                metadata={
                    "worker": "resident_main",
                    "skip_reason": SORT_ADJUST_UNCONFIRMED_REASON,
                },
            ),
            commit_guard=fixture.commit_guard,
        )
        state = app.repositories.runtime_states.get(fixture.target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(fixture.target.id)

    assert outcome.kind == ScanCommitOutcomeKind.SKIP_COMMITTED
    assert outcome.committed_visible_scan_state is True
    assert outcome.scan_run_id > 0
    assert outcome.reason == SORT_ADJUST_UNCONFIRMED_REASON
    assert outcome.side_effects.wrote_scan_run is True
    assert outcome.side_effects.wrote_latest_scan is False
    assert outcome.side_effects.cleared_latest_scan is True
    assert outcome.side_effects.wrote_match_history is False
    assert outcome.side_effects.enqueued_match_notification_outbox is False
    assert outcome.side_effects.updated_runtime_state is True
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.consecutive_scan_skip_count == 1
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.SUCCESS
    assert latest_scan.metadata["scan_skipped"] is True
    assert latest_items == []


def test_scan_commit_coordinator_skip_stale_owner_writes_nothing(
    tmp_path: Path,
) -> None:
    """stale protective skip 不得寫 visible scan state 或 runtime outbox。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        current_state = app.services.targets.mark_target_running(
            fixture.target.id,
            "worker-b",
            page_id="page-b",
        )
        current_guard = scan_commit_guard_from_runtime_state(current_state)
        outcome = commit_guarded_protective_skip(
            app=app,
            target=fixture.target,
            result=ProtectiveSkipScanResult(
                target_id=fixture.target.id,
                url=fixture.target.canonical_url,
                metadata={
                    "worker": "resident_main",
                    "skip_reason": SORT_ADJUST_UNCONFIRMED_REASON,
                },
            ),
            commit_guard=fixture.commit_guard,
        )
        state = app.repositories.runtime_states.get(fixture.target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(fixture.target.id)
        pending_outbox = app.repositories.notification_outbox.list_pending()

    assert outcome.kind == ScanCommitOutcomeKind.GUARD_MISMATCH
    assert outcome.reason == "owner_changed_before_commit"
    assert outcome.side_effects.any is False
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert state.active_worker_id == current_guard.worker_id
    assert state.active_page_id == current_guard.page_id
    assert latest_scan is None
    assert latest_items == []
    assert pending_outbox == []


def test_scan_commit_coordinator_skip_target_inactive_writes_nothing(
    tmp_path: Path,
) -> None:
    """target inactive before protective skip commit 應回 typed outcome 且不寫入。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        app.services.targets.pause_target_monitoring(fixture.target.id)
        outcome = commit_guarded_protective_skip(
            app=app,
            target=fixture.target,
            result=ProtectiveSkipScanResult(
                target_id=fixture.target.id,
                url=fixture.target.canonical_url,
                metadata={
                    "worker": "resident_main",
                    "skip_reason": SORT_ADJUST_UNCONFIRMED_REASON,
                },
            ),
            commit_guard=fixture.commit_guard,
        )
        state = app.repositories.runtime_states.get(fixture.target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(fixture.target.id)
        pending_outbox = app.repositories.notification_outbox.list_pending()

    assert outcome.kind == ScanCommitOutcomeKind.TARGET_INACTIVE
    assert outcome.reason == "target_inactive_before_commit"
    assert outcome.committed_visible_scan_state is False
    assert outcome.side_effects.any is False
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert latest_scan is None
    assert latest_items == []
    assert pending_outbox == []


def test_scan_commit_coordinator_skip_post_write_guard_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """skipped scan 寫入後若 runtime guard 失敗，必須 rollback 而非回 rejection。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)

    def reject_skip_decision(*_args: Any, **_kwargs: Any) -> None:
        return None

    with pytest.raises(WorkerFailure) as exc_info:
        with SqliteApplicationContext(db_path) as app:
            monkeypatch.setattr(
                app.services.targets,
                "guarded_apply_scan_skip_decision",
                reject_skip_decision,
            )
            commit_guarded_protective_skip(
                app=app,
                target=fixture.target,
                result=ProtectiveSkipScanResult(
                    target_id=fixture.target.id,
                    url=fixture.target.canonical_url,
                    metadata={
                        "worker": "resident_main",
                        "skip_reason": SORT_ADJUST_UNCONFIRMED_REASON,
                    },
                ),
                commit_guard=fixture.commit_guard,
            )

    assert exc_info.value.reason == TARGET_STOPPED_REASON
    with SqliteApplicationContext(db_path) as app:
        _assert_no_visible_scan_writes(app, fixture.target.id)
        state = app.repositories.runtime_states.get(fixture.target.id)

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert state.active_worker_id == fixture.commit_guard.worker_id
    assert state.active_page_id == fixture.commit_guard.page_id


def test_scan_commit_coordinator_failure_reports_guard_mismatch_without_writes(
    tmp_path: Path,
) -> None:
    """failure wrapper 遇舊 guard 時不得寫 failure scan 或 runtime outbox。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        old_state = app.services.targets.ensure_runtime_state(fixture.target.id)
        app.repositories.runtime_states.save(
            replace(old_state, active_worker_id="worker-b", active_page_id="page-b")
        )
        current_guard = scan_commit_guard_from_runtime_state(
            app.services.targets.ensure_runtime_state(fixture.target.id)
        )

    async def run_test() -> None:
        outcome = await _commit_failure_request_for_test(
            db_path=db_path,
            target_id=fixture.target.id,
            reason=UNKNOWN_REASON,
            message="stale boom",
            source="unknown_exception",
            worker_path="resident_main",
            commit_guard=fixture.commit_guard,
            exception_class="RuntimeError",
        )
        assert outcome.kind == ScanCommitOutcomeKind.GUARD_MISMATCH
        assert outcome.failure_decision is None
        assert outcome.committed_visible_scan_state is False
        assert outcome.side_effects.any is False

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(fixture.target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)
        pending_outbox = app.repositories.notification_outbox.list_pending()

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert state.active_worker_id == current_guard.worker_id
    assert state.active_page_id == current_guard.page_id
    assert latest_scan is None
    assert pending_outbox == []


def test_scan_commit_coordinator_failure_post_write_guard_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """failure scan 寫入後若 runtime guard 失敗，必須 rollback 而非回 rejection。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)

    def reject_failure_decision(*_args: Any, **_kwargs: Any) -> None:
        return None

    with pytest.raises(WorkerFailure) as exc_info:
        with SqliteApplicationContext(db_path) as app:
            monkeypatch.setattr(
                app.services.targets,
                "guarded_apply_scan_failure_decision",
                reject_failure_decision,
            )
            record_guarded_scan_failure_result(
                app=app,
                target_id=fixture.target.id,
                reason=UNKNOWN_REASON,
                message="post-write guard mismatch",
                source="unknown_exception",
                worker_path="resident_main",
                commit_guard=fixture.commit_guard,
                exception_class="RuntimeError",
            )

    assert exc_info.value.reason == TARGET_STOPPED_REASON
    with SqliteApplicationContext(db_path) as app:
        _assert_no_visible_scan_writes(app, fixture.target.id)
        state = app.repositories.runtime_states.get(fixture.target.id)

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert state.active_worker_id == fixture.commit_guard.worker_id
    assert state.active_page_id == fixture.commit_guard.page_id


def test_scan_commit_coordinator_failure_public_path_post_write_guard_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """public async failure coordinator 也必須 propagates post-write guard failure。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)

    def reject_failure_decision(
        self: TargetApplicationService,
        *_args: Any,
        **_kwargs: Any,
    ) -> None:
        return None

    monkeypatch.setattr(
        TargetApplicationService,
        "guarded_apply_scan_failure_decision",
        reject_failure_decision,
    )

    async def run_test() -> None:
        with pytest.raises(WorkerFailure) as exc_info:
            await _commit_failure_request_for_test(
                db_path=db_path,
                target_id=fixture.target.id,
                reason=UNKNOWN_REASON,
                message="public post-write guard mismatch",
                source="unknown_exception",
                worker_path="resident_main",
                commit_guard=fixture.commit_guard,
                exception_class="RuntimeError",
            )
        assert exc_info.value.reason == TARGET_STOPPED_REASON

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        _assert_no_visible_scan_writes(app, fixture.target.id)
        state = app.repositories.runtime_states.get(fixture.target.id)

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert state.active_worker_id == fixture.commit_guard.worker_id
    assert state.active_page_id == fixture.commit_guard.page_id


def test_scan_commit_coordinator_failure_reports_target_inactive_without_writes(
    tmp_path: Path,
) -> None:
    """failure wrapper 遇 target inactive 時不得寫 failure scan 或 runtime outbox。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        app.services.targets.pause_target_monitoring(fixture.target.id)

    async def run_test() -> None:
        outcome = await _commit_failure_request_for_test(
            db_path=db_path,
            target_id=fixture.target.id,
            reason=UNKNOWN_REASON,
            message="inactive boom",
            source="unknown_exception",
            worker_path="resident_main",
            commit_guard=fixture.commit_guard,
            exception_class="RuntimeError",
        )
        assert outcome.kind == ScanCommitOutcomeKind.TARGET_INACTIVE
        assert outcome.reason == "target_inactive_before_commit"
        assert outcome.failure_decision is None
        assert outcome.committed_visible_scan_state is False
        assert outcome.side_effects.any is False

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(fixture.target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)
        pending_outbox = app.repositories.notification_outbox.list_pending()

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert latest_scan is None
    assert pending_outbox == []


def test_scan_commit_coordinator_failure_reports_target_missing_without_writes(
    tmp_path: Path,
) -> None:
    """failure wrapper 遇 target deleted 時不得寫 failure scan 或 runtime outbox。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        target_id = fixture.target.id
        app.services.targets.delete_target(target_id)

    async def run_test() -> None:
        outcome = await _commit_failure_request_for_test(
            db_path=db_path,
            target_id=target_id,
            reason=UNKNOWN_REASON,
            message="missing boom",
            source="unknown_exception",
            worker_path="resident_main",
            commit_guard=fixture.commit_guard,
            exception_class="RuntimeError",
        )
        assert outcome.kind == ScanCommitOutcomeKind.TARGET_INACTIVE
        assert outcome.reason == "target_missing_before_commit"
        assert outcome.failure_decision is None
        assert outcome.committed_visible_scan_state is False
        assert outcome.side_effects.any is False

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        _assert_no_visible_scan_writes(app, target_id)


def test_scan_commit_coordinator_failure_reports_runtime_missing_without_writes(
    tmp_path: Path,
) -> None:
    """failure wrapper 遇 runtime missing 時不得寫 failure scan 或 runtime outbox。

    既有 failure guard helper 會先補 runtime row，因此可觀察 outcome 是
    runtime not running；此測試鎖住 no visible write 語義。
    """

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        app.repositories.runtime_states.connection.execute(
            "DELETE FROM target_runtime_state WHERE target_id = ?",
            (fixture.target.id,),
        )

    async def run_test() -> None:
        outcome = await _commit_failure_request_for_test(
            db_path=db_path,
            target_id=fixture.target.id,
            reason=UNKNOWN_REASON,
            message="missing runtime boom",
            source="unknown_exception",
            worker_path="resident_main",
            commit_guard=fixture.commit_guard,
            exception_class="RuntimeError",
        )
        assert outcome.kind == ScanCommitOutcomeKind.GUARD_MISMATCH
        assert outcome.reason == "runtime_not_running_before_commit"
        assert outcome.failure_decision is None
        assert outcome.committed_visible_scan_state is False
        assert outcome.side_effects.any is False

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        _assert_no_visible_scan_writes(app, fixture.target.id)


def test_scan_commit_coordinator_failure_reports_runtime_not_running_without_writes(
    tmp_path: Path,
) -> None:
    """failure wrapper 遇 runtime 已 idle 時不得寫 failure scan 或 runtime outbox。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        app.services.targets.guarded_mark_target_idle(
            fixture.target.id,
            worker_id=fixture.commit_guard.worker_id,
            started_at=fixture.commit_guard.started_at,
            page_id=fixture.commit_guard.page_id,
        )

    async def run_test() -> None:
        outcome = await _commit_failure_request_for_test(
            db_path=db_path,
            target_id=fixture.target.id,
            reason=UNKNOWN_REASON,
            message="idle runtime boom",
            source="unknown_exception",
            worker_path="resident_main",
            commit_guard=fixture.commit_guard,
            exception_class="RuntimeError",
        )
        assert outcome.kind == ScanCommitOutcomeKind.GUARD_MISMATCH
        assert outcome.reason == "runtime_not_running_before_commit"
        assert outcome.failure_decision is None
        assert outcome.committed_visible_scan_state is False
        assert outcome.side_effects.any is False

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        _assert_no_visible_scan_writes(app, fixture.target.id)


def test_scan_commit_coordinator_failure_reports_page_mismatch_without_writes(
    tmp_path: Path,
) -> None:
    """failure wrapper 遇 page owner mismatch 時不得寫 failure scan 或 runtime outbox。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        old_state = app.services.targets.ensure_runtime_state(fixture.target.id)
        app.repositories.runtime_states.save(replace(old_state, active_page_id="page-b"))

    async def run_test() -> None:
        outcome = await _commit_failure_request_for_test(
            db_path=db_path,
            target_id=fixture.target.id,
            reason=UNKNOWN_REASON,
            message="page mismatch boom",
            source="unknown_exception",
            worker_path="resident_main",
            commit_guard=fixture.commit_guard,
            exception_class="RuntimeError",
        )
        assert outcome.kind == ScanCommitOutcomeKind.GUARD_MISMATCH
        assert outcome.reason == "page_owner_changed_before_commit"
        assert outcome.failure_decision is None
        assert outcome.committed_visible_scan_state is False
        assert outcome.side_effects.any is False

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(fixture.target.id)
        _assert_no_visible_scan_writes(app, fixture.target.id)

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert state.active_worker_id == fixture.commit_guard.worker_id
    assert state.active_page_id == "page-b"
