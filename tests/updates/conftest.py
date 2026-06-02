"""Updater apply test fixtures。"""

from __future__ import annotations

import pytest

from facebook_monitor.updates import apply as updater_apply
from tests.updates.apply_test_helpers import trusted_public_keys


@pytest.fixture(autouse=True)
def trust_test_release_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """apply 階段使用測試 release public key 驗 signed manifest。"""

    monkeypatch.setattr(
        updater_apply,
        "TRUSTED_RELEASE_PUBLIC_KEYS",
        trusted_public_keys(),
    )
    monkeypatch.setattr(updater_apply, "APP_VERSION", "0.0.0")
