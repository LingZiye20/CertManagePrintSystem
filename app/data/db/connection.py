"""SQLite 连接与事务管理。

所有数据库连接都必须经由本模块创建，避免各模块各自采用不一致的外键设置与
事务方式；Repository、Service 和 UI 不得直接调用 ``sqlite3.connect``。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

BUSY_TIMEOUT_MS = 5000


@dataclass(frozen=True, slots=True)
class Database:
    """数据库位置与连接工厂。

    只保存数据库文件路径，不持有长期连接；每次操作使用独立连接，
    避免跨线程共享 ``sqlite3.Connection``。
    """

    db_path: Path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """打开自动提交连接，退出上下文时关闭。"""
        connection = self._open()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """打开显式写事务，成功提交，异常回滚，最后关闭连接。

        使用 ``BEGIN IMMEDIATE`` 立即取得写锁，避免读事务升级写锁时冲突。
        不支持嵌套事务；需要嵌套时应改为在同一事务内调用。
        """
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            connection.commit()
        finally:
            connection.close()

    def _open(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.db_path),
            timeout=BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,  # 关闭 sqlite3 隐式事务，统一由 transaction() 控制
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        return connection
