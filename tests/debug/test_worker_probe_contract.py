"""Debug worker probe contract tests."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


WORKER_PROBE = Path("scripts/debug/worker_probe.py")


def test_worker_probe_is_db_free_extractor_probe() -> None:
    """worker_probe 不應重新長成第二套 scan / notification pipeline。"""

    source = WORKER_PROBE.read_text(encoding="utf-8")

    assert "DB-free headless Facebook extractor probe" in source
    assert "send_ntfy_notification" not in source
    assert "worker_probe_seen_keys.json" not in source
    assert "evaluate_keyword_rules" not in source
    assert "run_duration_mode" not in source


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
