"""掃描項目的去重 key 與 alias 規則。

職責：集中保存 post / comment key 與 alias 規則，讓 seen、history
與 latest scan item 使用同一套穩定 key。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

from facebook_monitor.core.keyword_rules import normalize_for_match
from facebook_monitor.core.permalink_identity import extract_comment_id_from_value
from facebook_monitor.core.permalink_identity import normalize_permalink


POST_ID_PATTERN = re.compile(r"/posts/(\d{8,})(?:$|[/?#])")


@dataclass(frozen=True)
class ScanItemIdentity:
    """保存建立掃描項目去重 key 需要的最小欄位。"""

    text: str = ""
    permalink: str = ""
    author: str = ""
    timestamp_text: str = ""
    post_id: str = ""
    parent_post_id: str = ""
    comment_id: str = ""
    item_kind: str = "post"


def hash_item_key(raw_key: str) -> str:
    """將 raw key 雜湊成可保存於 DB 的穩定 key。"""

    return "v2:" + hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def normalize_for_key(value: object) -> str:
    """轉成較穩定的 key 片段，只保留中英文與數字。"""

    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", normalize_for_match(value))


def build_stable_text_signature(value: object) -> str:
    """將文字壓成較短且穩定的 fallback signature。"""

    return normalize_for_key(value)[:120]


def build_legacy_text_fingerprint(raw_text: str) -> str:
    """保留早期 probe 舊版文字 fingerprint，讓舊 seen row 仍可擋重複通知。"""

    text = " ".join(raw_text.split()).lower()
    text = re.sub(r"\b\d+\s*(分鐘|小時|天|週|月|年|m|h|d|w|mo|y)\b", "", text)
    text = re.sub(r"\d+", "#", text)
    return text[:500]


def extract_post_id_from_permalink(permalink: str) -> str:
    """從 canonical group post permalink 抽出 post id。"""

    match = POST_ID_PATTERN.search(str(permalink or ""))
    return match.group(1) if match else ""


def append_unique_key(keys: list[str], seen: set[str], value: str) -> None:
    """加入非空且未重複的 key。"""

    normalized = str(value or "").strip()
    if not normalized or normalized in seen:
        return
    seen.add(normalized)
    keys.append(normalized)


def build_composite_post_key(item: ScanItemIdentity) -> str:
    """依作者 / 時間 / 文字片段組出複合型去重 raw key。"""

    compact_author = normalize_for_key(item.author)
    compact_time = normalize_for_key(item.timestamp_text)
    compact_text = build_stable_text_signature(item.text)
    if compact_author and compact_time and compact_text:
        return f"author:{compact_author}||time:{compact_time}||text:{compact_text}"
    if compact_author and compact_text:
        return f"author:{compact_author}||text:{compact_text}"
    if compact_text:
        return f"text:{compact_text}"
    return ""


def build_fallback_id(item: ScanItemIdentity) -> str:
    """建立缺少 post id / permalink 時的最後防線 raw key。"""

    parts = [
        normalize_for_key(item.author),
        normalize_for_key(item.timestamp_text),
        build_stable_text_signature(item.text),
    ]
    return "||".join(part for part in parts if part)


def build_legacy_item_key(item: ScanItemIdentity) -> str:
    """建立早期 probe 舊版 sha256 key，避免既有 seen 資料失效。"""

    normalized_permalink = normalize_permalink(item.permalink)
    normalized = normalized_permalink or build_legacy_text_fingerprint(item.text)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def get_raw_item_key_aliases(item: ScanItemIdentity) -> tuple[str, ...]:
    """建立同一掃描項目的多組等價 raw key。"""

    keys: list[str] = []
    seen: set[str] = set()
    if item.item_kind == "comment":
        permalink = str(item.permalink or "").strip().lower()
        permalink_comment_id = extract_comment_id_from_value(permalink)
        composite_key = build_composite_post_key(item)
        if item.comment_id:
            append_unique_key(keys, seen, f"comment:{item.comment_id}")
        if permalink_comment_id:
            append_unique_key(keys, seen, f"comment-url:{permalink}")
        if item.parent_post_id and composite_key:
            append_unique_key(keys, seen, f"post:{item.parent_post_id}||{composite_key}")
        if composite_key:
            append_unique_key(keys, seen, f"comment-fallback:{composite_key}")
        append_unique_key(keys, seen, build_fallback_id(item))
        return tuple(keys)

    permalink = normalize_permalink(item.permalink)
    post_id = item.post_id or extract_post_id_from_permalink(permalink)
    composite_key = build_composite_post_key(item)
    fallback_id = build_fallback_id(item)

    if post_id:
        append_unique_key(keys, seen, f"id:{post_id}")
    if permalink:
        append_unique_key(keys, seen, f"url:{permalink}")
    append_unique_key(keys, seen, composite_key)
    append_unique_key(keys, seen, fallback_id)
    return tuple(keys)


def get_item_key_aliases(item: ScanItemIdentity) -> tuple[str, ...]:
    """建立可保存於 DB 的 item key aliases。"""

    keys: list[str] = []
    seen: set[str] = set()
    for raw_key in get_raw_item_key_aliases(item):
        append_unique_key(keys, seen, hash_item_key(raw_key))
    if item.item_kind != "comment":
        append_unique_key(keys, seen, build_legacy_item_key(item))
    return tuple(keys)


def get_primary_item_key(item: ScanItemIdentity) -> str:
    """回傳掃描項目的主要 key。"""

    aliases = get_item_key_aliases(item)
    return aliases[0] if aliases else ""


def aliases_overlap(left: Iterable[str], right: Iterable[str]) -> bool:
    """判斷兩組 aliases 是否代表可能相同的 item。"""

    return bool(set(left) & set(right))
