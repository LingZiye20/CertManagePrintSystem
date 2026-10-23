"""数据库连接与事务行为测试：外键开启、提交、回滚与连接释放。

全部使用 ``tmp_path`` 下的临时数据库，不触碰真实业务数据。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.data.db import Database, initialize_database
from app.data.db.connection import BUSY_TIMEOUT_MS


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db_path = tmp_path / "data" / "certificates.db"
    initialize_database(db_path)
    return Database(db_path)


def test_connection_enables_foreign_keys_and_busy_timeout(database: Database) -> None:
    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS


def test_transaction_commits_on_success(database: Database) -> None:
    with database.transaction() as connection:
        connection.execute(
            "UPDATE number_sequences SET next_value = ? WHERE sequence_key = 'degree'",
            (7,),
        )

    with database.connect() as connection:
        row = connection.execute(
            "SELECT next_value FROM number_sequences WHERE sequence_key = 'degree'"
        ).fetchone()
    assert row["next_value"] == 7


def test_transaction_rolls_back_on_exception(database: Database) -> None:
    with pytest.raises(RuntimeError, match="模拟失败"):
        with database.transaction() as connection:
            connection.execute(
                "UPDATE number_sequences SET next_value = ? WHERE sequence_key = 'degree'",
                (99,),
            )
            raise RuntimeError("模拟失败")

    with database.connect() as connection:
        row = connection.execute(
            "SELECT next_value FROM number_sequences WHERE sequence_key = 'degree'"
        ).fetchone()
    assert row["next_value"] == 1


def test_transaction_rolls_back_inserted_rows(database: Database) -> None:
    with pytest.raises(RuntimeError):
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO certificate_records (student_no) VALUES (?)",
                ("00123",),
            )
            raise RuntimeError("写入后失败")

    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS total FROM certificate_records"
        ).fetchone()["total"]
    assert count == 0


def test_connect_context_closes_connection(database: Database) -> None:
    with database.connect() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_transaction_context_closes_connection(database: Database) -> None:
    with database.transaction() as connection:
        connection.execute("SELECT 1")

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
