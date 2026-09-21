"""Admin target manager tests。"""

from __future__ import annotations

from pathlib import Path

import pytest

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_actions import TargetActionOutcome
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
import scripts.admin.manage_targets as manage_targets_module
from scripts.admin.manage_targets import format_keywords
from scripts.admin.manage_targets import parse_keywords_text
from scripts.admin.manage_targets import parse_yes_no
from scripts.admin.manage_targets import choose_target_action
from scripts.admin.manage_targets import run_manager
from scripts.admin.manage_targets import run_target_action


def test_parse_keywords_text_dedupes_and_trims() -> None:
    """keyword 輸入會去除空白與重複項目。"""

    assert parse_keywords_text("票, 交換,票,,讓票") == ("票", "交換", "讓票")


def test_format_keywords() -> None:
    """keyword 顯示格式在空值時有明確文字。"""

    assert format_keywords(()) == "(未設定)"
    assert format_keywords(("票", "交換")) == "票, 交換"


def test_parse_yes_no_keeps_current_on_blank() -> None:
    """y/n 輸入空白時保留目前值。"""

    assert parse_yes_no("", True)
    assert not parse_yes_no("", False)
    assert parse_yes_no("y", False)
    assert not parse_yes_no("否", True)


def test_choose_target_action_trims_input(monkeypatch) -> None:
    """target action 輸入會去除前後空白。"""

    monkeypatch.setattr("builtins.input", lambda _: " 2 ")

    assert choose_target_action() == "2"


def test_admin_start_uses_warning_aware_application_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Admin CLI 不得直接繞過 temporary-block 的 Web 風險確認契約。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="admin-warning",
                canonical_url="https://www.facebook.com/groups/admin-warning",
            )
        )

        calls: list[tuple[bool, int]] = []

        def warning_aware_start(
            selected_db_path: Path,
            target_id: str,
            *,
            temporary_block_warning_confirmed: bool = False,
            warning_generation: int = -1,
        ) -> TargetActionOutcome:
            assert selected_db_path == db_path
            assert target_id == target.id
            calls.append((temporary_block_warning_confirmed, warning_generation))
            if temporary_block_warning_confirmed and warning_generation == 4:
                return TargetActionOutcome(ok=True, message="target 已開始")
            next_generation = (
                4 if temporary_block_warning_confirmed and warning_generation == 3 else 3
            )
            return TargetActionOutcome(
                ok=False,
                message="Facebook 曾顯示暫時封鎖警告。",
                confirmation_required=True,
                warning_generation=next_generation,
            )

        monkeypatch.setattr(
            manage_targets_module,
            "restart_target_monitoring_action",
            warning_aware_start,
        )
        answers = iter(("y", "y"))
        monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

        run_target_action(app, target, "2", db_path=db_path)

        unchanged = app.repositories.targets.get(target.id)
    assert calls == [(False, -1), (True, 3), (True, 4)]
    assert unchanged is not None and unchanged.paused


def test_manager_stop_then_start_uses_separate_transactions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """互動期的前一筆 writer 必須先 commit，下一次 Start 不得撞到自己的 DB lock。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="admin-sequence",
                canonical_url="https://www.facebook.com/groups/admin-sequence",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    answers = iter(("1", "3", "1", "2", "q"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert run_manager(db_path) == 0

    with SqliteApplicationContext(db_path) as app:
        restarted = app.repositories.targets.get(target.id)
    assert restarted is not None and not restarted.paused
