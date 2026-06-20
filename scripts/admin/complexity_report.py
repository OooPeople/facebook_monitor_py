"""輸出 source maintainability ranking，供人工 review 使用。

本 CLI 使用 Lizard 產生 Python / JavaScript 函式的 NLOC、CCN 與 token
metrics，再由本 repo 的 wrapper 加上 known-large / watchlist annotation。
它只做統計與排序，不做 pass/fail gate，也不設定合格門檻；是否拆分仍需
人工判斷產品語義、狀態流程、交易邊界與測試風險。
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.admin.complexity_report_annotations import load_annotations_with_warnings
from scripts.admin.complexity_report_collect import collect_report
from scripts.admin.complexity_report_models import AnnotationLoadResult
from scripts.admin.complexity_report_models import DEFAULT_TOP
from scripts.admin.complexity_report_renderers import render_report


DEFAULT_PATHS = ("src", "scripts")
DEFAULT_EXTENSIONS = (".py", ".js", ".css", ".html")
DEFAULT_ANNOTATION_PATH = Path("docs/maintainability_annotations.json")
DEFAULT_EXCLUDE_GLOBS = (
    "**/__pycache__/**",
    "**/.venv/**",
    "**/node_modules/**",
    "src/facebook_monitor/webapp/static/vendor/**",
)


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


if __name__ == "__main__":
    raise SystemExit(main())
