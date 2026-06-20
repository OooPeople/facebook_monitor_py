"""Complexity report 的資料模型與穩定 schema 常數。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


SCHEMA_VERSION = 3
DEFAULT_TOP = 20
ANNOTATION_SCHEMA_VERSION = 2
ALLOWED_ANNOTATION_STATUSES = frozenset({"known_large", "watchlist"})
ALLOWED_SYMBOL_KINDS = frozenset({"file", "function", "class"})


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
    owner_class: str = ""

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

        payload: dict[str, object] = {
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
        if self.owner_class:
            payload["owner_class"] = self.owner_class
        return payload


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
class ClassRange:
    """保存 Python class 的 source line range，供 class-level annotation 使用。"""

    name: str
    qualified_name: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class ClassMetric:
    """保存單一 Python class 的 range 與內部函式摘要。"""

    path: Path
    language: str
    name: str
    long_name: str
    start_line: int
    end_line: int
    method_count: int
    nloc: int
    max_ccn: int
    max_function_nloc: int

    @property
    def display_path(self) -> str:
        """回傳適合 terminal 顯示的 repo-relative path。"""

        return self.path.as_posix()

    @property
    def line_count(self) -> int:
        """回傳 class source line span。"""

        return max(self.end_line - self.start_line + 1, 1)

    @property
    def display_name(self) -> str:
        """回傳報告中使用的 class 名稱。"""

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
            "line_count": self.line_count,
            "method_count": self.method_count,
            "nloc": self.nloc,
            "max_ccn": self.max_ccn,
            "max_function_nloc": self.max_function_nloc,
        }


@dataclass(frozen=True)
class ReviewAnnotation:
    """保存人工審查標註；標註只影響呈現，不是 gate。"""

    status: str
    path_glob: str
    symbol: str
    symbol_kind: str
    category: str
    reason: str
    must_not_add: tuple[str, ...] = ()
    split_trigger: str = ""

    def to_json(self) -> dict[str, object]:
        """轉成穩定 JSON shape。"""

        return {
            "status": self.status,
            "path_glob": self.path_glob,
            "symbol": self.symbol,
            "symbol_kind": self.symbol_kind,
            "category": self.category,
            "reason": self.reason,
            "must_not_add": list(self.must_not_add),
            "split_trigger": self.split_trigger,
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
    classes: tuple[ClassMetric, ...]
    analysis_errors: tuple[AnalysisError, ...]
    excluded_file_count: int
    annotations: tuple[ReviewAnnotation, ...]
    annotation_warnings: tuple[str, ...] = ()

