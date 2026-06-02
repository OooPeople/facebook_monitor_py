"""Support bundle redaction and alias helpers。

職責：集中支援包內所有 path / URL / identifier / freeform / metadata
輸出規則，避免 collectors 分散處理造成隱私 regression。
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import hashlib
import json
import re

from facebook_monitor.core.redaction import redact_sensitive_text

MAX_REDACTED_TEXT_LENGTH = 500
SUPPORT_BUNDLE_URL_RE = re.compile(r"\bhttps?://[^\s\"'<>]+", re.IGNORECASE)
SUPPORT_BUNDLE_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
SUPPORT_BUNDLE_LONG_NUMERIC_ID_RE = re.compile(r"\b\d{8,}\b")
SUPPORT_BUNDLE_IDENTIFIER_ASSIGNMENT_RE = re.compile(
    r"(?i)\b("
    r"target_id|targetid|queued_target_ids|queuedtargetids|target_ids|targetids|"
    r"item_key|itemkey|item_keys|itemkeys|group_id|groupid|scope_id|scopeid|"
    r"post_id|postid|parent_post_id|parentpostid|comment_id|commentid|"
    r"page_id|pageid|worker_id|workerid|owner_key|ownerkey|"
    r"logical_item_id|logicalitemid|dedupe_id|dedupeid|"
    r"notification_event_id|notificationeventid|source_scan_run_id|sourcescanrunid"
    r")\s*[:=]\s*(\[[^\]]*\]|\([^\)]*\)|[^\s,;]+)"
)
SUPPORT_BUNDLE_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s\"'<>]+")
SUPPORT_BUNDLE_POSIX_PATH_RE = re.compile(r"(?<!\w)/(?:[^/\s\"'<>]+/)+[^\s\"'<>]*")
SAFE_METADATA_STRING_KEYS = {
    "worker",
    "workermode",
    "targetkind",
    "collectionstrategy",
    "loadmoremode",
    "stopreason",
    "skipreason",
    "reason",
    "runtimeaction",
    "recoveryaction",
    "exceptionclass",
    "mode",
    "recoveryaction",
    "targetaction",
    "status",
    "textsource",
    "source",
    "scrolltargetlabel",
}
SAFE_REASON_CODE_VALUES = {
    "auto_load_more_disabled",
    "checkpoint_required",
    "comment_collection_stopped",
    "comment_load_more_guard_active",
    "comment_scroll_rounds_completed",
    "comment_scroll_stalled",
    "comment_stagnant_windows",
    "collection_stopped",
    "content_unavailable",
    "database_locked",
    "extractor_empty",
    "extractor_failed",
    "extractor_runtime",
    "login_required",
    "manual_skip",
    "no_comment_round_stats",
    "no_round_stats",
    "page_load_timeout",
    "playwright_failure_owner_changed",
    "profile_locked",
    "profile_missing",
    "running_claim_rejected",
    "scan_commit_guard_mismatch",
    "scan_guard_skipped",
    "scan_timeout",
    "scheduler_runtime",
    "scheduler_stopping",
    "scroll_rounds_completed",
    "scroll_stalled",
    "seen_stop_consecutive_seen",
    "session_invalid",
    "sort_adjust_unconfirmed",
    "sort_adjust_unconfirmed_skip",
    "sort_update_unconfirmed",
    "stagnant_windows",
    "stale_queued_recovered",
    "stale_running",
    "target_already_queued_or_running",
    "target_already_running",
    "target_argument_conflict",
    "target_count_reached",
    "target_invalid",
    "target_kind_unsupported",
    "target_missing",
    "target_not_active",
    "target_not_active_before_running",
    "target_stopped",
    "unknown",
    "unknown_failure_owner_changed",
    "visible_window_completed",
    "worker_failure_owner_changed",
    "worker_pool_unhealthy",
}
SAFE_METADATA_STRING_VALUES = {
    "collectionstrategy": {
        "comments_nested_scroll",
        "comments_visible_window",
        "feed_scroll_rounds",
        "feed_visible_window",
        "sort_adjust_skip",
    },
    "exceptionclass": {
        "DatabaseError",
        "Error",
        "OperationalError",
        "RuntimeError",
        "TimeoutError",
        "WorkerFailure",
    },
    "loadmoremode": {
        "comment_nested_scroll",
        "off",
        "scroll",
        "skipped",
    },
    "mode": {
        "comments_visible_window",
        "sort_adjust_skip",
    },
    "recoveryaction": {
        "scheduler_runtime_restart",
        "target_page_restart",
    },
    "runtimeaction": {
        "error",
        "idle",
        "will_retry",
    },
    "scrolltargetlabel": {
        "document.body",
        "document.scrollingElement",
        "window",
    },
    "source": {
        "comment",
        "comment_anchor",
        "comment_anchor_raw",
        "comment_container",
        "comment_permalink_anchor",
        "container",
        "fallback",
        "feed_dom",
        "group_permalink_anchor",
        "group_query_anchor",
        "groups_post_anchor",
        "launcher_guided_login",
        "permalink_php_anchor",
        "photo_gm_anchor",
        "playwright",
        "primary",
        "resident_main",
        "runtime_recovery",
        "scan_success",
        "scheduler_cancel",
        "unknown_exception",
        "unavailable",
        "worker_failure",
    },
    "status": {
        "active",
        "attempted",
        "available",
        "current",
        "error",
        "failed",
        "idle",
        "ignored_stale_url",
        "invalid_url",
        "launched",
        "not_checked",
        "not_found",
        "pending",
        "processing_failed",
        "processing_pending",
        "queued",
        "resolved",
        "running",
        "sent",
        "skipped",
        "stale_skipped",
        "stopped",
        "succeeded_changed",
        "succeeded_unchanged",
        "throttled",
        "verified",
    },
    "targetaction": {
        "error",
        "idle",
    },
    "targetkind": {
        "comments",
        "posts",
    },
    "textsource": {
        "comment",
        "container",
        "fallback",
        "primary",
    },
    "worker": {
        "comments_scan",
        "posts_scan",
    },
    "workermode": {
        "headed_compat",
        "headless",
    },
}
KNOWN_METADATA_KEYS = {
    "accumulatedcount",
    "addedcount",
    "arialabel",
    "author",
    "autoloadmore",
    "candidatecount",
    "collectedmeta",
    "collectionstrategy",
    "commentcount",
    "commentextractrounds",
    "commentid",
    "commentsmeta",
    "commentsort",
    "commentswithcommentidcount",
    "commentscrollcollectionenabled",
    "containerrole",
    "content",
    "cover",
    "debugmetadata",
    "description",
    "domsettleattempted",
    "domsettlecandidatecount",
    "domsettleobservations",
    "domsettlestable",
    "domsettlewaitms",
    "exceptionclass",
    "expandcount",
    "filteredemptytextcount",
    "filterednonpostcount",
    "groupid",
    "href",
    "itemkey",
    "itemkind",
    "label",
    "loadmoremode",
    "matchedkeyword",
    "maxwindowcount",
    "message",
    "mode",
    "name",
    "parentpostid",
    "parsedcount",
    "permalink",
    "permalinksource",
    "postid",
    "postidsource",
    "rawitemcount",
    "rawtext",
    "reason",
    "recoveryaction",
    "requestedscrollrounds",
    "roundcount",
    "roundindex",
    "rounds",
    "runtimeaction",
    "scannedcount",
    "scanskipped",
    "scopeid",
    "scrollaftertop",
    "scrollbeforetop",
    "scrollcollectionenabled",
    "scrollheight",
    "scrollmoved",
    "scrollmoveddistance",
    "scrollrounds",
    "scrollstep",
    "scrolltargetlabel",
    "scrolltargettop",
    "scrolly",
    "scrollwaitms",
    "skipreason",
    "sortadjust",
    "source",
    "stagnantwindows",
    "status",
    "stopreason",
    "targetaction",
    "targetcount",
    "targetkind",
    "text",
    "textsource",
    "title",
    "uniqueitemcount",
    "url",
    "worker",
    "workermode",
}
RUNTIME_DIAGNOSTIC_PATH_LABELS = {
    "data dir",
    "db path",
    "executable",
    "logs dir",
    "profile dir",
    "resource lock paths",
    "runtime dir",
    "static dir",
    "templates dir",
    "updates dir",
}
RUNTIME_DIAGNOSTIC_VALUE_LABELS = {
    "app",
    "asset version",
    "auto port",
    "auto-start scheduler",
    "browser mode",
    "build date",
    "frozen",
    "git commit",
    "host",
    "mode",
    "open browser",
    "packaging mode",
    "port",
    "python version",
    "reset runtime data on startup",
    "reset targets on startup",
    "resume active targets on startup",
    "scheduler",
    "scheduler interval seconds",
    "scheduler max concurrent scans",
    "scheduler tick seconds",
    "url",
    "version",
}
SAFE_APP_METADATA_KEYS = {
    "app_name",
    "app_version",
    "asset_version",
    "packaging_mode",
    "python_version",
}
SAFE_METADATA_NUMERIC_FRAGMENTS = (
    "attempt",
    "count",
    "distance",
    "height",
    "index",
    "limit",
    "ms",
    "observation",
    "round",
    "sec",
    "step",
    "streak",
    "top",
    "window",
)



@dataclass
class _SupportBundleAliases:
    """建立單一 bundle 內可 join 的匿名 id。"""

    mappings: dict[str, dict[str, str]] = field(default_factory=dict)

    def alias(self, namespace: str, value: object) -> str:
        """回傳指定 namespace 內穩定遞增 alias。"""

        normalized = str(value or "").strip()
        if not normalized:
            return ""
        namespace_map = self.mappings.setdefault(namespace, {})
        existing = namespace_map.get(normalized)
        if existing:
            return existing
        alias = f"{namespace}_{len(namespace_map) + 1:03d}"
        namespace_map[normalized] = alias
        return alias

    def aliases_by_namespace(self) -> dict[str, int]:
        """只輸出各 namespace alias 數量，不輸出原始對照表。"""

        return {
            namespace: len(namespace_map)
            for namespace, namespace_map in sorted(self.mappings.items())
        }



def _safe_exception_summary(exc: Exception) -> str:
    """回傳不含敏感值的短例外摘要。"""

    return exc.__class__.__name__

def _sanitize_app_metadata(payload: dict[str, str]) -> dict[str, object]:
    """metadata.json 只保留已知 build 欄位，未知值只留 presence。"""

    sanitized: dict[str, object] = {}
    for key, value in sorted(payload.items(), key=lambda item: str(item[0])):
        key_text = str(key)
        if key_text in SAFE_APP_METADATA_KEYS:
            sanitized[key_text] = _redacted_truncated(str(value), limit=160)
            continue
        sanitized[_safe_metadata_key_label(key_text)] = _metadata_presence(value)
    return sanitized


def _sanitize_metadata(payload: dict[str, object]) -> dict[str, object]:
    """對 scan metadata 做 bounded allowlist-ish redaction。"""

    return {
        _safe_metadata_key_label(key): _sanitize_metadata_value(key, value)
        for key, value in sorted(payload.items(), key=lambda item: str(item[0]))
    }


def _sanitize_metadata_value(
    key: str,
    value: object,
    *,
    depth: int = 0,
) -> object:
    """遞迴整理 metadata，限制深度與 list 長度。"""

    key_text = str(key)
    if depth >= 3:
        return _metadata_type_name(value)
    if _is_sensitive_metadata_key(key_text):
        return _metadata_presence(value)
    if isinstance(value, dict):
        return {
            _safe_metadata_key_label(child_key): _sanitize_metadata_value(
                str(child_key),
                child,
                depth=depth + 1,
            )
            for child_key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, list):
        return {
            "count": len(value),
            "sample": [
                _sanitize_metadata_value(key_text, item, depth=depth + 1)
                for item in value[:3]
            ],
            "truncated": len(value) > 3,
        }
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        if _is_safe_numeric_metadata_key(key_text):
            return value
        return {"type": type(value).__name__, "present": True}
    if _is_safe_string_metadata_key(key_text):
        return _safe_metadata_string_value(key_text, value)
    return _metadata_presence(value)


def _is_sensitive_metadata_key(key: str) -> bool:
    """判斷 metadata key 是否可能含有內容、URL 或外部 ID。"""

    normalized = key.lower()
    sensitive_fragments = (
        "arialabel",
        "author",
        "comment_id",
        "commentid",
        "content",
        "cover",
        "description",
        "group_id",
        "groupid",
        "href",
        "item_key",
        "itemkey",
        "label",
        "message",
        "name",
        "parent_post_id",
        "parentpostid",
        "permalink",
        "post_id",
        "postid",
        "scope_id",
        "scopeid",
        "text",
        "url",
    )
    return (
        normalized == "id"
        or normalized.endswith("id")
        or any(fragment in normalized for fragment in sensitive_fragments)
    ) and not _is_safe_string_metadata_key(key)


def _is_safe_string_metadata_key(key: str) -> bool:
    """判斷 metadata 字串值是否可保留。"""

    normalized = _normalize_metadata_key(key)
    return normalized in SAFE_METADATA_STRING_KEYS


def _is_safe_numeric_metadata_key(key: str) -> bool:
    """判斷 metadata 數值是否可保留。"""

    normalized = _normalize_metadata_key(key)
    return any(fragment in normalized for fragment in SAFE_METADATA_NUMERIC_FRAGMENTS)


def _normalize_metadata_key(key: str) -> str:
    """正規化 metadata key 供 allowlist 比對。"""

    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _safe_metadata_key_label(key: object) -> str:
    """保留程式碼式 metadata key；其他 key 只輸出短雜湊 label。"""

    text = str(key or "").strip()
    if (
        _normalize_metadata_key(text) in KNOWN_METADATA_KEYS
        and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,79}", text)
    ):
        return text
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"redacted_key_{digest}"


def _safe_metadata_string_value(key: str, value: object) -> object:
    """metadata allowlist 字串只保留 enum-like code。"""

    normalized_key = _normalize_metadata_key(key)
    text = str(value or "").strip()
    if normalized_key in {"reason", "skipreason", "stopreason"}:
        code = _safe_reason_code(text)
        if code:
            return code
        return _metadata_presence(value)
    allowed_values = SAFE_METADATA_STRING_VALUES.get(normalized_key, set())
    if text in allowed_values:
        return text
    code = _safe_reason_code(text)
    if normalized_key in {"runtimeaction", "recoveryaction", "targetaction"} and code:
        return code
    if code:
        return _metadata_presence(value)
    return _metadata_presence(value)


def _metadata_presence(value: object) -> dict[str, object]:
    """未知或敏感 metadata 只保留存在與長度，不輸出原值。"""

    if isinstance(value, dict):
        return {"type": "dict", "count": len(value), "redacted": True}
    if isinstance(value, list):
        return {"type": "list", "count": len(value), "redacted": True}
    text = "" if value is None else str(value)
    return {
        "type": type(value).__name__,
        "present": value is not None and text != "",
        "length": len(text),
        "redacted": True,
    }


def _metadata_text(metadata: dict[str, object], key: str) -> str:
    """從 metadata 讀取短文字。"""

    value = metadata.get(key)
    return str(value).strip() if value is not None else ""


def _metadata_type_name(value: object) -> str:
    """回傳 metadata value 型別名稱。"""

    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, dict):
        return f"dict[{len(value)}]"
    return type(value).__name__


def _merge_debug_metadata_counts(
    target_payload: dict[str, object],
    metadata: dict[str, object],
) -> None:
    """把單筆 latest item debug metadata 併入 target aggregate。"""

    key_counts = target_payload.setdefault("debug_key_counts", {})
    value_counts = target_payload.setdefault("debug_value_counts", {})
    if not isinstance(key_counts, dict) or not isinstance(value_counts, dict):
        return
    for key, value in metadata.items():
        if _is_sensitive_metadata_key(str(key)) and not _is_safe_string_metadata_key(str(key)):
            continue
        key_name = _safe_metadata_key_label(key)
        key_counts[key_name] = int(key_counts.get(key_name, 0)) + 1
        if _is_safe_string_metadata_key(str(key)) and isinstance(value, str):
            value_label = _safe_metadata_string_value(str(key), value)
        elif _is_safe_numeric_metadata_key(str(key)) and isinstance(value, (int, float, bool)):
            value_label = value
        else:
            value_label = None
        if isinstance(value_label, dict):
            value_label = None
        if value_label is not None and len(str(value_label)) <= 80:
            counter_key = f"{key_name}={value_label}"
            value_counts[counter_key] = int(value_counts.get(counter_key, 0)) + 1


def _failure_reason_from_error(error_message: str) -> str:
    """從格式化錯誤訊息取出可聚合的 reason。"""

    text = error_message.strip()
    if not text:
        return ""
    normalized = text.replace("：", ":", 1)
    return normalized.split(":", 1)[0].strip()


def _freeform_summary(
    value: str,
    *,
    aliases: _SupportBundleAliases | None = None,
) -> dict[str, object]:
    """對任意錯誤/reason/log 文字只輸出分類摘要，不輸出原句。"""

    text = str(value or "").strip()
    sanitized = _redacted_truncated(text, aliases=aliases)
    return {
        "present": bool(text),
        "length": len(text),
        "code": _safe_reason_code(_failure_reason_from_error(text)),
        "has_url": bool(SUPPORT_BUNDLE_URL_RE.search(text)),
        "has_path": bool(
            SUPPORT_BUNDLE_WINDOWS_PATH_RE.search(text)
            or SUPPORT_BUNDLE_POSIX_PATH_RE.search(text)
        ),
        "has_identifier": _has_identifier_signal(text),
        "has_secret_like": "[已隱藏]" in redact_sensitive_text(text),
        "redacted": bool(text),
        "sanitized_length": len(sanitized),
    }


def _log_line_summary(
    line: str,
    *,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """整理單行 log，不輸出 raw line。"""

    text = str(line or "")
    summary = _freeform_summary(text, aliases=aliases)
    summary["level"] = _log_level_hint(text)
    summary["timestamp_prefix"] = _timestamp_prefix(text)
    return summary


def _runtime_diagnostics_text(
    value: str,
    aliases: _SupportBundleAliases,
) -> str:
    """整理 runtime diagnostics 文字，不輸出 raw path 或未知 freeform value。"""

    lines = []
    for raw_line in str(value or "").splitlines()[:200]:
        line = raw_line.strip()
        if not line:
            lines.append("")
            continue
        if ":" not in line:
            summary = _freeform_summary(line, aliases=aliases)
            lines.append(
                "line: " + json.dumps(summary, ensure_ascii=False, sort_keys=True)
            )
            continue
        label, raw_field_value = line.split(":", 1)
        safe_label = _runtime_diagnostics_label(label)
        normalized_label = safe_label.lower()
        field_value = raw_field_value.strip()
        if normalized_label in RUNTIME_DIAGNOSTIC_PATH_LABELS:
            lines.append(f"{safe_label}: [path]")
        elif normalized_label in RUNTIME_DIAGNOSTIC_VALUE_LABELS:
            lines.append(
                f"{safe_label}: "
                f"{_redacted_truncated(field_value, limit=160, aliases=aliases)}"
            )
        else:
            summary = _freeform_summary(field_value, aliases=aliases)
            lines.append(
                f"{safe_label}: "
                + json.dumps(summary, ensure_ascii=False, sort_keys=True)
            )
    return "\n".join(lines)


def _runtime_diagnostics_label(value: str) -> str:
    """保留 settings/runtime diagnostics 內已知格式 label。"""

    text = str(value or "").strip()
    if (
        text.lower()
        in RUNTIME_DIAGNOSTIC_PATH_LABELS | RUNTIME_DIAGNOSTIC_VALUE_LABELS
    ):
        return text
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"redacted_label_{digest}"


def _safe_reason_code(value: str) -> str:
    """只保留由 ASCII 安全字元組成的 reason/code。"""

    code = _basic_reason_code(value)
    return code if code in SAFE_REASON_CODE_VALUES else ""


def _reason_count_bucket(value: str) -> str:
    """reason 彙總只輸出已知 code；未知 code 只保留存在訊號。"""

    code = _safe_reason_code(value)
    if code:
        return code
    return "unrecognized_code" if _basic_reason_code(value) else ""


def _basic_reason_code(value: str) -> str:
    """先移除 path/id/url，再解析 reason-like ASCII token。"""

    text = str(value or "").strip()
    if not text:
        return ""
    text = SUPPORT_BUNDLE_IDENTIFIER_ASSIGNMENT_RE.sub("", text)
    text = SUPPORT_BUNDLE_URL_RE.sub("", text)
    text = SUPPORT_BUNDLE_WINDOWS_PATH_RE.sub("", text)
    text = SUPPORT_BUNDLE_POSIX_PATH_RE.sub("", text)
    match = re.match(r"^[A-Za-z0-9_.-]{1,80}$", text)
    return text if match else ""


def _has_identifier_signal(text: str) -> bool:
    """判斷文字是否包含 identifier pattern。"""

    return bool(
        SUPPORT_BUNDLE_IDENTIFIER_ASSIGNMENT_RE.search(text)
        or SUPPORT_BUNDLE_UUID_RE.search(text)
        or SUPPORT_BUNDLE_LONG_NUMERIC_ID_RE.search(text)
    )


def _log_level_hint(text: str) -> str:
    """從 log line 中抓取常見 level。"""

    for level in ("ERROR", "WARNING", "INFO", "DEBUG", "CRITICAL"):
        if re.search(rf"\b{level}\b", text):
            return level.lower()
    return ""


def _timestamp_prefix(text: str) -> str:
    """保留行首 timestamp 片段，避免輸出其他內容。"""

    match = re.match(r"^(\d{4}-\d{2}-\d{2}[ T][0-9:.+-]+)", text)
    return match.group(1)[:40] if match else ""


def _redacted_truncated(
    value: str,
    *,
    limit: int = MAX_REDACTED_TEXT_LENGTH,
    aliases: _SupportBundleAliases | None = None,
) -> str:
    """先 redaction 再截斷文字。"""

    text = redact_sensitive_text(str(value or "")).strip()
    text = _redact_identifier_assignments(text, aliases)
    text = SUPPORT_BUNDLE_URL_RE.sub("[url]", text)
    text = SUPPORT_BUNDLE_WINDOWS_PATH_RE.sub("[path]", text)
    text = SUPPORT_BUNDLE_POSIX_PATH_RE.sub("[path]", text)
    text = SUPPORT_BUNDLE_UUID_RE.sub("[id]", text)
    text = SUPPORT_BUNDLE_LONG_NUMERIC_ID_RE.sub("[id]", text)
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _redact_identifier_assignments(
    text: str,
    aliases: _SupportBundleAliases | None,
) -> str:
    """遮掉 log/error 中常見 id assignment。"""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        raw_value = match.group(2)
        namespace = _identifier_namespace(key)
        if aliases is None or namespace is None:
            return f"{key}=[id]"
        alias_values = [
            aliases.alias(namespace, item)
            for item in _identifier_values(raw_value)
            if item
        ]
        if not alias_values:
            return f"{key}=[id]"
        if len(alias_values) == 1:
            return f"{key}={alias_values[0]}"
        return f"{key}=[{','.join(alias_values)}]"

    return SUPPORT_BUNDLE_IDENTIFIER_ASSIGNMENT_RE.sub(replace, text)


def _identifier_namespace(key: str) -> str | None:
    """依 log/error key 推導 alias namespace。"""

    normalized = key.lower()
    if "target" in normalized:
        return "target"
    if "item" in normalized:
        return "item"
    if "worker" in normalized or "owner" in normalized:
        return "worker"
    if "page" in normalized:
        return "page"
    if "scan_run" in normalized:
        return "scan_run"
    if "notification_event" in normalized:
        return "notification_event"
    if "dedupe" in normalized:
        return "dedupe"
    if "logical_item" in normalized:
        return "logical_item"
    if "scope" in normalized:
        return "scope"
    return None


def _identifier_values(raw_value: str) -> list[str]:
    """從常見 tuple/list/scalar 字串抽出 id 值。"""

    cleaned = raw_value.strip().strip("[](){}")
    if not cleaned:
        return []
    values = re.split(r"[,|]", cleaned)
    return [
        value.strip().strip("'\" ")
        for value in values
        if value.strip().strip("'\" ")
    ]


def _support_row_id_hash(*, table: str, row_id: str) -> str:
    """支援包內只輸出 row id 穩定短雜湊，避免外洩 target/item identifiers。"""

    digest = hashlib.sha256(f"{table}:{row_id}".encode("utf-8")).hexdigest()
    return digest[:12]
