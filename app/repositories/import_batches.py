"""Excel 导入批次记录 Repository。

本阶段只维护批次元数据，不读取 Excel、不做逐行校验或整批事务。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from app.repositories.base import Repository, fields_of, limit_clause

# 批次完成后允许更新的字段；file_name、started_at 等创建信息不可改写。
WRITABLE_FIELDS: tuple[str, ...] = ("finished_at", "status", "total_rows", "error_summary")


class ImportBatchRepository(Repository):
    """``import_batches`` 的读写入口。"""

    def create(
        self,
        file_name: str,
        status: str,
        *,
        total_rows: int = 0,
        started_at: str | None = None,
        finished_at: str | None = None,
        error_summary: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """创建批次记录并返回 ID；``started_at`` 为空时使用数据库默认时间。"""
        columns = ["file_name", "status", "total_rows", "finished_at", "error_summary"]
        params: list[Any] = [file_name, status, total_rows, finished_at, error_summary]
        if started_at is not None:
            columns.append("started_at")
            params.append(started_at)

        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT INTO import_batches ({', '.join(columns)}) VALUES ({placeholders})"
        with self._write(connection) as conn:
            cursor = conn.execute(sql, params)
            return int(cursor.lastrowid)

    def get(
        self,
        batch_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        """按 ID 查询；不存在返回 ``None``。"""
        with self._read(connection) as conn:
            row = conn.execute(
                "SELECT * FROM import_batches WHERE id = ?", (batch_id,)
            ).fetchone()
        return self._to_dict(row)

    def delete_all(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """删除全部导入批次并返回删除行数；仅用于系统重置。"""
        with self._write(connection) as conn:
            cursor = conn.execute("DELETE FROM import_batches")
            return int(cursor.rowcount)

    def list_batches(
        self,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """按开始时间范围（含边界）与状态筛选，默认最新批次在前。"""
        where, params = self._filter_clause(start_time, end_time, status)
        suffix, pagination_params = limit_clause(limit, offset)
        with self._read(connection) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM import_batches{where}
                ORDER BY started_at DESC, id DESC{suffix}
                """,
                [*params, *pagination_params],
            ).fetchall()
        return self._to_dicts(rows)

    def count_batches(
        self,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        status: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """返回满足条件的批次总数。"""
        where, params = self._filter_clause(start_time, end_time, status)
        with self._read(connection) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS total FROM import_batches{where}", params
            ).fetchone()
        return int(row["total"])

    def update(
        self,
        batch_id: int,
        changes: Mapping[str, Any],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """更新完成时间、状态、行数与错误摘要；不存在返回 ``False``。"""
        items = fields_of(changes, WRITABLE_FIELDS, action="更新导入批次")
        if not items:
            return False

        assignments = ", ".join(f"{name} = ?" for name, _ in items)
        sql = f"UPDATE import_batches SET {assignments} WHERE id = ?"
        with self._write(connection) as conn:
            cursor = conn.execute(sql, [value for _, value in items] + [batch_id])
            return cursor.rowcount == 1

    @staticmethod
    def _filter_clause(
        start_time: str | None,
        end_time: str | None,
        status: str | None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if start_time is not None:
            clauses.append("started_at >= ?")
            params.append(start_time)
        if end_time is not None:
            clauses.append("started_at <= ?")
            params.append(end_time)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if not clauses:
            return "", params
        return " WHERE " + " AND ".join(clauses), params
