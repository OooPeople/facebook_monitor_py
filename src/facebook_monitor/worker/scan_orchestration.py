"""Shared scan orchestration helpers。

職責：集中 posts/comments pipeline 共用的頁面 guard 與 scan policy 計算。
正式產品主路徑仍是 async resident；sync path 只作 fallback/debug。
"""

from __future__ import annotations

from typing import Any

from facebook_monitor.core.models import TargetConfig
from facebook_monitor.facebook.collection_policy import get_effective_scroll_rounds
from facebook_monitor.worker.errors import WorkerFailure

PROFILE_SESSION_FAILURE_REASONS = frozenset(
    {"login_required", "checkpoint_required", "session_invalid"}
)


def classify_facebook_session_failure(
    body_text: str,
    current_url: str = "",
) -> str | None:
    """依目前頁面資訊分類 Facebook session 失效原因。"""

    normalized_text = body_text.lower()
    normalized_url = current_url.lower()
    if _looks_like_checkpoint(normalized_text, normalized_url):
        return "checkpoint_required"
    if _looks_like_session_invalid(normalized_text, normalized_url):
        return "session_invalid"
    if _looks_like_login_page(normalized_text, normalized_url):
        return "login_required"
    return None


def ensure_facebook_login_present(body_text: str, current_url: str = "") -> None:
    """檢查頁面是否要求登入；需要登入時拋出 worker failure。"""

    reason = classify_facebook_session_failure(body_text, current_url)
    if reason:
        raise WorkerFailure(reason, "Facebook login is required.")


def ensure_sync_page_logged_in(page: Any) -> None:
    """sync Playwright page 登入 guard。"""

    ensure_facebook_login_present(
        page.locator("body").inner_text(timeout=10000),
        str(getattr(page, "url", "") or ""),
    )


async def ensure_async_page_logged_in(page: Any) -> None:
    """async Playwright page 登入 guard。"""

    body_text = await page.locator("body").inner_text(timeout=10000)
    ensure_facebook_login_present(body_text, str(getattr(page, "url", "") or ""))


def _looks_like_login_page(normalized_text: str, normalized_url: str) -> bool:
    """判斷 Facebook 是否落在登入頁或登入提示。"""

    if "/login" in normalized_url:
        return True
    login_markers = (
        "log into facebook",
        "log in to facebook",
        "登入 facebook",
        "登入你的 facebook",
    )
    return any(marker in normalized_text for marker in login_markers)


def _looks_like_checkpoint(normalized_text: str, normalized_url: str) -> bool:
    """判斷 Facebook 是否要求 checkpoint / 安全驗證。"""

    if "/checkpoint" in normalized_url:
        return True
    checkpoint_markers = (
        "checkpoint",
        "security check",
        "confirm your identity",
        "確認你的身分",
        "確認你的身份",
        "安全檢查",
    )
    return any(marker in normalized_text for marker in checkpoint_markers)


def _looks_like_session_invalid(normalized_text: str, normalized_url: str) -> bool:
    """判斷 Facebook 是否顯示 session 已過期。"""

    if "/recover" in normalized_url:
        return True
    session_markers = (
        "session expired",
        "please log in again",
        "請重新登入",
        "工作階段已過期",
    )
    return any(marker in normalized_text for marker in session_markers)


def resolve_effective_scan_scroll_rounds(
    *,
    config: TargetConfig,
    requested_scroll_rounds: int,
) -> int:
    """依 target config 與外部 request 計算實際 scroll rounds。"""

    return get_effective_scroll_rounds(
        target_count=config.max_items_per_scan,
        requested_scroll_rounds=requested_scroll_rounds,
        auto_load_more=config.auto_load_more,
    )
