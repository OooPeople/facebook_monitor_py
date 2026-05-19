"""正式本機 Web UI launcher。

職責：解析產品入口 CLI、集中 runtime path resolution，並啟動 FastAPI Web UI
與背景 resident scheduler。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from collections.abc import Sequence
import ipaddress
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import FrameType
from typing import Any
from urllib.parse import urlsplit
import webbrowser

import httpx
import uvicorn
import uvicorn.server

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import DEFAULT_WEBUI_FIXED_REFRESH_SECONDS
from facebook_monitor.core.defaults import PYTHON_WEBUI_RUNTIME_DEFAULTS
from facebook_monitor.persistence.repositories.app_settings import ProfileSessionState
from facebook_monitor.profile_login import GuidedLoginError
from facebook_monitor.profile_login import GuidedLoginOptions
from facebook_monitor.profile_login import profile_has_facebook_session_cookies
from facebook_monitor.profile_login import run_guided_facebook_login
from facebook_monitor.runtime.csrf_token import load_or_create_csrf_token
from facebook_monitor.runtime.instance_lock import AppInstanceLockError
from facebook_monitor.runtime.instance_lock import ServerInfo
from facebook_monitor.runtime.instance_lock import acquire_app_instance_lock
from facebook_monitor.runtime.instance_lock import acquire_resource_identity_lock
from facebook_monitor.runtime.logging_setup import configure_app_logging
from facebook_monitor.runtime.paths import add_runtime_path_arguments
from facebook_monitor.runtime.paths import resolve_runtime_paths_from_args
from facebook_monitor.runtime.startup_diagnostics import append_startup_log
from facebook_monitor.runtime.startup_diagnostics import build_startup_diagnostics
from facebook_monitor.runtime.startup_diagnostics import print_diagnostics
from facebook_monitor.runtime.windows_integration import ensure_standard_streams_for_gui_subsystem
from facebook_monitor.runtime.windows_integration import find_windows_tray_icon
from facebook_monitor.runtime.windows_integration import resolve_windows_tray_decision
from facebook_monitor.runtime.windows_integration import run_uvicorn_with_windows_tray
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER_ENV
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE
from facebook_monitor.updates.platforms import MACOS_APP_ENTRY
from facebook_monitor.version import APP_NAME
from facebook_monitor.webapp.app import create_app

logger = logging.getLogger(__name__)
DEFAULT_WEBUI_PORT = PYTHON_WEBUI_RUNTIME_DEFAULTS.port


def build_parser() -> argparse.ArgumentParser:
    """建立 Web UI launcher CLI parser。"""

    parser = argparse.ArgumentParser(description="Run the local Facebook Monitor Web UI.")
    parser.add_argument("--host", default=PYTHON_WEBUI_RUNTIME_DEFAULTS.host)
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=(
            f"Use a fixed local Web UI port. Defaults to {DEFAULT_WEBUI_PORT}, "
            "falling back to an available port when occupied."
        ),
    )
    port_group = parser.add_mutually_exclusive_group()
    port_group.add_argument(
        "--auto-port",
        dest="auto_port",
        action="store_true",
        help="Choose an available local port before starting the Web UI.",
    )
    port_group.add_argument(
        "--no-auto-port",
        dest="auto_port",
        action="store_false",
        help=f"Use the fixed port, defaulting to {DEFAULT_WEBUI_PORT} when --port is omitted.",
    )
    add_runtime_path_arguments(parser)
    browser_group = parser.add_mutually_exclusive_group()
    browser_group.add_argument(
        "--open-browser",
        dest="open_browser",
        action="store_true",
        help="Open the local Web UI URL in the default browser.",
    )
    browser_group.add_argument(
        "--no-open-browser",
        dest="open_browser",
        action="store_false",
        help="Do not open the local Web UI URL automatically.",
    )
    parser.set_defaults(auto_port=None, open_browser=None)
    parser.add_argument(
        "--scheduler-interval-seconds",
        type=float,
        default=DEFAULT_WEBUI_FIXED_REFRESH_SECONDS,
        help="Web UI background scheduler interval seconds.",
    )
    parser.add_argument(
        "--access-log",
        action="store_true",
        help="Print uvicorn HTTP access logs such as static file 304 responses.",
    )
    parser.add_argument(
        "--verbose-startup",
        action="store_true",
        help="Print full startup diagnostics to console. Diagnostics are always written to logs/startup.log.",
    )
    parser.add_argument(
        "--keep-runtime-data-on-startup",
        action="store_true",
        help=(
            "Keep previous scan/debug runtime data. By default Web UI startup clears "
            "scan_runs, latest_scan_items, notification_events and seen_items. "
            "Notification outbox and match history are retained."
        ),
    )
    parser.add_argument(
        "--graceful-shutdown-timeout-seconds",
        type=int,
        default=PYTHON_WEBUI_RUNTIME_DEFAULTS.graceful_shutdown_timeout_seconds,
        help=(
            "Maximum seconds uvicorn waits for long-lived dashboard connections such as SSE "
            "when CTRL+C shuts down the local Web UI."
        ),
    )
    tray_group = parser.add_mutually_exclusive_group()
    tray_group.add_argument(
        "--windows-tray",
        dest="windows_tray",
        action="store_true",
        help=(
            "Show a Windows system tray icon with Open and Exit actions. "
            "Defaults to enabled only for the frozen Windows EXE."
        ),
    )
    tray_group.add_argument(
        "--no-windows-tray",
        dest="windows_tray",
        action="store_false",
        help="Disable the Windows system tray icon.",
    )
    parser.set_defaults(windows_tray=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint：解析 runtime paths 後啟動 uvicorn server。"""

    ensure_standard_streams_for_gui_subsystem()
    relaunch_exit_code = _maybe_relaunch_via_macos_app(argv)
    if relaunch_exit_code is not None:
        return relaunch_exit_code
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        paths = resolve_runtime_paths_from_args(args)
    except ValueError as exc:
        parser.error(str(exc))
    if not _is_loopback_host(args.host):
        parser.error("--host must stay loopback for the local management UI")
    requested_port = DEFAULT_WEBUI_PORT if args.port is None else args.port
    explicit_auto_port = args.auto_port is True or requested_port == 0
    default_port_fallback = args.port is None and args.auto_port is None
    open_browser_on_start = True if args.open_browser is None else bool(args.open_browser)
    open_existing_browser = args.open_browser is True
    windows_tray_decision = resolve_windows_tray_decision(args.windows_tray)
    if windows_tray_decision.warning:
        print(windows_tray_decision.warning)
    if not 0 <= requested_port <= 65535:
        parser.error("--port must be between 0 and 65535")
    paths.ensure_writable_dirs()
    port, effective_auto_port = _resolve_server_port(
        args.host,
        requested_port,
        auto_port=explicit_auto_port,
        fallback_if_unavailable=default_port_fallback,
    )
    url = _local_url(args.host, port)
    app_log_path = configure_app_logging(
        paths.logs_dir,
        console_level=logging.INFO if args.verbose_startup else logging.WARNING,
    )
    logger.info("Configured app logging: %s", app_log_path)
    try:
        with acquire_app_instance_lock(paths.runtime_dir, "launcher") as instance_lock:
            try:
                with acquire_resource_identity_lock(
                    db_path=paths.db_path,
                    profile_dir=paths.profile_dir,
                    owner="launcher",
                ) as resource_lock:
                    logger.info(
                        "Acquired resource identity locks: %s (db=%s profile=%s)",
                        ", ".join(str(lock_path) for lock_path in resource_lock.lock_paths),
                        resource_lock.db_path,
                        resource_lock.profile_dir,
                    )
                    if not effective_auto_port and not _port_is_available(
                        args.host,
                        port,
                    ):
                        message = (
                            f"{APP_NAME} 無法啟動：{args.host}:{port} 已被使用。"
                            "請改用 --auto-port 或指定其他 --port。"
                        )
                        logger.error(message)
                        print(message)
                        return 2
                    if not _run_login_gate_if_needed(paths.profile_dir, paths.db_path):
                        return 2
                    instance_lock.write_server_info(host=args.host, port=port, url=url)
                    try:
                        diagnostics = build_startup_diagnostics(
                            paths=paths,
                            host=args.host,
                            port=port,
                            url=url,
                            auto_port=effective_auto_port,
                            open_browser=open_browser_on_start,
                            scheduler_interval_seconds=args.scheduler_interval_seconds,
                            reset_targets_on_startup=True,
                            resume_active_targets_on_startup=False,
                            reset_runtime_data_on_startup=not args.keep_runtime_data_on_startup,
                            access_log=args.access_log,
                            resource_lock_paths=resource_lock.lock_paths,
                        )
                        startup_log_path = append_startup_log(paths.logs_dir, diagnostics)
                        logger.info("Wrote startup diagnostics: %s", startup_log_path)
                        if args.verbose_startup:
                            print_diagnostics(diagnostics.lines)
                        else:
                            _print_startup_summary(
                                url=url,
                                data_dir=paths.data_dir,
                                logs_dir=paths.logs_dir,
                                startup_log_path=startup_log_path,
                            )
                        app = create_app(
                            db_path=paths.db_path,
                            profile_dir=paths.profile_dir,
                            templates_dir=paths.templates_dir,
                            static_dir=paths.static_dir,
                            auto_start_scheduler=True,
                            scheduler_interval_seconds=args.scheduler_interval_seconds,
                            reset_targets_on_startup=True,
                            reset_runtime_data_on_startup=not args.keep_runtime_data_on_startup,
                            csrf_token=load_or_create_csrf_token(paths.runtime_dir),
                        )
                        app.state.runtime_paths = paths
                        if open_browser_on_start:
                            _open_browser(url)
                        with _print_shutdown_feedback_on_signal():
                            uvicorn_kwargs: dict[str, Any] = {
                                "host": args.host,
                                "port": port,
                                "access_log": args.access_log,
                                "loop": "facebook_monitor.launcher:create_launcher_event_loop",
                                "log_level": (
                                    "info"
                                    if args.verbose_startup or args.access_log
                                    else "warning"
                                ),
                                "timeout_graceful_shutdown": (
                                    args.graceful_shutdown_timeout_seconds
                                ),
                            }
                            if windows_tray_decision.enabled:
                                run_uvicorn_with_windows_tray(
                                    app,
                                    url=url,
                                    icon_path=find_windows_tray_icon(paths),
                                    uvicorn_kwargs=uvicorn_kwargs,
                                    configure_server=lambda server: setattr(
                                        app.state,
                                        "request_shutdown",
                                        lambda: setattr(server, "should_exit", True),
                                    ),
                                )
                            else:
                                _run_uvicorn_with_shutdown_hook(app, **uvicorn_kwargs)
                    finally:
                        instance_lock.clear_server_info()
            except AppInstanceLockError as exc:
                logger.error("%s", exc)
                print(str(exc))
                return 2
    except AppInstanceLockError as exc:
        return _handle_existing_instance(
            exc.server_info,
            open_browser=open_existing_browser,
        )
    return 0


def _maybe_relaunch_via_macos_app(argv: Sequence[str] | None) -> int | None:
    """frozen macOS root binary 直啟時，轉交給 `.app` launcher 維持 Dock 生命週期。"""

    if sys.platform != "darwin":
        return None
    if not getattr(sys, "frozen", False):
        return None
    if os.environ.get(MACOS_APP_BUNDLE_LAUNCHER_ENV) == MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE:
        return None
    executable = Path(sys.executable).resolve()
    if executable.name != MACOS_APP_ENTRY:
        return None
    launcher = executable.parent / MACOS_APP_BUNDLE_LAUNCHER
    if not launcher.is_file():
        return None
    child_args = list(sys.argv[1:] if argv is None else argv)
    env = os.environ.copy()
    env[MACOS_APP_BUNDLE_LAUNCHER_ENV] = MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE
    try:
        subprocess.Popen(  # noqa: S603
            [str(launcher), *child_args],
            close_fds=True,
            cwd=str(executable.parent),
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        print(f"無法透過 macOS app launcher 啟動，改用目前程序繼續：{exc}")
        return None
    return 0


def _run_login_gate_if_needed(profile_dir: Path, db_path: Path) -> bool:
    """needs_login 狀態時先開 Facebook 登入視窗，再啟動 Web UI。"""

    with SqliteApplicationContext(db_path) as app:
        status = app.repositories.app_settings.get_profile_session_status()
    if status.state != ProfileSessionState.NEEDS_LOGIN and profile_has_facebook_session_cookies(
        profile_dir
    ):
        return True
    if status.state == ProfileSessionState.NEEDS_LOGIN:
        print("偵測到 Facebook 需要重新登入；登入完成後才會啟動 Web UI。")
    else:
        print("找不到 Facebook 登入資料；登入完成後才會啟動 Web UI。")
    try:
        logged_in = run_guided_facebook_login(
            GuidedLoginOptions(profile_dir=profile_dir),
        )
    except GuidedLoginError as exc:
        logger.error("Guided Facebook login failed: %s", exc)
        print(f"無法開啟 Facebook 登入視窗：{exc}")
        return False
    if not logged_in:
        print("Facebook 登入尚未完成，Web UI 未啟動。請重新執行程式再試一次。")
        return False
    with SqliteApplicationContext(db_path) as app:
        app.repositories.app_settings.mark_profile_ok(source="launcher_guided_login")
    return True


def _handle_existing_instance(
    server_info: ServerInfo | None,
    *,
    open_browser: bool,
) -> int:
    """處理第二次啟動：若既有 server 健康，必要時開啟既有 URL 後退出。"""

    if server_info is not None and _server_is_healthy(server_info.url):
        print(f"{APP_NAME} 已在執行：{server_info.url}")
        if open_browser:
            _open_browser(server_info.url)
        return 0
    if server_info is not None:
        print(
            f"{APP_NAME} 可能正在啟動或暫時沒有回應：{server_info.url}"
        )
        return 2
    print(f"{APP_NAME} 已在執行，但目前找不到 server 資訊。")
    return 2


def _run_uvicorn_with_shutdown_hook(app: Any, **uvicorn_kwargs: Any) -> None:
    """啟動 plain uvicorn server，並暴露 Web UI 可呼叫的 shutdown hook。"""

    config = uvicorn.Config(app, **uvicorn_kwargs)
    server = uvicorn.Server(config)
    app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
    server.run()


def _server_is_healthy(url: str) -> bool:
    """檢查既有 server 的 `/health` endpoint。"""

    try:
        response = httpx.get(f"{url.rstrip('/')}/health", timeout=1.0)
    except httpx.HTTPError:
        return False
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return payload.get("status") == "ok"


def _is_loopback_host(host: str) -> bool:
    """判斷 Web UI bind host 是否限制在本機 loopback。"""

    normalized = host.strip().strip("[]").lower()
    if normalized in {"localhost", ""}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _local_url(host: str, port: int) -> str:
    """建立瀏覽器可開啟的 local URL。"""

    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{browser_host}:{port}"


def _print_startup_summary(
    *,
    url: str,
    data_dir: Path,
    logs_dir: Path,
    startup_log_path: Path,
) -> None:
    """輸出日常啟動摘要；完整診斷保留在 startup.log。"""

    print(APP_NAME)
    print(f"Web UI：{url}")
    print(f"資料目錄：{data_dir}")
    print(f"Log 目錄：{logs_dir}")
    print(f"啟動診斷：{startup_log_path}")
    print("按 CTRL+C 停止。")


@contextlib.contextmanager
def _print_shutdown_feedback_on_signal():
    """在 uvicorn 收到中斷訊號時先輸出使用者可見的關閉提示。"""

    original_handle_exit = uvicorn.server.Server.handle_exit
    feedback_printed = False

    def handle_exit_with_feedback(
        server: uvicorn.server.Server,
        sig: int,
        frame: FrameType | None,
    ) -> None:
        nonlocal feedback_printed
        if not feedback_printed:
            feedback_printed = True
            print("已收到停止指令，正在結束 Web UI...", flush=True)
        original_handle_exit(server, sig, frame)

    uvicorn.server.Server.handle_exit = handle_exit_with_feedback
    try:
        yield
    finally:
        uvicorn.server.Server.handle_exit = original_handle_exit


def _resolve_server_port(
    host: str,
    requested_port: int,
    *,
    auto_port: bool,
    fallback_if_unavailable: bool = False,
) -> tuple[int, bool]:
    """解析實際 Web UI port 與是否使用 auto-port。

    預設啟動先嘗試固定 port；只有該 port 已被占用時才 fallback 到可用 port。
    `--auto-port` 或 `--port 0` 則直接挑可用 port。
    """

    if auto_port or requested_port == 0:
        return _choose_available_port(host), True
    if fallback_if_unavailable and not _port_is_available(host, requested_port):
        return _choose_available_port(host), True
    return requested_port, False


def _port_is_available(host: str, port: int) -> bool:
    """檢查固定 Web UI port 是否可綁定，避免 uvicorn 啟動後才爆錯。"""

    if port == 0:
        return True
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    bind_host = "::" if host == "::" else host
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.bind((bind_host, port))
    except OSError:
        return False
    return True


def _choose_available_port(host: str) -> int:
    """向 OS 詢問目前可用的 TCP port。

    這不是長期保留 port，只是讓 `server.json`、startup diagnostics 與
    uvicorn 啟動參數在 auto-port 模式下能使用同一個實際 port。
    """

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    bind_host = "::1" if host == "::" else host
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((bind_host, 0))
        return int(sock.getsockname()[1])


def _open_browser(url: str) -> None:
    """開啟使用者預設瀏覽器；失敗時只印出 URL。"""

    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        print(f"拒絕開啟非 http URL：{url}")
        return
    opened = webbrowser.open(url)
    if not opened:
        print(f"請在瀏覽器開啟：{url}")


def create_launcher_event_loop() -> asyncio.AbstractEventLoop:
    """建立 launcher 專用 event loop，並安裝 Windows Proactor 關閉噪音過濾。"""

    loop = asyncio.new_event_loop()
    loop.set_exception_handler(_handle_launcher_event_loop_exception)
    return loop


def _handle_launcher_event_loop_exception(
    loop: asyncio.AbstractEventLoop,
    context: dict[str, object],
) -> None:
    """過濾 Windows Proactor pipe 關閉時偶發的 WinError 10022。"""

    if _is_windows_proactor_connection_lost_noise(context):
        return
    loop.default_exception_handler(context)


def _is_windows_proactor_connection_lost_noise(context: dict[str, object]) -> bool:
    """判斷是否為 Windows Proactor transport 關閉連線時的已知噪音。"""

    if sys.platform != "win32":
        return False
    exception = context.get("exception")
    if not isinstance(exception, OSError) or getattr(exception, "winerror", None) != 10022:
        return False
    callback_text = f"{context.get('handle', '')} {context.get('message', '')}"
    return "_ProactorBasePipeTransport._call_connection_lost" in callback_text


if __name__ == "__main__":
    raise SystemExit(main())
