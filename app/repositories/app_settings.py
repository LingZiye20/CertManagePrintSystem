"""业务配置 Repository。"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import NOW_SQL, Repository


class AppSettingsRepository(Repository):
    """``app_settings`` 的读写入口；只处理业务配置，不涉及 QSettings。"""

    def get(
        self,
        key: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        """按 key 查询；不存在返回 ``None``。"""
        with self._read(connection) as conn:
            row = conn.execute(
                "SELECT * FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        return self._to_dict(row)

    def list_all(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """查询全部配置，按 ``key`` 稳定排序。"""
        with self._read(connection) as conn:
            rows = conn.execute("SELECT * FROM app_settings ORDER BY key ASC").fetchall()
        return self._to_dicts(rows)

    def exists(
        self,
        key: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """判断配置是否存在。"""
        with self._read(connection) as conn:
            row = conn.execute("SELECT 1 FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row is not None

    def create(
        self,
        key: str,
        value: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """新增配置；key 重复时传播 ``sqlite3.IntegrityError``。"""
        with self._write(connection) as conn:
            conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (key, value))

    def update(
        self,
        key: str,
        value: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """更新已有配置；不存在返回 ``False``。"""
        with self._write(connection) as conn:
            cursor = conn.execute(
                f"UPDATE app_settings SET value = ?, updated_at = {NOW_SQL} WHERE key = ?",
                (value, key),
            )
            return cursor.rowcount == 1

    def set_value(
        self,
        key: str,
        value: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """按 key 新增或更新配置，不返回新增/更新状态。

        冲突语义：key 不存在时插入；已存在时仅更新 value 与 updated_at，
        不删除再插入，也不影响其他配置。
        """
        with self._write(connection) as conn:
            conn.execute(
                f"""
                INSERT INTO app_settings (key, value) VALUES (?, ?)
                ON CONFLICT (key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = {NOW_SQL}
                """,
                (key, value),
            )

    def delete(
        self,
        key: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """删除配置；不存在返回 ``False``。"""
        with self._write(connection) as conn:
            cursor = conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
            return cursor.rowcount == 1

    def delete_all(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """删除全部业务配置并返回删除行数；仅用于系统重置。"""
        with self._write(connection) as conn:
            cursor = conn.execute("DELETE FROM app_settings")
            return int(cursor.rowcount)
