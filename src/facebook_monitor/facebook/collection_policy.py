"""Facebook 掃描收集策略。

職責：集中 userscript 的 scan limit / dynamic window 規則，避免 worker、
Web UI 與 extractor 各自推估掃描上限。
"""

from __future__ import annotations

from facebook_monitor.core.scan_limits import clamp_target_post_count

CANDIDATE_MULTIPLIER = 6
MAX_WINDOW_MULTIPLIER = 2
CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT = 3


def get_candidate_collection_limit(target_count: int) -> int:
    """根據目標貼文數推估候選容器上限。"""

    return max(12, clamp_target_post_count(target_count) * CANDIDATE_MULTIPLIER)


def get_dynamic_max_windows(target_count: int) -> int:
    """根據目標貼文數推估安全掃描視窗上限。"""

    return clamp_target_post_count(target_count) * MAX_WINDOW_MULTIPLIER


def get_dynamic_scroll_rounds(target_count: int) -> int:
    """將 JS 的 maxWindowCount 轉成 Python 的 scroll action 次數。"""

    return max(get_dynamic_max_windows(target_count) - 1, 0)


def get_effective_scroll_rounds(
    *,
    target_count: int,
    requested_scroll_rounds: int,
    auto_load_more: bool,
) -> int:
    """決定本輪 posts 掃描可使用的最大捲動次數。

    `requested_scroll_rounds=0` 保留給 probe / fallback 用來強制只掃目前視窗；
    其他情況則以 userscript 的 targetCount * 2 動態安全上限為主。
    """

    if not auto_load_more:
        return 0
    normalized_requested = max(int(requested_scroll_rounds), 0)
    if normalized_requested == 0:
        return 0
    return max(normalized_requested, get_dynamic_scroll_rounds(target_count))
