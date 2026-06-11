"""Facebook sort control compatibility facade。

職責：保留既有 `facebook_monitor.facebook.sort_controls` import surface；
實作分散在 sort_runtime / sort_native_click / sort_scripts / sort_results。
"""

from __future__ import annotations

from facebook_monitor.facebook.sort_native_click import try_native_comment_sort_click
from facebook_monitor.facebook.sort_native_click import try_native_comment_sort_click_async
from facebook_monitor.facebook.sort_native_click import try_native_feed_sort_click
from facebook_monitor.facebook.sort_native_click import try_native_feed_sort_click_async
from facebook_monitor.facebook.sort_results import COMMENT_SORT_DESCRIPTION_FRAGMENTS
from facebook_monitor.facebook.sort_results import COMMENT_SORT_LABELS
from facebook_monitor.facebook.sort_results import COMMENT_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_results import COMMENT_SORT_OPTION_WAIT_INTERVAL_MS
from facebook_monitor.facebook.sort_results import COMMENT_SORT_OPTION_WAIT_TIMEOUT_MS
from facebook_monitor.facebook.sort_results import FEED_SORT_LABELS
from facebook_monitor.facebook.sort_results import FEED_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_results import NativeSortAttempt
from facebook_monitor.facebook.sort_results import NativeSortSpec
from facebook_monitor.facebook.sort_results import SORT_CONFIRM_INTERVAL_MS
from facebook_monitor.facebook.sort_results import SORT_CONFIRM_TIMEOUT_MS
from facebook_monitor.facebook.sort_results import SORT_DIAGNOSTIC_FIELD_ALIASES
from facebook_monitor.facebook.sort_results import SORT_MENU_ROOT_SELECTOR
from facebook_monitor.facebook.sort_results import SORT_METHOD_JS_FALLBACK
from facebook_monitor.facebook.sort_results import SORT_METHOD_NATIVE_LOCATOR
from facebook_monitor.facebook.sort_results import SORT_MUTATION_SUPPRESSION_MS
from facebook_monitor.facebook.sort_results import SORT_MUTATION_SUPPRESSION_REASON
from facebook_monitor.facebook.sort_results import SORT_NATIVE_CLICK_TIMEOUT_MS
from facebook_monitor.facebook.sort_results import SORT_NATIVE_STAGE_CLICK_CONTROL
from facebook_monitor.facebook.sort_results import SORT_NATIVE_STAGE_CONFIRM_LABEL
from facebook_monitor.facebook.sort_results import SORT_NATIVE_STAGE_CURRENT_LABEL
from facebook_monitor.facebook.sort_results import SORT_NATIVE_STAGE_FIND_OPTION
from facebook_monitor.facebook.sort_results import SORT_OPTION_ROLES
from facebook_monitor.facebook.sort_results import SORT_OPTION_WAIT_INTERVAL_MS
from facebook_monitor.facebook.sort_results import SORT_OPTION_WAIT_TIMEOUT_MS
from facebook_monitor.facebook.sort_results import SORT_REASON_ALREADY_PREFERRED_SORT
from facebook_monitor.facebook.sort_results import SORT_REASON_AUTO_ADJUST_DISABLED
from facebook_monitor.facebook.sort_results import SORT_REASON_PREFERRED_SORT_OPTION_NOT_FOUND
from facebook_monitor.facebook.sort_results import SORT_REASON_RESULT_INVALID
from facebook_monitor.facebook.sort_results import SORT_REASON_SORT_CONTROL_NOT_FOUND
from facebook_monitor.facebook.sort_results import SORT_REASON_SORT_UPDATE_UNCONFIRMED
from facebook_monitor.facebook.sort_results import SORT_REASON_UNSUPPORTED_SCAN_TARGET
from facebook_monitor.facebook.sort_results import SORT_REASON_UPDATED_TO_PREFERRED_SORT
from facebook_monitor.facebook.sort_results import SortAdjustResult
from facebook_monitor.facebook.sort_results import _with_sort_diagnostics
from facebook_monitor.facebook.sort_results import build_disabled_sort_adjust_result
from facebook_monitor.facebook.sort_results import normalize_sort_adjust_result
from facebook_monitor.facebook.sort_runtime import ensure_preferred_comment_sort
from facebook_monitor.facebook.sort_runtime import ensure_preferred_comment_sort_async
from facebook_monitor.facebook.sort_runtime import ensure_preferred_feed_sort
from facebook_monitor.facebook.sort_runtime import ensure_preferred_feed_sort_async
from facebook_monitor.facebook.sort_scripts import COMMENT_SORT_ADJUST_SCRIPT
from facebook_monitor.facebook.sort_scripts import COMMENT_SORT_CURRENT_LABEL_SCRIPT
from facebook_monitor.facebook.sort_scripts import FEED_SORT_ADJUST_SCRIPT
from facebook_monitor.facebook.sort_scripts import FEED_SORT_CURRENT_LABEL_SCRIPT
from facebook_monitor.facebook.sort_scripts import SORT_MENU_CANDIDATE_TEXTS_SCRIPT

__all__ = [
    "COMMENT_SORT_ADJUST_SCRIPT",
    "COMMENT_SORT_CURRENT_LABEL_SCRIPT",
    "COMMENT_SORT_DESCRIPTION_FRAGMENTS",
    "COMMENT_SORT_LABELS",
    "COMMENT_SORT_NEWEST_LABEL",
    "COMMENT_SORT_OPTION_WAIT_INTERVAL_MS",
    "COMMENT_SORT_OPTION_WAIT_TIMEOUT_MS",
    "FEED_SORT_ADJUST_SCRIPT",
    "FEED_SORT_CURRENT_LABEL_SCRIPT",
    "FEED_SORT_LABELS",
    "FEED_SORT_NEWEST_LABEL",
    "NativeSortAttempt",
    "NativeSortSpec",
    "SORT_CONFIRM_INTERVAL_MS",
    "SORT_CONFIRM_TIMEOUT_MS",
    "SORT_DIAGNOSTIC_FIELD_ALIASES",
    "SORT_MENU_CANDIDATE_TEXTS_SCRIPT",
    "SORT_MENU_ROOT_SELECTOR",
    "SORT_METHOD_JS_FALLBACK",
    "SORT_METHOD_NATIVE_LOCATOR",
    "SORT_MUTATION_SUPPRESSION_MS",
    "SORT_MUTATION_SUPPRESSION_REASON",
    "SORT_NATIVE_CLICK_TIMEOUT_MS",
    "SORT_NATIVE_STAGE_CLICK_CONTROL",
    "SORT_NATIVE_STAGE_CONFIRM_LABEL",
    "SORT_NATIVE_STAGE_CURRENT_LABEL",
    "SORT_NATIVE_STAGE_FIND_OPTION",
    "SORT_OPTION_ROLES",
    "SORT_OPTION_WAIT_INTERVAL_MS",
    "SORT_OPTION_WAIT_TIMEOUT_MS",
    "SORT_REASON_ALREADY_PREFERRED_SORT",
    "SORT_REASON_AUTO_ADJUST_DISABLED",
    "SORT_REASON_PREFERRED_SORT_OPTION_NOT_FOUND",
    "SORT_REASON_RESULT_INVALID",
    "SORT_REASON_SORT_CONTROL_NOT_FOUND",
    "SORT_REASON_SORT_UPDATE_UNCONFIRMED",
    "SORT_REASON_UNSUPPORTED_SCAN_TARGET",
    "SORT_REASON_UPDATED_TO_PREFERRED_SORT",
    "SortAdjustResult",
    "_with_sort_diagnostics",
    "build_disabled_sort_adjust_result",
    "ensure_preferred_comment_sort",
    "ensure_preferred_comment_sort_async",
    "ensure_preferred_feed_sort",
    "ensure_preferred_feed_sort_async",
    "normalize_sort_adjust_result",
    "try_native_comment_sort_click",
    "try_native_comment_sort_click_async",
    "try_native_feed_sort_click",
    "try_native_feed_sort_click_async",
]
