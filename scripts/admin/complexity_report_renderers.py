"""Complexity report 的 text、Markdown 與 JSON renderer。"""

from __future__ import annotations

import json
from typing import Sequence

from scripts.admin.complexity_report_models import ClassMetric
from scripts.admin.complexity_report_models import ComplexityReport
from scripts.admin.complexity_report_models import DEFAULT_TOP
from scripts.admin.complexity_report_models import FileMetric
from scripts.admin.complexity_report_models import FunctionMetric
from scripts.admin.complexity_report_models import ReviewAnnotation
from scripts.admin.complexity_report_models import SCHEMA_VERSION
from scripts.admin.complexity_report_rankings import known_large_classes
from scripts.admin.complexity_report_rankings import known_large_files
from scripts.admin.complexity_report_rankings import known_large_functions
from scripts.admin.complexity_report_rankings import top_files_by_lines
from scripts.admin.complexity_report_rankings import top_functions_by_ccn
from scripts.admin.complexity_report_rankings import top_functions_by_nloc
from scripts.admin.complexity_report_rankings import watchlist_classes
from scripts.admin.complexity_report_rankings import watchlist_files
from scripts.admin.complexity_report_rankings import watchlist_functions


def report_to_json(
    report: ComplexityReport,
    *,
    top: int,
    include_known_large: bool = False,
) -> dict[str, object]:
    """轉成穩定 JSON report。"""

    return {
        "schema_version": SCHEMA_VERSION,
        "summary": {
            "paths": [path.as_posix() for path in report.paths],
            "source_file_count": len(report.source_files),
            "function_count": len(report.functions),
            "class_count": len(report.classes),
            "analysis_error_count": len(report.analysis_errors),
            "excluded_file_count": report.excluded_file_count,
            "annotation_count": len(report.annotations),
            "annotation_warning_count": len(report.annotation_warnings),
            "top": top,
            "metric_source": "lizard",
            "note": "ranking_only_review_hint",
        },
        "top_functions_by_ccn": [
            metric.to_json()
            for metric in top_functions_by_ccn(
                report.functions,
                top,
                annotations=report.annotations,
                include_known_large=include_known_large,
            )
        ],
        "top_functions_by_nloc": [
            metric.to_json()
            for metric in top_functions_by_nloc(
                report.functions,
                top,
                annotations=report.annotations,
                include_known_large=include_known_large,
            )
        ],
        "top_files_by_lines": [
            metric.to_json()
            for metric in top_files_by_lines(
                report.source_files,
                top,
                annotations=report.annotations,
                include_known_large=include_known_large,
            )
        ],
        "known_large_functions": [
            _annotated_function_json(metric, annotation)
            for metric, annotation in known_large_functions(
                report.functions,
                report.annotations,
                top=top,
            )
        ],
        "known_large_classes": [
            _annotated_class_json(metric, annotation)
            for metric, annotation in known_large_classes(
                report.classes,
                report.annotations,
                top=top,
            )
        ],
        "known_large_files": [
            _annotated_file_json(metric, annotation)
            for metric, annotation in known_large_files(
                report.source_files,
                report.annotations,
                top=top,
            )
        ],
        "watchlist_functions": [
            _annotated_function_json(metric, annotation)
            for metric, annotation in watchlist_functions(
                report.functions,
                report.annotations,
                top=top,
            )
        ],
        "watchlist_classes": [
            _annotated_class_json(metric, annotation)
            for metric, annotation in watchlist_classes(
                report.classes,
                report.annotations,
                top=top,
            )
        ],
        "watchlist_files": [
            _annotated_file_json(metric, annotation)
            for metric, annotation in watchlist_files(
                report.source_files,
                report.annotations,
                top=top,
            )
        ],
        "analysis_errors": [error.to_json() for error in report.analysis_errors],
        "annotation_warnings": list(report.annotation_warnings),
    }


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
            report_to_json(report, top=top, include_known_large=include_known_large),
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
            f"classes={len(report.classes)} "
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
            known_large_classes(report.classes, report.annotations, top=top),
            known_large_files(report.source_files, report.annotations, top=top),
        )
    )
    lines.append("")
    lines.extend(
        _render_watchlist_text(
            watchlist_functions(report.functions, report.annotations, top=top),
            watchlist_classes(report.classes, report.annotations, top=top),
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
    classes: Sequence[tuple[ClassMetric, ReviewAnnotation]],
    files: Sequence[tuple[FileMetric, ReviewAnnotation]],
) -> list[str]:
    """輸出 known-large 摘要，避免主排名被已審查項目佔滿。"""

    lines = ["Known-large annotations"]
    if not functions and not classes and not files:
        lines.append("No known-large entries matched the scanned paths.")
        return lines
    if classes:
        lines.append("classes:")
        lines.append(
            "rank  path:line  lang  lines  methods  max_ccn  class  category  reason  governance"
        )
        for rank, (class_metric, annotation) in enumerate(classes, start=1):
            lines.append(
                f"{rank:>4}  {class_metric.display_path}:{class_metric.start_line}  "
                f"{class_metric.language:<6}  {class_metric.line_count:>5}  "
                f"{class_metric.method_count:>7}  {class_metric.max_ccn:>7}  "
                f"{class_metric.display_name}  {annotation.category}  "
                f"{annotation.reason}  "
                f"{_annotation_governance_text(annotation)}"
            )
    if functions:
        lines.append("functions:")
        lines.append(
            "rank  path:line  lang  ccn  nloc  function  category  reason  governance"
        )
        for rank, (function_metric, annotation) in enumerate(functions, start=1):
            lines.append(
                f"{rank:>4}  {function_metric.display_path}:{function_metric.start_line}  "
                f"{function_metric.language:<6}  {function_metric.ccn:>3}  "
                f"{function_metric.nloc:>4}  {function_metric.display_name}  "
                f"{annotation.category}  {annotation.reason}  "
                f"{_annotation_governance_text(annotation)}"
            )
    if files:
        lines.append("files:")
        lines.append("rank  path  lang  lines  category  reason  governance")
        for rank, (file_metric, annotation) in enumerate(files, start=1):
            lines.append(
                f"{rank:>4}  {file_metric.display_path}  {file_metric.language:<6}  "
                f"{file_metric.total_lines:>5}  {annotation.category}  "
                f"{annotation.reason}  {_annotation_governance_text(annotation)}"
            )
    return lines


def _render_watchlist_text(
    functions: Sequence[tuple[FunctionMetric, ReviewAnnotation]],
    classes: Sequence[tuple[ClassMetric, ReviewAnnotation]],
    files: Sequence[tuple[FileMetric, ReviewAnnotation]],
) -> list[str]:
    """輸出人工 watchlist 摘要。"""

    lines = ["Watchlist annotations"]
    if not functions and not classes and not files:
        lines.append("No watchlist entries matched the scanned paths.")
        return lines
    if classes:
        lines.append("classes:")
        lines.append(
            "rank  path:line  lang  lines  methods  max_ccn  class  category  reason  governance"
        )
        for rank, (class_metric, annotation) in enumerate(classes, start=1):
            lines.append(
                f"{rank:>4}  {class_metric.display_path}:{class_metric.start_line}  "
                f"{class_metric.language:<6}  {class_metric.line_count:>5}  "
                f"{class_metric.method_count:>7}  {class_metric.max_ccn:>7}  "
                f"{class_metric.display_name}  {annotation.category}  "
                f"{annotation.reason}  "
                f"{_annotation_governance_text(annotation)}"
            )
    if functions:
        lines.append("functions:")
        lines.append(
            "rank  path:line  lang  ccn  nloc  function  category  reason  governance"
        )
        for rank, (function_metric, annotation) in enumerate(functions, start=1):
            lines.append(
                f"{rank:>4}  {function_metric.display_path}:{function_metric.start_line}  "
                f"{function_metric.language:<6}  {function_metric.ccn:>3}  "
                f"{function_metric.nloc:>4}  {function_metric.display_name}  "
                f"{annotation.category}  {annotation.reason}  "
                f"{_annotation_governance_text(annotation)}"
            )
    if files:
        lines.append("files:")
        lines.append("rank  path  lang  lines  category  reason  governance")
        for rank, (file_metric, annotation) in enumerate(files, start=1):
            lines.append(
                f"{rank:>4}  {file_metric.display_path}  {file_metric.language:<6}  "
                f"{file_metric.total_lines:>5}  {annotation.category}  "
                f"{annotation.reason}  {_annotation_governance_text(annotation)}"
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
        f"- Classes: `{len(report.classes)}`",
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
            known_large_classes(report.classes, report.annotations, top=top),
            known_large_files(report.source_files, report.annotations, top=top),
        )
    )
    lines.append("")
    lines.extend(
        _render_watchlist_markdown(
            watchlist_functions(report.functions, report.annotations, top=top),
            watchlist_classes(report.classes, report.annotations, top=top),
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
    classes: Sequence[tuple[ClassMetric, ReviewAnnotation]],
    files: Sequence[tuple[FileMetric, ReviewAnnotation]],
) -> list[str]:
    """輸出 markdown known-large 摘要。"""

    lines = ["## Known-Large Annotations", ""]
    if not functions and not classes and not files:
        lines.append("No known-large entries matched the scanned paths.")
        return lines
    if classes:
        lines.extend(
            [
                "### Classes",
                "",
                "| Rank | Location | Language | Lines | Methods | Max CCN | Class | Category | Reason | Governance |",
                "|---:|---|---|---:|---:|---:|---|---|---|---|",
            ]
        )
        for rank, (class_metric, annotation) in enumerate(classes, start=1):
            lines.append(
                f"| {rank} | `{class_metric.display_path}:{class_metric.start_line}` | "
                f"{class_metric.language} | {class_metric.line_count} | "
                f"{class_metric.method_count} | {class_metric.max_ccn} | "
                f"`{class_metric.display_name}` | {annotation.category} | "
                f"{annotation.reason} | {_annotation_governance_markdown(annotation)} |"
            )
    if functions:
        if classes:
            lines.append("")
        lines.extend(
            [
                "### Functions",
                "",
                "| Rank | Location | Language | CCN | NLOC | Function | Category | Reason | Governance |",
                "|---:|---|---|---:|---:|---|---|---|---|",
            ]
        )
        for rank, (function_metric, annotation) in enumerate(functions, start=1):
            lines.append(
                f"| {rank} | `{function_metric.display_path}:{function_metric.start_line}` | "
                f"{function_metric.language} | {function_metric.ccn} | "
                f"{function_metric.nloc} | `{function_metric.display_name}` | "
                f"{annotation.category} | "
                f"{annotation.reason} | {_annotation_governance_markdown(annotation)} |"
            )
    if files:
        if functions or classes:
            lines.append("")
        lines.extend(
            [
                "### Files",
                "",
                "| Rank | Path | Language | Lines | Category | Reason | Governance |",
                "|---:|---|---|---:|---|---|---|",
            ]
        )
        for rank, (file_metric, annotation) in enumerate(files, start=1):
            lines.append(
                f"| {rank} | `{file_metric.display_path}` | {file_metric.language} | "
                f"{file_metric.total_lines} | {annotation.category} | "
                f"{annotation.reason} | {_annotation_governance_markdown(annotation)} |"
            )
    return lines


def _render_watchlist_markdown(
    functions: Sequence[tuple[FunctionMetric, ReviewAnnotation]],
    classes: Sequence[tuple[ClassMetric, ReviewAnnotation]],
    files: Sequence[tuple[FileMetric, ReviewAnnotation]],
) -> list[str]:
    """輸出 markdown watchlist 摘要。"""

    lines = ["## Watchlist Annotations", ""]
    if not functions and not classes and not files:
        lines.append("No watchlist entries matched the scanned paths.")
        return lines
    if classes:
        lines.extend(
            [
                "### Classes",
                "",
                "| Rank | Location | Language | Lines | Methods | Max CCN | Class | Category | Reason | Governance |",
                "|---:|---|---|---:|---:|---:|---|---|---|---|",
            ]
        )
        for rank, (class_metric, annotation) in enumerate(classes, start=1):
            lines.append(
                f"| {rank} | `{class_metric.display_path}:{class_metric.start_line}` | "
                f"{class_metric.language} | {class_metric.line_count} | "
                f"{class_metric.method_count} | {class_metric.max_ccn} | "
                f"`{class_metric.display_name}` | {annotation.category} | "
                f"{annotation.reason} | {_annotation_governance_markdown(annotation)} |"
            )
    if functions:
        if classes:
            lines.append("")
        lines.extend(
            [
                "### Functions",
                "",
                "| Rank | Location | Language | CCN | NLOC | Function | Category | Reason | Governance |",
                "|---:|---|---|---:|---:|---|---|---|---|",
            ]
        )
        for rank, (function_metric, annotation) in enumerate(functions, start=1):
            lines.append(
                f"| {rank} | `{function_metric.display_path}:{function_metric.start_line}` | "
                f"{function_metric.language} | {function_metric.ccn} | "
                f"{function_metric.nloc} | `{function_metric.display_name}` | "
                f"{annotation.category} | "
                f"{annotation.reason} | {_annotation_governance_markdown(annotation)} |"
            )
    if files:
        if functions or classes:
            lines.append("")
        lines.extend(
            [
                "### Files",
                "",
                "| Rank | Path | Language | Lines | Category | Reason | Governance |",
                "|---:|---|---|---:|---|---|---|",
            ]
        )
        for rank, (file_metric, annotation) in enumerate(files, start=1):
            lines.append(
                f"| {rank} | `{file_metric.display_path}` | {file_metric.language} | "
                f"{file_metric.total_lines} | {annotation.category} | "
                f"{annotation.reason} | {_annotation_governance_markdown(annotation)} |"
            )
    return lines


def _annotation_governance_text(annotation: ReviewAnnotation) -> str:
    """回傳 text report 使用的 compact governance 摘要。"""

    parts: list[str] = []
    if annotation.must_not_add:
        parts.append(f"must_not_add={', '.join(annotation.must_not_add)}")
    if annotation.split_trigger:
        parts.append(f"split_trigger={annotation.split_trigger}")
    return "; ".join(parts) if parts else "-"


def _annotation_governance_markdown(annotation: ReviewAnnotation) -> str:
    """回傳 markdown table cell 使用的 compact governance 摘要。"""

    return _annotation_governance_text(annotation).replace("|", "\\|")


def _annotated_function_json(
    metric: FunctionMetric,
    annotation: ReviewAnnotation,
) -> dict[str, object]:
    payload = metric.to_json()
    payload["annotation"] = annotation.to_json()
    return payload


def _annotated_class_json(
    metric: ClassMetric,
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
