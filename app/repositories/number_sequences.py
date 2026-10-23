"""三类独立流水 Repository。"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import NOW_SQL, Repository


class NumberSequenceRepository(Repository):
    """``number_sequences`` 的读取与显式更新入口，不负责自动递增或编号生成。"""

    def get(
        self,
        sequence_key: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        """按流水键查询；不存在返回 ``None``。"""
        with self._read(connection) as conn:
            row = conn.execute(
                "SELECT * FROM number_sequences WHERE sequence_key = ?", (sequence_key,)
            ).fetchone()
        return self._to_dict(row)

    def list_all(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """查询全部流水，按 ``sequence_key`` 稳定排序。"""
        with self._read(connection) as conn:
            rows = conn.execute(
                "SELECT * FROM number_sequences ORDER BY sequence_key ASC"
            ).fetchall()
        return self._to_dicts(rows)

    def exists(
        self,
        sequence_key: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """判断流水记录是否存在。"""
        with self._read(connection) as conn:
            row = conn.execute(
                "SELECT 1 FROM number_sequences WHERE sequence_key = ?", (sequence_key,)
            ).fetchone()
        return row is not None

    def update_next_value(
        self,
        sequence_key: str,
        next_value: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """显式更新指定流水值；返回是否命中该流水。非法值由数据库约束拒绝。"""
        with self._write(connection) as conn:
            cursor = conn.execute(
                f"UPDATE number_sequences SET next_value = ?, updated_at = {NOW_SQL} "
                "WHERE sequence_key = ?",
                (next_value, sequence_key),
            )
            return cursor.rowcount == 1
