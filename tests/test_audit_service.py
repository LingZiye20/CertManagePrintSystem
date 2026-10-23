"""操作审计查询 Service 测试。"""

from __future__ import annotations

import sqlite3

import pytest

from app.data.db import Database
from app.repositories import OperationLogRepository
from app.services import AuditService, DataAccessError, ValidationError


@pytest.fixture
def logs(repository_database: Database) -> OperationLogRepository:
    return OperationLogRepository(repository_database)


@pytest.fixture
def service(repository_database: Database, logs: OperationLogRepository) -> AuditService:
    return AuditService(repository_database, logs=logs)


def test_list_recent_returns_newest_first(service: AuditService, logs: OperationLogRepository) -> None:
    logs.create("student_create", "certificate_record", "1", "新增学生 1", "success")
    logs.create("student_update", "certificate_record", "1", "更新学生 1", "success")

    entries = service.list_recent()

    assert [entry.operation_type for entry in entries] == [
        "student_update",
        "student_create",
    ]
    assert entries[0].summary == "更新学生 1"
    assert entries[0].result == "success"
    assert entries[0].target_id == "1"


def test_list_recent_filters_by_operation_type(service: AuditService, logs: OperationLogRepository) -> None:
    logs.create("student_create", "certificate_record", "1", "新增", "success")
    logs.create("settings_update", "app_setting", "x", "设置", "success")

    entries = service.list_recent(operation_type="settings_update")

    assert len(entries) == 1
    assert entries[0].operation_type == "settings_update"


def test_list_recent_respects_limit(service: AuditService, logs: OperationLogRepository) -> None:
    for index in range(5):
        logs.create("student_create", "certificate_record", str(index), f"新增 {index}", "success")

    assert len(service.list_recent(limit=2)) == 2


@pytest.mark.parametrize("limit", [0, -1, 1001, "10", True])
def test_invalid_limit_rejected(service: AuditService, limit: object) -> None:
    with pytest.raises(ValidationError):
        service.list_recent(limit=limit)  # type: ignore[arg-type]


def test_operation_types_are_sorted_and_distinct(
    service: AuditService, logs: OperationLogRepository
) -> None:
    logs.create("student_update", "certificate_record", "1", "x", "success")
    logs.create("student_create", "certificate_record", "1", "x", "success")
    logs.create("student_create", "certificate_record", "2", "x", "success")

    assert service.operation_types() == ("student_create", "student_update")


def test_query_is_read_only(service: AuditService, logs: OperationLogRepository) -> None:
    logs.create("student_create", "certificate_record", "1", "新增", "success")
    before = logs.count_logs()

    service.list_recent()
    service.operation_types()

    assert logs.count_logs() == before


def test_database_error_is_wrapped(
    service: AuditService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> list[dict[str, object]]:
        raise sqlite3.Error("boom")

    monkeypatch.setattr(service.logs, "list_logs", broken)

    with pytest.raises(DataAccessError):
        service.list_recent()
