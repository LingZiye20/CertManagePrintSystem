"""Repository 事务边界与失败路径测试。"""

from __future__ import annotations

import sqlite3

import pytest

from app.data.db import Database
from app.repositories import CertificateRecordRepository, OperationLogRepository


@pytest.fixture
def record_repo(repository_database: Database) -> CertificateRecordRepository:
    return CertificateRecordRepository(repository_database)


@pytest.fixture
def log_repo(repository_database: Database) -> OperationLogRepository:
    return OperationLogRepository(repository_database)


def test_caller_transaction_rolls_back_all_writes(
    repository_database: Database,
    record_repo: CertificateRecordRepository,
    log_repo: OperationLogRepository,
) -> None:
    with pytest.raises(RuntimeError, match="模拟业务失败"):
        with repository_database.transaction() as connection:
            record_id = record_repo.create({"student_no": "00123"}, connection=connection)
            log_repo.create(
                "import",
                "import_batch",
                "1",
                "批次开始",
                "running",
                connection=connection,
            )
            # 同一事务内可以读到未提交数据。
            assert record_repo.get(record_id, connection=connection) is not None
            raise RuntimeError("模拟业务失败")

    assert record_repo.count_records() == 0
    assert log_repo.count_logs() == 0


def test_caller_transaction_commits(
    repository_database: Database,
    record_repo: CertificateRecordRepository,
) -> None:
    with repository_database.transaction() as connection:
        record_repo.create({"student_no": "00123"}, connection=connection)
        # Repository 不得提交调用方事务。
        assert connection.in_transaction is True

    assert record_repo.count_records() == 1


def test_repository_does_not_close_caller_connection(
    repository_database: Database,
    record_repo: CertificateRecordRepository,
) -> None:
    with repository_database.transaction() as connection:
        record_repo.create({"student_no": "00123"}, connection=connection)
        assert connection.execute("SELECT 1").fetchone()[0] == 1
        assert connection.in_transaction is True


def test_write_requires_explicit_caller_transaction(
    repository_database: Database,
    record_repo: CertificateRecordRepository,
) -> None:
    with repository_database.connect() as connection:
        with pytest.raises(ValueError):
            record_repo.create({"student_no": "00123"}, connection=connection)

    assert record_repo.count_records() == 0


def test_constraint_failure_propagates_and_rolls_back(
    repository_database: Database,
    record_repo: CertificateRecordRepository,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with repository_database.transaction() as connection:
            record_repo.create({"student_no": "00123"}, connection=connection)
            record_repo.create({"student_no": "00123"}, connection=connection)

    assert record_repo.count_records() == 0


def test_repository_managed_write_rolls_back_on_failure(
    record_repo: CertificateRecordRepository,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        record_repo.create({"student_no": "  "})

    assert record_repo.count_records() == 0


def test_read_does_not_require_transaction(
    repository_database: Database,
    record_repo: CertificateRecordRepository,
) -> None:
    record_id = record_repo.create({"student_no": "00123"})

    with repository_database.connect() as connection:
        row = record_repo.get(record_id, connection=connection)
    assert row["student_no"] == "00123"
