"""Schema 初始化、版本记录与升级入口。

版本管理原则：

- ``schema_meta`` 中的 ``schema_version`` 是唯一版本来源。
- 首次初始化在单个事务内建表、写入默认记录与版本号；任何一步失败都整体回滚。
- 每次启动都会以 ``IF NOT EXISTS`` 补建缺失对象并补齐默认记录，因此重复初始化是幂等的，
  不会清空或覆盖已有业务数据或用户修改过的规则。
- 结构升级只能通过 ``MIGRATIONS`` 中登记的目标版本依次执行，不允许删除数据库重建。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Callable

from app.data.db.connection import Database
from app.data.db.schema import BASE_SCHEMA_SQL, SCHEMA_VERSION
from app.data.db.seed import seed_defaults

logger = logging.getLogger(__name__)

SCHEMA_META_TABLE = "schema_meta"
SCHEMA_VERSION_KEY = "schema_version"

Migration = Callable[[sqlite3.Connection], None]


def _add_education_levels_column(connection: sqlite3.Connection) -> None:
    """v1 → v2：为编号规则增加「编号应用条件」列，不重建表、不丢数据。"""
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(numbering_rules)").fetchall()
    }
    if "education_levels" in columns:
        return
    connection.execute(
        "ALTER TABLE numbering_rules "
        "ADD COLUMN education_levels TEXT NOT NULL DEFAULT ''"
    )


def _drop_number_sequences_table(connection: sqlite3.Connection) -> None:
    """v2 → v3：移除自动流水计数器表，改由用户手动输入起始流水号。"""
    connection.execute("DROP TABLE IF EXISTS number_sequences")


def _rebuild_numbering_rules_without_name_and_enabled(
    connection: sqlite3.Connection,
) -> None:
    """v3 → v4：重建编号规则表，删除 rule_name 与 enabled 两列。

    SQLite 无法直接删除带 CHECK 约束的列，因此在同一事务内建新表、复制数据、
    删除旧表并重命名。fixed_text_*、sequence_width、层次选择与 updated_at 原样保留。
    """
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(numbering_rules)").fetchall()
    }
    if not columns:
        return
    if "rule_name" not in columns and "enabled" not in columns:
        return

    levels_expression = (
        "COALESCE(education_levels, '[]')"
        if "education_levels" in columns
        else "'[]'"
    )
    connection.execute("DROP TABLE IF EXISTS numbering_rules_new")
    connection.execute(
        """
        CREATE TABLE numbering_rules_new (
            rule_key TEXT PRIMARY KEY
                CHECK (rule_key IN ('graduation', 'degree', 'other')),
            fixed_text_1 TEXT NOT NULL DEFAULT '',
            fixed_text_2 TEXT NOT NULL DEFAULT '',
            fixed_text_3 TEXT NOT NULL DEFAULT '',
            fixed_text_4 TEXT NOT NULL DEFAULT '',
            sequence_width INTEGER NOT NULL DEFAULT 4 CHECK (sequence_width >= 1),
            education_levels TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )
    connection.execute(
        f"""
        INSERT INTO numbering_rules_new (
            rule_key, fixed_text_1, fixed_text_2, fixed_text_3, fixed_text_4,
            sequence_width, education_levels, updated_at
        )
        SELECT rule_key, fixed_text_1, fixed_text_2, fixed_text_3, fixed_text_4,
               sequence_width, {levels_expression}, updated_at
        FROM numbering_rules
        """
    )
    connection.execute("DROP TABLE numbering_rules")
    connection.execute(
        "ALTER TABLE numbering_rules_new RENAME TO numbering_rules"
    )


# 后续结构升级在此登记：{目标版本号: 迁移函数}，迁移函数在事务内执行。
MIGRATIONS: dict[int, Migration] = {
    2: _add_education_levels_column,
    3: _drop_number_sequences_table,
    4: _rebuild_numbering_rules_without_name_and_enabled,
}


class SchemaVersionError(RuntimeError):
    """数据库版本缺失、不兼容或缺少迁移路径。"""


def initialize_database(db_path: Path) -> int:
    """初始化数据库并返回当前 Schema 版本。

    Args:
        db_path: 数据库文件路径，父目录不存在时自动创建。

    Raises:
        SchemaVersionError: 数据库版本高于程序支持范围，或存在未登记版本的数据表。
        sqlite3.Error: 数据库读写失败。
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    database = Database(db_path)

    with database.transaction() as connection:
        _create_schema_meta(connection)
        current_version = _read_version(connection)

        if current_version is None:
            _ensure_no_unregistered_tables(connection)
            _create_base_schema(connection)
            seed_defaults(connection)
            _write_version(connection, SCHEMA_VERSION)
            logger.info("数据库首次初始化完成：%s（schema v%s）", db_path, SCHEMA_VERSION)
            return SCHEMA_VERSION

        if current_version > SCHEMA_VERSION:
            raise SchemaVersionError(
                f"数据库 Schema 版本为 {current_version}，高于程序支持的 "
                f"{SCHEMA_VERSION}；已停止初始化，请升级程序或使用匹配的数据库。"
            )

        if current_version < SCHEMA_VERSION:
            _apply_migrations(connection, current_version)

        # 幂等补建：缺失的表/索引与默认记录可以补齐，已有数据不受影响。
        _create_base_schema(connection)
        seed_defaults(connection)

    logger.debug("数据库初始化检查完成：%s（schema v%s）", db_path, SCHEMA_VERSION)
    return SCHEMA_VERSION


def get_schema_version(db_path: Path) -> int | None:
    """读取数据库文件中的 Schema 版本；文件或版本记录不存在时返回 ``None``。"""
    db_path = Path(db_path)
    if not db_path.exists():
        return None

    with Database(db_path).connect() as connection:
        if not _table_exists(connection, SCHEMA_META_TABLE):
            return None
        return _read_version(connection)


def _create_schema_meta(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )


def _read_version(connection: sqlite3.Connection) -> int | None:
    row = connection.execute(
        "SELECT value FROM schema_meta WHERE key = ?",
        (SCHEMA_VERSION_KEY,),
    ).fetchone()
    if row is None:
        return None

    try:
        return int(row["value"])
    except (TypeError, ValueError) as exc:
        raise SchemaVersionError(f"schema_meta.schema_version 不是有效整数：{row['value']!r}") from exc


def _write_version(connection: sqlite3.Connection, version: int) -> None:
    connection.execute(
        """
        INSERT INTO schema_meta (key, value) VALUES (?, ?)
        ON CONFLICT (key) DO UPDATE SET
            value = excluded.value,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (SCHEMA_VERSION_KEY, str(version)),
    )


def _create_base_schema(connection: sqlite3.Connection) -> None:
    for statement in BASE_SCHEMA_SQL:
        connection.execute(statement)


def _apply_migrations(connection: sqlite3.Connection, current_version: int) -> None:
    for target_version in range(current_version + 1, SCHEMA_VERSION + 1):
        migration = MIGRATIONS.get(target_version)
        if migration is None:
            raise SchemaVersionError(
                f"缺少从 schema v{target_version - 1} 升级到 v{target_version} 的迁移步骤"
            )
        migration(connection)
        logger.info("数据库已升级到 schema v%s", target_version)
    _write_version(connection, SCHEMA_VERSION)


def _ensure_no_unregistered_tables(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
          AND name <> ?
        """,
        (SCHEMA_META_TABLE,),
    ).fetchall()
    if rows:
        names = "、".join(sorted(row["name"] for row in rows))
        raise SchemaVersionError(
            f"数据库缺少版本记录但已包含业务表（{names}），拒绝自动接管；"
            "请确认数据库来源或由维护人员处理。"
        )


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None
