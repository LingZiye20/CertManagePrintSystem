"""打印尝试记录 Repository。

本阶段只记录提交结果，不调用打印机、不渲染模板，也不定义打印状态枚举。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import Repository, limit_clause


class PrintRecordRepository(Repository):
    """``print_records`` 的读写入口；证书编号快照始终保留。"""

    def create(
        self,
        certificate_record_id: int | None,
        certificate_type: str,
        certificate_no_snapshot: str,
        result: str,
        *,
        template_id: int | None = None,
        printer_name: str | None = None,
        error_message: str | None = None,
        submitted_at: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """新增一条打印尝试记录并返回 ID。

        ``result`` 由调用方按实际提交结果传入，Repository 不猜测成功或失败；
        ``submitted_at`` 为空时使用数据库默认 UTC 时间戳。
        """
        columns = [
            "certificate_record_id",
            "certificate_type",
            "certificate_no_snapshot",
            "result",
            "template_id",
            "printer_name",
            "error_message",
        ]
        params: list[Any] = [
            certificate_record_id,
            certificate_type,
            certificate_no_snapshot,
            result,
            template_id,
            printer_name,
            error_message,
        ]
        if submitted_at is not None:
            columns.append("submitted_at")
            params.append(submitted_at)

        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT INTO print_records ({', '.join(columns)}) VALUES ({placeholders})"
        with self._write(connection) as conn:
            cursor = conn.execute(sql, params)
            return int(cursor.lastrowid)

    def get(
        self,
        record_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        """按 ID 查询；不存在返回 ``None``。"""
        with self._read(connection) as conn:
            row = conn.execute(
                "SELECT * FROM print_records WHERE id = ?", (record_id,)
            ).fetchone()
        return self._to_dict(row)

    def delete_all(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """删除全部打印记录并返回删除行数；仅用于系统重置。"""
        with self._write(connection) as conn:
            cursor = conn.execute("DELETE FROM print_records")
            return int(cursor.rowcount)

    def list_records(
        self,
        *,
        certificate_record_id: int | None = None,
        certificate_type: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """按证书记录、类型与时间范围（含边界）筛选，默认最新提交在前。"""
        where, params = self._filter_clause(
            certificate_record_id, certificate_type, start_time, end_time
        )
        suffix, pagination_params = limit_clause(limit, offset)
        with self._read(connection) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM print_records{where}
                ORDER BY submitted_at DESC, id DESC{suffix}
                """,
                [*params, *pagination_params],
            ).fetchall()
        return self._to_dicts(rows)

    def list_by_certificate_record(
        self,
        certificate_record_id: int,
        *,
        limit: int | None = None,
        offset: int | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """查询指定证书记录的打印历史。"""
        return self.list_records(
            certificate_record_id=certificate_record_id,
            limit=limit,
            offset=offset,
            connection=connection,
        )

    def count_records(
        self,
        *,
        certificate_record_id: int | None = None,
        certificate_type: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """返回满足条件的打印记录总数。"""
        where, params = self._filter_clause(
            certificate_record_id, certificate_type, start_time, end_time
        )
        with self._read(connection) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS total FROM print_records{where}", params
            ).fetchone()
        return int(row["total"])

    @staticmethod
    def _filter_clause(
        certificate_record_id: int | None,
        certificate_type: str | None,
        start_time: str | None,
        end_time: str | None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if certificate_record_id is not None:
            clauses.append("certificate_record_id = ?")
            params.append(certificate_record_id)
        if certificate_type is not None:
            clauses.append("certificate_type = ?")
            params.append(certificate_type)
        if start_time is not None:
            clauses.append("submitted_at >= ?")
            params.append(start_time)
        if end_time is not None:
            clauses.append("submitted_at <= ?")
            params.append(end_time)
        if not clauses:
            return "", params
        return " WHERE " + " AND ".join(clauses), params
