"""StudentService 业务规则测试：校验、唯一性、事务、审计与分页。"""

from __future__ import annotations

import sqlite3

import pytest

from app.repositories import PrintRecordRepository
from app.services import (
    AuditWriteError,
    DataAccessError,
    DeleteBlockedError,
    DuplicateStudentNoError,
    DuplicateValueError,
    RecordNotFoundError,
    StudentQuery,
    StudentService,
    ValidationError,
)


def _student_no_sequence(count: int) -> list[str]:
    return [f"S-{index:02d}" for index in range(count)]


# ---------------------------------------------------------------- 学号校验


def test_create_trims_and_preserves_leading_zero(student_service: StudentService) -> None:
    record_id = student_service.create_record({"student_no": "  0020240001  ", "name": " 张三 "})

    row = student_service.records.get(record_id)
    assert row is not None
    assert row["student_no"] == "0020240001"
    assert row["name"] == "张三"


@pytest.mark.parametrize("student_no", ["", "   ", None])
def test_create_rejects_blank_student_no(
    student_service: StudentService, student_no: str | None
) -> None:
    with pytest.raises(ValidationError):
        student_service.create_record({"student_no": student_no})
    assert student_service.records.count_records() == 0


def test_create_rejects_non_text_values(student_service: StudentService) -> None:
    with pytest.raises(ValidationError):
        student_service.create_record({"student_no": 20240001})
    with pytest.raises(ValidationError):
        student_service.create_record({"student_no": "S-1", "name": 123})


def test_create_rejects_unknown_fields(student_service: StudentService) -> None:
    with pytest.raises(ValidationError):
        student_service.create_record({"student_no": "S-1", "unknown_field": "x"})
    assert student_service.records.count_records() == 0


def test_normalize_text_field_trims_and_returns_none_for_blank() -> None:
    assert StudentService.normalize_text_field("name", " 张三 ") == "张三"
    assert StudentService.normalize_text_field("name", "   ") is None
    assert StudentService.normalize_text_field("name", None) is None


def test_normalize_text_field_rejects_non_text() -> None:
    with pytest.raises(ValidationError):
        StudentService.normalize_text_field("student_no", 123)


@pytest.mark.parametrize(
    "birth_date", ["20260809", "2026.8.9", "2024/01/01", "2024-2-1", "2024-02-30", "abc"]
)
def test_arbitrary_date_text_accepted_and_stored_verbatim(
    student_service: StudentService, birth_date: str
) -> None:
    record_id = student_service.create_record(
        {"student_no": "S-1", "birth_date": birth_date}
    )
    assert student_service.records.get(record_id)["birth_date"] == birth_date


def test_update_accepts_arbitrary_date_text_verbatim(
    student_service: StudentService,
) -> None:
    record_id = student_service.create_record(
        {"student_no": "S-1", "enrollment_date": "2024-09-01"}
    )

    student_service.update_record(record_id, {"graduation_date": "20260809"})

    assert student_service.records.get(record_id)["graduation_date"] == "20260809"


def test_non_text_date_still_rejected(student_service: StudentService) -> None:
    with pytest.raises(ValidationError):
        student_service.create_record({"student_no": "S-1", "birth_date": 20260809})


def test_valid_and_empty_dates(student_service: StudentService) -> None:
    record_id = student_service.create_record(
        {"student_no": "S-1", "birth_date": "2024-02-29", "enrollment_date": ""}
    )
    row = student_service.records.get(record_id)
    assert row["birth_date"] == "2024-02-29"
    assert row["enrollment_date"] is None


def test_blank_optional_certificate_number_is_stored_as_null(
    student_service: StudentService,
) -> None:
    record_id = student_service.create_record(
        {"student_no": "S-1", "certificate_no": "   "}
    )
    assert student_service.records.get(record_id)["certificate_no"] is None


# ---------------------------------------------------------------- 唯一性


def test_duplicate_student_no_rejected_with_friendly_error(
    student_service: StudentService,
) -> None:
    student_service.create_record({"student_no": "0020240001", "name": "张三"})

    with pytest.raises(DuplicateStudentNoError) as excinfo:
        student_service.create_record({"student_no": "0020240001", "name": "李四"})

    assert "0020240001" in str(excinfo.value)
    assert student_service.records.count_records() == 1
    assert student_service.records.get_by_student_no("0020240001")["name"] == "张三"


def test_update_allows_keeping_own_student_no(student_service: StudentService) -> None:
    record_id = student_service.create_record({"student_no": "00123", "name": "甲"})

    student_service.update_record(
        record_id, {"student_no": "00123", "name": "乙", "certificate_no": "NO-1"}
    )

    row = student_service.records.get(record_id)
    assert row["student_no"] == "00123"
    assert row["name"] == "乙"
    assert row["certificate_no"] == "NO-1"


def test_update_rejects_changing_student_no(student_service: StudentService) -> None:
    record_id = student_service.create_record({"student_no": "00123", "name": "甲"})

    with pytest.raises(ValidationError) as excinfo:
        student_service.update_record(
            record_id, {"student_no": "00124", "name": "乙"}
        )

    message = str(excinfo.value)
    assert "学号" in message and "不可修改" in message
    row = student_service.records.get(record_id)
    assert row["student_no"] == "00123"
    assert row["name"] == "甲"

    failures = [
        log
        for log in student_service.logs.list_logs(operation_type="student_update")
        if log["result"] == "failure"
    ]
    assert failures
    assert "不可修改" in (failures[0]["error_message"] or "")


def test_update_rejects_taking_another_records_student_no(
    student_service: StudentService,
) -> None:
    first = student_service.create_record({"student_no": "S-1"})
    second = student_service.create_record({"student_no": "S-2"})

    with pytest.raises(ValidationError):
        student_service.update_record(second, {"student_no": "S-1"})

    assert student_service.records.get(second)["student_no"] == "S-2"
    assert student_service.records.get(first)["student_no"] == "S-1"


def test_database_unique_constraint_converted_without_covering_data(
    student_service: StudentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    student_service.create_record({"student_no": "S-1"})
    # 模拟并发窗口：预检查看不到冲突，最终由数据库唯一约束兜底。
    monkeypatch.setattr(
        student_service.records, "get_by_student_no", lambda *args, **kwargs: None
    )

    with pytest.raises(DuplicateValueError):
        student_service.create_record({"student_no": "S-1"})

    assert student_service.records.count_records() == 1


def test_update_database_unique_constraint_converted(
    student_service: StudentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    student_service.create_record({"student_no": "S-1", "certificate_no": "NO-1"})
    second = student_service.create_record({"student_no": "S-2", "certificate_no": "NO-2"})
    monkeypatch.setattr(
        student_service.records, "get_by_student_no", lambda *args, **kwargs: None
    )

    with pytest.raises(DuplicateValueError):
        student_service.update_record(second, {"certificate_no": "NO-1"})

    row = student_service.records.get(second)
    assert row["student_no"] == "S-2"
    assert row["certificate_no"] == "NO-2"


# ---------------------------------------------------------------- 增改删失败


def test_update_missing_record(student_service: StudentService) -> None:
    with pytest.raises(RecordNotFoundError):
        student_service.update_record(999999, {"name": "不存在"})


def test_update_requires_at_least_one_field(student_service: StudentService) -> None:
    record_id = student_service.create_record({"student_no": "S-1"})
    with pytest.raises(ValidationError):
        student_service.update_record(record_id, {})


def test_update_rejects_blank_student_no(student_service: StudentService) -> None:
    record_id = student_service.create_record({"student_no": "S-1"})
    with pytest.raises(ValidationError):
        student_service.update_record(record_id, {"student_no": "  "})
    assert student_service.records.get(record_id)["student_no"] == "S-1"


def test_delete_success_and_missing(student_service: StudentService) -> None:
    record_id = student_service.create_record({"student_no": "S-1", "name": "张三"})

    student_service.delete_record(record_id)
    assert student_service.records.count_records() == 0

    with pytest.raises(RecordNotFoundError):
        student_service.delete_record(record_id)


def test_delete_keeps_print_record_snapshot(student_service: StudentService) -> None:
    print_repo = PrintRecordRepository(student_service.database)
    record_id = student_service.create_record(
        {"student_no": "S-1", "certificate_no": "NO-1"}
    )
    print_id = print_repo.create(record_id, "graduation", "NO-1", "success")

    student_service.delete_record(record_id)

    row = print_repo.get(print_id)
    assert row is not None
    assert row["certificate_record_id"] is None
    assert row["certificate_no_snapshot"] == "NO-1"


def test_delete_blocked_by_database_constraint(
    student_service: StudentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    record_id = student_service.create_record({"student_no": "S-1"})

    def _blocked(*args, **kwargs):
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(student_service.records, "delete", _blocked)

    with pytest.raises(DeleteBlockedError):
        student_service.delete_record(record_id)
    assert student_service.records.get(record_id) is not None


# ---------------------------------------------------------------- 查询


def test_search_pagination_sort_and_filter(student_service: StudentService) -> None:
    for student_no in _student_no_sequence(25):
        student_service.create_record({"student_no": student_no, "name": "同名"})

    first = student_service.search(StudentQuery(page=1, page_size=20))
    assert first.total == 25
    assert first.page_count == 2
    assert len(first.records) == 20
    assert [row["id"] for row in first.records] == sorted(row["id"] for row in first.records)

    second = student_service.search(StudentQuery(page=2, page_size=20))
    assert second.page == 2
    assert len(second.records) == 5
    assert not {row["id"] for row in first.records} & {row["id"] for row in second.records}

    filtered = student_service.search(
        StudentQuery(filters={"student_no": "S-07"}, page=1, page_size=20)
    )
    assert filtered.total == 1
    assert filtered.records[0]["student_no"] == "S-07"

    descending = student_service.search(
        StudentQuery(order_by="student_no", descending=True, page=1, page_size=20)
    )
    assert descending.records[0]["student_no"] == "S-24"


def test_search_out_of_range_page_clamps(student_service: StudentService) -> None:
    for student_no in _student_no_sequence(5):
        student_service.create_record({"student_no": student_no})

    result = student_service.search(StudentQuery(page=99, page_size=20))
    assert result.page == 1
    assert result.page_count == 1
    assert len(result.records) == 5


def test_search_after_deletion_recomputes_page_count(student_service: StudentService) -> None:
    ids = [
        student_service.create_record({"student_no": student_no})
        for student_no in _student_no_sequence(21)
    ]
    assert student_service.search(StudentQuery(page=2, page_size=20)).page == 2

    for record_id in ids[:2]:
        student_service.delete_record(record_id)

    result = student_service.search(StudentQuery(page=2, page_size=20))
    assert result.total == 19
    assert result.page == 1
    assert result.page_count == 1


@pytest.mark.parametrize(
    ("page", "page_size"),
    [(0, 20), (-1, 20), (1, 30), (1, 0)],
)
def test_search_rejects_invalid_paging(
    student_service: StudentService, page: int, page_size: int
) -> None:
    with pytest.raises(ValidationError):
        student_service.search(StudentQuery(page=page, page_size=page_size))


@pytest.mark.parametrize("page_size", [20, 50, 100])
def test_search_accepts_valid_page_sizes(
    student_service: StudentService, page_size: int
) -> None:
    result = student_service.search(StudentQuery(page=1, page_size=page_size))
    assert result.page_size == page_size


@pytest.mark.parametrize("page_size", [20.0, 50.0, True, "20", None])
def test_search_rejects_non_integer_page_size(
    student_service: StudentService, page_size: object
) -> None:
    with pytest.raises(ValidationError):
        student_service.search(StudentQuery(page=1, page_size=page_size))  # type: ignore[arg-type]


def test_search_rejects_invalid_sort_and_filter_fields(
    student_service: StudentService,
) -> None:
    with pytest.raises(ValidationError):
        student_service.search(StudentQuery(order_by="name; DROP TABLE certificate_records"))
    with pytest.raises(ValidationError):
        student_service.search(StudentQuery(filters={"id_number": "x"}))


def test_search_empty_result(student_service: StudentService) -> None:
    result = student_service.search(StudentQuery(filters={"name": "不存在"}))
    assert result.total == 0
    assert result.page == 1
    assert result.page_count == 1
    assert result.records == []


def test_search_database_error_translated(
    student_service: StudentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(student_service.records, "count_records", _boom)

    with pytest.raises(DataAccessError):
        student_service.search()


# ---------------------------------------------------------------- 打印取数


def test_list_for_print_returns_all_matching_records(
    student_service: StudentService,
) -> None:
    for index in range(25):
        student_service.create_record(
            {"student_no": f"S-{index:02d}", "name": "打印测试"}
        )
    student_service.create_record({"student_no": "X-1", "name": "其他"})

    rows = student_service.list_for_print(
        filters={"name": "打印"}, order_by="student_no"
    )

    assert len(rows) == 25
    assert rows[0]["student_no"] == "S-00"
    assert rows[-1]["student_no"] == "S-24"

    descending = student_service.list_for_print(
        filters={"name": "打印"}, order_by="student_no", descending=True
    )
    assert descending[0]["student_no"] == "S-24"


def test_list_for_print_rejects_invalid_sort_and_filter(
    student_service: StudentService,
) -> None:
    with pytest.raises(ValidationError):
        student_service.list_for_print(order_by="unknown")
    with pytest.raises(ValidationError):
        student_service.list_for_print(filters={"id_number": "x"})


def test_records_by_ids_preserves_requested_order(
    student_service: StudentService,
) -> None:
    first = student_service.create_record({"student_no": "S-1"})
    second = student_service.create_record({"student_no": "S-2"})

    rows = student_service.records_by_ids([second, first, 999999])

    assert [row["id"] for row in rows] == [second, first]
    assert student_service.records_by_ids([]) == []


# ---------------------------------------------------------------- 审计


def test_success_audit_written_without_sensitive_data(student_service: StudentService) -> None:
    student_service.create_record(
        {
            "student_no": "S-1",
            "name": "张三",
            "id_number": "110101199001011234",
        }
    )

    logs = student_service.logs.list_logs(operation_type="student_create")
    assert len(logs) == 1
    assert logs[0]["result"] == "success"
    assert logs[0]["target_type"] == "certificate_record"
    assert "110101199001011234" not in logs[0]["summary"]


def test_failure_audit_written(student_service: StudentService) -> None:
    student_service.create_record({"student_no": "S-1"})

    with pytest.raises(DuplicateStudentNoError):
        student_service.create_record({"student_no": "S-1", "id_number": "110101199001011234"})

    logs = student_service.logs.list_logs(operation_type="student_create")
    failures = [log for log in logs if log["result"] == "failure"]
    assert len(failures) == 1
    assert "110101199001011234" not in failures[0]["summary"]
    assert "已存在" in failures[0]["error_message"]


def test_success_audit_failure_rolls_back_create(
    student_service: StudentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(student_service.logs, "create", _boom)

    with pytest.raises(AuditWriteError):
        student_service.create_record({"student_no": "S-1"})

    assert student_service.records.count_records() == 0


def test_success_audit_failure_rolls_back_update(
    student_service: StudentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    record_id = student_service.create_record({"student_no": "S-1", "name": "甲"})

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(student_service.logs, "create", _boom)

    with pytest.raises(AuditWriteError):
        student_service.update_record(record_id, {"name": "乙"})
    assert student_service.records.get(record_id)["name"] == "甲"


def test_success_audit_failure_rolls_back_delete(
    student_service: StudentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    record_id = student_service.create_record({"student_no": "S-1"})

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(student_service.logs, "create", _boom)

    with pytest.raises(AuditWriteError):
        student_service.delete_record(record_id)
    assert student_service.records.get(record_id) is not None


def test_failure_audit_error_does_not_mask_original_error(
    student_service: StudentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    student_service.create_record({"student_no": "S-1"})

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("audit unavailable")

    monkeypatch.setattr(student_service.logs, "create", _boom)

    with pytest.raises(DuplicateStudentNoError):
        student_service.create_record({"student_no": "S-1"})
