"""Typed scan metadata models。

職責：集中 posts/comments worker 寫入 scan run 的 metadata shape。
SQLite 仍保存 JSON dict，但 worker 不再直接在 pipeline 內散落 magic keys。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PostScanRoundMetadata:
    """保存 posts extractor 單輪診斷 metadata。"""

    round_index: int
    raw_item_count: int
    unique_item_count: int
    scroll_y: int
    scroll_height: int
    scroll_target_label: str = ""
    scroll_target_top: int | None = None
    added_count: int | None = None
    stagnant_windows: int | None = None
    scroll_moved: bool | None = None
    scroll_before_top: int | None = None
    scroll_after_top: int | None = None
    scroll_moved_distance: int | None = None
    scroll_step: int | None = None
    load_more_mode: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        """轉成 latest scan JSON metadata。"""

        metadata: dict[str, Any] = {
            "round_index": self.round_index,
            "raw_item_count": self.raw_item_count,
            "unique_item_count": self.unique_item_count,
            "scroll_y": self.scroll_y,
            "scroll_height": self.scroll_height,
        }
        if self.scroll_target_label:
            metadata["scroll_target_label"] = self.scroll_target_label
            metadata["scroll_target_top"] = self.scroll_target_top
            metadata["added_count"] = self.added_count
            metadata["stagnant_windows"] = self.stagnant_windows
        if self.scroll_moved is not None:
            metadata["scroll_moved"] = self.scroll_moved
            metadata["scroll_before_top"] = self.scroll_before_top
            metadata["scroll_after_top"] = self.scroll_after_top
            metadata["scroll_moved_distance"] = self.scroll_moved_distance
            metadata["scroll_step"] = self.scroll_step
            metadata["load_more_mode"] = self.load_more_mode
        return metadata


@dataclass(frozen=True)
class PostScanMetadata:
    """保存 posts scan run metadata。"""

    worker: str
    collection_strategy: str
    auto_load_more: bool
    scroll_collection_enabled: bool
    target_count: int
    scanned_count: int
    candidate_count: int
    round_count: int
    max_window_count: int
    requested_scroll_rounds: int
    scroll_rounds: int
    scroll_wait_ms: int
    load_more_mode: str
    stop_reason: str
    collected_meta: dict[str, Any]
    sort_adjust: dict[str, Any]
    rounds: tuple[PostScanRoundMetadata, ...]

    def to_metadata(self) -> dict[str, Any]:
        """轉成 scan run JSON metadata。"""

        return {
            "worker": self.worker,
            "collection_strategy": self.collection_strategy,
            "auto_load_more": self.auto_load_more,
            "scroll_collection_enabled": self.scroll_collection_enabled,
            "target_count": self.target_count,
            "scanned_count": self.scanned_count,
            "candidate_count": self.candidate_count,
            "round_count": self.round_count,
            "max_window_count": self.max_window_count,
            "requested_scroll_rounds": self.requested_scroll_rounds,
            "scroll_rounds": self.scroll_rounds,
            "scroll_wait_ms": self.scroll_wait_ms,
            "load_more_mode": self.load_more_mode,
            "stop_reason": self.stop_reason,
            "collected_meta": self.collected_meta,
            "sort_adjust": self.sort_adjust,
            "rounds": [round_item.to_metadata() for round_item in self.rounds],
        }


@dataclass(frozen=True)
class CommentScanRoundMetadata:
    """保存 comments extractor 單輪診斷 metadata。"""

    round_index: int
    raw_item_count: int
    unique_item_count: int
    candidate_count: int
    parsed_count: int
    accumulated_count: int
    filtered_empty_text_count: int
    filtered_non_post_count: int
    comments_with_comment_id_count: int
    scroll_moved: bool | None
    scroll_target_label: str
    scroll_before_top: int | None
    scroll_after_top: int | None
    scroll_moved_distance: int | None
    scroll_step: int | None
    load_more_mode: str
    added_count: int
    stagnant_windows: int

    def to_metadata(self) -> dict[str, Any]:
        """轉成 latest scan JSON metadata。"""

        return {
            "round_index": self.round_index,
            "raw_item_count": self.raw_item_count,
            "unique_item_count": self.unique_item_count,
            "candidate_count": self.candidate_count,
            "parsed_count": self.parsed_count,
            "accumulated_count": self.accumulated_count,
            "filtered_empty_text_count": self.filtered_empty_text_count,
            "filtered_non_post_count": self.filtered_non_post_count,
            "comments_with_comment_id_count": self.comments_with_comment_id_count,
            "scroll_moved": self.scroll_moved,
            "scroll_target_label": self.scroll_target_label,
            "scroll_before_top": self.scroll_before_top,
            "scroll_after_top": self.scroll_after_top,
            "scroll_moved_distance": self.scroll_moved_distance,
            "scroll_step": self.scroll_step,
            "load_more_mode": self.load_more_mode,
            "added_count": self.added_count,
            "stagnant_windows": self.stagnant_windows,
        }


@dataclass(frozen=True)
class CommentScanMetadata:
    """保存 comments scan run metadata。"""

    worker: str
    collection_strategy: str
    comment_count: int
    target_count: int
    candidate_count: int
    round_count: int
    requested_scroll_rounds: int
    scroll_rounds: int
    scroll_wait_ms: int
    auto_load_more: bool
    load_more_mode: str
    comment_scroll_collection_enabled: bool
    stop_reason: str
    comment_sort: dict[str, Any]
    comment_extract_rounds: tuple[CommentScanRoundMetadata, ...]
    comments_meta: dict[str, Any]

    def to_metadata(self) -> dict[str, Any]:
        """轉成 scan run JSON metadata。"""

        return {
            "worker": self.worker,
            "collection_strategy": self.collection_strategy,
            "comment_count": self.comment_count,
            "target_count": self.target_count,
            "candidate_count": self.candidate_count,
            "round_count": self.round_count,
            "requested_scroll_rounds": self.requested_scroll_rounds,
            "scroll_rounds": self.scroll_rounds,
            "scroll_wait_ms": self.scroll_wait_ms,
            "auto_load_more": self.auto_load_more,
            "load_more_mode": self.load_more_mode,
            "comment_scroll_collection_enabled": self.comment_scroll_collection_enabled,
            "stop_reason": self.stop_reason,
            "comment_sort": self.comment_sort,
            "comment_extract_rounds": [
                round_item.to_metadata() for round_item in self.comment_extract_rounds
            ],
            "comments_meta": self.comments_meta,
        }
