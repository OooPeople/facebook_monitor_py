"""One-shot dispatch fallback tests。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.worker.one_shot_dispatch import select_one_shot_target


def test_select_one_shot_target_by_group_id_when_multiple_targets_exist(tmp_path: Path) -> None:
    """fallback/debug one-shot dispatch 可用 group id 選取指定 posts target。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )

        selected = select_one_shot_target(app, target_id="", group_id="222")

        assert selected.id != first.id
        assert selected.id == second.id
