"""应用日志初始化。

日志写入用户数据目录下的 ``logs/app.log``，按大小轮转；
同时输出到控制台便于开发调试。重复调用不会重复挂载 handler。
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s:%(lineno)d - %(message)s"
MAX_LOG_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3

# 用于识别本应用挂载的 handler，避免测试或重复初始化时叠加 handler。
_HANDLER_MARKER = "cert_system_handler"


def _is_app_handler(handler: logging.Handler) -> bool:
    return bool(getattr(handler, _HANDLER_MARKER, False))


def setup_logging(log_file: Path, level: int = logging.INFO) -> Path:
    """初始化根日志器，返回实际使用的日志文件路径。

    Args:
        log_file: 日志文件路径，其父目录会被自动创建。
        level: 根日志器级别，默认 ``INFO``。
    """
    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        if _is_app_handler(handler):
            root.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    setattr(file_handler, _HANDLER_MARKER, True)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    setattr(console_handler, _HANDLER_MARKER, True)
    root.addHandler(console_handler)

    return log_file
