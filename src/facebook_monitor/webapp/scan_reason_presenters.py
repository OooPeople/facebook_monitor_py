"""Scan reason code 的 Web UI 顯示文字。"""

from __future__ import annotations

from facebook_monitor.core.user_messages import format_failure_reason as _format_failure_reason


def format_scan_stop_reason(value: str) -> str:
    """把 worker 內部停止原因轉成 UI 可讀文字。"""

    labels = {
        "target_count_reached": "達到目標筆數",
        "seen_stop_consecutive_seen": "前段內容重複，略過深度掃描",
        "scroll_rounds_completed": "完成捲動輪數",
        "scroll_stalled": "頁面未產生可用捲動",
        "stagnant_windows": "連續多輪沒有新增項目",
        "collection_stopped": "抽取流程停止",
        "no_round_stats": "無輪次資料",
        "visible_window_completed": "完成可見留言抽取",
        "auto_load_more_disabled": "已停用自動載入更多",
        "no_comment_round_stats": "無留言輪次資料",
        "comment_scroll_stalled": "留言區未產生可用捲動",
        "comment_stagnant_windows": "留言連續多輪沒有新增項目",
        "comment_scroll_rounds_completed": "完成留言捲動輪數",
        "comment_collection_stopped": "留言抽取流程停止",
        "comment_load_more_guard_active": "留言載入更多 guard 使用中",
        "sort_adjust_unconfirmed_skip": "調整排序失敗，已跳過掃描",
    }
    return labels.get(value, value or "(未知)")


def format_scan_failure_reason(value: str) -> str:
    """把 failed scan reason 轉成 UI 可讀文字。"""

    return _format_failure_reason(value)


def format_scan_cycle_result_reason(value: str) -> str:
    """把最近一輪停止原因轉成 target card 可讀的低干擾文案。"""

    labels = {
        "target_count_reached": "已達目標項目數",
        "seen_stop_consecutive_seen": "前段內容重複，略過深度掃描",
        "scroll_rounds_completed": "已完成深度掃描",
        "scroll_stalled": "頁面無法繼續捲動",
        "stagnant_windows": "多輪未找到新項目",
        "collection_stopped": "抽取流程結束",
        "no_round_stats": "沒有掃描輪次資料",
        "visible_window_completed": "已完成可見留言掃描",
        "auto_load_more_disabled": "未啟用深度掃描",
        "no_comment_round_stats": "沒有留言掃描輪次資料",
        "comment_scroll_stalled": "留言區無法繼續捲動",
        "comment_stagnant_windows": "多輪未找到新留言",
        "comment_scroll_rounds_completed": "已完成留言深度掃描",
        "comment_collection_stopped": "留言抽取流程結束",
        "comment_load_more_guard_active": "留言載入更多正在使用中",
        "sort_adjust_unconfirmed_skip": "調整排序失敗，已跳過掃描",
    }
    return labels.get(value, value or "未知原因")
