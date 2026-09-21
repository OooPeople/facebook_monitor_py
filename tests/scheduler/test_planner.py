"""正式 target scheduler planner tests。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import utc_now
from facebook_monitor.scheduler.planner import TargetSchedulePlanner


def test_planner_publishes_display_due_only_when_changed(tmp_path: Path) -> None:
    """Planner 只發布 UI 顯示用 due 變更，不在每個 tick 重寫。"""

    db_path = tmp_path / "app.db"
    now = utc_now()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.clear_target_scan_request(target.id)
        app.repositories.configs.save_for_target(
            target,
            TargetConfig(target_id=target.id, fixed_refresh_sec=60),
        )

    published: list[tuple[str, object]] = []
    planner = TargetSchedulePlanner(
        on_display_next_due_changed=lambda target_id, due_at: published.append(
            (target_id, due_at)
        )
    )
    due_targets = planner.list_due_targets(
        db_path,
        default_interval_seconds=60,
        now=now,
    )
    planner.list_due_targets(
        db_path,
        default_interval_seconds=60,
        now=now + timedelta(seconds=1),
    )

    assert due_targets
    assert published == [(target.id, now)]

    planner.mark_dispatched(due_targets[0], now=now + timedelta(seconds=5))

    assert published[-1] == (target.id, now + timedelta(seconds=65))
    assert planner.list_due_targets(
        db_path,
        default_interval_seconds=60,
        now=now + timedelta(seconds=64),
    ) == ()
    due_again = planner.list_due_targets(
        db_path,
        default_interval_seconds=60,
        now=now + timedelta(seconds=65),
    )
    assert len(due_again) == 1
    assert due_again[0].target_id == target.id
    assert due_again[0].due_at == now + timedelta(seconds=65)

    with SqliteApplicationContext(db_path) as app:
        app.services.targets.pause_target_monitoring(target.id)

    planner.list_due_targets(
        db_path,
        default_interval_seconds=60,
        now=now + timedelta(seconds=6),
    )

    assert published[-1] == (target.id, None)


def test_planner_skips_error_target(tmp_path: Path) -> None:
    """Resident planner 不自動排程已進入 error 的 target。"""

    db_path = tmp_path / "app.db"
    now = utc_now()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.mark_target_error(target.id, "content_unavailable")

    planner = TargetSchedulePlanner()

    assert (
        planner.list_due_targets(
            db_path,
            default_interval_seconds=60,
            now=now,
        )
        == ()
    )
