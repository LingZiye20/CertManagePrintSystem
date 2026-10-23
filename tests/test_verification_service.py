"""证书核验服务测试：类型映射、状态判定、快照固定、导航与只读性。"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from app.data.db import Database
from app.services import StudentService
from app.services.errors import ValidationError
from app.services.verification_service import (
    VERIFICATION_MISSING_NUMBER,
    VERIFICATION_MISSING_RECORD,
    VERIFICATION_READ_FAILED,
    VERIFICATION_READY,
    VerificationService,
)


@pytest.fixture
def students(repository_database: Database) -> StudentService:
    return StudentService(repository_database)


@pytest.fixture
def service(repository_database: Database) -> VerificationService:
    return VerificationService(repository_database)


def _create(students: StudentService, student_no: str, **values: Any) -> int:
    data = {"student_no": student_no, "name": f"学生{student_no}", **values}
    return students.create_record(data)


def _snapshot(database: Database) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
    """核验前后对比用的业务数据快照（记录、编号流水、审计与打印记录数）。"""
    with database.connect() as connection:
        records = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM certificate_records ORDER BY id"
            ).fetchall()
        ]
        sequences = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM number_sequences ORDER BY sequence_key"
            ).fetchall()
        ]
        log_count = int(
            connection.execute("SELECT COUNT(*) AS total FROM operation_logs").fetchone()[
                "total"
            ]
        )
        print_count = int(
            connection.execute("SELECT COUNT(*) AS total FROM print_records").fetchone()[
                "total"
            ]
        )
    return records, sequences, log_count, print_count


# ---------------------------------------------------------------- 类型映射


@pytest.mark.parametrize(
    ("certificate_type", "number_field", "expected"),
    [
        ("graduation", "certificate_no", "BJ0001"),
        ("degree", "degree_certificate_no", "XW0001"),
        ("other", "other_certificate_no", "QT0001"),
    ],
)
def test_certificate_type_maps_to_own_number_field(
    service: VerificationService,
    students: StudentService,
    certificate_type: str,
    number_field: str,
    expected: str,
) -> None:
    _create(
        students,
        "S-001",
        certificate_no="BJ0001",
        degree_certificate_no="XW0001",
        other_certificate_no="QT0001",
    )

    session = service.build_session(certificate_type)

    assert session.number_field == number_field
    assert session.certificate_type == certificate_type
    entry = service.load_entry(session, 0)
    assert entry.status == VERIFICATION_READY
    assert entry.status_label == "可核对"
    assert entry.certificate_no == expected
    assert entry.total == 1
    assert entry.position == 1


def test_missing_number_reports_missing_status(
    service: VerificationService, students: StudentService
) -> None:
    _create(students, "S-001", certificate_no="BJ0001")

    session = service.build_session("degree")
    entry = service.load_entry(session, 0)

    assert entry.status == VERIFICATION_MISSING_NUMBER
    assert entry.status_label == "缺少证书编号"
    assert entry.certificate_no is None
    assert entry.student_no == "S-001"
    assert entry.name == "学生S-001"


# ---------------------------------------------------------------- 快照与导航


def test_session_ids_follow_snapshot_order_and_stay_fixed(
    service: VerificationService, students: StudentService
) -> None:
    _create(students, "S-002")
    _create(students, "S-001")
    _create(students, "S-003")

    session = service.build_session("graduation", order_by="student_no", descending=True)
    snapshot_ids = session.record_ids

    rows = service.students.list_for_print(
        {}, order_by="student_no", descending=True
    )
    assert snapshot_ids == tuple(row["id"] for row in rows)
    assert [service.load_entry(session, index).student_no for index in range(3)] == [
        "S-003",
        "S-002",
        "S-001",
    ]

    # 会话建立后的新增与删除不改变已固定的 ID 集合与顺序。
    _create(students, "S-000")
    students.delete_record(snapshot_ids[0])

    assert session.record_ids == snapshot_ids
    assert session.total == 3
    assert service.load_entry(session, 0).status == VERIFICATION_MISSING_RECORD


def test_deleted_record_keeps_position_and_navigation_continues(
    service: VerificationService, students: StudentService
) -> None:
    first = _create(students, "S-001", certificate_no="BJ0001")
    second = _create(students, "S-002", certificate_no="BJ0002")
    third = _create(students, "S-003", certificate_no="BJ0003")
    session = service.build_session("graduation")
    assert session.record_ids == (first, second, third)

    students.delete_record(second)

    entry = service.load_entry(session, 1)
    assert entry.status == VERIFICATION_MISSING_RECORD
    assert entry.status_label == "记录已删除"
    assert entry.record_id == second
    assert entry.student_no is None
    assert entry.certificate_no is None

    assert service.next_index(session, 1) == 2
    assert service.previous_index(session, 1) == 0
    assert service.load_entry(session, 2).status == VERIFICATION_READY


def test_navigation_clamps_and_reports_edges(
    service: VerificationService, students: StudentService
) -> None:
    _create(students, "S-001")
    _create(students, "S-002")
    session = service.build_session("graduation")

    assert service.previous_index(session, 0) == 0
    assert service.next_index(session, 1) == 1
    assert service.next_index(session, 0) == 1
    assert service.can_previous(session, 0) is False
    assert service.can_previous(session, 1) is True
    assert service.can_next(session, 0) is True
    assert service.can_next(session, 1) is False


def test_reload_refetches_with_same_type_filters_and_order(
    service: VerificationService, students: StudentService
) -> None:
    _create(students, "S-001", class_name="一班")
    session = service.build_session(
        "degree", {"class_name": "一班"}, order_by="student_no"
    )
    assert session.total == 1

    new_id = _create(students, "S-000", class_name="一班")
    _create(students, "S-009", class_name="二班")

    reloaded = service.reload(session)

    assert reloaded.certificate_type == "degree"
    assert reloaded.number_field == "degree_certificate_no"
    assert reloaded.filters == {"class_name": "一班"}
    assert reloaded.order_by == "student_no"
    assert reloaded.descending is False
    assert reloaded.record_ids[0] == new_id
    assert reloaded.total == 2
    # 重新载入后从第一条开始，由界面把位置重置为 0。
    assert service.load_entry(reloaded, 0).record_id == new_id


def test_empty_session_rejects_navigation(
    service: VerificationService,
) -> None:
    session = service.build_session("graduation")

    assert session.total == 0
    with pytest.raises(ValidationError):
        service.load_entry(session, 0)
    with pytest.raises(ValidationError):
        service.next_index(session, 0)
    with pytest.raises(ValidationError):
        service.can_next(session, 0)


# ---------------------------------------------------------------- 异常与校验


def test_read_failure_is_not_reported_as_deleted(
    service: VerificationService,
    students: StudentService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(students, "S-001")
    session = service.build_session("graduation")

    def _boom(record_id: int) -> dict[str, Any] | None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(service.records, "get", _boom)

    entry = service.load_entry(session, 0)

    assert entry.status == VERIFICATION_READ_FAILED
    assert entry.status_label == "读取失败"
    assert entry.status != VERIFICATION_MISSING_RECORD
    assert "database is locked" in (entry.error or "")


def test_unexpected_read_error_is_also_reported_as_failure(
    service: VerificationService,
    students: StudentService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(students, "S-001")
    session = service.build_session("graduation")

    def _boom(record_id: int) -> dict[str, Any] | None:
        raise RuntimeError("意外错误")

    monkeypatch.setattr(service.records, "get", _boom)

    entry = service.load_entry(session, 0)
    assert entry.status == VERIFICATION_READ_FAILED
    assert "意外错误" in (entry.error or "")


@pytest.mark.parametrize("index", [True, False, 1.0, -1, 2, "0"])
def test_invalid_index_is_rejected(
    service: VerificationService, students: StudentService, index: Any
) -> None:
    _create(students, "S-001")
    session = service.build_session("graduation")

    with pytest.raises(ValidationError):
        service.load_entry(session, index)
    with pytest.raises(ValidationError):
        service.next_index(session, index)


def test_invalid_certificate_type_is_rejected(service: VerificationService) -> None:
    with pytest.raises(ValidationError):
        service.build_session("bachelor")


def test_invalid_order_by_is_rejected(service: VerificationService) -> None:
    with pytest.raises(ValidationError):
        service.build_session("graduation", order_by="unknown_column")


def test_invalid_filter_field_is_rejected(service: VerificationService) -> None:
    with pytest.raises(ValidationError):
        service.build_session("graduation", {"unknown_field": "x"})


# ---------------------------------------------------------------- 当前值


def test_entry_shows_current_database_values(
    service: VerificationService, students: StudentService
) -> None:
    record_id = _create(students, "S-001", certificate_no="BJ0001")
    session = service.build_session("graduation")

    students.update_record(
        record_id, {"name": "李四", "certificate_no": "BJ9999", "class_name": "二班"}
    )

    entry = service.load_entry(session, 0)
    assert entry.name == "李四"
    assert entry.class_name == "二班"
    assert entry.certificate_no == "BJ9999"
    assert entry.status == VERIFICATION_READY


# ---------------------------------------------------------------- 只读性


def test_verification_never_writes_business_data_or_audit(
    service: VerificationService, students: StudentService
) -> None:
    _create(students, "S-001", certificate_no="BJ0001")
    _create(students, "S-002", degree_certificate_no="XW0001")
    before = _snapshot(service.database)

    for certificate_type in ("graduation", "degree", "other"):
        session = service.build_session(certificate_type)
        for index in range(session.total):
            service.load_entry(session, index)
            service.can_next(session, index)
            service.next_index(session, index)
        service.reload(session)

    after = _snapshot(service.database)

    assert after == before
    assert after[2] == before[2], "核验不应新增 operation_logs"
    assert after[0] == before[0], "核验不应修改 certificate_records"
    assert after[1] == before[1], "核验不应改变编号流水"
    assert after[3] == 0, "核验不应产生打印记录"


def test_verification_does_not_write_verification_results(
    service: VerificationService, students: StudentService
) -> None:
    _create(students, "S-001")
    session = service.build_session("graduation")
    service.load_entry(session, 0)

    with service.database.connect() as connection:
        operation_types = {
            row["operation_type"]
            for row in connection.execute(
                "SELECT operation_type FROM operation_logs"
            ).fetchall()
        }

    assert all(
        "verif" not in str(operation_type).lower()
        for operation_type in operation_types
    )
