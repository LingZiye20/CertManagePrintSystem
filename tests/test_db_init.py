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
    "number_sequences",
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

    assert version == SCHEMA_VERSION == 1
    assert db_path.is_file()
    assert get_schema_version(db_path) == 1
    assert _schema_object_names(db_path, "table") == EXPECTED_TABLES
    assert _schema_object_names(db_path, "index") == EXPECTED_INDEXES

    with Database(db_path).connect() as connection:
        meta = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert meta["value"] == "1"

        rules = connection.execute(
            """
            SELECT rule_key, rule_name, fixed_text_1, fixed_text_2, fixed_text_3,
                   fixed_text_4, sequence_width, enabled
            FROM numbering_rules ORDER BY rule_key
            """
        ).fetchall()
        sequences = connection.execute(
            "SELECT sequence_key, next_value FROM number_sequences ORDER BY sequence_key"
        ).fetchall()

    assert [row["rule_key"] for row in rules] == ["degree", "graduation", "other"]
    assert [row["rule_name"] for row in rules] == ["学位证书", "毕结业证书", "其他证书"]
    for row in rules:
        assert row["fixed_text_1"] == ""
        assert row["fixed_text_2"] == ""
        assert row["fixed_text_3"] == ""
        assert row["fixed_text_4"] == ""
        assert row["sequence_width"] == 4
        assert row["enabled"] == 1

    assert [row["sequence_key"] for row in sequences] == ["degree", "graduation", "other"]
    assert all(row["next_value"] == 1 for row in sequences)

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
            SET sequence_width = 6, fixed_text_1 = 'GX', enabled = 0
            WHERE rule_key = 'graduation'
            """
        )
        connection.execute(
            "UPDATE number_sequences SET next_value = 42 WHERE sequence_key = 'degree'"
        )
        connection.execute("DELETE FROM numbering_rules WHERE rule_key = 'other'")
        connection.execute("DELETE FROM number_sequences WHERE sequence_key = 'other'")
        connection.execute(
            "INSERT INTO certificate_records (student_no) VALUES ('00123')"
        )

    initialize_database(db_path)
    initialize_database(db_path)

    assert get_schema_version(db_path) == 1
    assert _count(db_path, "numbering_rules") == 3
    assert _count(db_path, "number_sequences") == 3
    assert _count(db_path, "certificate_records") == 1

    with Database(db_path).connect() as connection:
        graduation = connection.execute(
            "SELECT * FROM numbering_rules WHERE rule_key = 'graduation'"
        ).fetchone()
        degree_sequence = connection.execute(
            "SELECT next_value FROM number_sequences WHERE sequence_key = 'degree'"
        ).fetchone()
        other = connection.execute(
            "SELECT * FROM numbering_rules WHERE rule_key = 'other'"
        ).fetchone()
        other_sequence = connection.execute(
            "SELECT next_value FROM number_sequences WHERE sequence_key = 'other'"
        ).fetchone()
        student_no = connection.execute(
            "SELECT student_no FROM certificate_records"
        ).fetchone()

    # 用户修改必须保留。
    assert graduation["sequence_width"] == 6
    assert graduation["fixed_text_1"] == "GX"
    assert graduation["enabled"] == 0
    assert degree_sequence["next_value"] == 42
    assert student_no["student_no"] == "00123"
    # 被删除的默认记录会被补齐为初始值，不重复、不覆盖其他规则。
    assert other["sequence_width"] == 4
    assert other["fixed_text_1"] == ""
    assert other["enabled"] == 1
    assert other_sequence["next_value"] == 1


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
# 启动集成
# ---------------------------------------------------------------------------


def test_bootstrap_creates_database_and_is_repeatable(tmp_path: Path) -> None:
    paths = AppPaths.for_program(tmp_path / "program")

    context = bootstrap(paths)
    assert context.schema_version == 1
    assert context.database.db_path == paths.db_file
    assert paths.db_file.is_file()
    assert get_schema_version(paths.db_file) == 1

    # 重复启动不会重复插入默认记录。
    second = bootstrap(paths)
    assert second.schema_version == 1
    assert _count(paths.db_file, "numbering_rules") == 3
    assert _count(paths.db_file, "number_sequences") == 3
