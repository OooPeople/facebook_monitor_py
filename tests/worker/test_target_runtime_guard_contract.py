"""Worker/scheduler runtime service guard contract tests。"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FORMAL_RUNTIME_BOUNDARY_FILES = (
    ROOT / "src/facebook_monitor/scheduler/one_shot_loop.py",
    ROOT / "src/facebook_monitor/worker/resident_main.py",
    ROOT / "src/facebook_monitor/worker/resident_main_executor.py",
    ROOT / "src/facebook_monitor/worker/resident_main_executor_attempt.py",
    ROOT / "src/facebook_monitor/worker/resident_maintenance.py",
    ROOT / "src/facebook_monitor/worker/resident_shared.py",
    ROOT / "src/facebook_monitor/worker/scan_finalize.py",
    ROOT / "src/facebook_monitor/worker/scan_failure_finalize.py",
    ROOT / "src/facebook_monitor/worker/sync_resident_fallback.py",
)
APPLICATION_RUNTIME_SERVICE_FILES = (
    ROOT / "src/facebook_monitor/application/services.py",
    ROOT / "src/facebook_monitor/application/target_runtime_service.py",
)
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
        "record_skipped_scan",
        "force_apply_scan_skip_decision",
    ),
    (
        "src/facebook_monitor/worker/scan_finalize.py",
        "mark_target_idle_for_scan_commit",
        "force_mark_target_idle",
    ),
    (
        "src/facebook_monitor/worker/scan_failure_finalize.py",
        "record_guarded_scan_failure",
        "force_apply_scan_failure_decision",
    ),
    (
        "src/facebook_monitor/worker/scan_failure_finalize.py",
        "record_active_targets_runtime_failure_notifications",
        "force_apply_scan_failure_decision",
    ),
    (
        "src/facebook_monitor/worker/sync_resident_fallback.py",
        "run_sync_resident_fallback_cycle",
        "force_mark_resident_target_error",
    ),
}
SCAN_COMMIT_HELPERS = {
    "record_guarded_scan_failure_for_db",
    "record_guarded_scan_failure_for_db_async",
    "record_skipped_scan",
    "finalize_scan_items",
    "mark_target_idle_for_scan_commit",
}
ALLOWED_EXPLICIT_NONE_SCAN_COMMIT_GUARDS = {
    (
        "src/facebook_monitor/worker/resident_maintenance.py",
        "record_refresh_runtime_failure",
        "record_guarded_scan_failure_for_db",
    ),
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
                violations.append(
                    f"{relative_path}:{lineno}:{context}:{name}:missing commit_guard"
                )
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


def test_formal_runtime_paths_do_not_write_runtime_state_directly() -> None:
    """runtime state 寫回必須集中在 service，避免熱路徑繞過 owner guard。"""

    violations: list[str] = []
    for path in FORMAL_RUNTIME_BOUNDARY_FILES:
        relative_path = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            path_parts = _attribute_path(node.func)
            if len(path_parts) >= 2 and path_parts[-2:] == ("runtime_states", "save"):
                violations.append(f"{relative_path}:{node.lineno}:runtime_states.save")

    assert violations == []
