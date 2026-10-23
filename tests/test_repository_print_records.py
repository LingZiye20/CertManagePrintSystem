"""PrintRecordRepository 测试：快照保留、筛选与外键行为。"""

from __future__ import annotations

import sqlite3

import pytest

from app.data.db import Database
from app.repositories import (
    CertificateRecordRepository,
    CertificateTemplateRepository,
    PrintRecordRepository,
)

EARLY = "2026-10-20T08:00:00.000Z"
LATE = "2026-10-22T08:00:00.000Z"


@pytest.fixture
def repo(repository_database: Database) -> PrintRecordRepository:
    return PrintRecordRepository(repository_database)


@pytest.fixture
def record_repo(repository_database: Database) -> CertificateRecordRepository:
    return CertificateRecordRepository(repository_database)


@pytest.fixture
def template_repo(repository_database: Database) -> CertificateTemplateRepository:
    return CertificateTemplateRepository(repository_database)


def test_create_and_get(repo: PrintRecordRepository, record_repo: CertificateRecordRepository) -> None:
    record_id = record_repo.create({"student_no": "S-1", "certificate_no": "BJ001"})

    print_id = repo.create(
        record_id,
        "graduation",
        "BJ001",
        "submitted",
        printer_name="打印机甲",
        submitted_at=EARLY,
    )

    row = repo.get(print_id)
    assert row["id"] == print_id
    assert row["certificate_record_id"] == record_id
    assert row["certificate_type"] == "graduation"
    assert row["certificate_no_snapshot"] == "BJ001"
    assert row["printer_name"] == "打印机甲"
    assert row["result"] == "submitted"
    assert row["submitted_at"] == EARLY
    assert repo.get(999999) is None


def test_snapshot_survives_record_deletion(
    repo: PrintRecordRepository, record_repo: CertificateRecordRepository
) -> None:
    record_id = record_repo.create({"student_no": "S-1", "certificate_no": "BJ001"})
    print_id = repo.create(record_id, "graduation", "BJ001", "submitted")

    record_repo.delete(record_id)

    row = repo.get(print_id)
    assert row["certificate_record_id"] is None
    assert row["certificate_no_snapshot"] == "BJ001"


def test_template_reference_is_set_null_on_delete(
    repo: PrintRecordRepository,
    record_repo: CertificateRecordRepository,
    template_repo: CertificateTemplateRepository,
) -> None:
    record_id = record_repo.create({"student_no": "S-1"})
    template_id = template_repo.create(
        name="模板",
        certificate_type="graduation",
        canvas_width_mm=210.0,
        canvas_height_mm=297.0,
        template_json="{}",
    )
    print_id = repo.create(
        record_id, "graduation", "BJ001", "submitted", template_id=template_id
    )

    template_repo.delete(template_id)

    assert repo.get(print_id)["template_id"] is None


def test_filters_by_record_type_and_time(
    repo: PrintRecordRepository, record_repo: CertificateRecordRepository
) -> None:
    first_record = record_repo.create({"student_no": "S-1"})
    second_record = record_repo.create({"student_no": "S-2"})

    repo.create(first_record, "graduation", "BJ001", "submitted", submitted_at=EARLY)
    repo.create(first_record, "graduation", "XW001", "submitted", submitted_at=LATE)
    repo.create(second_record, "degree", "XW002", "failed", submitted_at=EARLY)

    assert [row["certificate_type"] for row in repo.list_by_certificate_record(first_record)] == [
        "graduation",
        "graduation",
    ]
    assert repo.count_records(certificate_record_id=first_record) == 2
    assert repo.count_records(certificate_record_id=second_record) == 1
    assert repo.count_records(certificate_type="degree") == 1

    ranged = repo.list_records(start_time=LATE, end_time=LATE)
    assert [row["certificate_no_snapshot"] for row in ranged] == ["XW001"]
    assert repo.count_records(start_time=LATE, end_time=LATE) == 1

    assert repo.count_records() == 3
    # 默认最新提交在前。
    assert [row["id"] for row in repo.list_records(limit=1)] == [ranged[0]["id"]]


def test_foreign_key_and_snapshot_constraints_propagate(repo: PrintRecordRepository) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(999999, "graduation", "BJ001", "submitted")
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(None, "graduation", "  ", "submitted")
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(None, "unknown", "BJ001", "submitted")


def test_arbitrary_result_text_is_allowed(repo: PrintRecordRepository) -> None:
    print_id = repo.create(None, "other", "QT001", "待业务确认的提交结果")

    assert repo.get(print_id)["result"] == "待业务确认的提交结果"
