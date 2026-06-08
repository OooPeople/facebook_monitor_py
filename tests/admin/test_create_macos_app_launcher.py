"""macOS `.app` launcher bundle 建立測試。"""

from __future__ import annotations

import plistlib
from pathlib import Path
import subprocess

from scripts.admin.create_macos_app_launcher import BUNDLE_NAME
from scripts.admin.create_macos_app_launcher import create_macos_app_launcher
from scripts.admin import create_macos_app_launcher as launcher_builder
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_IDENTIFIER
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER_ENV
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_NOTIFICATION_SEND_FLAG
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_NOTIFICATION_SOCKET_ENV
from tests.helpers.macos_bundle import assert_posix_executable_when_supported


def test_create_macos_app_launcher_builds_dock_visible_bundle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """launcher bundle 應保留 Dock 顯示能力，並啟動 onedir 內的 executable。"""

    app_root = tmp_path / "facebook-monitor"
    app_root.mkdir()
    executable = app_root / "facebook-monitor"
    executable.write_text("binary", encoding="utf-8")
    executable.chmod(0o755)
    stale_helper = app_root / launcher_builder.STALE_NOTIFICATION_HELPER_BUNDLE_NAME
    stale_helper.mkdir()
    icon_source = tmp_path / "facebook-monitor.png"
    icon_source.write_text("png", encoding="utf-8")
    compile_commands: list[list[str]] = []
    sign_commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        if name == "clang":
            return "/usr/bin/clang"
        if name == "codesign":
            return "/usr/bin/codesign"
        return None

    def fake_run(command: list[str], *, check: bool, **kwargs: object) -> None:
        del kwargs
        assert check
        if command[0] == "/usr/bin/codesign":
            sign_commands.append(command)
            return
        compile_commands.append(command)
        source_args = [arg for arg in command if arg.endswith(".m")]
        assert len(source_args) == 1
        source = Path(source_args[0])
        source_text = source.read_text(encoding="utf-8")
        output = Path(command[command.index("-o") + 1])
        if output.name == "facebook-monitor-launcher":
            assert "NSTask" in source_text
            assert "applicationShouldTerminate" in source_text
            assert "InstallTerminationSignalHandler" in source_text
            assert "UNUserNotificationCenter" in source_text
            assert "NSUserNotificationCenter" not in source_text
            assert "defaultSound" in source_text
            assert "ReadNotificationPayload" in source_text
            assert "getNotificationSettingsWithCompletionHandler" in source_text
            assert "dispatch_semaphore_wait" in source_text
            assert "FD_CLOEXEC" in source_text
            assert "HandleNotificationClient" in source_text
            assert "parent_usernotifications" in source_text
            assert "AF_UNIX" in source_text
            assert "NotificationHelperAppDelegate" not in source_text
            assert "RunNotificationHelper" not in source_text
            assert "removeObjectForKey" in source_text
            assert MACOS_APP_BUNDLE_NOTIFICATION_SEND_FLAG in source_text
            assert MACOS_APP_BUNDLE_NOTIFICATION_SOCKET_ENV in source_text
        output.write_bytes(b"native-launcher")

    monkeypatch.setattr(launcher_builder.shutil, "which", fake_which)
    monkeypatch.setattr(launcher_builder.subprocess, "run", fake_run)

    bundle = create_macos_app_launcher(
        app_root=app_root,
        icon_source=icon_source,
        version="0.1.0",
        convert_icon=False,
    )

    assert bundle == app_root / BUNDLE_NAME
    assert not stale_helper.exists()
    launcher = bundle / "Contents" / "MacOS" / "facebook-monitor-launcher"
    assert_posix_executable_when_supported(launcher)
    assert launcher.read_bytes() == b"native-launcher"
    assert len(compile_commands) == 1
    assert sign_commands == [
        [
            "/usr/bin/codesign",
            "--force",
            "--deep",
            "--sign",
            "-",
            str(bundle),
        ],
    ]
    command = next(
        command
        for command in compile_commands
        if command[command.index("-o") + 1] == str(launcher)
    )
    assert command[0] == "/usr/bin/clang"
    assert "-fobjc-arc" in command
    assert ["-arch", "arm64"] == command[command.index("-arch") : command.index("-arch") + 2]
    assert ["-framework", "Cocoa"] == command[
        command.index("-framework") : command.index("-framework") + 2
    ]
    assert "UserNotifications" in command
    assert command[command.index("-o") + 1] == str(launcher)
    plist = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
    assert plist["CFBundleExecutable"] == "facebook-monitor-launcher"
    assert plist["CFBundleIconFile"] == "facebook-monitor"
    assert plist["CFBundleIdentifier"] == MACOS_APP_BUNDLE_IDENTIFIER
    assert plist["CFBundlePackageType"] == "APPL"
    assert plist["CFBundleShortVersionString"] == "0.1.0"
    assert "LSUIElement" not in plist
    assert "LSBackgroundOnly" not in plist


def test_create_macos_app_launcher_can_emit_source_for_unit_tests(
    tmp_path: Path,
) -> None:
    """測試模式可輸出 source，避免單元測試依賴本機 clang。"""

    app_root = tmp_path / "facebook-monitor"
    app_root.mkdir()
    executable = app_root / "facebook-monitor"
    executable.write_text("binary", encoding="utf-8")
    executable.chmod(0o755)
    icon_source = tmp_path / "facebook-monitor.png"
    icon_source.write_text("png", encoding="utf-8")

    bundle = create_macos_app_launcher(
        app_root=app_root,
        icon_source=icon_source,
        version="0.1.0",
        convert_icon=False,
        compile_launcher=False,
        ad_hoc_sign=False,
    )

    launcher_source = (
        bundle / "Contents" / "MacOS" / "facebook-monitor-launcher"
    ).read_text(encoding="utf-8")
    assert "NSApplicationActivationPolicyRegular" in launcher_source
    assert "NSTask" in launcher_source
    assert "applicationShouldTerminate" in launcher_source
    assert "InstallTerminationSignalHandler" in launcher_source
    assert "UNUserNotificationCenter" in launcher_source
    assert "NSUserNotificationCenter" not in launcher_source
    assert "defaultSound" in launcher_source
    assert "runUntilDate" in launcher_source
    assert "getNotificationSettingsWithCompletionHandler" in launcher_source
    assert "dispatch_semaphore_wait" in launcher_source
    assert "FD_CLOEXEC" in launcher_source
    assert "ReadNotificationPayload" in launcher_source
    assert "error_domain" in launcher_source
    assert "parent_usernotifications" in launcher_source
    assert "launcher_usernotifications" in launcher_source
    assert "readDataToEndOfFile" in launcher_source
    assert "HandleNotificationClient" in launcher_source
    assert "AF_UNIX" in launcher_source
    assert "SOCK_STREAM" in launcher_source
    assert "dispatch_get_main_queue" in launcher_source
    assert "NotificationHelperAppDelegate" not in launcher_source
    assert "RunNotificationHelper" not in launcher_source
    assert "removeObjectForKey" in launcher_source
    assert MACOS_APP_BUNDLE_NOTIFICATION_SEND_FLAG in launcher_source
    assert MACOS_APP_BUNDLE_NOTIFICATION_SOCKET_ENV in launcher_source
    assert '[arg hasPrefix:@"-psn_"]' in launcher_source
    assert MACOS_APP_BUNDLE_LAUNCHER_ENV in launcher_source
    assert MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE in launcher_source
    assert "setEnvironment" in launcher_source
    assert 'exec "$EXECUTABLE" "$@"' not in launcher_source


def test_create_macos_app_launcher_prefers_xcrun_clang(
    monkeypatch,
) -> None:
    """launcher 編譯優先使用 Xcode SDK 解析到的 clang。"""

    def fake_which(name: str) -> str | None:
        if name == "xcrun":
            return "/usr/bin/xcrun"
        if name == "clang":
            return "/usr/bin/clang"
        return None

    monkeypatch.setattr(launcher_builder.shutil, "which", fake_which)
    monkeypatch.setattr(
        launcher_builder.subprocess,
        "check_output",
        lambda command, **kwargs: "/Applications/Xcode.app/clang\n",
    )

    assert launcher_builder._find_clang() == "/Applications/Xcode.app/clang"


def test_create_macos_app_launcher_uses_xcrun_sdk_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """編譯 native launcher 時帶入 macOS SDK path，避免 Cocoa header 解析失敗。"""

    app_root = tmp_path / "facebook-monitor"
    app_root.mkdir()
    executable = app_root / "facebook-monitor"
    executable.write_text("binary", encoding="utf-8")
    executable.chmod(0o755)
    icon_source = tmp_path / "facebook-monitor.png"
    icon_source.write_text("png", encoding="utf-8")
    compile_commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        if name == "xcrun":
            return "/usr/bin/xcrun"
        return None

    def fake_check_output(command: list[str], **kwargs: object) -> str:
        del kwargs
        if "--find" in command:
            return "/usr/bin/clang\n"
        if "--show-sdk-path" in command:
            return "/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk\n"
        raise AssertionError(f"unexpected command: {command}")

    def fake_run(command: list[str], *, check: bool, **kwargs: object) -> None:
        del kwargs
        assert check
        compile_commands.append(command)
        Path(command[-1]).write_bytes(b"native-launcher")

    monkeypatch.setattr(launcher_builder.shutil, "which", fake_which)
    monkeypatch.setattr(launcher_builder.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(launcher_builder.subprocess, "run", fake_run)

    create_macos_app_launcher(
        app_root=app_root,
        icon_source=icon_source,
        version="0.1.0",
        convert_icon=False,
        ad_hoc_sign=False,
    )

    assert len(compile_commands) == 1
    for command in compile_commands:
        assert "-isysroot" in command
        assert "/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk" in command


def test_create_macos_app_launcher_reports_missing_clang(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """缺少 compiler 時應明確失敗，避免退回會消失的 shell launcher。"""

    app_root = tmp_path / "facebook-monitor"
    app_root.mkdir()
    executable = app_root / "facebook-monitor"
    executable.write_text("binary", encoding="utf-8")
    executable.chmod(0o755)
    icon_source = tmp_path / "facebook-monitor.png"
    icon_source.write_text("png", encoding="utf-8")

    monkeypatch.setattr(launcher_builder.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        launcher_builder.subprocess,
        "check_output",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, "xcrun")
        ),
    )

    try:
        create_macos_app_launcher(
            app_root=app_root,
            icon_source=icon_source,
            version="0.1.0",
            convert_icon=False,
        )
    except ValueError as exc:
        assert "clang" in str(exc)
    else:
        raise AssertionError("expected missing clang to fail")
