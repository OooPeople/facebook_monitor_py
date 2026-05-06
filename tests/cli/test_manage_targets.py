"""Phase B interactive target manager tests。"""

from __future__ import annotations

from scripts.phase_b_manage_targets import format_keywords
from scripts.phase_b_manage_targets import parse_keywords_text
from scripts.phase_b_manage_targets import parse_yes_no
from scripts.phase_b_manage_targets import choose_target_action


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
