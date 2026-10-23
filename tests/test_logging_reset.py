"""日志清空工具测试。"""

from __future__ import annotations

import logging
from pathlib import Path

from app.utils.logging_reset import truncate_log_file
from app.utils.logging_setup import _is_app_handler, setup_logging


def _cleanup_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if _is_app_handler(handler):
            root.removeHandler(handler)
            handler.close()


def test_truncate_plain_file(tmp_path: Path) -> None:
    log_file = tmp_path / "app.log"
    log_file.write_text("previous content\n", encoding="utf-8")

    assert truncate_log_file(log_file) is None
    assert log_file.read_text(encoding="utf-8") == ""


def test_truncate_while_handler_is_open(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "app.log"
    try:
        setup_logging(log_file)
        logging.getLogger("test").info("before reset")
        assert log_file.stat().st_size > 0

        assert truncate_log_file(log_file) is None
        assert log_file.stat().st_size == 0

        logging.getLogger("test").info("after reset")
        assert "after reset" in log_file.read_text(encoding="utf-8")
    finally:
        _cleanup_handlers()


def test_truncate_failure_returns_warning(tmp_path: Path) -> None:
    directory = tmp_path / "not-a-file"
    directory.mkdir()

    warning = truncate_log_file(directory)

    assert warning is not None
    assert "日志" in warning
