"""complexity / maintainability ranking admin 工具測試。"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.admin.complexity_report import main
from scripts.admin.complexity_report_annotations import load_annotations
from scripts.admin.complexity_report_annotations import load_annotations_with_warnings
from scripts.admin.complexity_report_collect import collect_report
from scripts.admin.complexity_report_models import ReviewAnnotation
from scripts.admin.complexity_report_rankings import known_large_classes
from scripts.admin.complexity_report_rankings import known_large_files
from scripts.admin.complexity_report_rankings import known_large_functions
from scripts.admin.complexity_report_rankings import top_files_by_lines
from scripts.admin.complexity_report_rankings import top_functions_by_ccn
from scripts.admin.complexity_report_rankings import watchlist_classes
from scripts.admin.complexity_report_rankings import watchlist_files
from scripts.admin.complexity_report_rankings import watchlist_functions
from scripts.admin.complexity_report_renderers import render_report


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
                        "must_not_add": ["DB writes"],
                        "split_trigger": "new policy family",
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
    rendered_markdown = render_report(report, top=5, format_name="markdown")

    assert [metric.name for metric in ranked] == ["current_hotspot"]
    assert payload["known_large_functions"][0]["name"] == "huge_known"
    assert payload["known_large_functions"][0]["ccn"] > 0
    assert "Known-large annotations" in rendered
    assert "huge_known" in rendered
    assert "reviewed fixture" in rendered
    assert "must_not_add=DB writes; split_trigger=new policy family" in rendered
    assert "| Governance |" in rendered_markdown
    assert "must_not_add=DB writes; split_trigger=new policy family" in rendered_markdown


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

    assert payload["schema_version"] == 3
    assert payload["summary"]["top"] == 3
    assert payload["summary"]["metric_source"] == "lizard"
    assert payload["summary"]["note"] == "ranking_only_review_hint"
    assert payload["summary"]["class_count"] == 0
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
            symbol_kind="function",
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


def test_complexity_report_class_symbol_annotation_matches_ast_owner(
    tmp_path: Path,
) -> None:
    """class-level annotation 應靠 AST owner range 命中，不依賴 Lizard long_name。"""

    source = tmp_path / "dashboard_models.py"
    source.write_text(
        "\n".join(
            [
                "class TargetRow:",
                "    @property",
                "    def status_label(self):",
                "        return 'running'",
                "",
                "    def branchy(self, value):",
                "        if value:",
                "            return 'yes'",
                "        return 'no'",
                "",
                "class OtherRow:",
                "    def ignored(self):",
                "        return None",
            ]
        ),
        encoding="utf-8",
    )
    annotations = (
        ReviewAnnotation(
            status="watchlist",
            path_glob=source.resolve().as_posix(),
            symbol="TargetRow",
            symbol_kind="class",
            category="fixture_class",
            reason="class row should be reported once",
        ),
    )

    report = collect_report(
        [source],
        include_extensions=(".py",),
        exclude_globs=(),
        annotations=annotations,
    )
    rows = watchlist_classes(report.classes, report.annotations, top=10)
    payload = json.loads(render_report(report, top=10, format_name="json"))
    rendered_markdown = render_report(report, top=10, format_name="markdown")

    assert report.annotation_warnings == ()
    assert [metric.display_name for metric, _ in rows] == ["TargetRow"]
    assert rows[0][0].method_count == 2
    assert any(
        item["name"] == "TargetRow"
        for item in payload["watchlist_classes"]
    )
    assert "### Classes" in rendered_markdown
    assert "TargetRow" in rendered_markdown


def test_complexity_report_class_known_large_hides_member_functions(
    tmp_path: Path,
) -> None:
    """class-level known-large 應預設隱藏 member functions，但保留 class 摘要。"""

    source = tmp_path / "sample.py"
    source.write_text(
        "\n".join(
            [
                "class LegacyPresenter:",
                "    def noisy(self, value):",
                "        if value:",
                "            return 1",
                "        return 0",
                "",
                "def current_hotspot(value):",
                "    if value:",
                "        return 1",
                "    return 0",
            ]
        ),
        encoding="utf-8",
    )
    annotations = (
        ReviewAnnotation(
            status="known_large",
            path_glob=source.resolve().as_posix(),
            symbol="LegacyPresenter",
            symbol_kind="class",
            category="fixture_class",
            reason="hide class methods from primary ranking",
        ),
    )

    report = collect_report(
        [source],
        include_extensions=(".py",),
        exclude_globs=(),
        annotations=annotations,
    )
    ranked = top_functions_by_ccn(
        report.functions,
        top=10,
        annotations=report.annotations,
    )
    ranked_with_known = top_functions_by_ccn(
        report.functions,
        top=10,
        annotations=report.annotations,
        include_known_large=True,
    )
    known_rows = known_large_classes(report.classes, report.annotations, top=10)

    assert [metric.name for metric in ranked] == ["current_hotspot"]
    assert {metric.name for metric in ranked_with_known} == {
        "current_hotspot",
        "noisy",
    }
    assert [metric.display_name for metric, _ in known_rows] == ["LegacyPresenter"]


def test_complexity_report_watchlist_sections_keep_symbol_kinds_separate(
    tmp_path: Path,
) -> None:
    """file/function/class watchlist rows 不應在 report sections 互相展開。"""

    source = tmp_path / "sample.py"
    source.write_text(
        "\n".join(
            [
                "class WatchedClass:",
                "    def method(self, value):",
                "        if value:",
                "            return 1",
                "        return 0",
                "",
                "def watched_function(value):",
                "    if value:",
                "        return 1",
                "    return 0",
                "",
                "def file_level_only(value):",
                "    if value:",
                "        return 1",
                "    return 0",
            ]
        ),
        encoding="utf-8",
    )
    annotations = (
        ReviewAnnotation(
            status="watchlist",
            path_glob=source.resolve().as_posix(),
            symbol="",
            symbol_kind="file",
            category="fixture_file",
            reason="file section only",
        ),
        ReviewAnnotation(
            status="watchlist",
            path_glob=source.resolve().as_posix(),
            symbol="watched_function",
            symbol_kind="function",
            category="fixture_function",
            reason="function section only",
        ),
        ReviewAnnotation(
            status="watchlist",
            path_glob=source.resolve().as_posix(),
            symbol="WatchedClass",
            symbol_kind="class",
            category="fixture_class",
            reason="class section only",
        ),
    )

    report = collect_report(
        [source],
        include_extensions=(".py",),
        exclude_globs=(),
        annotations=annotations,
    )
    function_rows = watchlist_functions(report.functions, report.annotations, top=10)
    class_rows = watchlist_classes(report.classes, report.annotations, top=10)
    file_rows = watchlist_files(report.source_files, report.annotations, top=10)
    payload = json.loads(render_report(report, top=10, format_name="json"))

    assert [metric.name for metric, _ in function_rows] == ["watched_function"]
    assert [metric.display_name for metric, _ in class_rows] == ["WatchedClass"]
    assert [metric.display_path for metric, _ in file_rows] == [
        source.resolve().as_posix()
    ]
    assert [item["name"] for item in payload["watchlist_functions"]] == [
        "watched_function"
    ]
    assert [item["name"] for item in payload["watchlist_classes"]] == [
        "WatchedClass"
    ]
    assert [item["path"] for item in payload["watchlist_files"]] == [
        source.resolve().as_posix()
    ]


def test_complexity_report_file_known_large_suppresses_primary_without_function_noise(
    tmp_path: Path,
) -> None:
    """file-level known-large 可壓制主排行，但不展開成 known_large_functions。"""

    known_source = tmp_path / "known.py"
    known_source.write_text(
        "\n".join(
            [
                "def known_file_hotspot(value):",
                "    if value == 1:",
                "        return 1",
                "    if value == 2:",
                "        return 2",
                "    return 0",
            ]
        ),
        encoding="utf-8",
    )
    current_source = tmp_path / "current.py"
    current_source.write_text(
        "\n".join(
            [
                "def current_hotspot(value):",
                "    if value:",
                "        return 1",
                "    return 0",
            ]
        ),
        encoding="utf-8",
    )
    annotations = (
        ReviewAnnotation(
            status="known_large",
            path_glob=known_source.resolve().as_posix(),
            symbol="",
            symbol_kind="file",
            category="fixture_file",
            reason="hide whole reviewed file from primary ranking",
        ),
    )

    report = collect_report(
        [tmp_path],
        include_extensions=(".py",),
        exclude_globs=(),
        annotations=annotations,
    )
    ranked_functions = top_functions_by_ccn(
        report.functions,
        top=10,
        annotations=report.annotations,
    )
    ranked_files = top_files_by_lines(
        report.source_files,
        top=10,
        annotations=report.annotations,
    )
    ranked_functions_with_known = top_functions_by_ccn(
        report.functions,
        top=10,
        annotations=report.annotations,
        include_known_large=True,
    )
    ranked_files_with_known = top_files_by_lines(
        report.source_files,
        top=10,
        annotations=report.annotations,
        include_known_large=True,
    )
    known_function_rows = known_large_functions(
        report.functions,
        report.annotations,
        top=10,
    )
    known_file_rows = known_large_files(report.source_files, report.annotations, top=10)
    payload = json.loads(render_report(report, top=10, format_name="json"))

    assert {metric.name for metric in ranked_functions} == {"current_hotspot"}
    assert {metric.display_path for metric in ranked_files} == {
        current_source.resolve().as_posix()
    }
    assert {metric.name for metric in ranked_functions_with_known} == {
        "current_hotspot",
        "known_file_hotspot",
    }
    assert {metric.display_path for metric in ranked_files_with_known} == {
        current_source.resolve().as_posix(),
        known_source.resolve().as_posix(),
    }
    assert known_function_rows == []
    assert [metric.display_path for metric, _ in known_file_rows] == [
        known_source.resolve().as_posix()
    ]
    assert payload["known_large_functions"] == []
    assert [item["path"] for item in payload["known_large_files"]] == [
        known_source.resolve().as_posix()
    ]


def test_complexity_report_file_annotation_overlap_is_status_aware(
    tmp_path: Path,
) -> None:
    """broad watchlist 在前時，specific known-large 仍應壓制主排行。"""

    known_source = tmp_path / "known.py"
    known_source.write_text(
        "\n".join(
            [
                "def known_file_hotspot(value):",
                "    if value == 1:",
                "        return 1",
                "    if value == 2:",
                "        return 2",
                "    return 0",
            ]
        ),
        encoding="utf-8",
    )
    current_source = tmp_path / "current.py"
    current_source.write_text(
        "\n".join(
            [
                "def current_hotspot(value):",
                "    if value:",
                "        return 1",
                "    return 0",
            ]
        ),
        encoding="utf-8",
    )
    annotations = (
        ReviewAnnotation(
            status="watchlist",
            path_glob=(tmp_path / "*.py").resolve().as_posix(),
            symbol="",
            symbol_kind="file",
            category="fixture_broad_watchlist",
            reason="broad file watchlist comes first",
        ),
        ReviewAnnotation(
            status="known_large",
            path_glob=known_source.resolve().as_posix(),
            symbol="",
            symbol_kind="file",
            category="fixture_specific_known",
            reason="specific known-large still suppresses",
        ),
    )

    report = collect_report(
        [tmp_path],
        include_extensions=(".py",),
        exclude_globs=(),
        annotations=annotations,
    )
    ranked_functions = top_functions_by_ccn(
        report.functions,
        top=10,
        annotations=report.annotations,
    )
    ranked_files = top_files_by_lines(
        report.source_files,
        top=10,
        annotations=report.annotations,
    )
    known_file_rows = known_large_files(report.source_files, report.annotations, top=10)
    watchlist_file_rows = watchlist_files(report.source_files, report.annotations, top=10)

    assert {metric.name for metric in ranked_functions} == {"current_hotspot"}
    assert {metric.display_path for metric in ranked_files} == {
        current_source.resolve().as_posix()
    }
    assert [metric.display_path for metric, _ in known_file_rows] == [
        known_source.resolve().as_posix()
    ]
    assert {metric.display_path for metric, _ in watchlist_file_rows} == {
        current_source.resolve().as_posix(),
        known_source.resolve().as_posix(),
    }


def test_complexity_report_symbol_warning_only_for_in_scope_scan(
    tmp_path: Path,
) -> None:
    """只有 path_glob 命中本次掃描範圍時，symbol no-op 才應產生 warning。"""

    source = tmp_path / "sample.py"
    source.write_text(
        "class PresentClass:\n    def current(self):\n        return 1\n",
        encoding="utf-8",
    )
    annotations = (
        ReviewAnnotation(
            status="watchlist",
            path_glob=source.resolve().as_posix(),
            symbol="MissingClass",
            symbol_kind="class",
            category="fixture_class",
            reason="missing class should warn",
        ),
        ReviewAnnotation(
            status="watchlist",
            path_glob=(tmp_path / "outside.py").resolve().as_posix(),
            symbol="AlsoMissing",
            symbol_kind="class",
            category="fixture_class",
            reason="out-of-scope class should not warn",
        ),
    )

    report = collect_report(
        [source],
        include_extensions=(".py",),
        exclude_globs=(),
        annotations=annotations,
    )

    assert report.annotation_warnings == (
        (
            "watchlist: class symbol 'MissingClass' did not match scanned path "
            f"'{source.resolve().as_posix()}'"
        ),
    )


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


def test_complexity_report_rejects_invalid_symbol_kind(
    tmp_path: Path,
) -> None:
    """symbol_kind 錯誤或缺少必要 symbol 時應明確 warning。"""

    annotation_path = tmp_path / "annotations.json"
    annotation_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "watchlist": [
                    {
                        "path_glob": "src/example.py",
                        "symbol_kind": "class",
                        "category": "fixture",
                        "reason": "missing symbol",
                    },
                    {
                        "path_glob": "src/example.py",
                        "symbol": "Example",
                        "symbol_kind": "file",
                        "category": "fixture",
                        "reason": "file annotation with symbol",
                    },
                    {
                        "path_glob": "src/example.py",
                        "symbol": "Example",
                        "symbol_kind": "module",
                        "category": "fixture",
                        "reason": "invalid kind",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = load_annotations_with_warnings(annotation_path)

    assert result.annotations == ()
    assert len(result.warnings) == 3
    assert any("class annotation requires symbol" in warning for warning in result.warnings)
    assert any("file annotation must not define symbol" in warning for warning in result.warnings)
    assert any("unsupported symbol_kind='module'" in warning for warning in result.warnings)


def test_complexity_report_loads_annotation_governance_metadata(
    tmp_path: Path,
) -> None:
    """annotation 裡的人工作業邊界需被 report model 承載。"""

    annotation_path = tmp_path / "annotations.json"
    annotation_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "watchlist": [
                    {
                        "path_glob": "src/example.py",
                        "category": "fixture",
                        "reason": "valid item",
                        "must_not_add": ["DB writes", "", 123],
                        "split_trigger": "new policy family",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = load_annotations_with_warnings(annotation_path)
    annotation = result.annotations[0]

    assert result.warnings == ()
    assert annotation.symbol_kind == "file"
    assert annotation.must_not_add == ("DB writes",)
    assert annotation.split_trigger == "new policy family"
    assert annotation.to_json()["symbol_kind"] == "file"
    assert annotation.to_json()["must_not_add"] == ["DB writes"]
    assert annotation.to_json()["split_trigger"] == "new policy family"


def test_default_maintainability_annotations_load_without_warnings() -> None:
    """追蹤中的 maintainability annotations 不應有 JSON/schema 警告。"""

    result = load_annotations_with_warnings(Path("docs/maintainability_annotations.json"))

    assert result.warnings == ()
    assert result.annotations


def test_default_maintainability_annotations_keep_review_signal() -> None:
    """預設 annotations 應維持 known-large / watchlist 的人工治理訊號。"""

    result = load_annotations_with_warnings(Path("docs/maintainability_annotations.json"))
    known_large = [
        annotation for annotation in result.annotations if annotation.status == "known_large"
    ]
    watchlist = [
        annotation for annotation in result.annotations if annotation.status == "watchlist"
    ]
    watchlist_paths = {annotation.path_glob for annotation in watchlist}
    all_paths = {annotation.path_glob for annotation in result.annotations}
    class_symbols = {
        annotation.symbol
        for annotation in watchlist
        if annotation.symbol_kind == "class"
    }
    function_symbols = {
        annotation.symbol
        for annotation in watchlist
        if annotation.symbol_kind == "function"
    }

    assert len(known_large) == 8
    assert len(watchlist) == 25
    assert "scripts/admin/complexity_report*.py" in watchlist_paths
    assert "src/facebook_monitor/facebook/feed_extractor.py" not in {
        annotation.path_glob for annotation in known_large
    }
    assert "src/facebook_monitor/facebook/feed_extractor.py" in watchlist_paths
    assert "src/facebook_monitor/updates/apply.py" in watchlist_paths
    assert "src/facebook_monitor/worker/scan_failure_finalize.py" in watchlist_paths
    assert (
        "src/facebook_monitor/persistence/repositories/notification_outbox.py"
        in watchlist_paths
    )
    assert "src/facebook_monitor/webapp/scan_diagnostics_*.py" in watchlist_paths
    assert "src/facebook_monitor/worker/resident_cover_image_*.py" in watchlist_paths
    assert "src/facebook_monitor/worker/resident_maintenance_*.py" in watchlist_paths
    assert {"TargetRow", "SidebarGroupSection"} <= class_symbols
    assert {
        "prepare_guarded_skipped_scan_commit",
        "record_prepared_guarded_skipped_scan",
    } <= function_symbols
    assert "src/facebook_monitor/webapp/templates/_target_card.html" not in all_paths
    assert "src/facebook_monitor/webapp/templates/_target_sidebar.html" not in all_paths
    assert "src/facebook_monitor/webapp/static/dashboard/sidebar_layout.js" not in all_paths


def test_default_dashboard_model_class_annotations_match_report() -> None:
    """正式 dashboard model class annotations 應實際出現在 watchlist class rows。"""

    result = load_annotations_with_warnings(Path("docs/maintainability_annotations.json"))
    report = collect_report(
        [Path("src/facebook_monitor/webapp/dashboard_models.py")],
        include_extensions=(".py",),
        exclude_globs=(),
        annotations=result.annotations,
        annotation_warnings=result.warnings,
    )
    rows = watchlist_classes(report.classes, report.annotations, top=20)
    names = {metric.display_name for metric, _ in rows}

    assert report.annotation_warnings == ()
    assert {"TargetRow", "SidebarGroupSection"} <= names


def test_default_scan_finalize_function_annotations_match_report() -> None:
    """正式 scan finalize watchlist function annotations 應實際命中 report rows。"""

    result = load_annotations_with_warnings(Path("docs/maintainability_annotations.json"))
    report = collect_report(
        [Path("src/facebook_monitor/worker/scan_finalize.py")],
        include_extensions=(".py",),
        exclude_globs=(),
        annotations=result.annotations,
        annotation_warnings=result.warnings,
    )
    rows = watchlist_functions(report.functions, report.annotations, top=20)
    names = {metric.name for metric, _ in rows}

    assert report.annotation_warnings == ()
    assert {
        "prepare_guarded_skipped_scan_commit",
        "record_prepared_guarded_skipped_scan",
    } <= names
