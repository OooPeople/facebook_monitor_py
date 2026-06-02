"""Launcher CLI test fixtures。"""

from __future__ import annotations

import pytest

from facebook_monitor import launcher


@pytest.fixture(autouse=True)
def assume_existing_profile_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """launcher 行為測試預設不進 first-run login gate。"""

    monkeypatch.setattr(launcher, "profile_has_facebook_session_cookies", lambda _path: True)
