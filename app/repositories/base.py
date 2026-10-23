"""Repository 公共基础设施。

只提供连接选择、行转换、字段白名单与分页/排序校验，不实现通用 CRUD，
也不引入继承式 Repository 框架。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any, Iterator

from app.data.db import Database

# 统一时间戳表达式：UTC ISO-8601 文本，与阶段 2 的 Schema 默认值一致。
NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


class Repository:
    """所有 Repository 的基类，只负责连接管道。

    连接所有权约定：

    - 未传入 ``connection`` 时由 Repository 自行创建连接，并交由 ``Database`` 的
      上下文管理器负责提交、回滚与关闭。
    - 传入 ``connection`` 时由调用方负责生命周期；Repository 不提交、不回滚、不关闭，
      并要求该连接处于显式事务中。
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    @contextmanager
    def _read(self, connection: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
        if connection is not None:
            yield connection
            return
        with self.database.connect() as owned:
            yield owned

    @contextmanager
    def _write(self, connection: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
        if connection is not None:
            if not connection.in_transaction:
                raise ValueError("传入的数据库连接必须处于显式事务中")
            yield connection
            return
        with self.database.transaction() as owned:
            yield owned

    @staticmethod
    def _to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        return [dict(row) for row in rows]


def fields_of(
    values: Mapping[str, Any],
    allowed: tuple[str, ...],
    *,
    action: str,
) -> list[tuple[str, Any]]:
    """校验字段名白名单并返回 ``(字段, 值)`` 列表。

    未登记字段直接抛 ``ValueError``，避免把用户输入当作 SQL 结构。
    """
    unknown = [key for key in values if key not in allowed]
    if unknown:
        raise ValueError(f"{action}包含不允许的字段：{'、'.join(unknown)}")
    return list(values.items())


def validate_pagination(limit: int | None, offset: int | None) -> None:
    """校验分页参数：``limit >= 1``、``offset >= 0``。"""
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
        raise ValueError("limit 必须是大于等于 1 的整数")
    if offset is not None and (not isinstance(offset, int) or isinstance(offset, bool) or offset < 0):
        raise ValueError("offset 必须是大于等于 0 的整数")


def limit_clause(limit: int | None, offset: int | None) -> tuple[str, list[Any]]:
    """生成分页 SQL 片段与参数；无分页时返回空片段。"""
    validate_pagination(limit, offset)
    if limit is None:
        if offset is None:
            return "", []
        # SQLite 要求 OFFSET 必须与 LIMIT 同时出现。
        return " LIMIT -1 OFFSET ?", [offset]
    if offset is None:
        return " LIMIT ?", [limit]
    return " LIMIT ? OFFSET ?", [limit, offset]


def order_clause(order_by: str, descending: bool, allowed: tuple[str, ...]) -> str:
    """生成稳定排序片段；排序列必须位于白名单内，并以主键兜底。"""
    if order_by not in allowed:
        raise ValueError(f"不支持的排序字段：{order_by}")
    direction = "DESC" if descending else "ASC"
    if order_by == "id":
        return f" ORDER BY id {direction}"
    return f" ORDER BY {order_by} {direction}, id {direction}"
