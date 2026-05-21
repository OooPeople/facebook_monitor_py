"""Scan failure policy tests。"""

from __future__ import annotations

from facebook_monitor.core.scan_failure_policy import decide_scan_failure


def test_page_load_timeout_retries_until_third_failure() -> None:
    """page_load_timeout 前兩次回 idle，第三次才進 error。"""

    first = decide_scan_failure("page_load_timeout", source="playwright")
    second = decide_scan_failure(
        "page_load_timeout",
        source="playwright",
        previous_failure_reason="page_load_timeout",
        previous_failure_count=1,
    )
    third = decide_scan_failure(
        "page_load_timeout",
        source="playwright",
        previous_failure_reason="page_load_timeout",
        previous_failure_count=2,
    )

    assert first.retryable is True
    assert first.target_action == "idle"
    assert first.runtime_action == "will_retry"
    assert first.discard_page is True
    assert first.retry_streak == 1
    assert first.retry_limit == 3
    assert second.retryable is True
    assert second.target_action == "idle"
    assert second.retry_streak == 2
    assert third.retryable is False
    assert third.target_action == "error"
    assert third.runtime_action == "error"
    assert third.retry_streak == 3


def test_legacy_retryable_idle_failures_do_not_count_toward_streak() -> None:
    """extractor_empty / target_stopped 保留既有回 idle 語義但不累計 streak。"""

    decision = decide_scan_failure(
        "extractor_empty",
        source="worker_failure",
        previous_failure_reason="page_load_timeout",
        previous_failure_count=2,
    )

    assert decision.retryable is True
    assert decision.target_action == "idle"
    assert decision.runtime_action == "idle"
    assert decision.counts_toward_streak is False
    assert decision.retry_streak == 0


def test_scan_timeout_still_errors_immediately() -> None:
    """scan_timeout 是 worker 自身逾時，不能被 page_load_timeout 策略同化。"""

    decision = decide_scan_failure("scan_timeout", source="worker_failure")

    assert decision.retryable is False
    assert decision.target_action == "error"
    assert decision.runtime_action == "error"
    assert decision.counts_toward_streak is False
    assert decision.discard_page is False


def test_scheduler_cancel_keeps_target_idle() -> None:
    """scheduler stop 取消中的掃描不應把 target 變成終止 error。"""

    decision = decide_scan_failure("scheduler_stopping", source="scheduler_cancel")

    assert decision.retryable is True
    assert decision.target_action == "idle"
    assert decision.runtime_action == "idle"
    assert decision.counts_toward_streak is False
    assert decision.discard_page is False
