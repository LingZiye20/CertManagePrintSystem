"""应用日志文件清理工具。

系统重置需要清空运行日志；日志文件通常被 ``RotatingFileHandler`` 持有。
本模块只做截断，不新增 handler、不改变日志格式。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def truncate_log_file(log_file: Path) -> str | None:
    """清空日志文件；成功返回 ``None``，失败返回可展示的告警。"""
    path = Path(log_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8"):
            pass
    except OSError as exc:
        logger.warning("清空应用日志失败：%s（%s）", path, exc)
        return f"应用日志未能清空，可稍后手动处理：{path}"
    return None
