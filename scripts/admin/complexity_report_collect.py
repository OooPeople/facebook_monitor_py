"""Complexity report 的 source 收集與 metric extraction。"""

from __future__ import annotations

import ast
import fnmatch
from pathlib import Path
from typing import Iterable
from typing import Sequence

import lizard  # type: ignore[import-untyped]

from scripts.admin.complexity_report_models import AnalysisError
from scripts.admin.complexity_report_models import ClassMetric
from scripts.admin.complexity_report_models import ClassRange
from scripts.admin.complexity_report_models import ComplexityReport
from scripts.admin.complexity_report_models import FileMetric
from scripts.admin.complexity_report_models import FunctionMetric
from scripts.admin.complexity_report_models import ReviewAnnotation
from scripts.admin.complexity_report_models import SourcePath
from scripts.admin.complexity_report_rankings import annotation_runtime_warnings


LIZARD_EXTENSIONS = frozenset({".py", ".js"})
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def collect_report(
    paths: Iterable[Path],
    *,
    include_extensions: Sequence[str],
    exclude_globs: Sequence[str],
    annotations: Sequence[ReviewAnnotation] = (),
    annotation_warnings: Sequence[str] = (),
) -> ComplexityReport:
    """收集 source 檔案與 Lizard 函式指標；不套用任何門檻。"""

    input_paths = tuple(paths)
    normalized_paths = tuple(_report_path_for_path(path) for path in input_paths)
    loaded_annotations = tuple(annotations)
    source_files: list[FileMetric] = []
    functions: list[FunctionMetric] = []
    classes: list[ClassMetric] = []
    analysis_errors: list[AnalysisError] = []
    excluded_file_count = 0
    for source_path in iter_source_files(
        input_paths,
        include_extensions=include_extensions,
        exclude_globs=exclude_globs,
    ):
        if source_path is None:
            excluded_file_count += 1
            continue
        file_metric, file_functions, file_classes, error = analyze_source_file(source_path)
        if file_metric is not None:
            source_files.append(file_metric)
        functions.extend(file_functions)
        classes.extend(file_classes)
        if error is not None:
            analysis_errors.append(error)
    runtime_annotation_warnings = annotation_runtime_warnings(
        annotations=loaded_annotations,
        source_files=source_files,
        functions=functions,
        classes=classes,
    )
    return ComplexityReport(
        paths=normalized_paths,
        source_files=tuple(source_files),
        functions=tuple(functions),
        classes=tuple(classes),
        analysis_errors=tuple(analysis_errors),
        excluded_file_count=excluded_file_count,
        annotations=loaded_annotations,
        annotation_warnings=(*tuple(annotation_warnings), *runtime_annotation_warnings),
    )


def iter_source_files(
    paths: Iterable[Path],
    *,
    include_extensions: Sequence[str],
    exclude_globs: Sequence[str],
) -> Iterable[SourcePath | None]:
    """列出指定 path 底下的 source 檔；排除項以 None 計數。"""

    extensions = {extension.casefold() for extension in include_extensions}
    for root in paths:
        candidates = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in candidates:
            if not path.is_file() or path.suffix.casefold() not in extensions:
                continue
            report_path = _report_path_for_path(path)
            if _matches_any_glob(report_path, exclude_globs):
                yield None
                continue
            yield SourcePath(actual_path=path, report_path=report_path)


def analyze_source_file(
    source_path: SourcePath,
) -> tuple[
    FileMetric | None,
    tuple[FunctionMetric, ...],
    tuple[ClassMetric, ...],
    AnalysisError | None,
]:
    """分析單一 source 檔案；Python / JS 函式指標交由 Lizard。"""

    actual_path = source_path.actual_path
    report_path = source_path.report_path
    try:
        text = actual_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return (
            None,
            (),
            (),
            AnalysisError(path=report_path, message=f"utf8_decode_error:{exc}"),
        )
    except OSError as exc:
        return None, (), (), AnalysisError(path=report_path, message=f"read_error:{exc}")

    lines = text.splitlines()
    language = _language_for_path(actual_path)
    functions: tuple[FunctionMetric, ...] = ()
    file_nloc = _estimated_code_line_count(lines, language=language)
    error: AnalysisError | None = None
    class_ranges = _class_ranges_for_source(text, language=language)
    if actual_path.suffix.casefold() in LIZARD_EXTENSIONS:
        try:
            file_info = lizard.analyze_file(str(actual_path))
        except Exception as exc:  # pragma: no cover - Lizard errors are environment-specific.
            error = AnalysisError(path=report_path, message=f"lizard_error:{exc}")
        else:
            file_nloc = int(getattr(file_info, "nloc", file_nloc) or 0)
            functions = tuple(
                _function_metric_from_lizard(
                    report_path,
                    language,
                    function_info,
                    class_ranges=class_ranges,
                )
                for function_info in getattr(file_info, "function_list", ())
            )
    class_metrics = tuple(
        _class_metric_from_range(report_path, language, class_range, functions)
        for class_range in class_ranges
    )

    file_metric = FileMetric(
        path=report_path,
        language=language,
        total_lines=len(lines),
        estimated_code_lines=_estimated_code_line_count(lines, language=language),
        nloc=file_nloc,
        function_count=len(functions),
        max_ccn=max((function.ccn for function in functions), default=0),
        max_function_nloc=max((function.nloc for function in functions), default=0),
        max_token_count=max((function.token_count for function in functions), default=0),
    )
    return file_metric, functions, class_metrics, error


def _function_metric_from_lizard(
    path: Path,
    language: str,
    function_info: object,
    *,
    class_ranges: Sequence[ClassRange] = (),
) -> FunctionMetric:
    """將 Lizard FunctionInfo 轉成 repo 穩定 report model。"""

    start_line = int(getattr(function_info, "start_line", 0) or 0)
    end_line = int(getattr(function_info, "end_line", start_line) or start_line)
    return FunctionMetric(
        path=path,
        language=language,
        name=str(getattr(function_info, "name", "") or ""),
        long_name=str(getattr(function_info, "long_name", "") or ""),
        start_line=start_line,
        end_line=end_line,
        nloc=int(getattr(function_info, "nloc", 0) or 0),
        ccn=int(getattr(function_info, "cyclomatic_complexity", 0) or 0),
        token_count=int(getattr(function_info, "token_count", 0) or 0),
        parameter_count=int(getattr(function_info, "parameter_count", 0) or 0),
        owner_class=_owner_class_for_line(class_ranges, start_line),
    )


def _class_metric_from_range(
    path: Path,
    language: str,
    class_range: ClassRange,
    functions: Sequence[FunctionMetric],
) -> ClassMetric:
    """將 Python class range 與其 member functions 合併成 class report row。"""

    member_functions = [
        metric
        for metric in functions
        if metric.owner_class == class_range.qualified_name
        or metric.owner_class.startswith(f"{class_range.qualified_name}.")
    ]
    return ClassMetric(
        path=path,
        language=language,
        name=class_range.name,
        long_name=class_range.qualified_name,
        start_line=class_range.start_line,
        end_line=class_range.end_line,
        method_count=len(member_functions),
        nloc=sum(metric.nloc for metric in member_functions),
        max_ccn=max((metric.ccn for metric in member_functions), default=0),
        max_function_nloc=max(
            (metric.nloc for metric in member_functions),
            default=0,
        ),
    )


def _class_ranges_for_source(text: str, *, language: str) -> tuple[ClassRange, ...]:
    """回傳 Python source 中的 class line ranges；其他語言先交給 Lizard 名稱。"""

    if language != "python":
        return ()
    try:
        module = ast.parse(text)
    except SyntaxError:
        return ()
    ranges: list[ClassRange] = []
    _append_python_class_ranges(module.body, ranges, parent_name="")
    return tuple(ranges)


def _append_python_class_ranges(
    nodes: Sequence[ast.stmt],
    ranges: list[ClassRange],
    *,
    parent_name: str,
) -> None:
    """遞迴收集 Python class ranges，保留 nested class 的 qualified name。"""

    for node in nodes:
        if not isinstance(node, ast.ClassDef):
            continue
        qualified_name = f"{parent_name}.{node.name}" if parent_name else node.name
        ranges.append(
            ClassRange(
                name=node.name,
                qualified_name=qualified_name,
                start_line=int(getattr(node, "lineno", 0) or 0),
                end_line=int(getattr(node, "end_lineno", getattr(node, "lineno", 0)) or 0),
            )
        )
        _append_python_class_ranges(node.body, ranges, parent_name=qualified_name)


def _owner_class_for_line(class_ranges: Sequence[ClassRange], line: int) -> str:
    """回傳包含指定 line 的最內層 class qualified name。"""

    matches = [
        class_range
        for class_range in class_ranges
        if class_range.start_line <= line <= class_range.end_line
    ]
    if not matches:
        return ""
    owner = max(matches, key=lambda class_range: class_range.start_line)
    return owner.qualified_name


def _report_path_for_path(path: Path) -> Path:
    """回傳報告與 annotation matching 使用的穩定路徑。"""

    try:
        return path.resolve().relative_to(PROJECT_ROOT)
    except ValueError:
        return path.resolve()
    except OSError:
        return path


def _matches_any_glob(path: Path, patterns: Sequence[str]) -> bool:
    """回傳 path 是否符合任一 glob pattern。"""

    normalized = path.as_posix()
    return any(
        fnmatch.fnmatch(normalized, pattern.replace("\\", "/"))
        for pattern in patterns
    )


def _language_for_path(path: Path) -> str:
    """依副檔名回傳報告用 language label。"""

    return {
        ".py": "python",
        ".js": "javascript",
        ".css": "css",
        ".html": "html",
    }.get(path.suffix.casefold(), path.suffix.casefold().lstrip(".") or "unknown")


def _estimated_code_line_count(lines: Sequence[str], *, language: str) -> int:
    """估算非空、非註解行數；只有檔案大小排行使用。"""

    if language == "python":
        return sum(
            1
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        )
    if language in {"javascript", "css"}:
        return sum(
            1
            for line in lines
            if line.strip() and not line.lstrip().startswith(("//", "/*", "*"))
        )
    return sum(1 for line in lines if line.strip())

