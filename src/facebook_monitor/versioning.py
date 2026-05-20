"""版本字串解析與轉換規則。

職責：集中 Git tag、release 比較與 Windows version resource 共用的
版本解析，避免不同 release 工具各自接受不同格式。
"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ParsedVersion:
    """可比較的簡化版本表示，支援 stable 與 rc 版本。"""

    release: tuple[int, ...]
    prerelease_label: str
    prerelease_number: int

    def sort_key(self) -> tuple[tuple[int, ...], int, int]:
        """回傳版本排序 key；同 release 下 stable 大於 rc。"""

        prerelease_rank = 1 if not self.prerelease_label else 0
        return (self.release, prerelease_rank, self.prerelease_number)


def normalize_version_text(value: str) -> str:
    """移除 Git tag 常見前綴，保留 app version 本體。"""

    normalized = value.strip()
    if normalized.startswith(("v", "V")):
        normalized = normalized[1:]
    return normalized


def parse_version(value: str) -> ParsedVersion:
    """解析專案目前使用的簡化語意版本。"""

    normalized = normalize_version_text(value).replace("-rc", "rc")
    match = re.fullmatch(r"(\d+(?:\.\d+)*)(?:rc(\d+))?", normalized)
    if match is None:
        raise ValueError("invalid_version")
    release = tuple(int(part) for part in match.group(1).split("."))
    prerelease_number = int(match.group(2) or 0)
    prerelease_label = "rc" if match.group(2) else ""
    return ParsedVersion(
        release=release,
        prerelease_label=prerelease_label,
        prerelease_number=prerelease_number,
    )


def windows_version_tuple(version: str) -> tuple[int, int, int, int]:
    """將 app version 轉成 Windows version resource 使用的四段 tuple。"""

    parsed = parse_version(version)
    if len(parsed.release) != 3 or parsed.prerelease_label not in {"", "rc"}:
        raise ValueError(f"unsupported app version for Windows resource: {version}")
    return (
        parsed.release[0],
        parsed.release[1],
        parsed.release[2],
        parsed.prerelease_number,
    )


def windows_file_version(version: str) -> str:
    """將 app version 轉成 Windows FileVersion 字串。"""

    return ".".join(str(part) for part in windows_version_tuple(version))
