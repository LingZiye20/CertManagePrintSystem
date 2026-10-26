"""数据库初始化、版本管理与幂等性测试。

全部使用 ``tmp_path`` 下的临时数据库，不触碰真实业务数据。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.bootstrap import bootstrap
from app.config.paths import AppPaths
from app.data.db import (
    SCHEMA_VERSION,
    Database,
    SchemaVersionError,
    get_schema_version,
    initialize_database,
)

EXPECTED_TABLES = {
    "schema_meta",
    "certificate_records",
    "numbering_rules",
    "certificate_templates",
    "app_settings",
    "operation_logs",
    "print_records",
    "import_batches",
}

EXPECTED_INDEXES = {
    "ux_certificate_templates_builtin",
    "idx_certificate_records_name",
    "idx_certificate_records_class_name",
    "idx_operation_logs_created_at",
    "idx_print_records_certificate_record_id",
    "idx_print_records_submitted_at",
    "idx_import_batches_started_at",
}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "certificates.db"


def _schema_object_names(db_path: Path, object_type: str) -> set[str]:
    with Database(db_path).connect() as connection:
        return {
            row["name"]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = ? AND name NOT LIKE 'sqlite_%'
                """,
                (object_type,),
            )
        }


def _count(db_path: Path, table: str) -> int:
    with Database(db_path).connect() as connection:
        return int(
            connection.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()["total"]
        )


# ---------------------------------------------------------------------------
# 首次初始化
# ---------------------------------------------------------------------------


def test_first_initialization_creates_schema_version_and_defaults(db_path: Path) -> None:
    assert not db_path.exists()
    assert get_schema_version(db_path) is None

    version = initialize_database(db_path)

    assert version == SCHEMA_VERSION == 4
    assert db_path.is_file()
    assert get_schema_version(db_path) == 4
    assert _schema_object_names(db_path, "table") == EXPECTED_TABLES
    assert _schema_object_names(db_path, "index") == EXPECTED_INDEXES

    with Database(db_path).connect() as connection:
        meta = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert meta["value"] == "4"

        rules = connection.execute(
            """
            SELECT rule_key, fixed_text_1, fixed_text_2, fixed_text_3,
                   fixed_text_4, sequence_width, education_levels
            FROM numbering_rules ORDER BY rule_key
            """
        ).fetchall()

    assert [row["rule_key"] for row in rules] == ["degree", "graduation", "other"]
    for row in rules:
        assert row["fixed_text_1"] == ""
        assert row["fixed_text_2"] == ""
        assert row["fixed_text_3"] == ""
        assert row["fixed_text_4"] == ""
        assert row["sequence_width"] == 4
        assert row["education_levels"] == "[]"

    # 模板结构尚未确认，阶段 2 只建表，不插入内置模板种子。
    assert _count(db_path, "certificate_templates") == 0


def test_get_schema_version_is_none_for_database_without_meta(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True)
    with Database(db_path).transaction() as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")

    assert get_schema_version(db_path) is None


# ---------------------------------------------------------------------------
# 幂等初始化
# ---------------------------------------------------------------------------


def test_repeated_initialization_keeps_data_and_user_changes(db_path: Path) -> None:
    initialize_database(db_path)
    with Database(db_path).transaction() as connection:
        connection.execute(
            """
            UPDATE numbering_rules
            SET sequence_width = 6, fixed_text_1 = 'GX',
                education_levels = '["本科"]'
            WHERE rule_key = 'graduation'
            """
        )
        connection.execute("DELETE FROM numbering_rules WHERE rule_key = 'other'")
        connection.execute(
            "INSERT INTO certificate_records (student_no) VALUES ('00123')"
        )

    initialize_database(db_path)
    initialize_database(db_path)

    assert get_schema_version(db_path) == 4
    assert _count(db_path, "numbering_rules") == 3
    assert _count(db_path, "certificate_records") == 1

    with Database(db_path).connect() as connection:
        graduation = connection.execute(
            "SELECT * FROM numbering_rules WHERE rule_key = 'graduation'"
        ).fetchone()
        other = connection.execute(
            "SELECT * FROM numbering_rules WHERE rule_key = 'other'"
        ).fetchone()
        student_no = connection.execute(
            "SELECT student_no FROM certificate_records"
        ).fetchone()

    # 用户修改必须保留。
    assert graduation["sequence_width"] == 6
    assert graduation["fixed_text_1"] == "GX"
    assert graduation["education_levels"] == '["本科"]'
    assert student_no["student_no"] == "00123"
    # 被删除的默认记录会被补齐为初始值，不重复、不覆盖其他规则。
    assert other["sequence_width"] == 4
    assert other["fixed_text_1"] == ""
    assert other["education_levels"] == "[]"


# ---------------------------------------------------------------------------
# 失败与版本不兼容
# ---------------------------------------------------------------------------


def test_initialization_failure_rolls_back_entire_schema(db_path: Path, monkeypatch) -> None:
    def _failing_seed(connection) -> None:  # noqa: ANN001 - 测试替身
        raise RuntimeError("种子写入失败")

    monkeypatch.setattr("app.data.db.migrations.seed_defaults", _failing_seed)

    with pytest.raises(RuntimeError, match="种子写入失败"):
        initialize_database(db_path)

    assert get_schema_version(db_path) is None
    assert _schema_object_names(db_path, "table") == set()
    assert _schema_object_names(db_path, "index") == set()


def test_refuses_database_with_unregistered_tables(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True)
    with Database(db_path).transaction() as connection:
        connection.execute("CREATE TABLE legacy_data (id INTEGER PRIMARY KEY)")

    with pytest.raises(SchemaVersionError, match="legacy_data"):
        initialize_database(db_path)

    # 不得删表重建。
    assert "legacy_data" in _schema_object_names(db_path, "table")
    assert "certificate_records" not in _schema_object_names(db_path, "table")
    assert get_schema_version(db_path) is None


def test_rejects_newer_schema_version_without_data_loss(db_path: Path) -> None:
    initialize_database(db_path)
    with Database(db_path).transaction() as connection:
        connection.execute(
            "UPDATE schema_meta SET value = '999' WHERE key = 'schema_version'"
        )
        connection.execute(
            "INSERT INTO certificate_records (student_no) VALUES ('00123')"
        )

    with pytest.raises(SchemaVersionError, match="999"):
        initialize_database(db_path)

    assert get_schema_version(db_path) == 999
    assert _count(db_path, "certificate_records") == 1


def test_rejects_lower_schema_version_without_registered_migration(db_path: Path) -> None:
    initialize_database(db_path)
    with Database(db_path).transaction() as connection:
        connection.execute(
            "UPDATE schema_meta SET value = '0' WHERE key = 'schema_version'"
        )
        connection.execute(
            "INSERT INTO certificate_records (student_no) VALUES ('00123')"
        )

    with pytest.raises(SchemaVersionError, match="迁移"):
        initialize_database(db_path)

    assert get_schema_version(db_path) == 0
    assert _count(db_path, "certificate_records") == 1


def test_rejects_non_integer_schema_version(db_path: Path) -> None:
    initialize_database(db_path)
    with Database(db_path).transaction() as connection:
        connection.execute(
            "UPDATE schema_meta SET value = 'not-a-number' WHERE key = 'schema_version'"
        )

    with pytest.raises(SchemaVersionError, match="不是有效整数"):
        initialize_database(db_path)


# ---------------------------------------------------------------------------
# 结构迁移
# ---------------------------------------------------------------------------


def test_migrates_v1_database_to_latest_and_keeps_data(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "certificates.db"
    db_path.parent.mkdir(parents=True)

    # 手工构造一个 schema v1 的编号规则表，模拟旧版本数据库。
    with Database(db_path).transaction() as connection:
        connection.execute(
            """
            CREATE TABLE schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('schema_version', '1')"
        )
        connection.execute(
            """
            CREATE TABLE numbering_rules (
                rule_key TEXT PRIMARY KEY,
                rule_name TEXT NOT NULL,
                fixed_text_1 TEXT NOT NULL DEFAULT '',
                fixed_text_2 TEXT NOT NULL DEFAULT '',
                fixed_text_3 TEXT NOT NULL DEFAULT '',
                fixed_text_4 TEXT NOT NULL DEFAULT '',
                sequence_width INTEGER NOT NULL DEFAULT 4,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            INSERT INTO numbering_rules (
                rule_key, rule_name, fixed_text_1, sequence_width, enabled
            ) VALUES ('graduation', '旧规则', 'GX', 6, 1)
            """
        )

    assert initialize_database(db_path) == 4
    assert get_schema_version(db_path) == 4

    with Database(db_path).connect() as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(numbering_rules)")
        }
        rows = {
            row["rule_key"]: row
            for row in connection.execute("SELECT * FROM numbering_rules")
        }
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert "education_levels" in columns
    # v4 重建后不再保留规则名称与启用列。
    assert "rule_name" not in columns
    assert "enabled" not in columns
    # 旧数据完整保留。
    assert rows["graduation"]["fixed_text_1"] == "GX"
    assert rows["graduation"]["sequence_width"] == 6
    assert rows["graduation"]["education_levels"] == ""
    # 缺失的默认规则会被补齐，新列默认空数组。
    assert rows["degree"]["education_levels"] == "[]"
    assert rows["other"]["education_levels"] == "[]"
    assert "number_sequences" not in tables


def test_migrates_v2_database_to_latest_and_drops_sequence_table(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data" / "certificates.db"
    db_path.parent.mkdir(parents=True)

    # 手工构造一个 schema v2 的数据库，含编号规则、流水表与业务数据。
    with Database(db_path).transaction() as connection:
        connection.execute(
            """
            CREATE TABLE schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('schema_version', '2')"
        )
        connection.execute(
            """
            CREATE TABLE numbering_rules (
                rule_key TEXT PRIMARY KEY,
                rule_name TEXT NOT NULL,
                fixed_text_1 TEXT NOT NULL DEFAULT '',
                fixed_text_2 TEXT NOT NULL DEFAULT '',
                fixed_text_3 TEXT NOT NULL DEFAULT '',
                fixed_text_4 TEXT NOT NULL DEFAULT '',
                sequence_width INTEGER NOT NULL DEFAULT 4,
                enabled INTEGER NOT NULL DEFAULT 1,
                education_levels TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            INSERT INTO numbering_rules (
                rule_key, rule_name, fixed_text_1, sequence_width,
                enabled, education_levels
            ) VALUES ('graduation', '旧规则', 'GX', 6, 1, '["本科"]')
            """
        )
        connection.execute(
            """
            CREATE TABLE number_sequences (
                sequence_key TEXT PRIMARY KEY,
                next_value INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            "INSERT INTO number_sequences (sequence_key, next_value) VALUES ('graduation', 7)"
        )
        connection.execute(
            """
            CREATE TABLE certificate_records (
                id INTEGER PRIMARY KEY,
                student_no TEXT NOT NULL UNIQUE,
                name TEXT,
                class_name TEXT,
                certificate_no TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO certificate_records (student_no, name, certificate_no) "
            "VALUES ('00123', '旧学生', 'GX0006')"
        )

    assert initialize_database(db_path) == 4
    assert get_schema_version(db_path) == 4

    with Database(db_path).connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(numbering_rules)")
        }
        record = connection.execute(
            "SELECT student_no, name, certificate_no FROM certificate_records"
        ).fetchone()
        rule = connection.execute(
            "SELECT fixed_text_1, sequence_width, education_levels "
            "FROM numbering_rules WHERE rule_key = 'graduation'"
        ).fetchone()

    assert "number_sequences" not in tables
    assert "rule_name" not in columns
    assert "enabled" not in columns
    # 业务数据与规则数据完整保留。
    assert record["student_no"] == "00123"
    assert record["name"] == "旧学生"
    assert record["certificate_no"] == "GX0006"
    assert rule["fixed_text_1"] == "GX"
    assert rule["sequence_width"] == 6
    assert rule["education_levels"] == '["本科"]'


# ---------------------------------------------------------------------------
# 启动集成
# ---------------------------------------------------------------------------


def test_bootstrap_creates_database_and_is_repeatable(tmp_path: Path) -> None:
    paths = AppPaths.for_program(tmp_path / "program")

    context = bootstrap(paths)
    assert context.schema_version == 4
    assert context.database.db_path == paths.db_file
    assert paths.db_file.is_file()
    assert get_schema_version(paths.db_file) == 4

    # 重复启动不会重复插入默认记录。
    second = bootstrap(paths)
    assert second.schema_version == 4
    assert _count(paths.db_file, "numbering_rules") == 3
