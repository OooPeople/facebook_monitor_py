"""Static Web UI contract test helpers。"""

from __future__ import annotations

import re


def css_rule_body(css: str, selector: str) -> str:
    """擷取單一 selector 規則內容，讓樣式契約測試只檢查局部宣告。"""

    return css.split(f"{selector} {{", 1)[1].split("}", 1)[0]


def input_tags(template: str, field_name: str) -> list[str]:
    """回傳指定 name 的 input tags，供靜態模板契約測試檢查屬性。"""

    return re.findall(
        rf'<input\b(?=[^>]*\bname="{re.escape(field_name)}")[^>]*>',
        template,
    )
