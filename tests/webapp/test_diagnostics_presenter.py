"""Scan diagnostics presenter tests。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from datetime import timezone

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import NotificationOutboxSummary
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.webapp.scan_diagnostics_items import (
    format_latest_scan_item_debug_lines,
)
from facebook_monitor.webapp.scan_diagnostics_presenter import (
    build_scan_diagnostics_text,
)
from facebook_monitor.webapp.scan_diagnostics_sort_sections import (
    append_sort_diagnostics_block,
)
from facebook_monitor.webapp.scan_reason_presenters import format_scan_failure_reason
from facebook_monitor.webapp.scan_reason_presenters import format_scan_cycle_result_reason
from facebook_monitor.webapp.scan_reason_presenters import format_scan_stop_reason
from facebook_monitor.webapp.time_presenters import format_datetime_for_ui


_STARTED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
_FINISHED_AT = datetime(2026, 1, 2, 3, 4, 35, tzinfo=timezone.utc)
_OLD_PENDING_AT = datetime(2026, 1, 2, 3, 5, 5, tzinfo=timezone.utc)


def _target() -> TargetDescriptor:
    """建立穩定 target fixture，讓 diagnostics text 順序測試容易閱讀。"""

    return TargetDescriptor(
        id="target-1",
        name="票券社團",
        target_kind=TargetKind.POSTS,
        group_id="group-1",
        scope_id="group-1",
        canonical_url="https://www.facebook.com/groups/group-1",
    )


def _runtime_state() -> TargetRuntimeState:
    """建立含 runtime diagnostics 欄位的 target state fixture。"""

    return TargetRuntimeState(
        target_id="target-1",
        runtime_status=TargetRuntimeStatus.RUNNING,
        last_page_reloaded_at=_STARTED_AT,
        last_enqueued_at=_STARTED_AT,
        last_started_at=_STARTED_AT,
        last_finished_at=_FINISHED_AT,
        enqueue_reason="manual",
        active_worker_id="worker-1",
        active_page_id="page-1",
        scan_guard_count=2,
    )


def _outbox_summary() -> NotificationOutboxSummary:
    """建立穩定 outbox summary fixture。"""

    return NotificationOutboxSummary(
        target_id="target-1",
        pending_count=1,
        processing_count=2,
        failed_count=3,
        terminal_count=4,
        oldest_pending_updated_at=_OLD_PENDING_AT,
        max_attempts=5,
    )


def _config(*, max_items_per_scan: int | None = None) -> TargetConfig:
    """建立穩定 target config fixture。"""

    config = TargetConfig(target_id="target-1")
    if max_items_per_scan is None:
        return config
    return replace(config, max_items_per_scan=max_items_per_scan)


def test_empty_scan_diagnostics_text_keeps_ordered_lines() -> None:
    """尚無掃描時，診斷文字順序與 outbox 位置不可漂移。"""

    oldest_pending_at = format_datetime_for_ui(_OLD_PENDING_AT)
    text = build_scan_diagnostics_text(
        target=_target(),
        config=_config(),
        runtime_state=_runtime_state(),
        latest_scan_run=None,
        notification_outbox_summary=_outbox_summary(),
    )

    assert text.splitlines() == [
        "target_id=target-1",
        "target_kind=posts",
        "group_id=group-1",
        "parent_post_id=(none)",
        "scope_id=group-1",
        "runtime_status=running",
        "scan_status=(none)",
        "outbox=pending:1,processing:2,failed:3,terminal:4,"
        f"oldest_pending:{oldest_pending_at},max_attempts:5",
        "note=尚無掃描診斷",
    ]


def test_success_scan_diagnostics_text_keeps_ordered_sections() -> None:
    """成功掃描診斷文字需保留 section 順序與 metadata_json 最後一行。"""

    scan = ScanRun(
        target_id="target-1",
        status=ScanStatus.SUCCESS,
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
        item_count=2,
        matched_count=1,
        metadata={
            "new_count": 1,
            "target_count": 3,
            "candidate_count": 2,
            "round_count": 1,
            "stop_reason": "target_count_reached",
            "sort_adjust": {
                "attempted": True,
                "changed": False,
                "preferred_label": "新貼文",
                "before_label": "熱門貼文",
                "after_label": "新貼文",
                "reason": "already_preferred",
                "mutation_suppression_ms": 3200,
                "mutation_suppression_reason": "auto_adjust_sort",
            },
            "comments_meta": {"mode": "comments", "targetCount": 2},
            "collected_meta": {"mode": "posts", "attempted": True},
            "rounds": [
                {
                    "round_index": 0,
                    "raw_item_count": 3,
                    "unique_item_count": 2,
                    "scroll_y": 100,
                    "scroll_height": 500,
                }
            ],
        },
    )
    latest_item = LatestScanItem(
        target_id="target-1",
        scan_run_id=1,
        item_kind=ItemKind.POST,
        item_key="post-1",
        item_index=0,
        author="作者",
        text="第一行 第二行",
        display_text="第一行\n第二行",
        permalink="https://example.test/post",
        matched_keyword="票",
        debug_metadata={"textLength": 7},
    )

    started_at = format_datetime_for_ui(_STARTED_AT)
    finished_at = format_datetime_for_ui(_FINISHED_AT)
    oldest_pending_at = format_datetime_for_ui(_OLD_PENDING_AT)
    lines = build_scan_diagnostics_text(
        target=_target(),
        config=_config(max_items_per_scan=3),
        runtime_state=_runtime_state(),
        latest_scan_run=scan,
        latest_scan_items=(latest_item,),
        notification_outbox_summary=_outbox_summary(),
    ).splitlines()

    assert lines[:24] == [
        "target_id=target-1",
        "target_kind=posts",
        "group_id=group-1",
        "parent_post_id=(none)",
        "scope_id=group-1",
        "runtime_status=running",
        "queued=False",
        "running=True",
        "active_worker_id=worker-1",
        "active_page_id=page-1",
        f"last_page_reloaded_at={started_at}",
        "enqueue_reason=manual",
        f"last_enqueued_at={started_at}",
        f"last_started_at={started_at}",
        f"last_finished_at={finished_at}",
        "scan_guard_count=2",
        "last_skip_reason=(none)",
        "outbox=pending:1,processing:2,failed:3,terminal:4,"
        f"oldest_pending:{oldest_pending_at},max_attempts:5",
        "scan_status=success",
        f"finished_at={finished_at}",
        "item_count=2",
        "matched_count=1",
        "new_count=1",
        "target_count=3",
    ]
    assert lines.index("sort_adjust:") < lines.index("comments_meta:")
    assert lines.index("comments_meta:") < lines.index("collected_meta:")
    assert lines.index("rounds:") < lines.index("latest_scan_items:")
    assert lines[-1].startswith("metadata_json=")


def test_failed_scan_diagnostics_text_keeps_failure_fields_and_latest_failed_scan() -> None:
    """failed scan diagnostics 需保留 failed-only 欄位與 latest_failed_scan 區塊。"""

    scan = ScanRun(
        target_id="target-1",
        status=ScanStatus.FAILED,
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
        item_count=0,
        matched_count=0,
        error_message="page_load_timeout: timeout",
        metadata={
            "reason": "page_load_timeout",
            "retryable": True,
            "runtime_action": "will_retry",
            "retry_streak": 1,
            "retry_limit": 3,
        },
    )

    lines = build_scan_diagnostics_text(
        target=_target(),
        config=_config(),
        runtime_state=_runtime_state(),
        latest_scan_run=scan,
        latest_failed_scan_run=scan,
    ).splitlines()

    assert "scan_status=failed" in lines
    assert "failure_reason=頁面載入逾時" in lines
    assert "retryable=True" in lines
    assert "runtime_action=will_retry" in lines
    assert "retry_streak=1" in lines
    assert "retry_limit=3" in lines
    assert lines[lines.index("latest_failed_scan:") - 1] == ""
    assert "reason=頁面載入逾時" in lines
    assert (
        "error=頁面載入逾時：頁面載入、重新導向或重新整理時中斷，"
        "掃描中的頁面內容已失效；請稍後重試。"
    ) in lines
    assert lines[-1].startswith("metadata_json=")


def test_append_sort_block_shows_menu_candidate_texts_when_present() -> None:
    """sort block 有候選文字時才顯示，方便複製給 review。"""

    lines: list[str] = []
    append_sort_diagnostics_block(
        lines,
        "comment_sort",
        {
            "attempted": True,
            "changed": False,
            "preferred_label": "由新到舊",
            "before_label": "最相關",
            "after_label": "最相關",
            "reason": "preferred_sort_option_not_found",
            "mutation_suppression_ms": 3200,
            "mutation_suppression_reason": "auto_adjust_sort",
            "menu_candidate_texts": ["最相關", "所有留言"],
        },
    )

    assert 'menu_candidate_texts=["最相關", "所有留言"]' in lines


def test_append_sort_block_hides_empty_menu_candidate_texts() -> None:
    """空候選文字不污染日常 diagnostics。"""

    lines: list[str] = []
    append_sort_diagnostics_block(
        lines,
        "comment_sort",
        {
            "attempted": False,
            "changed": False,
            "preferred_label": "由新到舊",
            "menu_candidate_texts": [],
        },
    )

    assert not any(line.startswith("menu_candidate_texts=") for line in lines)


def test_append_sort_block_shows_native_and_fallback_diagnostics() -> None:
    """sort block 顯示 native/fallback 階段診斷，方便排查排序失敗。"""

    lines: list[str] = []
    append_sort_diagnostics_block(
        lines,
        "comment_sort",
        {
            "attempted": True,
            "changed": False,
            "preferred_label": "由新到舊",
            "before_label": "最相關",
            "after_label": "最相關",
            "reason": "preferred_sort_option_not_found",
            "method": "js_fallback",
            "fallback_used": True,
            "fallback_recovery": "escape",
            "native_failure_stage": "click_control",
            "native_exception_class": "TimeoutError",
            "menu_opened": False,
            "preferred_option_count": 0,
            "clicked_option_text": "",
        },
    )

    assert "method=js_fallback" in lines
    assert "fallback_used=True" in lines
    assert "fallback_recovery=escape" in lines
    assert "native_failure_stage=click_control" in lines
    assert "native_exception_class=TimeoutError" in lines
    assert "menu_opened=False" in lines
    assert "preferred_option_count=0" in lines
    assert not any(line.startswith("clicked_option_text=") for line in lines)


def test_latest_scan_debug_lines_show_display_text_lengths() -> None:
    """latest item diagnostics 需顯示 display text 長度，方便判斷換行抽取狀態。"""

    lines = format_latest_scan_item_debug_lines(
        LatestScanItem(
            target_id="target-1",
            scan_run_id=1,
            item_kind=ItemKind.POST,
            item_key="item-1",
            item_index=0,
            author="作者",
            text="第一行 第二行",
            display_text="第一行\n第二行",
            debug_metadata={
                "textLength": 7,
                "displayTextLength": 6,
                "rawTextLength": 7,
                "rawDisplayTextLength": 6,
            },
        )
    )

    assert "  text=第一行 第二行" in lines
    assert "  textLength=7" in lines
    assert "  displayTextLength=6" in lines
    assert "  rawTextLength=7" in lines
    assert "  rawDisplayTextLength=6" in lines


def test_sort_unconfirmed_skip_reason_is_user_readable() -> None:
    """排序未確認的保護性跳過會顯示在本輪結果位置。"""

    assert (
        format_scan_cycle_result_reason("sort_adjust_unconfirmed_skip")
        == "調整排序失敗，已跳過掃描"
    )


def test_content_unavailable_failure_reason_is_user_readable() -> None:
    """內容不可見的 failed scan reason 會顯示成連結已失效。"""

    assert format_scan_failure_reason("content_unavailable") == "連結已失效"
    assert (
        format_scan_stop_reason("sort_adjust_unconfirmed_skip")
        == "調整排序失敗，已跳過掃描"
    )
