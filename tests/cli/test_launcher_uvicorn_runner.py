"""Launcher single-instance behavior tests。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from typing import cast


from facebook_monitor import launcher
import facebook_monitor.runtime.windows_integration as windows_integration

def test_plain_uvicorn_runner_exposes_shutdown_hook(monkeypatch) -> None:
    """非 Windows tray 路徑也要讓 Web UI 可要求 uvicorn 關閉。"""

    class FakeNotifier:
        def __init__(self) -> None:
            self.requested_count = 0

        def request_stop(self) -> None:
            self.requested_count += 1

    class FakeState:
        def __init__(self) -> None:
            self.dashboard_revision_notifier = FakeNotifier()

    class FakeApp:
        def __init__(self) -> None:
            self.state = FakeState()

    class FakeConfig:
        def __init__(self, app: object, **kwargs: object) -> None:
            self.app = app
            self.kwargs = kwargs

    class FakeServer:
        def __init__(self, config: FakeConfig) -> None:
            self.config = config
            self.should_exit = False

        def run(self) -> None:
            shutdown = getattr(cast(Any, self.config.app).state, "request_shutdown")
            shutdown()

    created_servers: list[FakeServer] = []

    def fake_server(config: FakeConfig) -> FakeServer:
        server = FakeServer(config)
        created_servers.append(server)
        return server

    monkeypatch.setattr(launcher.uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(launcher.uvicorn, "Server", fake_server)

    app = FakeApp()
    launcher._run_uvicorn_with_shutdown_hook(app, host="127.0.0.1", port=8765)

    assert created_servers[0].config.kwargs == {"host": "127.0.0.1", "port": 8765}
    assert created_servers[0].should_exit is True
    assert app.state.dashboard_revision_notifier.requested_count == 1


def test_plain_uvicorn_runner_suppresses_shutdown_keyboard_interrupt(
    monkeypatch,
) -> None:
    """Ctrl+C graceful shutdown 不應把 KeyboardInterrupt traceback 顯示給使用者。"""

    class FakeState:
        pass

    class FakeApp:
        state = FakeState()

    class FakeConfig:
        def __init__(self, app: object, **kwargs: object) -> None:
            self.app = app
            self.kwargs = kwargs

    class FakeServer:
        def __init__(self, config: FakeConfig) -> None:
            self.config = config
            self.should_exit = False

        def run(self) -> None:
            raise KeyboardInterrupt

    monkeypatch.setattr(launcher.uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(launcher.uvicorn, "Server", FakeServer)

    launcher._run_uvicorn_with_shutdown_hook(FakeApp(), host="127.0.0.1", port=8765)


def test_windows_tray_uvicorn_runner_suppresses_shutdown_keyboard_interrupt(
    monkeypatch,
) -> None:
    """tray runner 也要把 Ctrl+C 關閉視為正常結束並清掉 tray icon。"""

    class FakeConfig:
        def __init__(self, app: object, **kwargs: object) -> None:
            self.app = app
            self.kwargs = kwargs

    class FakeServer:
        def __init__(self, config: FakeConfig) -> None:
            self.config = config
            self.should_exit = False

        def run(self) -> None:
            raise KeyboardInterrupt

    class FakeTray:
        def __init__(self) -> None:
            self.stop_count = 0

        def stop(self) -> None:
            self.stop_count += 1

    tray = FakeTray()

    def fake_start_windows_tray_icon(**kwargs: object) -> FakeTray:
        return tray

    monkeypatch.setattr(windows_integration.uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(windows_integration.uvicorn, "Server", FakeServer)
    monkeypatch.setattr(
        "facebook_monitor.runtime.windows_tray.start_windows_tray_icon",
        fake_start_windows_tray_icon,
    )

    windows_integration.run_uvicorn_with_windows_tray(
        object(),
        url="http://127.0.0.1:8765",
        icon_path=None,
        uvicorn_kwargs={"host": "127.0.0.1", "port": 8765},
    )

    assert tray.stop_count == 1


def test_windows_tray_exit_uses_app_shutdown_hook(monkeypatch) -> None:
    """tray Exit 應走 app shutdown hook，讓 Web UI 可先關閉長 SSE。"""

    class FakeState:
        def __init__(self) -> None:
            self.shutdown_count = 0

        def request_shutdown(self) -> None:
            self.shutdown_count += 1

    class FakeApp:
        def __init__(self) -> None:
            self.state = FakeState()

    class FakeConfig:
        def __init__(self, app: object, **kwargs: object) -> None:
            self.app = app
            self.kwargs = kwargs

    captured_exit_callbacks: list[Callable[[], None]] = []

    class FakeServer:
        def __init__(self, config: FakeConfig) -> None:
            self.config = config
            self.should_exit = False

        def run(self) -> None:
            captured_exit_callbacks[0]()

    class FakeTray:
        def stop(self) -> None:
            pass

    def fake_start_windows_tray_icon(**kwargs: object) -> FakeTray:
        captured_exit_callbacks.append(cast(Callable[[], None], kwargs["on_exit"]))
        return FakeTray()

    monkeypatch.setattr(windows_integration.uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(windows_integration.uvicorn, "Server", FakeServer)
    monkeypatch.setattr(
        "facebook_monitor.runtime.windows_tray.start_windows_tray_icon",
        fake_start_windows_tray_icon,
    )

    app = FakeApp()
    windows_integration.run_uvicorn_with_windows_tray(
        app,
        url="http://127.0.0.1:8765",
        icon_path=None,
        uvicorn_kwargs={"host": "127.0.0.1", "port": 8765},
    )

    assert app.state.shutdown_count == 1


def test_launcher_shutdown_feedback_wraps_uvicorn_signal_handler(
    monkeypatch,
    capsys,
) -> None:
    """收到中斷訊號時應立即提示，並保留 uvicorn 原本關閉流程。"""

    handled_signals: list[int] = []

    def fake_handle_exit(server: object, sig: int, frame: object | None) -> None:
        handled_signals.append(sig)

    monkeypatch.setattr(launcher.uvicorn.server.Server, "handle_exit", fake_handle_exit)
    original_handle_exit = launcher.uvicorn.server.Server.handle_exit

    shutdown_callback_count = 0

    def on_shutdown_requested() -> None:
        nonlocal shutdown_callback_count
        shutdown_callback_count += 1

    with launcher._print_shutdown_feedback_on_signal(
        on_shutdown_requested=on_shutdown_requested,
    ):
        wrapped_handle_exit = cast(
            Callable[[object, int, object | None], None],
            launcher.uvicorn.server.Server.handle_exit,
        )
        assert wrapped_handle_exit is not original_handle_exit
        wrapped_handle_exit(object(), 2, None)
        wrapped_handle_exit(object(), 2, None)

    assert launcher.uvicorn.server.Server.handle_exit is original_handle_exit
    assert handled_signals == [2, 2]
    assert shutdown_callback_count == 1
    output = capsys.readouterr().out
    assert output.count("已收到停止指令，正在結束 Web UI...") == 1


def test_launcher_filters_windows_proactor_connection_lost_noise(monkeypatch) -> None:
    """Windows Proactor 關閉 pipe 時的 WinError 10022 不應噴到 console。"""

    monkeypatch.setattr(launcher.sys, "platform", "win32")
    exception = OSError("bad argument")
    setattr(exception, "winerror", 10022)
    context = {
        "exception": exception,
        "handle": "<Handle _ProactorBasePipeTransport._call_connection_lost()>",
        "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost()",
    }

    assert launcher._is_windows_proactor_connection_lost_noise(context)


def test_launcher_event_loop_factory_import_path_is_valid() -> None:
    """uvicorn 設定中的 custom loop factory import path 必須可解析。"""

    config = launcher.uvicorn.Config(
        lambda scope, receive, send: None,
        loop="facebook_monitor.launcher:create_launcher_event_loop",
    )

    assert config.get_loop_factory() is launcher.create_launcher_event_loop


def test_launcher_keeps_unrelated_asyncio_exceptions(monkeypatch) -> None:
    """非目標 WinError 不能被 launcher exception handler 吃掉。"""

    monkeypatch.setattr(launcher.sys, "platform", "win32")
    exception = OSError("other")
    setattr(exception, "winerror", 10054)

    assert not launcher._is_windows_proactor_connection_lost_noise(
        {
            "exception": exception,
            "handle": "<Handle _ProactorBasePipeTransport._call_connection_lost()>",
        }
    )
