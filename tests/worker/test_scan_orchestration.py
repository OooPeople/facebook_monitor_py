"""Shared scan orchestration tests。"""

from __future__ import annotations

import pytest

from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.scan_orchestration import classify_facebook_session_failure
from facebook_monitor.worker.scan_orchestration import ensure_facebook_login_present


def test_classifies_facebook_login_failure_from_url_and_body() -> None:
    """scan guard 會把登入、checkpoint 與 session 過期分類成穩定 reason。"""

    assert (
        classify_facebook_session_failure(
            "Log into Facebook to continue",
            "https://www.facebook.com/login/",
        )
        == "login_required"
    )
    assert (
        classify_facebook_session_failure(
            "Confirm your identity",
            "https://www.facebook.com/checkpoint/123",
        )
        == "checkpoint_required"
    )
    assert classify_facebook_session_failure("Session expired. Please log in again") == (
        "session_invalid"
    )
    assert classify_facebook_session_failure("社團貼文列表", "https://www.facebook.com/") is None


def test_ensure_facebook_login_present_raises_worker_failure_reason() -> None:
    """需要重新登入時保留可被 profile status 判斷的 WorkerFailure reason。"""

    with pytest.raises(WorkerFailure) as exc_info:
        ensure_facebook_login_present(
            "安全檢查",
            "https://www.facebook.com/checkpoint/",
        )

    assert exc_info.value.reason == "checkpoint_required"
