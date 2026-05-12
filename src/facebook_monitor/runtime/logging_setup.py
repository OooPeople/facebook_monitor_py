"""Runtime logging setup。

職責：集中本機 app 的基礎 logging 設定，讓未來 portable / EXE 模式
可以穩定在 logs 目錄找到啟動與應用程式 log。
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys


APP_LOG_FILE_NAME = "app.log"
ERROR_LOG_FILE_NAME = "error.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3
MANAGED_HANDLER_FLAG = "_facebook_monitor_managed_handler"


def configure_app_logging(
    logs_dir: Path,
    *,
    console: bool = True,
    console_level: int = logging.WARNING,
) -> Path:
    """設定 root logger 輸出到 rotating app log，必要時同步輸出 console。"""

    logs_dir.mkdir(parents=True, exist_ok=True)
    app_log_path = logs_dir / APP_LOG_FILE_NAME
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    _remove_managed_handlers(root_logger)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        app_log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    setattr(file_handler, MANAGED_HANDLER_FLAG, True)
    root_logger.addHandler(file_handler)
    error_handler = RotatingFileHandler(
        logs_dir / ERROR_LOG_FILE_NAME,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)
    setattr(error_handler, MANAGED_HANDLER_FLAG, True)
    root_logger.addHandler(error_handler)
    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(console_level)
        setattr(console_handler, MANAGED_HANDLER_FLAG, True)
        root_logger.addHandler(console_handler)
    return app_log_path


def reset_app_logging() -> None:
    """移除由本模組建立的 logging handlers，主要供測試清理。"""

    _remove_managed_handlers(logging.getLogger())


def _remove_managed_handlers(logger: logging.Logger) -> None:
    """移除前次 configure 建立的 handler，避免測試或重入時重複輸出。"""

    for handler in list(logger.handlers):
        if not getattr(handler, MANAGED_HANDLER_FLAG, False):
            continue
        logger.removeHandler(handler)
        handler.close()
