"""Maintainability annotation 載入與 schema validation。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping
from typing import Sequence

from scripts.admin.complexity_report_models import ALLOWED_ANNOTATION_STATUSES
from scripts.admin.complexity_report_models import ALLOWED_SYMBOL_KINDS
from scripts.admin.complexity_report_models import ANNOTATION_SCHEMA_VERSION
from scripts.admin.complexity_report_models import AnnotationLoadResult
from scripts.admin.complexity_report_models import ReviewAnnotation


def load_annotations(path: Path | None) -> tuple[ReviewAnnotation, ...]:
    """讀取 known-large / watchlist annotations；保留舊呼叫端的簡單回傳值。"""

    return load_annotations_with_warnings(path).annotations


def load_annotations_with_warnings(path: Path | None) -> AnnotationLoadResult:
    """讀取 annotation JSON；設定問題只回 warning，不讓報告失敗。"""

    if path is None or not path.is_file():
        return AnnotationLoadResult(annotations=(), warnings=())
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return AnnotationLoadResult(
            annotations=(),
            warnings=(f"{path.as_posix()}: unable to load annotations: {exc}",),
        )
    annotations: list[ReviewAnnotation] = []
    warnings: list[str] = []
    if isinstance(payload, dict):
        schema_version = payload.get("schema_version")
        if schema_warning := _annotation_schema_warning(
            schema_version,
            source=path.as_posix(),
        ):
            warnings.append(schema_warning)
        _extend_annotations_from_payloads(
            annotations,
            warnings,
            "known_large",
            _annotation_section_list(payload, "known_large", warnings),
            section="known_large",
        )
        _extend_annotations_from_payloads(
            annotations,
            warnings,
            "watchlist",
            _annotation_section_list(payload, "watchlist", warnings),
            section="watchlist",
        )
        _extend_annotations_from_payloads(
            annotations,
            warnings,
            "watchlist",
            _annotation_section_list(payload, "annotations", warnings),
            section="annotations",
        )
    else:
        warnings.append(f"{path.as_posix()}: root JSON value must be an object")
    return AnnotationLoadResult(
        annotations=tuple(annotations),
        warnings=tuple(warnings),
    )


def _extend_annotations_from_payloads(
    annotations: list[ReviewAnnotation],
    warnings: list[str],
    default_status: str,
    payloads: Sequence[object],
    *,
    section: str,
) -> None:
    """將一組 JSON annotation 轉成 model，錯誤項目只記 warning。"""

    for index, item in enumerate(payloads):
        annotation, warning = _annotation_from_payload(
            default_status,
            item,
            source=f"{section}[{index}]",
        )
        if warning is not None:
            warnings.append(warning)
            continue
        if annotation is not None:
            annotations.append(annotation)


def _annotation_from_payload(
    default_status: str,
    payload: object,
    *,
    source: str,
) -> tuple[ReviewAnnotation | None, str | None]:
    if not isinstance(payload, dict):
        return None, f"{source}: annotation item must be an object"
    status = str(payload.get("status") or default_status)
    if status not in ALLOWED_ANNOTATION_STATUSES:
        return (
            None,
            (
                f"{source}: unsupported status={status!r}; "
                f"allowed={sorted(ALLOWED_ANNOTATION_STATUSES)}"
            ),
        )
    path_glob = str(payload.get("path_glob") or "")
    if not path_glob:
        return None, f"{source}: missing path_glob"
    symbol = str(payload.get("symbol") or "")
    symbol_kind = _annotation_symbol_kind(payload.get("symbol_kind"), symbol=symbol)
    if symbol_kind is None:
        return (
            None,
            (
                f"{source}: unsupported symbol_kind={payload.get('symbol_kind')!r}; "
                f"allowed={sorted(ALLOWED_SYMBOL_KINDS)}"
            ),
        )
    if symbol_kind == "file" and symbol:
        return None, f"{source}: file annotation must not define symbol"
    if symbol_kind in {"function", "class"} and not symbol:
        return None, f"{source}: {symbol_kind} annotation requires symbol"
    return (
        ReviewAnnotation(
            status=status,
            path_glob=path_glob,
            symbol=symbol,
            symbol_kind=symbol_kind,
            category=str(payload.get("category") or ""),
            reason=str(payload.get("reason") or ""),
            must_not_add=_annotation_string_list(payload.get("must_not_add")),
            split_trigger=str(payload.get("split_trigger") or ""),
        ),
        None,
    )


def _annotation_symbol_kind(value: object, *, symbol: str) -> str | None:
    """解析 annotation symbol kind；舊資料依是否有 symbol 自動推斷。"""

    if value is None or value == "":
        return "function" if symbol else "file"
    symbol_kind = str(value)
    return symbol_kind if symbol_kind in ALLOWED_SYMBOL_KINDS else None


def _annotation_string_list(value: object) -> tuple[str, ...]:
    """解析 annotation 裡供人工治理用的字串清單。"""

    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def _annotation_schema_warning(schema_version: object, *, source: str) -> str | None:
    """回傳 annotation schema warning；合法或未宣告時回 None。"""

    if schema_version is None:
        return None
    if not isinstance(schema_version, (str, int)):
        return (
            f"{source}: invalid schema_version={schema_version!r}; "
            f"expected {ANNOTATION_SCHEMA_VERSION}"
        )
    try:
        version = int(schema_version)
    except (TypeError, ValueError):
        return (
            f"{source}: invalid schema_version={schema_version!r}; "
            f"expected {ANNOTATION_SCHEMA_VERSION}"
        )
    if version != ANNOTATION_SCHEMA_VERSION:
        return (
            f"{source}: unsupported schema_version={schema_version}; "
            f"expected {ANNOTATION_SCHEMA_VERSION}"
        )
    return None


def _annotation_section_list(
    payload: Mapping[str, object],
    key: str,
    warnings: list[str],
) -> list[object]:
    """讀取 annotation section；非 list 時只回 warning。"""

    value = payload.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return value
    warnings.append(f"{key}: annotation section must be a list")
    return []
