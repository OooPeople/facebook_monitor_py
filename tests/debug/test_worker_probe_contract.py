"""Debug worker probe contract tests."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
WORKER_PROBE = ROOT / "scripts/debug/worker_probe.py"


def _production_call_sites(symbol: str) -> tuple[str, ...]:
    """列出 production source 對指定 extractor symbol 的直接呼叫位置。"""

    sites: list[str] = []
    for source_root in (ROOT / "src/facebook_monitor", ROOT / "scripts"):
        for path in source_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                called_name = ""
                if isinstance(node.func, ast.Name):
                    called_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    called_name = node.func.attr
                if called_name == symbol:
                    sites.append(path.relative_to(ROOT).as_posix())
    return tuple(sorted(sites))


def test_worker_probe_is_db_free_extractor_probe() -> None:
    """worker_probe 不應重新長成第二套 scan / notification pipeline。"""

    source = WORKER_PROBE.read_text(encoding="utf-8")

    assert "DB-free headless Facebook extractor probe" in source
    assert "send_ntfy_notification" not in source
    assert "worker_probe_seen_keys.json" not in source
    assert "evaluate_keyword_rules" not in source
    assert "run_duration_mode" not in source
    assert "collect_items_with_diagnostics" in source
    assert "collect_comment_items" not in source


def test_comments_collectors_have_only_formal_async_production_caller() -> None:
    """Comments load-more 只由 formal async pipeline 持有，不存在 sync production caller。"""

    assert _production_call_sites("collect_comment_items_with_diagnostics") == ()
    assert _production_call_sites("collect_comment_items_with_diagnostics_async") == (
        "src/facebook_monitor/worker/comments_pipeline.py",
    )


def test_worker_probe_help_exposes_only_extractor_probe_options() -> None:
    """CLI help 只暴露 extractor diagnostics 所需的最小選項。"""

    result = subprocess.run(
        [sys.executable, str(WORKER_PROBE), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    help_text = result.stdout

    assert "DB-free headless Facebook extractor probe" in help_text
    assert "--max-items" in help_text
    assert "--scroll-rounds" in help_text
    assert "--scroll-wait-ms" in help_text
    assert "--diagnostics" in help_text
    assert "--ntfy-topic" not in help_text
    assert "--notify-test" not in help_text
    assert "--notify-on-new" not in help_text
    assert "--reset-seen" not in help_text
    assert "--duration-minutes" not in help_text
