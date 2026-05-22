"""complexity report admin 工具測試。"""

from __future__ import annotations

from pathlib import Path

from scripts.admin.complexity_report import collect_findings
from scripts.admin.complexity_report import render_findings


def test_complexity_report_collects_long_or_branchy_functions(tmp_path: Path) -> None:
    """report-only 工具應能找出超過門檻的函式。"""

    source = tmp_path / "sample.py"
    source.write_text(
        "\n".join(
            [
                "def small():",
                "    return 1",
                "",
                "def branchy(value):",
                "    if value:",
                "        return 1",
                "    if value == 2:",
                "        return 2",
                "    return 3",
            ]
        ),
        encoding="utf-8",
    )

    findings = collect_findings([source], max_complexity=2, max_lines=80)
    report = render_findings(findings)

    assert [finding.name for finding in findings] == ["branchy"]
    assert "sample.py:4" in report
    assert "branchy" in report


def test_complexity_report_renders_empty_report() -> None:
    """沒有 findings 時輸出穩定摘要。"""

    assert render_findings([]) == (
        "No functions exceeded the configured complexity or length thresholds."
    )
