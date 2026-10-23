"""ImportBatchRepository 测试：批次创建、更新、筛选与约束。"""

from __future__ import annotations

import sqlite3

import pytest

from app.data.db import Database
from app.repositories import ImportBatchRepository

EARLY = "2026-10-20T08:00:00.000Z"
MIDDLE = "2026-10-21T08:00:00.000Z"
LATE = "2026-10-22T08:00:00.000Z"


@pytest.fixture
def repo(repository_database: Database) -> ImportBatchRepository:
    return ImportBatchRepository(repository_database)


def test_create_and_get(repo: ImportBatchRepository) -> None:
    batch_id = repo.create("students.xlsx", "running", total_rows=3, started_at=EARLY)

    row = repo.get(batch_id)
    assert row["id"] == batch_id
    assert row["file_name"] == "students.xlsx"
    assert row["status"] == "running"
    assert row["total_rows"] == 3
    assert row["started_at"] == EARLY
    assert row["finished_at"] is None
    assert row["error_summary"] is None
    assert repo.get(999999) is None


def test_update_completion_fields(repo: ImportBatchRepository) -> None:
    batch_id = repo.create("students.xlsx", "running")

    assert (
        repo.update(
            batch_id,
            {
                "finished_at": LATE,
                "status": "success",
                "total_rows": 10,
                "error_summary": "无",
            },
        )
        is True
    )

    row = repo.get(batch_id)
    assert row["finished_at"] == LATE
    assert row["status"] == "success"
    assert row["total_rows"] == 10
    assert row["error_summary"] == "无"


def test_update_restrictions(repo: ImportBatchRepository) -> None:
    batch_id = repo.create("students.xlsx", "running")

    assert repo.update(999999, {"status": "success"}) is False
    assert repo.update(batch_id, {}) is False
    with pytest.raises(ValueError):
        repo.update(batch_id, {"file_name": "other.xlsx"})
    with pytest.raises(ValueError):
        repo.update(batch_id, {"started_at": LATE})


def test_list_and_count_filters(repo: ImportBatchRepository) -> None:
    repo.create("early.xlsx", "success", total_rows=1, started_at=EARLY)
    repo.create("middle.xlsx", "running", total_rows=2, started_at=MIDDLE)
    repo.create("late.xlsx", "failed", total_rows=3, started_at=LATE)

    assert [row["file_name"] for row in repo.list_batches()] == [
        "late.xlsx",
        "middle.xlsx",
        "early.xlsx",
    ]
    assert [row["file_name"] for row in repo.list_batches(start_time=MIDDLE, end_time=LATE)] == [
        "late.xlsx",
        "middle.xlsx",
    ]
    assert [row["file_name"] for row in repo.list_batches(status="running")] == ["middle.xlsx"]
    assert repo.count_batches() == 3
    assert repo.count_batches(start_time=MIDDLE, end_time=LATE) == 2
    assert repo.count_batches(status="running") == 1
    assert repo.list_batches(status="unknown") == []


def test_invalid_values_propagate(repo: ImportBatchRepository) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        repo.create("students.xlsx", "running", total_rows=-1)

    batch_id = repo.create("students.xlsx", "running", total_rows=2)
    with pytest.raises(sqlite3.IntegrityError):
        repo.update(batch_id, {"total_rows": -1})

    assert repo.get(batch_id)["total_rows"] == 2


def test_arbitrary_status_text_is_allowed(repo: ImportBatchRepository) -> None:
    batch_id = repo.create("students.xlsx", "待业务确认的状态")

    assert repo.get(batch_id)["status"] == "待业务确认的状态"


def test_pagination_validation(repo: ImportBatchRepository) -> None:
    with pytest.raises(ValueError):
        repo.list_batches(limit=0)
    with pytest.raises(ValueError):
        repo.list_batches(offset=-1)
