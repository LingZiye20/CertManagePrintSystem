"""三类证书编号规则 Repository。"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from app.repositories.base import NOW_SQL, Repository, fields_of

# 允许更新的字段；rule_key 为主键，不允许修改。
WRITABLE_FIELDS: tuple[str, ...] = (
    "rule_name",
    "fixed_text_1",
    "fixed_text_2",
    "fixed_text_3",
    "fixed_text_4",
    "sequence_width",
    "enabled",
)


class NumberingRuleRepository(Repository):
    """``numbering_rules`` 的读取与更新入口，不包含编号生成算法。"""

    def get(
        self,
        rule_key: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        """按规则键查询；不存在返回 ``None``。"""
        with self._read(connection) as conn:
            row = conn.execute(
                "SELECT * FROM numbering_rules WHERE rule_key = ?", (rule_key,)
            ).fetchone()
        return self._to_dict(row)

    def list_all(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """查询全部规则，按 ``rule_key`` 稳定排序。"""
        with self._read(connection) as conn:
            rows = conn.execute(
                "SELECT * FROM numbering_rules ORDER BY rule_key ASC"
            ).fetchall()
        return self._to_dicts(rows)

    def exists(
        self,
        rule_key: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """判断规则是否存在。"""
        with self._read(connection) as conn:
            row = conn.execute(
                "SELECT 1 FROM numbering_rules WHERE rule_key = ?", (rule_key,)
            ).fetchone()
        return row is not None

    def update(
        self,
        rule_key: str,
        changes: Mapping[str, Any],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """更新指定规则；返回是否命中该规则，主键不可修改。"""
        items = fields_of(changes, WRITABLE_FIELDS, action="更新编号规则")
        if not items:
            return False

        assignments = ", ".join(f"{name} = ?" for name, _ in items)
        sql = (
            f"UPDATE numbering_rules SET {assignments}, updated_at = {NOW_SQL} "
            "WHERE rule_key = ?"
        )
        with self._write(connection) as conn:
            cursor = conn.execute(sql, [value for _, value in items] + [rule_key])
            return cursor.rowcount == 1

    def delete_all(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """删除全部编号规则并返回删除行数；仅用于系统重置后重建默认值。"""
        with self._write(connection) as conn:
            cursor = conn.execute("DELETE FROM numbering_rules")
            return int(cursor.rowcount)
