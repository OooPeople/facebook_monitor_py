"""ntfy sender tests。"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import threading
from typing import Any
from uuid import uuid4

import httpx

from facebook_monitor.notifications.desktop import build_macos_native_notification_payload
from facebook_monitor.notifications.desktop import build_macos_osascript_notification_command
from facebook_monitor.notifications.desktop import build_desktop_notification_command
from facebook_monitor.notifications.desktop import parse_macos_native_notification_result
from facebook_monitor.notifications.desktop import resolve_macos_parent_notification_socket
from facebook_monitor.notifications.desktop import send_macos_native_notification_payload_to_socket
from facebook_monitor.notifications.desktop import resolve_macos_native_notification_launcher
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER_ENV
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_NOTIFICATION_SEND_FLAG
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_NOTIFICATION_SOCKET_ENV
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.discord import truncate_discord_content
from facebook_monitor.notifications.discord_url import validate_discord_webhook_url
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.notifications.ntfy import to_ascii_header_value


@contextmanager
def macos_notification_socket_file(path: Path):
    """建立 resolver 可接受的測試用 macOS socket path。"""

    sock: socket.socket | None = None
    active_path = path
    if os.name != "nt" and len(str(active_path)) >= 100:
        active_path = Path("/tmp") / f"fbm-notify-{uuid4().hex}.sock"
    if os.name != "nt" and hasattr(socket, "AF_UNIX"):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(active_path))
    else:
        active_path.write_text("socket", encoding="utf-8")
    try:
        yield active_path
    finally:
        if sock is not None:
            sock.close()
        active_path.unlink(missing_ok=True)


def test_send_ntfy_notification_matches_product_encoding(monkeypatch: Any) -> None:
    """ntfy topic 走 URL encode，中文內容放 UTF-8 body，不放進非 ASCII header。"""

    calls: list[dict[str, Any]] = []

    def fake_post(url: str, *, content: bytes, headers: dict[str, str], timeout: int) -> httpx.Response:
        """記錄送出參數，避免測試真的呼叫 ntfy。"""

        calls.append(
            {
                "url": url,
                "content": content,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return httpx.Response(200)

    monkeypatch.setattr(httpx, "post", fake_post)

    result = send_ntfy_notification(
        NtfyConfig(
            topic="中文 topic",
            click_url="https://www.facebook.com/groups/1/posts/2",
        ),
        "Facebook 監視命中: 票券",
        "社團: 測試社團\n內容: 中文內容",
    )

    assert result.ok
    assert calls[0]["url"].endswith("/%E4%B8%AD%E6%96%87%20topic")
    assert calls[0]["content"] == "社團: 測試社團\n內容: 中文內容".encode("utf-8")
    assert calls[0]["headers"]["Content-Type"] == "text/plain; charset=utf-8"
    assert calls[0]["headers"]["Title"] == "Facebook group match"
    assert calls[0]["headers"]["Priority"] == "default"
    assert calls[0]["headers"]["Tags"] == "bell"
    assert calls[0]["headers"]["Click"] == "https://www.facebook.com/groups/1/posts/2"


def test_to_ascii_header_value_keeps_ascii_and_falls_back_for_unicode() -> None:
    """HTTP header 只保留 ASCII，避免 httpx 編碼中文 header 失敗。"""

    assert to_ascii_header_value("Facebook group match", fallback="fallback") == (
        "Facebook group match"
    )
    assert to_ascii_header_value("Facebook 監視命中", fallback="fallback") == "fallback"


def test_send_ntfy_notification_sanitizes_http_exception_message(monkeypatch: Any) -> None:
    """ntfy HTTP 例外訊息不得把 topic / URL 寫進診斷。"""

    def fake_post(
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
        timeout: int,
    ) -> httpx.Response:
        """模擬 httpx 例外內含完整 endpoint。"""

        raise httpx.ConnectError(f"failed to connect {url}")

    monkeypatch.setattr(httpx, "post", fake_post)

    result = send_ntfy_notification(
        NtfyConfig(topic="private-topic"),
        "Facebook group match",
        "message",
    )

    assert not result.ok
    assert result.message == "ntfy_failed:ConnectError"
    assert "private-topic" not in result.message


def test_send_desktop_notification_uses_powershell_balloon_tip(monkeypatch: Any) -> None:
    """desktop sender 參考 hotel_price_watch 的 PowerShell balloon tip 作法。"""

    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> None:
        """記錄命令，避免測試真的發桌面通知。"""

        calls.append(command)

    monkeypatch.setattr("sys.platform", "win32")
    result = send_desktop_notification(
        "Facebook group match",
        "作者: O'Neil",
        command_runner=fake_runner,
    )

    assert result.ok
    assert result.message == "desktop_sent"
    command = calls[0]
    assert command[:3] == ["powershell", "-NoProfile", "-Command"]
    assert "System.Windows.Forms.NotifyIcon" in command[3]
    assert "O''Neil" in command[3]


def test_build_desktop_notification_command_escapes_single_quotes() -> None:
    """PowerShell single-quoted string 會轉義單引號。"""

    command = build_desktop_notification_command(title="A'B", message="C'D")

    assert "$notify.BalloonTipTitle = 'A''B';" in command[3]
    assert "$notify.BalloonTipText = 'C''D';" in command[3]


def test_send_desktop_notification_sanitizes_runner_exception(monkeypatch: Any) -> None:
    """desktop 例外訊息不得把通知內容或 command 寫進診斷。"""

    def failing_runner(_command: list[str]) -> None:
        """模擬 runner 例外內含通知內容。"""

        raise RuntimeError("failed to show private notification body")

    monkeypatch.setattr("sys.platform", "win32")

    result = send_desktop_notification(
        "Facebook group match",
        "private notification body",
        command_runner=failing_runner,
    )

    assert not result.ok
    assert result.message == "desktop_failed:RuntimeError"
    assert "private notification body" not in result.message


def test_resolve_macos_native_notification_launcher_uses_frozen_app_bundle(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """macOS frozen root binary 可定位同一 onedir 內的 `.app` launcher。"""

    app_root = tmp_path / "facebook-monitor"
    executable = app_root / "facebook-monitor"
    launcher = app_root / MACOS_APP_BUNDLE_LAUNCHER
    launcher.parent.mkdir(parents=True)
    executable.write_text("binary", encoding="utf-8")
    launcher.write_text("launcher", encoding="utf-8")
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("sys.executable", str(executable))
    monkeypatch.setattr("sys.frozen", True, raising=False)

    assert resolve_macos_native_notification_launcher() == launcher


def test_send_desktop_notification_uses_macos_native_launcher_stdin(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """macOS frozen sender 以 app bundle launcher 發通知，payload 走 stdin JSON。"""

    app_root = tmp_path / "facebook-monitor"
    executable = app_root / "facebook-monitor"
    launcher = app_root / MACOS_APP_BUNDLE_LAUNCHER
    launcher.parent.mkdir(parents=True)
    executable.write_text("binary", encoding="utf-8")
    launcher.write_text("launcher", encoding="utf-8")
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("sys.executable", str(executable))
    monkeypatch.setattr("sys.frozen", True, raising=False)
    calls: list[tuple[list[str], str]] = []

    def fake_runner(command: list[str], stdin_text: str) -> str:
        """記錄 launcher command 與 stdin payload。"""

        calls.append((command, stdin_text))
        return '{"ok":true,"message":"desktop_sent"}'

    result = send_desktop_notification(
        "Facebook Monitor 命中",
        "社團: 測試社團\n類型: 貼文\n命中: 票券",
        input_command_runner=fake_runner,
    )

    assert result.ok
    assert result.message == "desktop_sent"
    command, stdin_text = calls[0]
    assert command == [str(launcher), MACOS_APP_BUNDLE_NOTIFICATION_SEND_FLAG]
    assert "測試社團" not in command
    payload = json.loads(stdin_text)
    assert payload["title"] == "Facebook Monitor 命中"
    assert payload["body"] == "社團: 測試社團\n類型: 貼文\n命中: 票券"
    assert "sound" not in payload
    assert payload["schema_version"] == 1
    assert payload["identifier"].startswith("facebook-monitor-")


def test_resolve_macos_parent_notification_socket_uses_launcher_environment(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """macOS child process 只在 `.app` launcher 提供 socket path 時啟用 IPC。"""

    executable = tmp_path / "facebook-monitor"
    executable.write_text("binary", encoding="utf-8")
    socket_path = tmp_path / "notify.sock"
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("sys.executable", str(executable))
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setenv(MACOS_APP_BUNDLE_LAUNCHER_ENV, MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE)

    with macos_notification_socket_file(socket_path) as active_socket_path:
        monkeypatch.setenv(MACOS_APP_BUNDLE_NOTIFICATION_SOCKET_ENV, str(active_socket_path))
        assert resolve_macos_parent_notification_socket() == str(active_socket_path)

    assert resolve_macos_parent_notification_socket() is None


def test_resolve_macos_parent_notification_socket_ignores_source_mode_env(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """source mode 即使殘留 socket env，也不可誤走 frozen native IPC。"""

    socket_path = tmp_path / "notify.sock"
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.delattr("sys.frozen", raising=False)

    with macos_notification_socket_file(socket_path) as active_socket_path:
        monkeypatch.setenv(MACOS_APP_BUNDLE_NOTIFICATION_SOCKET_ENV, str(active_socket_path))
        assert resolve_macos_parent_notification_socket() is None


def test_resolve_macos_parent_notification_socket_requires_launcher_environment(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """frozen child 沒有 `.app` launcher marker 時不可接受 socket env。"""

    executable = tmp_path / "facebook-monitor"
    executable.write_text("binary", encoding="utf-8")
    socket_path = tmp_path / "notify.sock"
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("sys.executable", str(executable))
    monkeypatch.setattr("sys.frozen", True, raising=False)

    with macos_notification_socket_file(socket_path) as active_socket_path:
        monkeypatch.setenv(MACOS_APP_BUNDLE_NOTIFICATION_SOCKET_ENV, str(active_socket_path))
        assert resolve_macos_parent_notification_socket() is None


def test_send_desktop_notification_uses_macos_parent_socket_first(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """frozen `.app` child 優先交給常駐母程序送 native notification。"""

    app_root = tmp_path / "facebook-monitor"
    executable = app_root / "facebook-monitor"
    launcher = app_root / MACOS_APP_BUNDLE_LAUNCHER
    socket_path = tmp_path / "notify.sock"
    launcher.parent.mkdir(parents=True)
    executable.write_text("binary", encoding="utf-8")
    launcher.write_text("launcher", encoding="utf-8")
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("sys.executable", str(executable))
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setenv(MACOS_APP_BUNDLE_LAUNCHER_ENV, MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE)
    socket_calls: list[tuple[str, str]] = []
    launcher_calls: list[tuple[list[str], str]] = []

    def fake_socket_sender(path: str, payload: str) -> str:
        """記錄 socket payload。"""

        socket_calls.append((path, payload))
        return '{"ok":true,"message":"desktop_sent","backend":"usernotifications"}'

    def fake_launcher(command: list[str], stdin_text: str) -> str:
        launcher_calls.append((command, stdin_text))
        return '{"ok":true,"message":"desktop_sent"}'

    with macos_notification_socket_file(socket_path) as active_socket_path:
        monkeypatch.setenv(MACOS_APP_BUNDLE_NOTIFICATION_SOCKET_ENV, str(active_socket_path))
        result = send_desktop_notification(
            "Facebook Monitor 命中",
            "社團: 測試社團\n類型: 貼文\n命中: 票券",
            input_command_runner=fake_launcher,
            macos_socket_payload_sender=fake_socket_sender,
        )

    assert result.ok
    assert result.message == "desktop_sent"
    assert launcher_calls == []
    path, payload_text = socket_calls[0]
    assert path == str(active_socket_path)
    payload = json.loads(payload_text)
    assert payload["body"] == "社團: 測試社團\n類型: 貼文\n命中: 票券"


def test_send_desktop_notification_falls_back_to_macos_launcher_when_socket_fails(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """parent socket 暫時不可用時，frozen build 仍可回退到 launcher stdin mode。"""

    app_root = tmp_path / "facebook-monitor"
    executable = app_root / "facebook-monitor"
    launcher = app_root / MACOS_APP_BUNDLE_LAUNCHER
    socket_path = tmp_path / "notify.sock"
    launcher.parent.mkdir(parents=True)
    executable.write_text("binary", encoding="utf-8")
    launcher.write_text("launcher", encoding="utf-8")
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("sys.executable", str(executable))
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setenv(MACOS_APP_BUNDLE_LAUNCHER_ENV, MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE)
    launcher_calls: list[tuple[list[str], str]] = []

    def failing_socket_sender(path: str, payload: str) -> str:
        del path, payload
        raise TimeoutError

    def fake_launcher(command: list[str], stdin_text: str) -> str:
        launcher_calls.append((command, stdin_text))
        return '{"ok":true,"message":"desktop_sent"}'

    with macos_notification_socket_file(socket_path) as active_socket_path:
        monkeypatch.setenv(MACOS_APP_BUNDLE_NOTIFICATION_SOCKET_ENV, str(active_socket_path))
        result = send_desktop_notification(
            "Facebook Monitor 命中",
            "社團: 測試社團\n類型: 貼文\n命中: 票券",
            input_command_runner=fake_launcher,
            macos_socket_payload_sender=failing_socket_sender,
        )

    assert result.ok
    command, stdin_text = launcher_calls[0]
    assert command == [str(launcher), MACOS_APP_BUNDLE_NOTIFICATION_SEND_FLAG]
    assert json.loads(stdin_text)["body"] == "社團: 測試社團\n類型: 貼文\n命中: 票券"


def test_send_desktop_notification_preserves_macos_parent_structured_failure(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """parent 授權失敗會保留結構化失敗，不改走其他 app 身分。"""

    app_root = tmp_path / "facebook-monitor"
    executable = app_root / "facebook-monitor"
    launcher = app_root / MACOS_APP_BUNDLE_LAUNCHER
    socket_path = tmp_path / "notify.sock"
    launcher.parent.mkdir(parents=True)
    executable.write_text("binary", encoding="utf-8")
    launcher.write_text("launcher", encoding="utf-8")
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("sys.executable", str(executable))
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setenv(MACOS_APP_BUNDLE_LAUNCHER_ENV, MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE)
    launcher_calls: list[tuple[list[str], str]] = []

    def failing_parent(path: str, payload: str) -> str:
        del path, payload
        return '{"ok":false,"message":"desktop_failed:macos_authorization_error"}'

    def fake_launcher(command: list[str], stdin_text: str) -> str:
        launcher_calls.append((command, stdin_text))
        return '{"ok":true,"message":"desktop_sent"}'

    with macos_notification_socket_file(socket_path) as active_socket_path:
        monkeypatch.setenv(MACOS_APP_BUNDLE_NOTIFICATION_SOCKET_ENV, str(active_socket_path))
        result = send_desktop_notification(
            "Facebook Monitor 命中",
            "社團: 測試社團\n類型: 貼文\n命中: 票券",
            input_command_runner=fake_launcher,
            macos_socket_payload_sender=failing_parent,
        )

    assert not result.ok
    assert result.message == "desktop_failed:macos_authorization_error"
    assert launcher_calls == []


def test_send_desktop_notification_keeps_parent_denied_failure(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """main app UserNotifications 被拒絕時，必須回報主 app 權限問題。"""

    app_root = tmp_path / "facebook-monitor"
    executable = app_root / "facebook-monitor"
    socket_path = tmp_path / "notify.sock"
    app_root.mkdir(parents=True)
    executable.write_text("binary", encoding="utf-8")
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("sys.executable", str(executable))
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setenv(MACOS_APP_BUNDLE_LAUNCHER_ENV, MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE)
    launcher_calls: list[tuple[list[str], str]] = []

    def failing_parent(path: str, payload: str) -> str:
        del path, payload
        return '{"ok":false,"message":"desktop_failed:macos_permission_denied"}'

    def fake_launcher(command: list[str], stdin_text: str) -> str:
        launcher_calls.append((command, stdin_text))
        return '{"ok":true,"message":"desktop_sent"}'

    with macos_notification_socket_file(socket_path) as active_socket_path:
        monkeypatch.setenv(MACOS_APP_BUNDLE_NOTIFICATION_SOCKET_ENV, str(active_socket_path))
        result = send_desktop_notification(
            "Facebook Monitor 命中",
            "社團: 測試社團\n類型: 貼文\n命中: 票券",
            input_command_runner=fake_launcher,
            macos_socket_payload_sender=failing_parent,
        )

    assert not result.ok
    assert result.message == "desktop_failed:macos_permission_denied"
    assert launcher_calls == []


def test_send_macos_native_notification_payload_to_socket_roundtrips_payload(
    tmp_path: Path,
) -> None:
    """Python socket sender 應送出完整 UTF-8 JSON 並讀回 launcher result。"""

    if not hasattr(socket, "AF_UNIX"):
        return

    del tmp_path
    socket_path = Path("/tmp") / f"fbm-notify-test-{uuid4().hex}.sock"
    received_payloads: list[str] = []
    ready = threading.Event()

    def socket_server() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(socket_path))
            server.listen(1)
            ready.set()
            connection, _ = server.accept()
            with connection:
                chunks: list[bytes] = []
                while True:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                received_payloads.append(b"".join(chunks).decode("utf-8"))
                connection.sendall(b'{"ok":true,"message":"desktop_sent"}\n')

    try:
        thread = threading.Thread(target=socket_server, daemon=True)
        thread.start()
        assert ready.wait(timeout=2)

        output = send_macos_native_notification_payload_to_socket(
            str(socket_path),
            '{"title":"Facebook Monitor","body":"社團: 測試社團"}',
        )
        thread.join(timeout=2)

        assert output == '{"ok":true,"message":"desktop_sent"}\n'
        assert received_payloads == ['{"title":"Facebook Monitor","body":"社團: 測試社團"}']
    finally:
        socket_path.unlink(missing_ok=True)


def test_send_desktop_notification_uses_macos_osascript_fallback(
    monkeypatch: Any,
) -> None:
    """macOS source mode 沒有 native launcher 時保留 osascript fallback。"""

    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> None:
        calls.append(command)

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.delattr("sys.frozen", raising=False)
    result = send_desktop_notification(
        "Facebook Monitor 命中",
        "社團: 測試社團\n類型: 貼文\n命中: 票券",
        command_runner=fake_runner,
    )

    assert result.ok
    assert result.message == "desktop_sent"
    command = calls[0]
    assert command[:2] == ["/usr/bin/osascript", "-e"]
    assert not any("sound name" in item for item in command)
    assert command[-2:] == [
        "Facebook Monitor 命中",
        "社團: 測試社團\n類型: 貼文\n命中: 票券",
    ]


def test_send_desktop_notification_uses_osascript_when_source_mode_has_stale_socket_env(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """source mode 殘留 socket env 時仍應使用 osascript fallback。"""

    socket_path = tmp_path / "notify.sock"
    socket_path.write_text("not a socket", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> None:
        calls.append(command)

    def failing_socket_sender(path: str, payload: str) -> str:
        raise AssertionError(f"source mode should ignore socket env: {path} {payload}")

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.delattr("sys.frozen", raising=False)
    monkeypatch.setenv(MACOS_APP_BUNDLE_NOTIFICATION_SOCKET_ENV, str(socket_path))

    result = send_desktop_notification(
        "Facebook Monitor 命中",
        "社團: 測試社團\n類型: 貼文\n命中: 票券",
        command_runner=fake_runner,
        macos_socket_payload_sender=failing_socket_sender,
    )

    assert result.ok
    assert calls[0][:2] == ["/usr/bin/osascript", "-e"]


def test_macos_native_notification_result_preserves_structured_failures() -> None:
    """native notification failed JSON 應回傳可本地化錯誤碼。"""

    result = parse_macos_native_notification_result(
        '{"ok":false,"message":"desktop_failed:macos_permission_denied"}'
    )

    assert not result.ok
    assert result.message == "desktop_failed:macos_permission_denied"


def test_macos_native_notification_result_fails_when_alerts_disabled() -> None:
    """macOS 收下但不會顯示 banner 時，不可回報 desktop_sent。"""

    result = parse_macos_native_notification_result(
        '{"ok":true,"message":"desktop_sent","alert_setting":1}'
    )

    assert not result.ok
    assert result.message == "desktop_failed:macos_alert_disabled"


def test_macos_native_notification_result_fails_when_notification_center_disabled() -> None:
    """macOS notification center 設定關閉時，不可回報 desktop_sent。"""

    result = parse_macos_native_notification_result(
        '{"ok":true,"message":"desktop_sent","details":{"notification_center_setting":1}}'
    )

    assert not result.ok
    assert result.message == "desktop_failed:macos_alert_disabled"


def test_build_macos_native_notification_payload_does_not_escape_newlines() -> None:
    """native notification stdin JSON 保留通知 body 換行。"""

    payload = json.loads(
        build_macos_native_notification_payload(
            title="Facebook Monitor 命中",
            message="社團: 測試社團\n類型: 留言\n命中: 票券",
        )
    )

    assert payload["body"].splitlines() == [
        "社團: 測試社團",
        "類型: 留言",
        "命中: 票券",
    ]


def test_build_macos_osascript_notification_command_uses_argv() -> None:
    """osascript fallback 也必須用 argv 傳通知文字，避免 AppleScript escape 風險。"""

    command = build_macos_osascript_notification_command(
        title="A'B",
        message='C"D\nE',
    )

    assert command[-2:] == ["A'B", 'C"D\nE']
    assert not any("sound name" in item for item in command)


def test_send_discord_notification_matches_webhook_payload(
    monkeypatch: Any,
) -> None:
    """Discord sender 送 JSON content，並保留產品長度上限。"""

    calls: list[dict[str, Any]] = []

    def fake_post(
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> httpx.Response:
        """記錄送出參數，避免測試真的呼叫 Discord。"""

        calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return httpx.Response(204)

    monkeypatch.setattr(httpx, "post", fake_post)
    result = send_discord_notification(
        DiscordConfig(webhook_url="https://discord.com/api/webhooks/1234567890/token_value"),
        "Facebook group match",
        "社團: 測試社團",
    )

    assert result.ok
    assert result.status_code == 204
    assert result.message == "discord_sent"
    assert calls[0]["url"] == "https://discord.com/api/webhooks/1234567890/token_value"
    payload = calls[0]["json"]
    assert payload["username"] == "facebook_monitor_py"
    assert payload["allowed_mentions"] == {"parse": []}
    assert payload["content"] == "社團: 測試社團"
    assert "flags" not in payload
    assert "components" not in payload
    assert "embeds" not in payload
    assert calls[0]["headers"]["Accept"] == "*/*"


def test_send_discord_notification_retries_short_rate_limit(
    monkeypatch: Any,
) -> None:
    """Discord 429 有短 Retry-After 時，sender 會等待後重送一次。"""

    calls: list[int] = []
    sleeps: list[float] = []

    def fake_post(
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> httpx.Response:
        """第一次回 rate limit，第二次成功。"""

        calls.append(len(calls) + 1)
        if len(calls) == 1:
            return httpx.Response(
                429,
                json={"message": "You are being rate limited.", "retry_after": 0.25},
            )
        return httpx.Response(204)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("facebook_monitor.notifications.discord.time.sleep", sleeps.append)

    result = send_discord_notification(
        DiscordConfig(webhook_url="https://discord.com/api/webhooks/1234567890/token_value"),
        "Facebook group match",
        "社團: 測試社團",
    )

    assert result.ok
    assert result.status_code == 204
    assert result.message == "discord_sent"
    assert calls == [1, 2]
    assert sleeps == [0.25]


def test_send_discord_notification_reports_rate_limit_details(
    monkeypatch: Any,
) -> None:
    """Discord 429 超過等待上限時，訊息保留 retry-after 與 Discord body 摘要。"""

    def fake_post(
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> httpx.Response:
        """回傳不可立即等待的 Discord rate limit。"""

        return httpx.Response(
            429,
            headers={"Retry-After": "30"},
            json={
                "message": "You are being rate limited.",
                "retry_after": 30,
                "global": False,
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    result = send_discord_notification(
        DiscordConfig(webhook_url="https://discord.com/api/webhooks/1234567890/token_value"),
        "Facebook group match",
        "社團: 測試社團",
    )

    assert not result.ok
    assert result.status_code == 429
    assert result.message == (
        "discord_failed:429 retry_after=30s global=false "
        "message=You are being rate limited."
    )


def test_send_discord_notification_sanitizes_http_exception_message(
    monkeypatch: Any,
) -> None:
    """Discord HTTP 例外訊息不得把 webhook URL 寫進診斷。"""

    def fake_post(
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> httpx.Response:
        """模擬 httpx 例外內含完整 webhook。"""

        raise httpx.ConnectError(f"failed to connect {url}")

    monkeypatch.setattr(httpx, "post", fake_post)

    result = send_discord_notification(
        DiscordConfig(webhook_url="https://discord.com/api/webhooks/1234567890/private-token"),
        "Facebook group match",
        "message",
    )

    assert not result.ok
    assert result.message == "discord_failed:ConnectError"
    assert "private-token" not in result.message


def test_truncate_discord_content_uses_conservative_limit() -> None:
    """Discord content 會限制長度，避免超過 webhook 上限。"""

    content = truncate_discord_content("x" * 2000, limit=20)

    assert content == "x" * 17 + "..."


def test_validate_discord_webhook_url_rejects_non_discord_hosts() -> None:
    """Discord webhook URL 不可退化成 generic webhook。"""

    for value in (
        "http://discord.com/api/webhooks/123/token",
        "https://example.com/api/webhooks/123/token",
        "https://discord.com.evil.test/api/webhooks/123/token",
        "https://127.0.0.1/api/webhooks/123/token",
        "https://user:pass@discord.com/api/webhooks/123/token",
        "https://discord.com:8443/api/webhooks/123/token",
        "https://discord.com/api/not-webhooks/123/token",
        "https://discord.com/api/webhooks/not-numeric/token",
        "https://discord.com/api/webhooks/123/token?wait=true",
    ):
        try:
            validate_discord_webhook_url(value)
        except ValueError:
            continue
        raise AssertionError(f"expected invalid Discord webhook URL: {value}")


def test_send_discord_notification_does_not_post_invalid_webhook(
    monkeypatch: Any,
) -> None:
    """舊資料或污染資料中的 invalid webhook 不可觸發 HTTP POST。"""

    calls: list[str] = []

    def fake_post(*args: object, **kwargs: object) -> httpx.Response:
        calls.append("called")
        return httpx.Response(204)

    monkeypatch.setattr(httpx, "post", fake_post)

    result = send_discord_notification(
        DiscordConfig(webhook_url="https://127.0.0.1/api/webhooks/123/token"),
        "Facebook group match",
        "message",
    )

    assert not result.ok
    assert result.message == "discord_webhook_invalid"
    assert calls == []
