"""Shared scan orchestration tests。"""

from __future__ import annotations

import pytest

from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.scan_orchestration import classify_facebook_session_failure
from facebook_monitor.worker.scan_orchestration import classify_facebook_content_unavailable
from facebook_monitor.worker.scan_orchestration import ensure_facebook_content_available
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


def test_classifies_facebook_content_unavailable_page() -> None:
    """scan guard 會辨識 Facebook 內容不可見頁，避免誤歸類成排序失敗。"""

    body_text = (
        "目前無法查看此內容 "
        "會發生此情況，通常是因為擁有者僅與一小群用戶分享內容、"
        "變更了分享對象，或是刪除了內容。"
    )

    assert (
        classify_facebook_content_unavailable(
            body_text,
            "https://www.facebook.com/groups/1370511589953459/posts/2772468963091041",
        )
        == "content_unavailable"
    )
    assert classify_facebook_content_unavailable("社團貼文列表") is None


def test_ensure_facebook_content_available_raises_worker_failure_reason() -> None:
    """內容不可見時保留獨立 failure reason。"""

    with pytest.raises(WorkerFailure) as exc_info:
        ensure_facebook_content_available(
            "This content isn't available right now. The owner may have deleted it.",
            "https://www.facebook.com/groups/1/posts/2",
        )

    assert exc_info.value.reason == "content_unavailable"
    assert "目前無法查看此內容" in str(exc_info.value)
