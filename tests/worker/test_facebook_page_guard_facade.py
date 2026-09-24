"""Facebook page guard 相容 façade 的公開契約測試。"""

from facebook_monitor.worker import facebook_page_guard


_EXPECTED_PUBLIC_NAMES = [
    "AsyncScannablePageLike",
    "FACEBOOK_PAGE_GUARD_EVIDENCE_CODE",
    "FacebookPageGuardDiagnostics",
    "FacebookPageGuardEvidence",
    "FacebookPageGuardFinding",
    "SyncScannablePageLike",
    "assess_async_facebook_page",
    "assess_sync_facebook_page",
    "classify_facebook_content_unavailable_evidence",
    "classify_facebook_scan_page_failure",
    "classify_facebook_session_failure",
    "classify_facebook_temporary_block",
    "ensure_async_page_logged_in",
    "ensure_async_page_scannable",
    "ensure_facebook_login_present",
    "ensure_sync_page_logged_in",
    "ensure_sync_page_scannable",
]


def test_facebook_page_guard_facade_preserves_public_exports() -> None:
    """鎖定拆分前的公開名稱，避免既有 caller import 被悄悄破壞。"""

    assert facebook_page_guard.__all__ == _EXPECTED_PUBLIC_NAMES
    for name in _EXPECTED_PUBLIC_NAMES:
        getattr(facebook_page_guard, name)
