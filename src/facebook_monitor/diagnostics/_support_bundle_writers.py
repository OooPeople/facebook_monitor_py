"""Support bundle zip section writers。

職責：封裝 section 寫入、manifest status 與 section-level failure
降級，避免單一 collector 失敗造成整包建立失敗。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import zipfile

from facebook_monitor.diagnostics._support_bundle_redaction import _safe_exception_summary


@dataclass
class _BundleSectionStatus:
    """記錄單一 support bundle section 的收集結果。"""

    name: str
    file: str
    status: str = "ok"
    error: str = ""

    def to_json(self) -> dict[str, str]:
        """轉成 manifest 內的穩定 JSON shape。"""

        payload = {
            "name": self.name,
            "file": self.file,
            "status": self.status,
        }
        if self.error:
            payload["error"] = self.error
        return payload


def _write_json_section(
    archive: zipfile.ZipFile,
    sections: list[_BundleSectionStatus],
    *,
    name: str,
    filename: str,
    collect: Callable[[], object],
) -> None:
    """收集並寫入 JSON section；失敗時保留 unavailable payload。"""

    try:
        payload = collect()
        content = _json_text(payload)
        sections.append(_BundleSectionStatus(name=name, file=filename))
    except Exception as exc:
        payload = _unavailable_payload(exc)
        content = _json_text(payload)
        sections.append(
            _BundleSectionStatus(
                name=name,
                file=filename,
                status="unavailable",
                error=_safe_exception_summary(exc),
            )
        )
    _write_text(archive, filename, content)


def _write_text_section_from_collect(
    archive: zipfile.ZipFile,
    sections: list[_BundleSectionStatus],
    *,
    name: str,
    filename: str,
    collect: Callable[[], str],
) -> None:
    """收集並寫入文字 section；失敗時保留 unavailable 文字 payload。"""

    try:
        content = str(collect())
        sections.append(_BundleSectionStatus(name=name, file=filename))
    except Exception as exc:
        content = f"available: false\nerror: {_safe_exception_summary(exc)}\n"
        sections.append(
            _BundleSectionStatus(
                name=name,
                file=filename,
                status="unavailable",
                error=_safe_exception_summary(exc),
            )
        )
    _write_text(archive, filename, content)


def _unavailable_payload(exc: Exception) -> dict[str, object]:
    """建立 section 失敗時的安全 payload。"""

    return {
        "available": False,
        "error": _safe_exception_summary(exc),
    }


def _write_json(
    archive: zipfile.ZipFile,
    name: str,
    payload: object,
) -> None:
    """以穩定 UTF-8 JSON 寫入 zip。"""

    _write_text(archive, name, _json_text(payload))


def _json_text(payload: object) -> str:
    """將 JSON payload 轉成穩定文字，讓 section writer 可隔離序列化錯誤。"""

    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_text(archive: zipfile.ZipFile, name: str, content: str) -> None:
    """將文字內容寫入 zip，避免呼叫端重複處理 encoding。"""

    archive.writestr(name, content.encode("utf-8"))
