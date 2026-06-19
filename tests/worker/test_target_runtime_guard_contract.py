"""Worker/scheduler runtime service guard contract tests。"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from facebook_monitor.persistence.repositories.target_runtime_state import (
    TargetRuntimeStateRepository,
)


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = ROOT / "src/facebook_monitor"
APPLICATION_DIR = ROOT / "src/facebook_monitor/application"
TARGET_RUNTIME_MODULE_FILES = tuple(sorted(APPLICATION_DIR.glob("target_runtime_*.py")))
FORMAL_RUNTIME_FACADE_MODULE = "target_runtime_service"
PACKAGE_SOURCE_FILES = tuple(sorted(PACKAGE_DIR.rglob("*.py")))
FORMAL_RUNTIME_BOUNDARY_FILES = (
    ROOT / "src/facebook_monitor/scheduler/runtime_recovery.py",
    ROOT / "src/facebook_monitor/scheduler/one_shot_loop.py",
    ROOT / "src/facebook_monitor/worker/attempt_cleanup.py",
    ROOT / "src/facebook_monitor/worker/attempt_outcomes.py",
    ROOT / "src/facebook_monitor/worker/attempt_transitions.py",
    ROOT / "src/facebook_monitor/worker/resident_main.py",
    ROOT / "src/facebook_monitor/worker/resident_main_executor.py",
    ROOT / "src/facebook_monitor/worker/resident_main_executor_attempt.py",
    ROOT / "src/facebook_monitor/worker/resident_failure_decisions.py",
    ROOT / "src/facebook_monitor/worker/resident_maintenance.py",
    ROOT / "src/facebook_monitor/worker/resident_shared.py",
    ROOT / "src/facebook_monitor/worker/scan_commit_coordinator.py",
    ROOT / "src/facebook_monitor/worker/scan_commit_outcomes.py",
    ROOT / "src/facebook_monitor/worker/scan_commit_permissions.py",
    ROOT / "src/facebook_monitor/worker/scan_commit_requests.py",
    ROOT / "src/facebook_monitor/worker/scan_commit_side_effects.py",
    ROOT / "src/facebook_monitor/worker/scan_commit_validation.py",
    ROOT / "src/facebook_monitor/worker/scan_finalize.py",
    ROOT / "src/facebook_monitor/worker/scan_failure_finalize.py",
    ROOT / "src/facebook_monitor/worker/sync_resident_fallback.py",
)
FORMAL_ASYNC_SCANNER_FILES = (
    ROOT / "src/facebook_monitor/worker/posts_pipeline.py",
    ROOT / "src/facebook_monitor/worker/comments_pipeline.py",
)
FORMAL_ASYNC_SCANNER_FUNCTIONS = {
    "scan_posts_page_async_commit_ready",
    "scan_comments_target_page_async_commit_ready",
}
APPLICATION_RUNTIME_SERVICE_FILES = (
    APPLICATION_DIR / "services.py",
    *TARGET_RUNTIME_MODULE_FILES,
)
FORBIDDEN_FORMAL_RUNTIME_SUBSERVICE_MODULES = {
    path.stem for path in TARGET_RUNTIME_MODULE_FILES if path.stem != FORMAL_RUNTIME_FACADE_MODULE
}
FORBIDDEN_FORMAL_RUNTIME_SUBSERVICE_FULL_MODULES = {
    f"facebook_monitor.application.{module}"
    for module in FORBIDDEN_FORMAL_RUNTIME_SUBSERVICE_MODULES
}
PUBLIC_RUNTIME_FACADE_SYMBOLS = {
    "QueueAdmissionResult",
    "ScanSkipDecision",
    "StaleRunningRecovery",
    "TargetRuntimeService",
}
REMOVED_APPLICATION_RUNTIME_ALIASES = {
    "apply_scan_failure_decision_if_owner",
    "apply_scan_skip_decision_if_owner",
    "mark_target_error_if_owner",
    "mark_target_idle_if_owner",
    "mark_target_page_reloaded_if_owner",
    "mark_target_retriable_failure_if_owner",
    "record_target_heartbeat_if_owner",
    "try_mark_target_running",
}
UNGUARDED_RUNTIME_METHODS = {
    "apply_scan_failure_decision",
    "apply_scan_skip_decision",
    "mark_target_error",
    "mark_target_idle",
    "mark_target_page_reloaded",
    "mark_target_retriable_failure",
    "mark_target_running",
    "record_target_heartbeat",
}
LEGACY_QUEUE_ADMISSION_METHODS = {"mark_target_queued"}
ALLOWED_FORCE_RUNTIME_CALLS = {
    (
        "src/facebook_monitor/worker/resident_main_executor.py",
        "ExecutorWorkerPool.stop",
        "force_mark_resident_target_idle",
    ),
    (
        "src/facebook_monitor/worker/resident_main_executor.py",
        "ExecutorWorkerPool._write_target_retry_after_runtime_restart",
        "force_request_target_retry_after_runtime_restart",
    ),
    (
        "src/facebook_monitor/worker/resident_shared.py",
        "force_mark_resident_target_error.operation",
        "force_mark_target_error",
    ),
    (
        "src/facebook_monitor/worker/resident_shared.py",
        "force_mark_resident_target_idle.operation",
        "force_mark_target_idle",
    ),
    (
        "src/facebook_monitor/worker/scan_finalize.py",
        "_record_skipped_scan",
        "force_apply_scan_skip_decision",
    ),
    (
        "src/facebook_monitor/worker/scan_finalize.py",
        "mark_target_idle_for_scan_commit",
        "force_mark_target_idle",
    ),
    (
        "src/facebook_monitor/worker/scan_failure_finalize.py",
        "record_guarded_scan_failure_result",
        "force_apply_scan_failure_decision",
    ),
    (
        "src/facebook_monitor/worker/scan_failure_finalize.py",
        "record_active_targets_runtime_failure_notifications",
        "force_apply_scan_failure_decision",
    ),
    (
        "src/facebook_monitor/worker/sync_resident_fallback.py",
        "_load_sync_resident_target_attempt",
        "force_mark_resident_target_error",
    ),
}
SCAN_COMMIT_HELPERS = {
    "commit_guarded_protective_skip",
    "commit_success",
    "classify_scan_commit_permission",
    "FailureScanCommitRequest",
    "record_guarded_skipped_scan",
    "record_guarded_scan_failure_decision_for_db",
    "record_guarded_scan_failure_result_for_db_async",
    "record_unguarded_skipped_scan_for_one_shot",
    "finalize_scan_items",
    "mark_target_idle_for_scan_commit",
}
FORMAL_RUNTIME_FORBIDDEN_SKIP_COMMIT_HELPERS = {
    "record_unguarded_skipped_scan_for_one_shot",
}
FORMAL_ASYNC_SCANNER_FORBIDDEN_COMMIT_HELPERS = {
    "finalize_scan_items",
    "mark_target_idle_for_scan_commit",
    "record_guarded_skipped_scan",
    "record_guarded_scan_failure_decision_for_db",
    "record_unguarded_skipped_scan_for_one_shot",
}
SUCCESS_COMMITTED_ALLOWED_FILES = {
    "src/facebook_monitor/worker/attempt_transitions.py",
    "src/facebook_monitor/worker/scan_commit_coordinator.py",
    "src/facebook_monitor/worker/scan_commit_outcomes.py",
}
ALLOWED_EXPLICIT_NONE_SCAN_COMMIT_GUARDS = {
    (
        "src/facebook_monitor/worker/resident_maintenance.py",
        "record_refresh_runtime_failure",
        "record_guarded_scan_failure_decision_for_db",
    ),
    (
        "src/facebook_monitor/worker/scan_commit_coordinator.py",
        "commit_failure_request_for_db_async",
        "record_guarded_scan_failure_result_for_db_async",
    ),
}
SQL_WRITE_PREFIXES = ("INSERT INTO", "UPDATE", "DELETE FROM", "REPLACE INTO")
ALLOWED_TARGET_RUNTIME_STATE_SQL_WRITES = {
    (
        "src/facebook_monitor/worker/resident_main.py",
        "_write_display_next_due_at_best_effort",
        (
            "UPDATE target_runtime_state "
            "SET display_next_due_at = ?, updated_at = ? "
            "WHERE target_id = ?"
        ),
    ),
}
READ_ONLY_RUNTIME_STATE_REPOSITORY_METHODS = {
    "get",
    "list_all",
    "list_by_targets",
    "list_desired_active",
}
MUTATING_RUNTIME_STATE_REPOSITORY_METHODS = {
    name
    for name, member in inspect.getmembers(
        TargetRuntimeStateRepository,
        inspect.isfunction,
    )
    if not name.startswith("_") and name not in READ_ONLY_RUNTIME_STATE_REPOSITORY_METHODS
}


class _CallContextVisitor(ast.NodeVisitor):
    """收集 call site 與 class/function context。"""

    def __init__(self) -> None:
        self.context: list[str] = []
        self.calls: list[tuple[int, str, ast.Call]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.context.append(node.name)
        self.generic_visit(node)
        self.context.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.context.append(node.name)
        self.generic_visit(node)
        self.context.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.context.append(node.name)
        self.generic_visit(node)
        self.context.pop()

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        self.calls.append((node.lineno, ".".join(self.context), node))
        self.generic_visit(node)


class _RuntimeStateRepositoryAliasVisitor(ast.NodeVisitor):
    """收集指向 runtime state repository 的本地 alias 名稱。"""

    def __init__(self) -> None:
        self.aliases: set[str] = set()

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        self._record_targets(node.targets, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if node.value is not None:
            self._record_targets((node.target,), node.value)
        self.generic_visit(node)

    def _record_targets(
        self, targets: tuple[ast.expr, ...] | list[ast.expr], value: ast.AST
    ) -> None:
        if not _is_runtime_state_repository_expr(value):
            return
        for target in targets:
            if isinstance(target, ast.Name):
                self.aliases.add(target.id)


class _SqlWriteVisitor(ast.NodeVisitor):
    """收集 raw SQL write 與所在函式 context。"""

    def __init__(self) -> None:
        self.context: list[str] = []
        self.writes: list[tuple[int, str, str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.context.append(node.name)
        self.generic_visit(node)
        self.context.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.context.append(node.name)
        self.generic_visit(node)
        self.context.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.context.append(node.name)
        self.generic_visit(node)
        self.context.pop()

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if isinstance(node.value, str):
            statement = _target_runtime_state_sql_write_statement(node.value)
            if statement:
                self.writes.append((node.lineno, ".".join(self.context), statement))
        self.generic_visit(node)


def _attribute_path(node: ast.AST) -> tuple[str, ...]:
    """把 attribute chain 轉成可比對的名稱序列。"""

    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return (*_attribute_path(node.value), node.attr)
    if isinstance(node, ast.Call):
        return _attribute_path(node.func)
    return ()


def _called_name(node: ast.Call) -> str:
    """回傳函式呼叫的最後一段名稱。"""

    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _resolved_import_from_module(path: Path, node: ast.ImportFrom) -> str:
    """解析 absolute / relative import module，供 import guard 統一比對。"""

    module = node.module or ""
    if node.level == 0:
        return module
    package_parts = path.relative_to(ROOT / "src").with_suffix("").parts[:-1]
    parent_count = max(node.level - 1, 0)
    base_parts = package_parts[: max(len(package_parts) - parent_count, 0)]
    module_parts = tuple(part for part in module.split(".") if part)
    return ".".join((*base_parts, *module_parts))


def _runtime_subservice_import_guard_files() -> tuple[Path, ...]:
    """列出不得直接 import runtime 子服務的 production modules。"""

    return tuple(
        path
        for path in PACKAGE_SOURCE_FILES
        if not (
            path.parent == APPLICATION_DIR
            and (
                path.stem.startswith("target_runtime_") or path.stem == FORMAL_RUNTIME_FACADE_MODULE
            )
        )
    )


def _is_runtime_state_repository_expr(node: ast.AST) -> bool:
    """判斷 expression 是否指向 app.repositories.runtime_states。"""

    path_parts = _attribute_path(node)
    return len(path_parts) >= 2 and path_parts[-2:] == ("repositories", "runtime_states")


def _target_runtime_state_sql_write_statement(value: str) -> str:
    """回傳 raw SQL 對 target_runtime_state 的 normalized write statement。"""

    normalized = " ".join(value.strip().split())
    upper = normalized.upper()
    if "TARGET_RUNTIME_STATE" not in upper:
        return ""
    for prefix in SQL_WRITE_PREFIXES:
        expected = f"{prefix} TARGET_RUNTIME_STATE"
        if upper.startswith(expected):
            return normalized
    return ""


def _is_runtime_state_repository_mutator_call(
    path_parts: tuple[str, ...],
    *,
    aliases: set[str],
) -> bool:
    """判斷呼叫是否直接使用 runtime repository mutator。"""

    if not path_parts or path_parts[-1] not in MUTATING_RUNTIME_STATE_REPOSITORY_METHODS:
        return False
    if len(path_parts) >= 2 and path_parts[-2] == "runtime_states":
        return True
    return len(path_parts) >= 2 and path_parts[-2] in aliases


def test_formal_runtime_paths_do_not_call_unguarded_runtime_methods() -> None:
    """正式 worker/scheduler 路徑不可直接呼叫會覆寫 owner 的 unguarded API。"""

    violations: list[str] = []
    for path in FORMAL_RUNTIME_BOUNDARY_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if node.attr not in UNGUARDED_RUNTIME_METHODS:
                continue
            violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.attr}")

    assert violations == []


def test_formal_runtime_paths_do_not_call_legacy_queue_admission_api() -> None:
    """正式 runtime path 必須讀取 QueueAdmissionResult，不可丟掉 committed 語義。"""

    violations: list[str] = []
    for path in FORMAL_RUNTIME_BOUNDARY_FILES:
        relative_path = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _CallContextVisitor()
        visitor.visit(tree)
        for lineno, context, node in visitor.calls:
            name = _called_name(node)
            if name not in LEGACY_QUEUE_ADMISSION_METHODS:
                continue
            violations.append(f"{relative_path}:{lineno}:{context}:{name}")

    assert violations == []


def test_runtime_services_do_not_define_removed_alias_methods() -> None:
    """application runtime surface 只保留 claim/guarded 正式名稱。"""

    violations: list[str] = []
    for path in APPLICATION_RUNTIME_SERVICE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name in REMOVED_APPLICATION_RUNTIME_ALIASES:
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.name}")

    assert violations == []


def test_formal_runtime_paths_do_not_call_removed_runtime_aliases() -> None:
    """正式 worker/scheduler path 不回到舊的 alias 名稱。"""

    violations: list[str] = []
    for path in FORMAL_RUNTIME_BOUNDARY_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if node.attr in REMOVED_APPLICATION_RUNTIME_ALIASES:
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.attr}")

    assert violations == []


def test_formal_runtime_paths_do_not_call_legacy_or_unguarded_skip_api() -> None:
    """正式 runtime path 不得直接呼叫 legacy / unguarded skipped scan API。"""

    violations: list[str] = []
    for path in FORMAL_RUNTIME_BOUNDARY_FILES:
        relative_path = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _CallContextVisitor()
        visitor.visit(tree)
        for lineno, context, node in visitor.calls:
            name = _called_name(node)
            if name not in FORMAL_RUNTIME_FORBIDDEN_SKIP_COMMIT_HELPERS:
                continue
            violations.append(f"{relative_path}:{lineno}:{context}:{name}")

    assert violations == []


def test_formal_runtime_paths_only_coordinator_produces_success_committed() -> None:
    """Phase 6 後 SUCCESS_COMMITTED 只能由 coordinator 產生或由 model/transition 使用。"""

    violations: list[str] = []
    for path in FORMAL_RUNTIME_BOUNDARY_FILES:
        relative_path = path.relative_to(ROOT).as_posix()
        if relative_path in SUCCESS_COMMITTED_ALLOWED_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if node.attr != "SUCCESS_COMMITTED":
                continue
            violations.append(f"{relative_path}:{node.lineno}:SUCCESS_COMMITTED")

    assert violations == []


def test_formal_async_scanners_do_not_finalize_visible_scan_state() -> None:
    """formal async scanner 只能回傳 commit-ready result，不直接寫 visible scan state。"""

    violations: list[str] = []
    for path in FORMAL_ASYNC_SCANNER_FILES:
        relative_path = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _CallContextVisitor()
        visitor.visit(tree)
        for lineno, context, node in visitor.calls:
            function_name = context.split(".")[-1]
            if function_name not in FORMAL_ASYNC_SCANNER_FUNCTIONS:
                continue
            name = _called_name(node)
            if name not in FORMAL_ASYNC_SCANNER_FORBIDDEN_COMMIT_HELPERS:
                continue
            violations.append(f"{relative_path}:{lineno}:{context}:{name}")

    assert violations == []


def test_non_runtime_modules_do_not_import_runtime_subservices() -> None:
    """非 runtime 子服務 module 不得繞過 TargetRuntimeService facade。"""

    violations: list[str] = []
    for path in _runtime_subservice_import_guard_files():
        relative_path = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = _resolved_import_from_module(path, node)
                if module == "facebook_monitor.application":
                    for alias in node.names:
                        if alias.name.startswith("target_runtime_"):
                            violations.append(
                                f"{relative_path}:{node.lineno}:from {module} import {alias.name}"
                            )
                    continue
                if module in FORBIDDEN_FORMAL_RUNTIME_SUBSERVICE_FULL_MODULES:
                    for alias in node.names:
                        violations.append(
                            f"{relative_path}:{node.lineno}:from {module} import {alias.name}"
                        )
                    continue
                if module == "facebook_monitor.application.target_runtime_service":
                    for alias in node.names:
                        if alias.name in PUBLIC_RUNTIME_FACADE_SYMBOLS:
                            continue
                        violations.append(
                            f"{relative_path}:{node.lineno}:from {module} import {alias.name}"
                        )
                    continue
                if module.startswith("facebook_monitor.application.target_runtime_"):
                    for alias in node.names:
                        violations.append(
                            f"{relative_path}:{node.lineno}:from {module} import {alias.name}"
                        )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not alias.name.startswith("facebook_monitor.application.target_runtime_"):
                        continue
                    violations.append(f"{relative_path}:{node.lineno}:import {alias.name}")

    assert violations == []


def test_formal_runtime_paths_only_use_allowlisted_force_calls() -> None:
    """force runtime API 只能出現在明確命名的 fallback/recovery 呼叫點。"""

    violations: list[str] = []
    for path in FORMAL_RUNTIME_BOUNDARY_FILES:
        relative_path = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _CallContextVisitor()
        visitor.visit(tree)
        for lineno, context, node in visitor.calls:
            name = _called_name(node)
            if not name.startswith("force_"):
                continue
            if (relative_path, context, name) in ALLOWED_FORCE_RUNTIME_CALLS:
                continue
            violations.append(f"{relative_path}:{lineno}:{context}:{name}")

    assert violations == []


def test_formal_runtime_paths_pass_explicit_guard_to_scan_commit_helpers() -> None:
    """正式路徑呼叫 scan commit helper 時必須明確傳入非 None guard。"""

    violations: list[str] = []
    for path in FORMAL_RUNTIME_BOUNDARY_FILES:
        relative_path = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _CallContextVisitor()
        visitor.visit(tree)
        for lineno, context, node in visitor.calls:
            name = _called_name(node)
            if name not in SCAN_COMMIT_HELPERS:
                continue
            guard_keyword = next(
                (keyword for keyword in node.keywords if keyword.arg == "commit_guard"),
                None,
            )
            if guard_keyword is None:
                violations.append(f"{relative_path}:{lineno}:{context}:{name}:missing commit_guard")
                continue
            for keyword in node.keywords:
                if keyword is not guard_keyword:
                    continue
                if _is_unguarded_commit_guard_value(keyword.value):
                    if (
                        relative_path,
                        context,
                        name,
                    ) in ALLOWED_EXPLICIT_NONE_SCAN_COMMIT_GUARDS:
                        continue
                    violations.append(
                        f"{relative_path}:{lineno}:{context}:{name}:unguarded commit_guard"
                    )

    assert violations == []


def _is_unguarded_commit_guard_value(node: ast.AST) -> bool:
    """判斷 commit_guard 參數是否明確指定為 unguarded fallback。"""

    if isinstance(node, ast.Constant) and node.value is None:
        return True
    if isinstance(node, ast.Name) and node.id == "UNGUARDED_SCAN_COMMIT":
        return True
    if isinstance(node, ast.Attribute) and node.attr == "UNGUARDED_SCAN_COMMIT":
        return True
    if isinstance(node, ast.Attribute) and node.attr == "commit_guard":
        return True
    return False


def test_formal_runtime_paths_do_not_mutate_runtime_state_repository_directly() -> None:
    """runtime state mutating writes 必須集中在 service，避免繞過 owner guard。"""

    violations: list[str] = []
    for path in FORMAL_RUNTIME_BOUNDARY_FILES:
        relative_path = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        alias_visitor = _RuntimeStateRepositoryAliasVisitor()
        alias_visitor.visit(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            path_parts = _attribute_path(node.func)
            if _is_runtime_state_repository_mutator_call(
                path_parts,
                aliases=alias_visitor.aliases,
            ):
                violations.append(f"{relative_path}:{node.lineno}:{'.'.join(path_parts)}")

    assert violations == []


def test_formal_runtime_paths_do_not_write_runtime_state_raw_sql_without_allowlist() -> None:
    """raw SQL 寫 runtime state 必須是明確 allowlisted 的 UI-only 例外。"""

    violations: list[str] = []
    for path in FORMAL_RUNTIME_BOUNDARY_FILES:
        relative_path = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _SqlWriteVisitor()
        visitor.visit(tree)
        for lineno, context, statement in visitor.writes:
            if (relative_path, context, statement) in ALLOWED_TARGET_RUNTIME_STATE_SQL_WRITES:
                continue
            violations.append(f"{relative_path}:{lineno}:{context}:{statement}")

    assert violations == []
