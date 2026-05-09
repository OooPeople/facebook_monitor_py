"""Admin console tests。"""

from __future__ import annotations

from scripts.admin.console import print_menu
from scripts.admin.console import prompt_scan_group_id


def test_print_menu_contains_core_actions(capsys) -> None:
    """主選單包含新增、設定與掃描三個核心動作。"""

    print_menu()

    text = capsys.readouterr().out
    assert "新增/保存社團 target" in text
    assert "編輯/啟停 target" in text
    assert "執行一次掃描" in text


def test_prompt_scan_group_id_trims_input(monkeypatch) -> None:
    """掃描 group id 會去除使用者輸入前後空白。"""

    monkeypatch.setattr("builtins.input", lambda _: " 222518561920110 ")

    assert prompt_scan_group_id() == "222518561920110"
