"""Persistence smoke tests。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.persistence.sqlite import LogicalItemRepository
from facebook_monitor.persistence.sqlite import SeenItemRepository
from facebook_monitor.persistence.sqlite import SqliteConnection
from facebook_monitor.persistence.sqlite import TargetRepository
from facebook_monitor.persistence.sqlite import initialize_schema


def test_seen_items_clear_scope_only_deletes_that_scan_scope(tmp_path: Path) -> None:
    """清除 seen scope 僅影響指定 target scope，避免跨 target 重播通知。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        repo = SeenItemRepository(connection)
        repo.mark_seen_aliases(
            SeenItem(scope_id="scope-a", item_key="same-item", item_kind=ItemKind.POST),
            ("same-item", "same-item-alias"),
        )
        repo.mark_seen(
            SeenItem(
                scope_id="scope-b",
                item_key="same-item",
                item_kind=ItemKind.POST,
            )
        )

        assert repo.clear_scope("scope-a") == 2

        assert not repo.has_seen("scope-a", "same-item")
        assert not repo.has_seen("scope-a", "same-item-alias")
        assert repo.has_seen("scope-b", "same-item")


def test_logical_items_reuse_alias_and_comment_identity(tmp_path: Path) -> None:
    """logical item 以 alias 與 comments parent/comment id 防止 identity drift。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        target = TargetDescriptor.for_comments(
            group_id="111",
            parent_post_id="999",
            canonical_url="https://www.facebook.com/groups/111/posts/999",
        )
        TargetRepository(connection).save(target)
        repo = LogicalItemRepository(connection)

        first = repo.mark_seen_aliases(
            target_id=target.id,
            item=SeenItem(
                scope_id=target.scope_id,
                item_key="comment-alias-a",
                item_kind=ItemKind.COMMENT,
                parent_post_id="999",
                comment_id="comment-1",
            ),
            item_keys=("comment-alias-a", "comment-alias-b"),
        )
        second = repo.mark_seen_aliases(
            target_id=target.id,
            item=SeenItem(
                scope_id=target.scope_id,
                item_key="comment-alias-c",
                item_kind=ItemKind.COMMENT,
                parent_post_id="999",
                comment_id="comment-1",
            ),
            item_keys=("comment-alias-c",),
        )

    assert first.is_new
    assert not second.is_new
    assert second.logical_item_id == first.logical_item_id


def test_logical_items_reuse_post_alias_drift_without_merging_conflicts(
    tmp_path: Path,
) -> None:
    """posts 透過重疊 alias 承接 identity drift，已屬他項目的 alias 不強制合併。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        target = TargetDescriptor.for_group_posts(
            group_id="111",
            canonical_url="https://www.facebook.com/groups/111",
        )
        TargetRepository(connection).save(target)
        repo = LogicalItemRepository(connection)

        first = repo.mark_seen_aliases(
            target_id=target.id,
            item=SeenItem(
                scope_id=target.scope_id,
                item_key="post-a-canonical",
                item_kind=ItemKind.POST,
            ),
            item_keys=("post-a-canonical", "post-a-permalink"),
        )
        second = repo.mark_seen_aliases(
            target_id=target.id,
            item=SeenItem(
                scope_id=target.scope_id,
                item_key="post-a-new-key",
                item_kind=ItemKind.POST,
            ),
            item_keys=("post-a-permalink", "post-a-new-key"),
        )
        conflicting = repo.mark_seen_aliases(
            target_id=target.id,
            item=SeenItem(
                scope_id=target.scope_id,
                item_key="post-b-canonical",
                item_kind=ItemKind.POST,
            ),
            item_keys=("post-b-canonical",),
        )
        conflict_owner_before = connection.execute(
            """
            SELECT logical_item_id
            FROM logical_item_aliases
            WHERE target_id = ?
              AND alias_key = 'post-b-canonical'
            """,
            (target.id,),
        ).fetchone()
        conflict_attempt = repo.mark_seen_aliases(
            target_id=target.id,
            item=SeenItem(
                scope_id=target.scope_id,
                item_key="post-a-new-key",
                item_kind=ItemKind.POST,
            ),
            item_keys=("post-a-new-key", "post-b-canonical"),
        )
        conflict_owner_after = connection.execute(
            """
            SELECT logical_item_id
            FROM logical_item_aliases
            WHERE target_id = ?
              AND alias_key = 'post-b-canonical'
            """,
            (target.id,),
        ).fetchone()

    assert first.is_new
    assert not second.is_new
    assert second.logical_item_id == first.logical_item_id
    assert conflicting.logical_item_id != first.logical_item_id
    assert conflict_attempt.logical_item_id == first.logical_item_id
    assert conflict_owner_before is not None
    assert conflict_owner_after is not None
    assert conflict_owner_after["logical_item_id"] == conflict_owner_before["logical_item_id"]
