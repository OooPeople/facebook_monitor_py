"""輸出 Python 函式的簡易 complexity / 長度報告。

此工具只作 review 輔助，不預設讓既有大型模組 fail。若未來要導入 gate，
應先用本報告建立 baseline，再逐步 ratchet。
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_PATHS = ("src", "scripts", "tests")
DEFAULT_MAX_COMPLEXITY = 12
DEFAULT_MAX_LINES = 80


@dataclass(frozen=True)
class FunctionFinding:
    """保存單一函式的 complexity 報告列。"""

    path: Path
    name: str
    lineno: int
    end_lineno: int
    complexity: int
    line_count: int

    @property
    def display_path(self) -> str:
        """回傳適合 terminal 顯示的 repo-relative path。"""

        return self.path.as_posix()


def iter_python_files(paths: Iterable[Path]) -> Iterable[Path]:
    """列出指定 path 底下的 Python 檔案。"""

    for path in paths:
        if path.is_file() and path.suffix == ".py":
            yield path
        elif path.is_dir():
            yield from sorted(path.rglob("*.py"))


def collect_findings(
    paths: Iterable[Path],
    *,
    max_complexity: int = DEFAULT_MAX_COMPLEXITY,
    max_lines: int = DEFAULT_MAX_LINES,
) -> list[FunctionFinding]:
    """收集超過 complexity 或行數門檻的函式。"""

    findings: list[FunctionFinding] = []
    for path in iter_python_files(paths):
        findings.extend(
            finding
            for finding in analyze_file(path)
            if finding.complexity > max_complexity or finding.line_count > max_lines
        )
    return sorted(
        findings,
        key=lambda finding: (
            max(finding.complexity - max_complexity, 0),
            max(finding.line_count - max_lines, 0),
            finding.display_path,
            finding.lineno,
        ),
        reverse=True,
    )


def analyze_file(path: Path) -> list[FunctionFinding]:
    """分析單一 Python 檔案內的函式。"""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        FunctionFinding(
            path=path,
            name=_qualified_name(node),
            lineno=node.lineno,
            end_lineno=getattr(node, "end_lineno", node.lineno),
            complexity=_complexity(node),
            line_count=max(getattr(node, "end_lineno", node.lineno) - node.lineno + 1, 1),
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


def _qualified_name(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """目前先回傳函式名；class owner 可由 path/line 追查。"""

    return node.name


def _complexity(node: ast.AST) -> int:
    """用常見分支節點估算 cyclomatic complexity。"""

    complexity = 1
    for child in ast.walk(node):
        if isinstance(child, ast.If | ast.For | ast.AsyncFor | ast.While | ast.ExceptHandler):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += max(len(child.values) - 1, 0)
        elif isinstance(child, ast.IfExp):
            complexity += 1
        elif isinstance(child, ast.Assert):
            complexity += 1
        elif isinstance(child, ast.comprehension):
            complexity += 1 + len(child.ifs)
        elif isinstance(child, ast.Match):
            complexity += max(len(child.cases), 1)
        elif isinstance(child, ast.Try):
            complexity += len(child.handlers)
            if child.orelse:
                complexity += 1
            if child.finalbody:
                complexity += 1
    return complexity


def render_findings(findings: list[FunctionFinding]) -> str:
    """將 findings 格式化為純文字報告。"""

    if not findings:
        return "No functions exceeded the configured complexity or length thresholds."
    lines = ["path:line  complexity  lines  function"]
    for finding in findings:
        lines.append(
            f"{finding.display_path}:{finding.lineno}  "
            f"{finding.complexity:>10}  {finding.line_count:>5}  {finding.name}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """建立 CLI parser。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path(path) for path in DEFAULT_PATHS],
        help="要掃描的檔案或目錄，預設為 src scripts tests。",
    )
    parser.add_argument("--max-complexity", type=int, default=DEFAULT_MAX_COMPLEXITY)
    parser.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES)
    return parser


def main(argv: list[str] | None = None) -> int:
    """執行 complexity report CLI。"""

    args = build_parser().parse_args(argv)
    findings = collect_findings(
        args.paths,
        max_complexity=args.max_complexity,
        max_lines=args.max_lines,
    )
    print(render_findings(findings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
