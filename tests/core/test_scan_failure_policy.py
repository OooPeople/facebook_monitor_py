"""Scan failure policy tests。"""

from __future__ import annotations

from facebook_monitor.core.scan_failure_policy import decide_scan_failure
from facebook_monitor.core.scan_failure_policy import is_runtime_failure_notification_terminal


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
    assert first.notification_failure_count == 1
    assert second.retryable is True
    assert second.target_action == "idle"
    assert second.retry_streak == 2
    assert third.retryable is False
    assert third.target_action == "error"
    assert third.runtime_action == "error"
    assert third.retry_streak == 3
    assert third.notification_failure_count == 3


def test_scheduler_runtime_retries_until_third_failure() -> None:
    """scheduler_runtime 前兩次只要求重啟 runtime，第三次才進 error。"""

    first = decide_scan_failure("scheduler_runtime", source="unknown_exception")
    third = decide_scan_failure(
        "scheduler_runtime",
        source="unknown_exception",
        previous_failure_reason="scheduler_runtime",
        previous_failure_count=2,
    )

    assert first.retryable is True
    assert first.target_action == "idle"
    assert first.runtime_action == "will_retry"
    assert first.auto_restart is True
    assert first.recovery_action == "scheduler_runtime_restart"
    assert first.retry_streak == 1
    assert first.retry_limit == 3
    assert third.retryable is False
    assert third.target_action == "error"
    assert third.runtime_action == "error"
    assert third.retry_streak == 3


def test_unknown_retries_by_default_until_third_failure() -> None:
    """未明確列為 terminal 的 reason 預設三次重試。"""

    first = decide_scan_failure("unknown", source="unknown_exception")
    third = decide_scan_failure(
        "unknown",
        source="unknown_exception",
        previous_failure_reason="unknown",
        previous_failure_count=2,
    )

    assert first.retryable is True
    assert first.target_action == "idle"
    assert first.runtime_action == "will_retry"
    assert first.discard_page is True
    assert first.auto_restart is True
    assert first.recovery_action == "target_page_restart"
    assert first.retry_streak == 1
    assert first.retry_limit == 3
    assert third.retryable is False
    assert third.target_action == "error"
    assert third.retry_streak == 3


def test_runtime_failure_notification_terminal_uses_failure_count() -> None:
    """runtime failure 通知資格用 failure_count 對齊 retry terminal 門檻。"""

    assert (
        is_runtime_failure_notification_terminal("unknown", failure_count=1)
        is False
    )
    assert (
        is_runtime_failure_notification_terminal("unknown", failure_count=3)
        is True
    )
    assert (
        is_runtime_failure_notification_terminal("login_required", failure_count=1)
        is True
    )
    assert (
        is_runtime_failure_notification_terminal("scheduler_stopping", failure_count=3)
        is False
    )


def test_scan_timeout_retries_and_discards_page() -> None:
    """scan_timeout 也應重啟 page，避免同一頁面狀態持續卡住。"""

    decision = decide_scan_failure("scan_timeout", source="worker_failure")

    assert decision.retryable is True
    assert decision.target_action == "idle"
    assert decision.runtime_action == "will_retry"
    assert decision.counts_toward_streak is True
    assert decision.discard_page is True
    assert decision.auto_restart is True


def test_extractor_empty_retries_instead_of_silent_idle() -> None:
    """extractor_empty 代表抽取異常，應累計重試而不是永遠安靜略過。"""

    decision = decide_scan_failure("extractor_empty", source="worker_failure")

    assert decision.retryable is True
    assert decision.target_action == "idle"
    assert decision.runtime_action == "will_retry"
    assert decision.counts_toward_streak is True
    assert decision.retry_streak == 1
    assert decision.discard_page is True


def test_sort_adjust_unconfirmed_is_recoverable_page_restart() -> None:
    """排序未確認升級後應走 target page restart，第三次才 terminal。"""

    first = decide_scan_failure("sort_adjust_unconfirmed", source="worker_failure")
    third = decide_scan_failure(
        "sort_adjust_unconfirmed",
        source="worker_failure",
        previous_failure_reason="sort_adjust_unconfirmed",
        previous_failure_count=2,
    )

    assert first.retryable is True
    assert first.target_action == "idle"
    assert first.recovery_action == "target_page_restart"
    assert first.retry_streak == 1
    assert first.retry_limit == 3
    assert third.retryable is False
    assert third.target_action == "error"
    assert third.retry_streak == 3


def test_target_stopped_keeps_target_idle_without_streak() -> None:
    """target_stopped 是使用者停止造成的非錯誤收斂，不應累計 streak。"""

    decision = decide_scan_failure(
        "target_stopped",
        source="worker_failure",
        previous_failure_reason="unknown",
        previous_failure_count=2,
    )

    assert decision.retryable is True
    assert decision.target_action == "idle"
    assert decision.runtime_action == "idle"
    assert decision.counts_toward_streak is False
    assert decision.retry_streak == 0


def test_login_required_errors_immediately() -> None:
    """登入/session 類錯誤需要使用者介入，不能延後到第三次才通知。"""

    terminal_reasons = (
        "login_required",
        "checkpoint_required",
        "session_invalid",
        "profile_missing",
        "profile_locked",
        "target_missing",
        "target_invalid",
        "target_kind_unsupported",
        "target_argument_conflict",
        "content_unavailable",
    )

    for reason in terminal_reasons:
        decision = decide_scan_failure(reason, source="worker_failure")

        assert decision.reason == reason
        assert decision.retryable is False
        assert decision.target_action == "error"
        assert decision.runtime_action == "error"
        assert decision.counts_toward_streak is False
        assert decision.notification_failure_count == 1


def test_scheduler_cancel_keeps_target_idle() -> None:
    """scheduler stop 取消中的掃描不應把 target 變成終止 error。"""

    decision = decide_scan_failure("scheduler_stopping", source="scheduler_cancel")

    assert decision.retryable is True
    assert decision.target_action == "idle"
    assert decision.runtime_action == "idle"
    assert decision.counts_toward_streak is False
    assert decision.discard_page is False
