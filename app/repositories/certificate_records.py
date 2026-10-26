"""学生/证书业务记录 Repository。"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from app.data.db import EXCEL_FIELD_NAMES
from app.repositories.base import (
    NOW_SQL,
    Repository,
    fields_of,
    limit_clause,
    order_clause,
)

# 可写字段：C.2 的 37 个 Excel 字段 + 照片相对路径。
# 复用 Schema 中的唯一字段清单，不在 Repository 内维护第二份定义。
WRITABLE_FIELDS: tuple[str, ...] = (*EXCEL_FIELD_NAMES, "photo_path")

# 排序白名单：业务字段与内部字段。
SORTABLE_FIELDS: tuple[str, ...] = ("id", *EXCEL_FIELD_NAMES, "photo_path", "created_at", "updated_at")

# 筛选白名单：学号精确匹配，其余文本字段包含匹配。
FILTERABLE_FIELDS: tuple[str, ...] = (
    "student_no",
    "name",
    "class_name",
    "major_name",
    "faculty_name",
)
EXACT_FILTER_FIELDS: frozenset[str] = frozenset({"student_no"})
LIKE_ESCAPE_CHAR = "\\"

# 三类证书编号字段；同一字段内唯一，不同字段之间允许出现相同文本。
NUMBER_FIELDS: tuple[str, ...] = (
    "certificate_no",
    "degree_certificate_no",
    "other_certificate_no",
)

# 照片批量导入可用的匹配字段；只在 Service 层做精确匹配，不进入 SQL LIKE。
PHOTO_MATCH_FIELDS: tuple[str, ...] = ("student_no", "id_number", "candidate_no")


class CertificateRecordRepository(Repository):
    """``certificate_records`` 的读写入口。"""

    def create(
        self,
        values: Mapping[str, Any],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """新增一条记录并返回主键 ID；字段名非法抛 ``ValueError``。"""
        items = fields_of(values, WRITABLE_FIELDS, action="新增学生/证书记录")
        if not items:
            raise ValueError("新增学生/证书记录至少需要一个字段")

        columns = ", ".join(name for name, _ in items)
        placeholders = ", ".join("?" for _ in items)
        sql = f"INSERT INTO certificate_records ({columns}) VALUES ({placeholders})"

        with self._write(connection) as conn:
            cursor = conn.execute(sql, [value for _, value in items])
            return int(cursor.lastrowid)

    def get(
        self,
        record_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        """按主键查询；不存在返回 ``None``。"""
        with self._read(connection) as conn:
            row = conn.execute(
                "SELECT * FROM certificate_records WHERE id = ?", (record_id,)
            ).fetchone()
        return self._to_dict(row)

    def get_by_student_no(
        self,
        student_no: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        """按学号精确查询；保留字符串语义与前导零，不存在返回 ``None``。"""
        with self._read(connection) as conn:
            row = conn.execute(
                "SELECT * FROM certificate_records WHERE student_no = ?", (student_no,)
            ).fetchone()
        return self._to_dict(row)

    def existing_student_nos(
        self,
        student_nos: Sequence[str],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> set[str]:
        """批量查询已存在的学号，精确匹配并保留前导零。

        SQLite 单条语句的参数数量有限，按 500 个一组查询。
        """
        unique = list(dict.fromkeys(n for n in student_nos if isinstance(n, str) and n))
        found: set[str] = set()
        if not unique:
            return found

        with self._read(connection) as conn:
            for start in range(0, len(unique), 500):
                chunk = unique[start : start + 500]
                placeholders = ", ".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""
                    SELECT student_no FROM certificate_records
                    WHERE student_no IN ({placeholders})
                    """,
                    chunk,
                ).fetchall()
                found.update(row["student_no"] for row in rows)
        return found

    def exists_by_number(
        self,
        field: str,
        number: str,
        *,
        exclude_id: int | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """判断指定编号字段中是否已存在该编号。

        只允许 :data:`NUMBER_FIELDS` 中的字段；不同证书类型的编号字段互不影响。
        """
        if field not in NUMBER_FIELDS:
            raise ValueError(f"不支持的编号字段：{field}")
        if not isinstance(number, str) or not number.strip():
            raise ValueError("证书编号必须是非空文本")

        sql = f"SELECT 1 FROM certificate_records WHERE {field} = ?"
        params: list[Any] = [number]
        if exclude_id is not None:
            sql += " AND id <> ?"
            params.append(exclude_id)
        with self._read(connection) as conn:
            row = conn.execute(sql, params).fetchone()
        return row is not None

    def list_records(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        order_by: str = "id",
        descending: bool = False,
        filters: Mapping[str, str] | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """查询记录列表，默认按 ``id ASC`` 稳定排序。

        ``filters`` 只接受白名单字段： ``student_no`` 精确匹配，其余字段包含匹配。
        空白筛选值会被忽略；筛选条件与 :meth:`count_records` 保持一致。
        """
        order = order_clause(order_by, descending, SORTABLE_FIELDS)
        suffix, params = limit_clause(limit, offset)
        where, filter_params = filter_clause(filters)
        with self._read(connection) as conn:
            rows = conn.execute(
                f"SELECT * FROM certificate_records{where}{order}{suffix}",
                [*filter_params, *params],
            ).fetchall()
        return self._to_dicts(rows)

    def list_ids(
        self,
        *,
        order_by: str = "id",
        descending: bool = False,
        filters: Mapping[str, str] | None = None,
        education_levels: Sequence[str] | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> list[int]:
        """按筛选与排序返回全部记录 ID（不分页）。

        排序规则与 :meth:`list_records` 完全一致；``education_levels`` 非空时
        只返回教育层次精确命中的记录，供批量编号使用。
        """
        order = order_clause(order_by, descending, SORTABLE_FIELDS)
        where, filter_params = filter_clause(filters)
        level_sql, level_params = education_level_clause(education_levels)
        if level_sql:
            where = f"{where} AND {level_sql}" if where else f" WHERE {level_sql}"
        with self._read(connection) as conn:
            rows = conn.execute(
                f"SELECT id FROM certificate_records{where}{order}",
                [*filter_params, *level_params],
            ).fetchall()
        return [int(row["id"]) for row in rows]

    def list_education_levels(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[str]:
        """返回数据中已出现的教育层次（去重、忽略空值、按值排序）。"""
        with self._read(connection) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT education_level FROM certificate_records
                WHERE education_level IS NOT NULL
                  AND trim(education_level) <> ''
                ORDER BY education_level ASC
                """
            ).fetchall()
        return [row["education_level"] for row in rows]

    def list_by_ids(
        self,
        record_ids: Sequence[int],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """按主键批量查询，返回存在的记录并按 ``id ASC`` 排序。

        重复 ID 自动去重；不存在的 ID 直接忽略；超过 500 个时分块查询。
        """
        unique = list(
            dict.fromkeys(
                int(record_id)
                for record_id in record_ids
                if isinstance(record_id, int) and not isinstance(record_id, bool)
            )
        )
        if not unique:
            return []

        rows: list[sqlite3.Row] = []
        with self._read(connection) as conn:
            for start in range(0, len(unique), 500):
                chunk = unique[start : start + 500]
                placeholders = ", ".join("?" for _ in chunk)
                rows.extend(
                    conn.execute(
                        f"""
                        SELECT * FROM certificate_records
                        WHERE id IN ({placeholders})
                        ORDER BY id ASC
                        """,
                        chunk,
                    ).fetchall()
                )
        rows.sort(key=lambda row: row["id"])
        return self._to_dicts(rows)

    def update(
        self,
        record_id: int,
        changes: Mapping[str, Any],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """更新指定记录；返回是否命中该记录。"""
        items = fields_of(changes, WRITABLE_FIELDS, action="更新学生/证书记录")
        if not items:
            return False

        assignments = ", ".join(f"{name} = ?" for name, _ in items)
        sql = (
            f"UPDATE certificate_records SET {assignments}, updated_at = {NOW_SQL} "
            "WHERE id = ?"
        )
        with self._write(connection) as conn:
            cursor = conn.execute(sql, [value for _, value in items] + [record_id])
            return cursor.rowcount == 1

    def delete(
        self,
        record_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """删除指定记录；不存在返回 ``False``，外键行为由 Schema 决定。"""
        with self._write(connection) as conn:
            cursor = conn.execute("DELETE FROM certificate_records WHERE id = ?", (record_id,))
            return cursor.rowcount == 1

    def delete_all(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """删除全部学生记录并返回删除行数；仅用于系统重置。"""
        with self._write(connection) as conn:
            cursor = conn.execute("DELETE FROM certificate_records")
            return int(cursor.rowcount)

    def count_records(
        self,
        *,
        filters: Mapping[str, str] | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """返回满足筛选条件的记录总数。"""
        where, params = filter_clause(filters)
        with self._read(connection) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS total FROM certificate_records{where}", params
            ).fetchone()
        return int(row["total"])

    def list_for_photo_matching(
        self,
        field: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """返回照片批量导入匹配所需的最小字段集，按 ``id ASC`` 排序。

        只允许 :data:`PHOTO_MATCH_FIELDS` 中的字段；匹配键的 ``strip()``
        语义由 Service 层负责，这里不做 LIKE、不做格式猜测。
        """
        if field not in PHOTO_MATCH_FIELDS:
            raise ValueError(f"不支持的照片匹配字段：{field}")

        with self._read(connection) as conn:
            rows = conn.execute(
                """
                SELECT id, student_no, name, id_number, candidate_no, photo_path
                FROM certificate_records
                ORDER BY id ASC
                """
            ).fetchall()
        return self._to_dicts(rows)

    def count_by_number_field(
        self,
        field: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """统计指定编号字段非空的记录数；只允许 :data:`NUMBER_FIELDS`。"""
        _require_number_field(field)
        with self._read(connection) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS total FROM certificate_records "
                f"WHERE {field} IS NOT NULL"
            ).fetchone()
        return int(row["total"])

    def clear_number_field(
        self,
        field: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """把指定编号字段全部置为 NULL，返回受影响记录数。

        只操作该编号字段与 ``updated_at``，不触碰其他两类编号或业务字段。
        """
        _require_number_field(field)
        with self._write(connection) as conn:
            cursor = conn.execute(
                f"UPDATE certificate_records SET {field} = NULL, updated_at = {NOW_SQL} "
                f"WHERE {field} IS NOT NULL"
            )
            return int(cursor.rowcount)

    def count_records_by_photo_path(
        self,
        photo_path: str,
        *,
        exclude_id: int | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """统计引用指定照片相对路径的记录数。

        用于清理旧照片前确认没有其他记录仍指向同一文件；
        ``exclude_id`` 可排除当前正在更新的记录。
        """
        if not isinstance(photo_path, str) or not photo_path.strip():
            raise ValueError("照片路径必须是非空文本")

        sql = "SELECT COUNT(*) AS total FROM certificate_records WHERE photo_path = ?"
        params: list[Any] = [photo_path]
        if exclude_id is not None:
            sql += " AND id <> ?"
            params.append(exclude_id)
        with self._read(connection) as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["total"])


def _require_number_field(field: str) -> None:
    """校验编号字段名，避免把用户输入当作 SQL 结构。"""
    if field not in NUMBER_FIELDS:
        raise ValueError(f"不支持的编号字段：{field}")


def education_level_clause(
    levels: Sequence[str] | None,
) -> tuple[str, list[Any]]:
    """生成教育层次精确匹配片段；空列表表示不限制。"""
    if not levels:
        return "", []
    values = [
        str(level).strip()
        for level in levels
        if isinstance(level, str) and str(level).strip()
    ]
    if not values:
        return "", []
    placeholders = ", ".join("?" for _ in values)
    return f"education_level IN ({placeholders})", values


def filter_clause(filters: Mapping[str, str] | None) -> tuple[str, list[Any]]:
    """生成筛选 SQL 片段与参数。

    仅允许 :data:`FILTERABLE_FIELDS` 中的字段；``student_no`` 精确匹配，
    其余字段使用 ``LIKE`` 包含匹配。LIKE 值中的 ``\\``、``%``、``_`` 会被转义，
    避免用户输入被当作通配符。
    """
    if filters is None:
        return "", []

    unknown = [key for key in filters if key not in FILTERABLE_FIELDS]
    if unknown:
        raise ValueError(f"不支持的筛选字段：{'、'.join(unknown)}")

    clauses: list[str] = []
    params: list[Any] = []
    for field, raw in filters.items():
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        if field in EXACT_FILTER_FIELDS:
            clauses.append(f"{field} = ?")
            params.append(text)
            continue
        escaped = (
            text.replace(LIKE_ESCAPE_CHAR, LIKE_ESCAPE_CHAR * 2)
            .replace("%", LIKE_ESCAPE_CHAR + "%")
            .replace("_", LIKE_ESCAPE_CHAR + "_")
        )
        clauses.append(f"{field} LIKE ? ESCAPE '{LIKE_ESCAPE_CHAR}'")
        params.append(f"%{escaped}%")

    if not clauses:
        return "", []
    return " WHERE " + " AND ".join(clauses), params
