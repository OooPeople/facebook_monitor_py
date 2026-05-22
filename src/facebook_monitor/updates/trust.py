"""Updater release trust roots。

職責：保存 runtime updater 內建信任的 release manifest public keys。
私鑰不可放在 repo；release 簽章腳本只應從本機檔案或 CI secret 讀取。
"""

from __future__ import annotations


TRUSTED_RELEASE_PUBLIC_KEYS = {
    "release-ed25519-2026q2": "/0fiNgg80SYYyN4qcbq8bgvFMXmE0cLQc8HTe8gIEIs=",
}
