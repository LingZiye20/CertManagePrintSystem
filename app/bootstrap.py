"""应用启动引导。

负责按固定顺序完成与界面无关的初始化：解析路径、创建用户目录、初始化日志、
初始化数据库 Schema 与默认数据，并把结果放入 ``AppContext`` 交给入口使用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.config.paths import AppPaths
from app.data.db import Database, initialize_database
from app.utils.logging_setup import setup_logging

_MODULE_LOGGER_NAME = "app.bootstrap"
logger = logging.getLogger(_MODULE_LOGGER_NAME)


@dataclass(frozen=True, slots=True)
class AppContext:
    """启动后需要向下游传递的运行期上下文。"""

    paths: AppPaths
    log_file: Path
    database: Database
    schema_version: int


def bootstrap(paths: AppPaths | None = None) -> AppContext:
    """执行启动初始化并返回应用上下文。

    Args:
        paths: 显式路径集合，测试时可传入临时目录；为空时使用程序所在目录。

    Raises:
        OSError: 程序目录无法创建或日志文件无法写入。
        SchemaVersionError: 数据库版本不兼容或来源不明。
        sqlite3.Error: 数据库初始化失败。
    """
    resolved = paths if paths is not None else AppPaths.for_program()
    resolved.ensure_user_dirs()
    log_file = setup_logging(resolved.log_file)

    logger.info(
        "应用启动：程序目录=%s 资源目录=%s 日志文件=%s",
        resolved.program_root,
        resolved.resource_dir,
        log_file,
    )

    schema_version = initialize_database(resolved.db_file)
    logger.info("数据库就绪：%s（schema v%s）", resolved.db_file, schema_version)

    return AppContext(
        paths=resolved,
        log_file=log_file,
        database=Database(resolved.db_file),
        schema_version=schema_version,
    )
