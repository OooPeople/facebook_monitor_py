"""輸出 source maintainability ranking，供人工 review 使用。

本工具使用 Lizard 產生 Python / JavaScript 函式的 NLOC、CCN 與 token
metrics，再由本 repo 的 wrapper 加上 known-large / watchlist annotation。
它只做統計與排序，不做 pass/fail gate，也不設定合格門檻；是否拆分仍需
人工判斷產品語義、狀態流程、交易邊界與測試風險。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fnmatch
import json
from pathlib import Path
from typing import Iterable
from typing import Mapping
from typing import Sequence

import lizard  # type: ignore[import-untyped]


SCHEMA_VERSION = 2
DEFAULT_PATHS = ("src", "scripts")
DEFAULT_TOP = 20
DEFAULT_EXTENSIONS = (".py", ".js", ".css", ".html")
LIZARD_EXTENSIONS = frozenset({".py", ".js"})
DEFAULT_ANNOTATION_PATH = Path("docs/maintainability_annotations.json")
DEFAULT_EXCLUDE_GLOBS = (
    "**/__pycache__/**",
    "**/.venv/**",
    "**/node_modules/**",
    "src/facebook_monitor/webapp/static/vendor/**",
)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ANNOTATION_SCHEMA_VERSION = 1
ALLOWED_ANNOTATION_STATUSES = frozenset({"known_large", "watchlist"})


@dataclass(frozen=True)
class SourcePath:
    """保存實際讀檔路徑與報告用 repo-relative 路徑。"""

    actual_path: Path
    report_path: Path


@dataclass(frozen=True)
class FunctionMetric:
    """保存單一 Lizard 函式指標。"""

    path: Path
    language: str
    name: str
    long_name: str
    start_line: int
    end_line: int
    nloc: int
    ccn: int
    token_count: int
    parameter_count: int

    @property
    def display_path(self) -> str:
        """回傳適合 terminal 顯示的 repo-relative path。"""

        return self.path.as_posix()

    @property
    def line_count(self) -> int:
        """回傳 source line span；NLOC 由 Lizard 另行提供。"""

        return max(self.end_line - self.start_line + 1, 1)

    @property
    def display_name(self) -> str:
        """回傳報告中使用的函式名稱。"""

        return self.long_name or self.name

    def to_json(self) -> dict[str, object]:
        """轉成穩定 JSON shape。"""

        return {
            "path": self.display_path,
            "language": self.language,
            "line": self.start_line,
            "end_line": self.end_line,
            "name": self.name,
            "long_name": self.long_name,
            "nloc": self.nloc,
            "ccn": self.ccn,
            "token_count": self.token_count,
            "parameter_count": self.parameter_count,
            "line_count": self.line_count,
        }


@dataclass(frozen=True)
class FileMetric:
    """保存單一 source 檔案的大小與 Lizard 函式摘要。"""

    path: Path
    language: str
    total_lines: int
    estimated_code_lines: int
    nloc: int
    function_count: int
    max_ccn: int
    max_function_nloc: int
    max_token_count: int

    @property
    def display_path(self) -> str:
        """回傳適合 terminal 顯示的 repo-relative path。"""

        return self.path.as_posix()

    def to_json(self) -> dict[str, object]:
        """轉成穩定 JSON shape。"""

        return {
            "path": self.display_path,
            "language": self.language,
            "total_lines": self.total_lines,
            "estimated_code_lines": self.estimated_code_lines,
            "nloc": self.nloc,
            "function_count": self.function_count,
            "max_ccn": self.max_ccn,
            "max_function_nloc": self.max_function_nloc,
            "max_token_count": self.max_token_count,
        }


@dataclass(frozen=True)
class AnalysisError:
    """保存讀檔或 Lizard analysis 錯誤。"""

    path: Path
    message: str

    @property
    def display_path(self) -> str:
        """回傳適合 terminal 顯示的 repo-relative path。"""

        return self.path.as_posix()

    def to_json(self) -> dict[str, str]:
        """轉成穩定 JSON shape。"""

        return {"path": self.display_path, "message": self.message}


@dataclass(frozen=True)
class ReviewAnnotation:
    """保存人工審查標註；標註只影響呈現，不是 gate。"""

    status: str
    path_glob: str
    symbol: str
    category: str
    reason: str

    def to_json(self) -> dict[str, str]:
        """轉成穩定 JSON shape。"""

        return {
            "status": self.status,
            "path_glob": self.path_glob,
            "symbol": self.symbol,
            "category": self.category,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AnnotationLoadResult:
    """保存 annotation 載入結果；設定錯誤只產生 warning，不讓報告失敗。"""

    annotations: tuple[ReviewAnnotation, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ComplexityReport:
    """保存一次 maintainability ranking 掃描結果。"""

    paths: tuple[Path, ...]
    source_files: tuple[FileMetric, ...]
    functions: tuple[FunctionMetric, ...]
    analysis_errors: tuple[AnalysisError, ...]
    excluded_file_count: int
    annotations: tuple[ReviewAnnotation, ...]
    annotation_warnings: tuple[str, ...] = ()

    def to_json(self, *, top: int, include_known_large: bool = False) -> dict[str, object]:
        """轉成穩定 JSON report。"""

        return {
            "schema_version": SCHEMA_VERSION,
            "summary": {
                "paths": [path.as_posix() for path in self.paths],
                "source_file_count": len(self.source_files),
                "function_count": len(self.functions),
                "analysis_error_count": len(self.analysis_errors),
                "excluded_file_count": self.excluded_file_count,
                "annotation_count": len(self.annotations),
                "annotation_warning_count": len(self.annotation_warnings),
                "top": top,
                "metric_source": "lizard",
                "note": "ranking_only_review_hint",
            },
            "top_functions_by_ccn": [
                metric.to_json()
                for metric in top_functions_by_ccn(
                    self.functions,
                    top,
                    annotations=self.annotations,
                    include_known_large=include_known_large,
                )
            ],
            "top_functions_by_nloc": [
                metric.to_json()
                for metric in top_functions_by_nloc(
                    self.functions,
                    top,
                    annotations=self.annotations,
                    include_known_large=include_known_large,
                )
            ],
            "top_files_by_lines": [
                metric.to_json()
                for metric in top_files_by_lines(
                    self.source_files,
                    top,
                    annotations=self.annotations,
                    include_known_large=include_known_large,
                )
            ],
            "known_large_functions": [
                _annotated_function_json(metric, annotation)
                for metric, annotation in known_large_functions(
                    self.functions,
                    self.annotations,
                    top=top,
                )
            ],
            "known_large_files": [
                _annotated_file_json(metric, annotation)
                for metric, annotation in known_large_files(
                    self.source_files,
                    self.annotations,
                    top=top,
                )
            ],
            "watchlist_functions": [
                _annotated_function_json(metric, annotation)
                for metric, annotation in watchlist_functions(
                    self.functions,
                    self.annotations,
                    top=top,
                )
            ],
            "watchlist_files": [
                _annotated_file_json(metric, annotation)
                for metric, annotation in watchlist_files(
                    self.source_files,
                    self.annotations,
                    top=top,
                )
            ],
            "analysis_errors": [error.to_json() for error in self.analysis_errors],
            "annotation_warnings": list(self.annotation_warnings),
        }


def collect_report(
    paths: Iterable[Path],
    *,
    include_extensions: Sequence[str] = DEFAULT_EXTENSIONS,
    exclude_globs: Sequence[str] = DEFAULT_EXCLUDE_GLOBS,
    annotations: Sequence[ReviewAnnotation] = (),
    annotation_warnings: Sequence[str] = (),
) -> ComplexityReport:
    """收集 source 檔案與 Lizard 函式指標；不套用任何門檻。"""

    input_paths = tuple(paths)
    normalized_paths = tuple(_report_path_for_path(path) for path in input_paths)
    source_files: list[FileMetric] = []
    functions: list[FunctionMetric] = []
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
        file_metric, file_functions, error = analyze_source_file(source_path)
        if file_metric is not None:
            source_files.append(file_metric)
        functions.extend(file_functions)
        if error is not None:
            analysis_errors.append(error)
    return ComplexityReport(
        paths=normalized_paths,
        source_files=tuple(source_files),
        functions=tuple(functions),
        analysis_errors=tuple(analysis_errors),
        excluded_file_count=excluded_file_count,
        annotations=tuple(annotations),
        annotation_warnings=tuple(annotation_warnings),
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
) -> tuple[FileMetric | None, tuple[FunctionMetric, ...], AnalysisError | None]:
    """分析單一 source 檔案；Python / JS 函式指標交由 Lizard。"""

    actual_path = source_path.actual_path
    report_path = source_path.report_path
    try:
        text = actual_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return None, (), AnalysisError(path=report_path, message=f"utf8_decode_error:{exc}")
    except OSError as exc:
        return None, (), AnalysisError(path=report_path, message=f"read_error:{exc}")

    lines = text.splitlines()
    language = _language_for_path(actual_path)
    functions: tuple[FunctionMetric, ...] = ()
    file_nloc = _estimated_code_line_count(lines, language=language)
    error: AnalysisError | None = None
    if actual_path.suffix.casefold() in LIZARD_EXTENSIONS:
        try:
            file_info = lizard.analyze_file(str(actual_path))
        except Exception as exc:  # pragma: no cover - Lizard errors are environment-specific.
            error = AnalysisError(path=report_path, message=f"lizard_error:{exc}")
        else:
            file_nloc = int(getattr(file_info, "nloc", file_nloc) or 0)
            functions = tuple(
                _function_metric_from_lizard(report_path, language, function_info)
                for function_info in getattr(file_info, "function_list", ())
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
    return file_metric, functions, error


def _function_metric_from_lizard(
    path: Path,
    language: str,
    function_info: object,
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
    )


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


def render_report(
    report: ComplexityReport,
    *,
    top: int = DEFAULT_TOP,
    format_name: str = "text",
    include_known_large: bool = False,
) -> str:
    """將 report 輸出為 text / markdown / json。"""

    if format_name == "json":
        return json.dumps(
            report.to_json(top=top, include_known_large=include_known_large),
            ensure_ascii=False,
            indent=2,
        )
    if format_name == "markdown":
        return _render_markdown_report(
            report,
            top=top,
            include_known_large=include_known_large,
        )
    return _render_text_report(
        report,
        top=top,
        include_known_large=include_known_large,
    )


def _render_text_report(
    report: ComplexityReport,
    *,
    top: int,
    include_known_large: bool,
) -> str:
    lines = [
        "Complexity / Maintainability Ranking",
        f"scanned_paths: {', '.join(path.as_posix() for path in report.paths)}",
        (
            "summary: "
            f"source_files={len(report.source_files)} "
            f"functions={len(report.functions)} "
            f"analysis_errors={len(report.analysis_errors)} "
            f"excluded_files={report.excluded_file_count} "
            f"annotations={len(report.annotations)} "
            f"annotation_warnings={len(report.annotation_warnings)} "
            f"top={top}"
        ),
        "metric_source: lizard",
        "note: ranking only; metrics are review hints and never change exit code.",
        (
            "known_large: hidden from primary rankings and listed separately."
            if not include_known_large
            else "known_large: included in primary rankings and listed separately."
        ),
        "",
    ]
    lines.extend(
        _render_function_table_text(
            "Top functions by CCN",
            top_functions_by_ccn(
                report.functions,
                top,
                annotations=report.annotations,
                include_known_large=include_known_large,
            ),
        )
    )
    lines.append("")
    lines.extend(
        _render_function_table_text(
            "Top functions by NLOC",
            top_functions_by_nloc(
                report.functions,
                top,
                annotations=report.annotations,
                include_known_large=include_known_large,
            ),
        )
    )
    lines.append("")
    lines.extend(
        _render_file_table_text(
            top_files_by_lines(
                report.source_files,
                top,
                annotations=report.annotations,
                include_known_large=include_known_large,
            )
        )
    )
    lines.append("")
    lines.extend(
        _render_known_large_text(
            known_large_functions(report.functions, report.annotations, top=top),
            known_large_files(report.source_files, report.annotations, top=top),
        )
    )
    lines.append("")
    lines.extend(
        _render_watchlist_text(
            watchlist_functions(report.functions, report.annotations, top=top),
            watchlist_files(report.source_files, report.annotations, top=top),
        )
    )
    if report.analysis_errors:
        lines.append("")
        lines.append("Analysis errors")
        lines.append("path  message")
        for error in report.analysis_errors:
            lines.append(f"{error.display_path}  {error.message}")
    if report.annotation_warnings:
        lines.append("")
        lines.append("Annotation warnings")
        for warning in report.annotation_warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_function_table_text(
    title: str,
    metrics: Sequence[FunctionMetric],
) -> list[str]:
    lines = [title]
    if not metrics:
        lines.append("No Lizard-supported functions found in the scanned paths.")
        return lines
    lines.append("rank  path:line  lang  ccn  nloc  tokens  params  function")
    for rank, metric in enumerate(metrics, start=1):
        lines.append(
            f"{rank:>4}  {metric.display_path}:{metric.start_line}  "
            f"{metric.language:<6}  {metric.ccn:>3}  {metric.nloc:>4}  "
            f"{metric.token_count:>6}  {metric.parameter_count:>6}  "
            f"{metric.display_name}"
        )
    return lines


def _render_file_table_text(metrics: Sequence[FileMetric]) -> list[str]:
    lines = ["Top source files by line count"]
    if not metrics:
        lines.append("No source files found in the scanned paths.")
        return lines
    lines.append(
        "rank  path  lang  lines  est_code_lines  nloc  functions  max_ccn  max_fn_nloc"
    )
    for rank, metric in enumerate(metrics, start=1):
        lines.append(
            f"{rank:>4}  {metric.display_path}  {metric.language:<6}  "
            f"{metric.total_lines:>5}  {metric.estimated_code_lines:>14}  "
            f"{metric.nloc:>4}  {metric.function_count:>9}  "
            f"{metric.max_ccn:>7}  {metric.max_function_nloc:>11}"
        )
    return lines


def _render_known_large_text(
    functions: Sequence[tuple[FunctionMetric, ReviewAnnotation]],
    files: Sequence[tuple[FileMetric, ReviewAnnotation]],
) -> list[str]:
    """輸出 known-large 摘要，避免主排名被已審查項目佔滿。"""

    lines = ["Known-large annotations"]
    if not functions and not files:
        lines.append("No known-large entries matched the scanned paths.")
        return lines
    if functions:
        lines.append("functions:")
        lines.append("rank  path:line  lang  ccn  nloc  function  category  reason")
        for rank, (metric, annotation) in enumerate(functions, start=1):
            lines.append(
                f"{rank:>4}  {metric.display_path}:{metric.start_line}  "
                f"{metric.language:<6}  {metric.ccn:>3}  {metric.nloc:>4}  "
                f"{metric.display_name}  {annotation.category}  {annotation.reason}"
            )
    if files:
        lines.append("files:")
        lines.append("rank  path  lang  lines  category  reason")
        for rank, (file_metric, annotation) in enumerate(files, start=1):
            lines.append(
                f"{rank:>4}  {file_metric.display_path}  {file_metric.language:<6}  "
                f"{file_metric.total_lines:>5}  {annotation.category}  {annotation.reason}"
            )
    return lines


def _render_watchlist_text(
    functions: Sequence[tuple[FunctionMetric, ReviewAnnotation]],
    files: Sequence[tuple[FileMetric, ReviewAnnotation]],
) -> list[str]:
    """輸出人工 watchlist 摘要。"""

    lines = ["Watchlist annotations"]
    if not functions and not files:
        lines.append("No watchlist entries matched the scanned paths.")
        return lines
    if functions:
        lines.append("functions:")
        lines.append("rank  path:line  lang  ccn  nloc  function  category  reason")
        for rank, (metric, annotation) in enumerate(functions, start=1):
            lines.append(
                f"{rank:>4}  {metric.display_path}:{metric.start_line}  "
                f"{metric.language:<6}  {metric.ccn:>3}  {metric.nloc:>4}  "
                f"{metric.display_name}  {annotation.category}  {annotation.reason}"
            )
    if files:
        lines.append("files:")
        lines.append("rank  path  lang  lines  category  reason")
        for rank, (file_metric, annotation) in enumerate(files, start=1):
            lines.append(
                f"{rank:>4}  {file_metric.display_path}  {file_metric.language:<6}  "
                f"{file_metric.total_lines:>5}  {annotation.category}  {annotation.reason}"
            )
    return lines


def _render_markdown_report(
    report: ComplexityReport,
    *,
    top: int,
    include_known_large: bool,
) -> str:
    lines = [
        "# Complexity / Maintainability Ranking",
        "",
        f"- Scanned paths: `{', '.join(path.as_posix() for path in report.paths)}`",
        f"- Source files: `{len(report.source_files)}`",
        f"- Functions: `{len(report.functions)}`",
        f"- Analysis errors: `{len(report.analysis_errors)}`",
        f"- Excluded files: `{report.excluded_file_count}`",
        f"- Annotations: `{len(report.annotations)}`",
        f"- Annotation warnings: `{len(report.annotation_warnings)}`",
        f"- Rows per section: `{top}`",
        "- Metric source: `lizard`",
        "- Note: ranking only; metrics are review hints and never change exit code.",
        "- Known-large entries are listed separately and omitted from primary rankings by default."
        if not include_known_large
        else "- Known-large entries are included in primary rankings and also listed separately.",
        "",
    ]
    lines.extend(
        _render_function_table_markdown(
            "Top Functions By CCN",
            top_functions_by_ccn(
                report.functions,
                top,
                annotations=report.annotations,
                include_known_large=include_known_large,
            ),
        )
    )
    lines.append("")
    lines.extend(
        _render_function_table_markdown(
            "Top Functions By NLOC",
            top_functions_by_nloc(
                report.functions,
                top,
                annotations=report.annotations,
                include_known_large=include_known_large,
            ),
        )
    )
    lines.append("")
    lines.extend(
        _render_file_table_markdown(
            "Top Source Files By Line Count",
            top_files_by_lines(
                report.source_files,
                top,
                annotations=report.annotations,
                include_known_large=include_known_large,
            ),
        )
    )
    lines.append("")
    lines.extend(
        _render_known_large_markdown(
            known_large_functions(report.functions, report.annotations, top=top),
            known_large_files(report.source_files, report.annotations, top=top),
        )
    )
    lines.append("")
    lines.extend(
        _render_watchlist_markdown(
            watchlist_functions(report.functions, report.annotations, top=top),
            watchlist_files(report.source_files, report.annotations, top=top),
        )
    )
    if report.analysis_errors:
        lines.append("")
        lines.append("## Analysis Errors")
        lines.append("")
        lines.append("| Path | Message |")
        lines.append("|---|---|")
        for error in report.analysis_errors:
            lines.append(f"| `{error.display_path}` | `{error.message}` |")
    if report.annotation_warnings:
        lines.append("")
        lines.append("## Annotation Warnings")
        lines.append("")
        for warning in report.annotation_warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_function_table_markdown(
    title: str,
    metrics: Sequence[FunctionMetric],
) -> list[str]:
    lines = [f"## {title}", ""]
    if not metrics:
        lines.append("No Lizard-supported functions found in the scanned paths.")
        return lines
    lines.extend(
        [
            "| Rank | Location | Language | CCN | NLOC | Tokens | Params | Function |",
            "|---:|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for rank, metric in enumerate(metrics, start=1):
        lines.append(
            f"| {rank} | `{metric.display_path}:{metric.start_line}` | "
            f"{metric.language} | {metric.ccn} | {metric.nloc} | "
            f"{metric.token_count} | {metric.parameter_count} | "
            f"`{metric.display_name}` |"
        )
    return lines


def _render_file_table_markdown(
    title: str,
    metrics: Sequence[FileMetric],
) -> list[str]:
    lines = [f"## {title}", ""]
    if not metrics:
        lines.append("No source files found in the scanned paths.")
        return lines
    lines.extend(
        [
            "| Rank | Path | Language | Lines | Estimated Code Lines | NLOC | Functions | Max CCN | Max Function NLOC |",
            "|---:|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for rank, metric in enumerate(metrics, start=1):
        lines.append(
            f"| {rank} | `{metric.display_path}` | {metric.language} | "
            f"{metric.total_lines} | {metric.estimated_code_lines} | {metric.nloc} | "
            f"{metric.function_count} | {metric.max_ccn} | {metric.max_function_nloc} |"
        )
    return lines


def _render_known_large_markdown(
    functions: Sequence[tuple[FunctionMetric, ReviewAnnotation]],
    files: Sequence[tuple[FileMetric, ReviewAnnotation]],
) -> list[str]:
    """輸出 markdown known-large 摘要。"""

    lines = ["## Known-Large Annotations", ""]
    if not functions and not files:
        lines.append("No known-large entries matched the scanned paths.")
        return lines
    if functions:
        lines.extend(
            [
                "### Functions",
                "",
                "| Rank | Location | Language | CCN | NLOC | Function | Category | Reason |",
                "|---:|---|---|---:|---:|---|---|---|",
            ]
        )
        for rank, (metric, annotation) in enumerate(functions, start=1):
            lines.append(
                f"| {rank} | `{metric.display_path}:{metric.start_line}` | "
                f"{metric.language} | {metric.ccn} | {metric.nloc} | "
                f"`{metric.display_name}` | {annotation.category} | "
                f"{annotation.reason} |"
            )
    if files:
        if functions:
            lines.append("")
        lines.extend(
            [
                "### Files",
                "",
                "| Rank | Path | Language | Lines | Category | Reason |",
                "|---:|---|---|---:|---|---|",
            ]
        )
        for rank, (file_metric, annotation) in enumerate(files, start=1):
            lines.append(
                f"| {rank} | `{file_metric.display_path}` | {file_metric.language} | "
                f"{file_metric.total_lines} | {annotation.category} | "
                f"{annotation.reason} |"
            )
    return lines


def _render_watchlist_markdown(
    functions: Sequence[tuple[FunctionMetric, ReviewAnnotation]],
    files: Sequence[tuple[FileMetric, ReviewAnnotation]],
) -> list[str]:
    """輸出 markdown watchlist 摘要。"""

    lines = ["## Watchlist Annotations", ""]
    if not functions and not files:
        lines.append("No watchlist entries matched the scanned paths.")
        return lines
    if functions:
        lines.extend(
            [
                "### Functions",
                "",
                "| Rank | Location | Language | CCN | NLOC | Function | Category | Reason |",
                "|---:|---|---|---:|---:|---|---|---|",
            ]
        )
        for rank, (metric, annotation) in enumerate(functions, start=1):
            lines.append(
                f"| {rank} | `{metric.display_path}:{metric.start_line}` | "
                f"{metric.language} | {metric.ccn} | {metric.nloc} | "
                f"`{metric.display_name}` | {annotation.category} | "
                f"{annotation.reason} |"
            )
    if files:
        if functions:
            lines.append("")
        lines.extend(
            [
                "### Files",
                "",
                "| Rank | Path | Language | Lines | Category | Reason |",
                "|---:|---|---|---:|---|---|",
            ]
        )
        for rank, (file_metric, annotation) in enumerate(files, start=1):
            lines.append(
                f"| {rank} | `{file_metric.display_path}` | {file_metric.language} | "
                f"{file_metric.total_lines} | {annotation.category} | "
                f"{annotation.reason} |"
            )
    return lines


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
        if (annotation := _annotation_for_function(metric, annotations)) is not None
        and annotation.status == "known_large"
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
        if (annotation := _annotation_for_file(metric, annotations)) is not None
        and annotation.status == "known_large"
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
        if (annotation := _annotation_for_function(metric, annotations)) is not None
        and annotation.status == "watchlist"
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
        if (annotation := _annotation_for_file(metric, annotations)) is not None
        and annotation.status == "watchlist"
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
    annotation = _annotation_for_function(metric, annotations)
    return annotation is not None and annotation.status == "known_large"


def _is_known_large_file(
    metric: FileMetric,
    annotations: Sequence[ReviewAnnotation],
) -> bool:
    annotation = _annotation_for_file(metric, annotations)
    return annotation is not None and annotation.status == "known_large"


def _annotation_for_function(
    metric: FunctionMetric,
    annotations: Sequence[ReviewAnnotation],
) -> ReviewAnnotation | None:
    for annotation in annotations:
        if annotation.symbol:
            if not (
                metric.name == annotation.symbol
                or metric.long_name == annotation.symbol
                or metric.long_name.startswith(f"{annotation.symbol}(")
                or metric.long_name.startswith(f"{annotation.symbol} ")
                or metric.long_name.endswith(f".{annotation.symbol}")
            ):
                continue
        if _path_matches_annotation(metric.path, annotation):
            return annotation
    return None


def _annotation_for_file(
    metric: FileMetric,
    annotations: Sequence[ReviewAnnotation],
) -> ReviewAnnotation | None:
    for annotation in annotations:
        if annotation.symbol:
            continue
        if _path_matches_annotation(metric.path, annotation):
            return annotation
    return None


def _path_matches_annotation(path: Path, annotation: ReviewAnnotation) -> bool:
    return fnmatch.fnmatch(path.as_posix(), annotation.path_glob.replace("\\", "/"))


def _annotated_function_json(
    metric: FunctionMetric,
    annotation: ReviewAnnotation,
) -> dict[str, object]:
    payload = metric.to_json()
    payload["annotation"] = annotation.to_json()
    return payload


def _annotated_file_json(
    metric: FileMetric,
    annotation: ReviewAnnotation,
) -> dict[str, object]:
    payload = metric.to_json()
    payload["annotation"] = annotation.to_json()
    return payload


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
    return (
        ReviewAnnotation(
            status=status,
            path_glob=path_glob,
            symbol=str(payload.get("symbol") or ""),
            category=str(payload.get("category") or ""),
            reason=str(payload.get("reason") or ""),
        ),
        None,
    )


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


def build_parser() -> argparse.ArgumentParser:
    """建立 CLI parser。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path(path) for path in DEFAULT_PATHS],
        help="要掃描的檔案或目錄，預設為 src scripts。",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        help="每個 ranking 區段顯示幾筆，預設 20；只影響輸出筆數。",
    )
    parser.add_argument(
        "--format",
        choices=("text", "markdown", "json"),
        default="text",
        help="輸出格式。",
    )
    parser.add_argument(
        "--include-extensions",
        default=",".join(DEFAULT_EXTENSIONS),
        help="納入檔案大小排行的副檔名清單，例如 .py,.js,.css,.html。",
    )
    parser.add_argument(
        "--exclude-glob",
        action="append",
        default=[],
        help="額外排除的 glob，可重複指定；排除項只從排名移除並計入 summary。",
    )
    parser.add_argument(
        "--annotations",
        type=Path,
        default=DEFAULT_ANNOTATION_PATH,
        help=(
            "known-large / watchlist annotation JSON；預設讀取 "
            "docs/maintainability_annotations.json，檔案不存在時略過。"
        ),
    )
    parser.add_argument(
        "--no-annotations",
        action="store_true",
        help="不讀取 annotation 檔，顯示純排名。",
    )
    parser.add_argument(
        "--include-known-large",
        action="store_true",
        help="將 known-large entries 也納入主排行；預設只列在 known-large section。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """執行 maintainability ranking CLI。"""

    args = build_parser().parse_args(argv)
    top = max(int(args.top), 1)
    include_extensions = _parse_extension_csv(args.include_extensions)
    exclude_globs = (*DEFAULT_EXCLUDE_GLOBS, *tuple(args.exclude_glob))
    annotation_result = (
        AnnotationLoadResult(annotations=(), warnings=())
        if args.no_annotations
        else load_annotations_with_warnings(args.annotations)
    )
    report = collect_report(
        args.paths,
        include_extensions=include_extensions,
        exclude_globs=exclude_globs,
        annotations=annotation_result.annotations,
        annotation_warnings=annotation_result.warnings,
    )
    print(
        render_report(
            report,
            top=top,
            format_name=args.format,
            include_known_large=bool(args.include_known_large),
        )
    )
    return 0


def _parse_extension_csv(value: str) -> tuple[str, ...]:
    """解析逗號分隔副檔名清單。"""

    extensions = []
    for raw_extension in value.split(","):
        extension = raw_extension.strip().casefold()
        if not extension:
            continue
        extensions.append(extension if extension.startswith(".") else f".{extension}")
    return tuple(dict.fromkeys(extensions))


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


if __name__ == "__main__":
    raise SystemExit(main())
