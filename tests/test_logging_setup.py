"""日志初始化测试：验证文件落盘、内容可读、重复初始化不叠加 handler。"""

from __future__ import annotations

import logging
from pathlib import Path

from app.utils.logging_setup import setup_logging


def _remove_app_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "cert_system_handler", False):
            root.removeHandler(handler)
            handler.close()


def _flush_handlers() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_setup_logging_writes_message(tmp_path) -> None:
    log_file = tmp_path / "logs" / "app.log"
    try:
        returned = setup_logging(log_file)
        logging.getLogger("test.logging").info("hello 日志")

        _flush_handlers()
        assert returned == log_file
        assert log_file.is_file()
        assert "hello 日志" in log_file.read_text(encoding="utf-8")
    finally:
        _remove_app_handlers()


def test_setup_logging_is_repeatable(tmp_path) -> None:
    log_file = tmp_path / "logs" / "app.log"
    try:
        setup_logging(log_file)
        setup_logging(log_file)

        file_handlers = [
            handler
            for handler in logging.getLogger().handlers
            if getattr(handler, "cert_system_handler", False)
            and hasattr(handler, "baseFilename")
            and Path(handler.baseFilename).resolve() == log_file.resolve()
        ]
        assert len(file_handlers) == 1
    finally:
        _remove_app_handlers()
