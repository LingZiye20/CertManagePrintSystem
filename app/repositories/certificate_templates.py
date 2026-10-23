"""证书模板 Repository。

只负责 ``certificate_templates`` 表的读写，不解析模板 JSON、不校验模板结构、
不渲染内容，也不创建内置模板种子；这些职责分别属于
``app/services/template_model.py``、``template_service.py`` 与 ``template_renderer.py``。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from app.repositories.base import (
    NOW_SQL,
    Repository,
    fields_of,
    limit_clause,
)

# 允许更新的字段；主键与时间戳不允许由调用方指定。
WRITABLE_FIELDS: tuple[str, ...] = (
    "name",
    "certificate_type",
    "canvas_width_mm",
    "canvas_height_mm",
    "template_json",
    "is_builtin",
)


class CertificateTemplateRepository(Repository):
    """``certificate_templates`` 的读写入口。"""

    def create(
        self,
        *,
        name: str,
        certificate_type: str,
        canvas_width_mm: float,
        canvas_height_mm: float,
        template_json: str,
        is_builtin: int = 0,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """新增模板并返回主键 ID；内置模板唯一约束由数据库执行。"""
        with self._write(connection) as conn:
            cursor = conn.execute(
                """
                INSERT INTO certificate_templates (
                    name, certificate_type, canvas_width_mm, canvas_height_mm,
                    template_json, is_builtin
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    certificate_type,
                    canvas_width_mm,
                    canvas_height_mm,
                    template_json,
                    is_builtin,
                ),
            )
            return int(cursor.lastrowid)

    def get(
        self,
        template_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        """按 ID 查询；不存在返回 ``None``。"""
        with self._read(connection) as conn:
            row = conn.execute(
                "SELECT * FROM certificate_templates WHERE id = ?", (template_id,)
            ).fetchone()
        return self._to_dict(row)

    def list_by_type(
        self,
        certificate_type: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """按证书类型查询模板，按 ``id ASC`` 稳定排序。"""
        suffix, params = limit_clause(limit, offset)
        with self._read(connection) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM certificate_templates
                WHERE certificate_type = ?
                ORDER BY id ASC{suffix}
                """,
                [certificate_type, *params],
            ).fetchall()
        return self._to_dicts(rows)

    def list_all(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """查询全部模板，按 ``id ASC`` 稳定排序。"""
        suffix, params = limit_clause(limit, offset)
        with self._read(connection) as conn:
            rows = conn.execute(
                f"SELECT * FROM certificate_templates ORDER BY id ASC{suffix}", params
            ).fetchall()
        return self._to_dicts(rows)

    def get_builtin(
        self,
        certificate_type: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        """查询指定证书类型的内置模板；不存在返回 ``None``。"""
        with self._read(connection) as conn:
            row = conn.execute(
                """
                SELECT * FROM certificate_templates
                WHERE certificate_type = ? AND is_builtin = 1
                """,
                (certificate_type,),
            ).fetchone()
        return self._to_dict(row)

    def update(
        self,
        template_id: int,
        changes: Mapping[str, Any],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """更新模板；返回是否命中该模板。"""
        items = fields_of(changes, WRITABLE_FIELDS, action="更新证书模板")
        if not items:
            return False

        assignments = ", ".join(f"{name} = ?" for name, _ in items)
        sql = (
            f"UPDATE certificate_templates SET {assignments}, updated_at = {NOW_SQL} "
            "WHERE id = ?"
        )
        with self._write(connection) as conn:
            cursor = conn.execute(sql, [value for _, value in items] + [template_id])
            return cursor.rowcount == 1

    def delete(
        self,
        template_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """删除模板；不存在返回 ``False``，打印记录引用由外键置空。"""
        with self._write(connection) as conn:
            cursor = conn.execute(
                "DELETE FROM certificate_templates WHERE id = ?", (template_id,)
            )
            return cursor.rowcount == 1
