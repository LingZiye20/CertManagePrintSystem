"""业务操作日志 Repository。"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import Repository, limit_clause


class OperationLogRepository(Repository):
    """``operation_logs`` 的读写入口；不自动生成审计日志，也不定义操作类型枚举。"""

    def create(
        self,
        operation_type: str,
        target_type: str,
        target_id: str | None,
        summary: str,
        result: str,
        error_message: str | None = None,
        *,
        created_at: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """新增一条日志并返回 ID。

        ``created_at`` 为空时使用数据库默认 UTC 时间戳；传入时应为统一的
        ``YYYY-MM-DDTHH:MM:SS.sssZ`` 文本。
        """
        columns = ["operation_type", "target_type", "target_id", "summary", "result", "error_message"]
        params: list[Any] = [operation_type, target_type, target_id, summary, result, error_message]
        if created_at is not None:
            columns.append("created_at")
            params.append(created_at)

        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT INTO operation_logs ({', '.join(columns)}) VALUES ({placeholders})"
        with self._write(connection) as conn:
            cursor = conn.execute(sql, params)
            return int(cursor.lastrowid)

    def get(
        self,
        log_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        """按 ID 查询；不存在返回 ``None``。"""
        with self._read(connection) as conn:
            row = conn.execute(
                "SELECT * FROM operation_logs WHERE id = ?", (log_id,)
            ).fetchone()
        return self._to_dict(row)

    def list_logs(
        self,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        operation_type: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """按时间范围（含边界）与操作类型筛选日志。

        默认按 ``created_at DESC, id DESC`` 稳定排序。
        """
        where, params = self._filter_clause(start_time, end_time, operation_type)
        suffix, pagination_params = limit_clause(limit, offset)
        with self._read(connection) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM operation_logs{where}
                ORDER BY created_at DESC, id DESC{suffix}
                """,
                [*params, *pagination_params],
            ).fetchall()
        return self._to_dicts(rows)

    def count_logs(
        self,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        operation_type: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """返回满足条件的日志总数。"""
        where, params = self._filter_clause(start_time, end_time, operation_type)
        with self._read(connection) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS total FROM operation_logs{where}", params
            ).fetchone()
        return int(row["total"])

    @staticmethod
    def _filter_clause(
        start_time: str | None,
        end_time: str | None,
        operation_type: str | None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if start_time is not None:
            clauses.append("created_at >= ?")
            params.append(start_time)
        if end_time is not None:
            clauses.append("created_at <= ?")
            params.append(end_time)
        if operation_type is not None:
            clauses.append("operation_type = ?")
            params.append(operation_type)
        if not clauses:
            return "", params
        return " WHERE " + " AND ".join(clauses), params
