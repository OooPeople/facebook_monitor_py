"""complexity / maintainability ranking admin 工具測試。"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.admin.complexity_report import collect_report
from scripts.admin.complexity_report import load_annotations
from scripts.admin.complexity_report import load_annotations_with_warnings
from scripts.admin.complexity_report import main
from scripts.admin.complexity_report import render_report
from scripts.admin.complexity_report import ReviewAnnotation
from scripts.admin.complexity_report import known_large_functions
from scripts.admin.complexity_report import top_functions_by_ccn


def test_complexity_report_collects_all_functions_and_ranks(tmp_path: Path) -> None:
    """report-only 工具應收集全部函式，不用門檻篩掉小函式。"""

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

    report = collect_report([source], include_extensions=(".py",), exclude_globs=())
    ranked = top_functions_by_ccn(report.functions, top=5)
    rendered = render_report(report, top=5)

    assert [metric.name for metric in report.functions] == ["small", "branchy"]
    assert [metric.name for metric in ranked] == ["branchy", "small"]
    assert ranked[0].ccn > ranked[1].ccn
    assert "metric_source: lizard" in rendered
    assert "ranking only" in rendered
    assert "branchy" in rendered
    assert "small" in rendered


def test_complexity_report_collects_javascript_functions(tmp_path: Path) -> None:
    """Lizard wrapper 應讓 first-party JS function 也進入函式排行。"""

    source = tmp_path / "dashboard.js"
    source.write_text(
        "function setup(value) { if (value) { return true; } return false; }\n",
        encoding="utf-8",
    )

    report = collect_report([source], include_extensions=(".js",), exclude_globs=())

    assert report.source_files[0].language == "javascript"
    assert [metric.name for metric in report.functions] == ["setup"]
    assert report.functions[0].ccn == 2


def test_complexity_report_known_large_is_separate_from_primary_ranking(
    tmp_path: Path,
) -> None:
    """known-large 標註不藏資料，但預設不佔住主排行。"""

    source = tmp_path / "sample.py"
    source.write_text(
        "\n".join(
            [
                "def huge_known(value):",
                "    if value:",
                "        return 1",
                "    if value == 2:",
                "        return 2",
                "    return 3",
                "",
                "def current_hotspot(value):",
                "    if value:",
                "        return 1",
                "    return 0",
            ]
        ),
        encoding="utf-8",
    )
    annotation_path = tmp_path / "annotations.json"
    annotation_path.write_text(
        json.dumps(
            {
                "known_large": [
                    {
                        "path_glob": source.as_posix(),
                        "symbol": "huge_known",
                        "category": "fixture",
                        "reason": "reviewed fixture",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    annotations = load_annotations(annotation_path)

    report = collect_report(
        [source],
        include_extensions=(".py",),
        exclude_globs=(),
        annotations=annotations,
    )
    ranked = top_functions_by_ccn(
        report.functions,
        top=5,
        annotations=report.annotations,
    )
    payload = json.loads(render_report(report, top=5, format_name="json"))
    rendered = render_report(report, top=5)

    assert [metric.name for metric in ranked] == ["current_hotspot"]
    assert payload["known_large_functions"][0]["name"] == "huge_known"
    assert payload["known_large_functions"][0]["ccn"] > 0
    assert "Known-large annotations" in rendered
    assert "huge_known" in rendered
    assert "reviewed fixture" in rendered


def test_complexity_report_excludes_vendor_files(tmp_path: Path) -> None:
    """vendor / third-party source 預設不污染 first-party ranking。"""

    vendor_file = (
        tmp_path
        / "src"
        / "facebook_monitor"
        / "webapp"
        / "static"
        / "vendor"
        / "sortable.esm.js"
    )
    vendor_file.parent.mkdir(parents=True)
    vendor_file.write_text("function vendorThing() { return true; }\n", encoding="utf-8")

    report = collect_report(
        [tmp_path / "src"],
        include_extensions=(".js",),
        exclude_globs=("**/static/vendor/**",),
    )

    assert report.excluded_file_count == 1
    assert report.source_files == ()
    assert report.functions == ()


def test_complexity_report_json_output_is_stable(tmp_path: Path) -> None:
    """JSON 輸出提供後續人工比較用的穩定 schema。"""

    source = tmp_path / "sample.py"
    source.write_text("def small():\n    return 1\n", encoding="utf-8")

    report = collect_report([source], include_extensions=(".py",), exclude_globs=())
    payload = json.loads(render_report(report, top=3, format_name="json"))

    assert payload["schema_version"] == 2
    assert payload["summary"]["top"] == 3
    assert payload["summary"]["metric_source"] == "lizard"
    assert payload["summary"]["note"] == "ranking_only_review_hint"
    assert payload["top_functions_by_ccn"][0]["name"] == "small"
    assert "token_count" in payload["top_functions_by_ccn"][0]
    assert "estimated_code_lines" in payload["top_files_by_lines"][0]
    assert "code_lines" not in payload["top_files_by_lines"][0]


def test_complexity_report_main_always_returns_zero_with_ranked_rows(
    tmp_path: Path,
    capsys,
) -> None:
    """這支工具是人工報告，不因排行內容改變 exit code。"""

    source = tmp_path / "sample.py"
    source.write_text("def branchy(value):\n    if value:\n        return 1\n", encoding="utf-8")

    assert main([str(source), "--top", "1", "--no-annotations"]) == 0
    assert "branchy" in capsys.readouterr().out


def test_complexity_report_normalizes_absolute_repo_paths_for_annotations() -> None:
    """從絕對路徑掃描 repo 檔案時，relative annotation 仍應命中。"""

    source = Path("scripts/admin/complexity_report.py").resolve()
    annotations = (
        ReviewAnnotation(
            status="known_large",
            path_glob="scripts/admin/complexity_report.py",
            symbol="main",
            category="fixture",
            reason="absolute path should still match relative annotation",
        ),
    )

    report = collect_report(
        [source],
        include_extensions=(".py",),
        exclude_globs=(),
        annotations=annotations,
    )
    rows = known_large_functions(report.functions, report.annotations, top=1000)

    assert any(metric.name == "main" for metric, _ in rows)
    assert {metric.path.as_posix() for metric in report.functions} == {
        "scripts/admin/complexity_report.py"
    }


def test_complexity_report_annotation_warnings_do_not_drop_valid_items(
    tmp_path: Path,
) -> None:
    """annotation typo 應出現在 warning，但合法項目仍可使用。"""

    annotation_path = tmp_path / "annotations.json"
    annotation_path.write_text(
        json.dumps(
            {
                "schema_version": "bad",
                "known_large": {"path_glob": "src/example.py"},
                "watchlist": [
                    {
                        "path_glob": "src/example.py",
                        "category": "fixture",
                        "reason": "valid item",
                    }
                ],
                "annotations": [
                    {"status": "typo", "path_glob": "src/example.py"},
                    {"status": "known_large"},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = load_annotations_with_warnings(annotation_path)

    assert [annotation.status for annotation in result.annotations] == ["watchlist"]
    assert len(result.warnings) == 4
    assert any("invalid schema_version" in warning for warning in result.warnings)
    assert any(
        "known_large: annotation section must be a list" in warning
        for warning in result.warnings
    )
    assert any("unsupported status='typo'" in warning for warning in result.warnings)
    assert any("missing path_glob" in warning for warning in result.warnings)
