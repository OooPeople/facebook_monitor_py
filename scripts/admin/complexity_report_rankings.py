"""Complexity report 的 ranking 與 annotation matching policy。"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Sequence

from scripts.admin.complexity_report_models import ClassMetric
from scripts.admin.complexity_report_models import FileMetric
from scripts.admin.complexity_report_models import FunctionMetric
from scripts.admin.complexity_report_models import ReviewAnnotation


def top_functions_by_ccn(
    functions: Sequence[FunctionMetric],
    top: int,
    *,
    annotations: Sequence[ReviewAnnotation] = (),
    include_known_large: bool = False,
) -> list[FunctionMetric]:
    """回傳 CCN 排名前 N 的函式。"""

    visible_functions = _visible_functions(
        functions,
        annotations=annotations,
        include_known_large=include_known_large,
    )
    return sorted(
        visible_functions,
        key=lambda metric: (
            metric.ccn,
            metric.nloc,
            metric.token_count,
            metric.display_path,
            -metric.start_line,
        ),
        reverse=True,
    )[:top]


def top_functions_by_nloc(
    functions: Sequence[FunctionMetric],
    top: int,
    *,
    annotations: Sequence[ReviewAnnotation] = (),
    include_known_large: bool = False,
) -> list[FunctionMetric]:
    """回傳 NLOC 排名前 N 的函式。"""

    visible_functions = _visible_functions(
        functions,
        annotations=annotations,
        include_known_large=include_known_large,
    )
    return sorted(
        visible_functions,
        key=lambda metric: (
            metric.nloc,
            metric.ccn,
            metric.token_count,
            metric.display_path,
            -metric.start_line,
        ),
        reverse=True,
    )[:top]


def top_files_by_lines(
    files: Sequence[FileMetric],
    top: int,
    *,
    annotations: Sequence[ReviewAnnotation] = (),
    include_known_large: bool = False,
) -> list[FileMetric]:
    """回傳行數排名前 N 的 source 檔案。"""

    visible_files = _visible_files(
        files,
        annotations=annotations,
        include_known_large=include_known_large,
    )
    return sorted(
        visible_files,
        key=lambda metric: (
            metric.total_lines,
            metric.max_ccn,
            metric.display_path,
        ),
        reverse=True,
    )[:top]


def known_large_functions(
    functions: Sequence[FunctionMetric],
    annotations: Sequence[ReviewAnnotation],
    *,
    top: int,
) -> list[tuple[FunctionMetric, ReviewAnnotation]]:
    """回傳符合 known-large annotation 的函式排名。"""

    rows = [
        (metric, annotation)
        for metric in functions
        if (
            annotation := _annotation_for_function_symbol_status(
                metric,
                annotations,
                status="known_large",
            )
        )
        is not None
    ]
    return sorted(
        rows,
        key=lambda row: (
            row[0].nloc,
            row[0].ccn,
            row[0].token_count,
            row[0].display_path,
        ),
        reverse=True,
    )[:top]


def known_large_files(
    files: Sequence[FileMetric],
    annotations: Sequence[ReviewAnnotation],
    *,
    top: int,
) -> list[tuple[FileMetric, ReviewAnnotation]]:
    """回傳符合 known-large annotation 的檔案排名。"""

    rows = [
        (metric, annotation)
        for metric in files
        if (
            annotation := _annotation_for_file_status(
                metric.path,
                annotations,
                status="known_large",
            )
        )
        is not None
    ]
    return sorted(
        rows,
        key=lambda row: (
            row[0].total_lines,
            row[0].max_ccn,
            row[0].display_path,
        ),
        reverse=True,
    )[:top]


def known_large_classes(
    classes: Sequence[ClassMetric],
    annotations: Sequence[ReviewAnnotation],
    *,
    top: int,
) -> list[tuple[ClassMetric, ReviewAnnotation]]:
    """回傳符合 known-large class annotation 的 class 排名。"""

    rows = [
        (metric, annotation)
        for metric in classes
        if (
            annotation := _annotation_for_class_status(
                metric,
                annotations,
                status="known_large",
            )
        )
        is not None
    ]
    return sorted(
        rows,
        key=lambda row: (
            row[0].line_count,
            row[0].nloc,
            row[0].max_ccn,
            row[0].display_path,
        ),
        reverse=True,
    )[:top]


def watchlist_functions(
    functions: Sequence[FunctionMetric],
    annotations: Sequence[ReviewAnnotation],
    *,
    top: int,
) -> list[tuple[FunctionMetric, ReviewAnnotation]]:
    """回傳符合 watchlist annotation 的函式排名。"""

    rows = [
        (metric, annotation)
        for metric in functions
        if (
            annotation := _annotation_for_function_symbol_status(
                metric,
                annotations,
                status="watchlist",
            )
        )
        is not None
    ]
    return sorted(
        rows,
        key=lambda row: (
            row[0].ccn,
            row[0].nloc,
            row[0].token_count,
            row[0].display_path,
        ),
        reverse=True,
    )[:top]


def watchlist_classes(
    classes: Sequence[ClassMetric],
    annotations: Sequence[ReviewAnnotation],
    *,
    top: int,
) -> list[tuple[ClassMetric, ReviewAnnotation]]:
    """回傳符合 watchlist class annotation 的 class 排名。"""

    rows = [
        (metric, annotation)
        for metric in classes
        if (
            annotation := _annotation_for_class_status(
                metric,
                annotations,
                status="watchlist",
            )
        )
        is not None
    ]
    return sorted(
        rows,
        key=lambda row: (
            row[0].line_count,
            row[0].nloc,
            row[0].max_ccn,
            row[0].display_path,
        ),
        reverse=True,
    )[:top]


def watchlist_files(
    files: Sequence[FileMetric],
    annotations: Sequence[ReviewAnnotation],
    *,
    top: int,
) -> list[tuple[FileMetric, ReviewAnnotation]]:
    """回傳符合 watchlist annotation 的檔案排名。"""

    rows = [
        (metric, annotation)
        for metric in files
        if (
            annotation := _annotation_for_file_status(
                metric.path,
                annotations,
                status="watchlist",
            )
        )
        is not None
    ]
    return sorted(
        rows,
        key=lambda row: (
            row[0].total_lines,
            row[0].max_ccn,
            row[0].display_path,
        ),
        reverse=True,
    )[:top]


def _visible_functions(
    functions: Sequence[FunctionMetric],
    *,
    annotations: Sequence[ReviewAnnotation],
    include_known_large: bool,
) -> list[FunctionMetric]:
    if include_known_large:
        return list(functions)
    return [
        metric
        for metric in functions
        if not _is_known_large_function(metric, annotations)
    ]


def _visible_files(
    files: Sequence[FileMetric],
    *,
    annotations: Sequence[ReviewAnnotation],
    include_known_large: bool,
) -> list[FileMetric]:
    if include_known_large:
        return list(files)
    return [
        metric
        for metric in files
        if not _is_known_large_file(metric, annotations)
    ]


def _is_known_large_function(
    metric: FunctionMetric,
    annotations: Sequence[ReviewAnnotation],
) -> bool:
    function_annotation = _annotation_for_function_symbol_status(
        metric,
        annotations,
        status="known_large",
    )
    if function_annotation is not None:
        return True
    class_annotation = _annotation_for_function_owner_class_status(
        metric,
        annotations,
        status="known_large",
    )
    if class_annotation is not None:
        return True
    return _has_file_annotation_status(
        metric.path,
        annotations,
        status="known_large",
    )


def _is_known_large_file(
    metric: FileMetric,
    annotations: Sequence[ReviewAnnotation],
) -> bool:
    return _has_file_annotation_status(
        metric.path,
        annotations,
        status="known_large",
    )


def _annotation_for_class_status(
    metric: ClassMetric,
    annotations: Sequence[ReviewAnnotation],
    *,
    status: str,
) -> ReviewAnnotation | None:
    for annotation in annotations:
        if annotation.symbol_kind != "class":
            continue
        if annotation.status != status:
            continue
        if not _class_symbol_matches(metric, annotation.symbol):
            continue
        if _path_matches_annotation(metric.path, annotation):
            return annotation
    return None


def _annotation_for_function_owner_class_status(
    metric: FunctionMetric,
    annotations: Sequence[ReviewAnnotation],
    *,
    status: str,
) -> ReviewAnnotation | None:
    for annotation in annotations:
        if annotation.symbol_kind != "class":
            continue
        if annotation.status != status:
            continue
        if not metric.owner_class:
            continue
        if not _class_name_matches(metric.owner_class, annotation.symbol):
            continue
        if _path_matches_annotation(metric.path, annotation):
            return annotation
    return None


def _annotation_for_function_symbol_status(
    metric: FunctionMetric,
    annotations: Sequence[ReviewAnnotation],
    *,
    status: str,
) -> ReviewAnnotation | None:
    """只回傳 function-level annotation，避免 file-level 標註污染函式區塊。"""

    for annotation in annotations:
        if annotation.symbol_kind != "function":
            continue
        if annotation.status != status:
            continue
        if not _function_symbol_matches(metric, annotation.symbol):
            continue
        if _path_matches_annotation(metric.path, annotation):
            return annotation
    return None


def _annotation_for_file_status(
    path: Path,
    annotations: Sequence[ReviewAnnotation],
    *,
    status: str,
) -> ReviewAnnotation | None:
    """依狀態回傳 file-level annotation，避免 broad annotation 順序影響輸出。"""

    for annotation in annotations:
        if annotation.symbol_kind != "file":
            continue
        if annotation.status != status:
            continue
        if _path_matches_annotation(path, annotation):
            return annotation
    return None


def _file_annotations_for_path(
    path: Path,
    annotations: Sequence[ReviewAnnotation],
) -> tuple[ReviewAnnotation, ...]:
    """回傳 path 命中的所有 file-level annotations。"""

    return tuple(
        annotation
        for annotation in annotations
        if annotation.symbol_kind == "file"
        and _path_matches_annotation(path, annotation)
    )


def _has_file_annotation_status(
    path: Path,
    annotations: Sequence[ReviewAnnotation],
    *,
    status: str,
) -> bool:
    """檢查 file-level annotation 狀態，避免 broad annotation 順序影響 suppression。"""

    return any(
        annotation.status == status
        for annotation in _file_annotations_for_path(path, annotations)
    )


def _function_symbol_matches(metric: FunctionMetric, symbol: str) -> bool:
    """比對 function-level annotation；保留舊 long_name/suffix 命中規則。"""

    return (
        metric.name == symbol
        or metric.long_name == symbol
        or metric.long_name.startswith(f"{symbol}(")
        or metric.long_name.startswith(f"{symbol} ")
        or metric.long_name.endswith(f".{symbol}")
    )


def _class_symbol_matches(metric: ClassMetric, symbol: str) -> bool:
    """比對 class-level annotation 的 class 名稱。"""

    return _class_name_matches(metric.name, symbol) or _class_name_matches(
        metric.long_name,
        symbol,
    )


def _class_name_matches(name: str, symbol: str) -> bool:
    """支援 simple class name 與 dotted qualified class name。"""

    return name == symbol or name.endswith(f".{symbol}")


def _path_matches_annotation(path: Path, annotation: ReviewAnnotation) -> bool:
    return fnmatch.fnmatch(path.as_posix(), annotation.path_glob.replace("\\", "/"))


def annotation_runtime_warnings(
    *,
    annotations: Sequence[ReviewAnnotation],
    source_files: Sequence[FileMetric],
    functions: Sequence[FunctionMetric],
    classes: Sequence[ClassMetric],
) -> tuple[str, ...]:
    """找出 path 在本次掃描範圍內、但 symbol 無法命中的 annotation。"""

    warnings: list[str] = []
    for annotation in annotations:
        if annotation.symbol_kind == "file":
            continue
        matching_files = [
            metric
            for metric in source_files
            if _path_matches_annotation(metric.path, annotation)
        ]
        if not matching_files:
            continue
        if annotation.symbol_kind == "function":
            matched = any(
                _path_matches_annotation(metric.path, annotation)
                and _function_symbol_matches(metric, annotation.symbol)
                for metric in functions
            )
        else:
            matched = any(
                _path_matches_annotation(metric.path, annotation)
                and _class_symbol_matches(metric, annotation.symbol)
                for metric in classes
            )
        if not matched:
            warnings.append(
                (
                    f"{annotation.status}: {annotation.symbol_kind} symbol "
                    f"{annotation.symbol!r} did not match scanned path "
                    f"{annotation.path_glob!r}"
                )
            )
    return tuple(warnings)
