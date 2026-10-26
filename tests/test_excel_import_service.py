"""固定 37 列 Excel 模板导入 Service 测试。"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest
from openpyxl import Workbook

from app.data.db import EXCEL_FIELD_NAMES, Database
from app.repositories import CertificateRecordRepository, ImportBatchRepository, OperationLogRepository
from app.services import ExcelImportService
from app.utils.excel_template import EXCEL_COLUMN_NAMES

CERTIFICATE_FIELDS = (
    "certificate_no",
    "degree_certificate_no",
    "other_certificate_no",
)


def _values(student_no: object = "00123", **overrides: object) -> list[object]:
    data: dict[str, object] = {name: None for name in EXCEL_FIELD_NAMES}
    data["student_no"] = student_no
    data["name"] = "测试"
    data.update(overrides)
    return [data[name] for name in EXCEL_FIELD_NAMES]


def _write_rows(
    path: Path,
    rows: list[list[object]],
    *,
    headers: tuple[str, ...] = EXCEL_COLUMN_NAMES,
) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "学生数据"
    sheet.append(list(headers))
    for row in rows:
        sheet.append(list(row))
    workbook.save(path)
    workbook.close()
    return path


@pytest.fixture
def import_service(repository_database: Database) -> ExcelImportService:
    return ExcelImportService(repository_database)


def _records(service: ExcelImportService) -> list[dict]:
    return service.records.list_records()


def _failed_batches(service: ExcelImportService) -> list[dict]:
    return service.batches.list_batches(status="failed")


# ---------------------------------------------------------------- 成功路径


def test_imports_valid_37_column_file(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(
        tmp_path / "students.xlsx",
        [
            _values(
                student_no="00123",
                name="张三",
                gender="男",
                birth_date="2000-01-02",
            ),
            _values(
                student_no="00124",
                name="李四",
                enrollment_date=date(2024, 9, 1),
            ),
        ],
    )

    result = import_service.import_file(path)

    assert result.status == "success"
    assert result.total_rows == 2
    assert result.imported_count == 2
    assert result.failed_count == 0
    assert result.issues == ()
    assert result.batch_id is not None
    assert result.batch_saved is True
    assert result.warnings == ()
    assert "成功" in result.message

    records = _records(import_service)
    assert [record["student_no"] for record in records] == ["00123", "00124"]
    assert records[0]["birth_date"] == "2000-01-02"
    assert records[1]["enrollment_date"] == "2024-09-01"
    for record in records:
        for field in CERTIFICATE_FIELDS:
            assert record[field] is None

    batches = import_service.batches.list_batches()
    assert len(batches) == 1
    assert batches[0]["status"] == "success"
    assert batches[0]["total_rows"] == 2
    assert batches[0]["error_summary"] is None

    logs = import_service.logs.list_logs(operation_type="excel_import")
    assert len(logs) == 1
    assert logs[0]["result"] == "success"
    assert logs[0]["target_id"] == str(result.batch_id)


def test_import_preserves_leading_zeros_and_internal_characters(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(
        tmp_path / "students.xlsx",
        [
            _values(student_no=" 00123 ", major_code=" 0101 "),
            _values(student_no="00 124"),
        ],
    )

    result = import_service.import_file(path)

    assert result.status == "success"
    records = _records(import_service)
    assert [record["student_no"] for record in records] == ["00123", "00 124"]
    assert records[0]["major_code"] == "0101"
    assert import_service.records.get_by_student_no("123") is None


def test_blank_rows_are_skipped_and_not_counted(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(
        tmp_path / "students.xlsx",
        [
            [None] * 37,
            _values(student_no="00123"),
            [None] * 37,
        ],
    )

    result = import_service.import_file(path)

    assert result.status == "success"
    assert result.total_rows == 1
    assert result.imported_count == 1
    assert len(_records(import_service)) == 1


def test_excel_date_cells_are_converted_to_iso(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(
        tmp_path / "students.xlsx",
        [
            _values(
                student_no="00123",
                birth_date=date(2024, 2, 29),
                enrollment_date=datetime(2024, 9, 1, 0, 0),
            )
        ],
    )

    result = import_service.import_file(path)

    assert result.status == "success"
    record = _records(import_service)[0]
    assert record["birth_date"] == "2024-02-29"
    assert record["enrollment_date"] == "2024-09-01"


# ---------------------------------------------------------------- 表头与文件


def test_header_missing_column_fails_entire_batch(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(
        tmp_path / "students.xlsx",
        [_values()[:36]],
        headers=EXCEL_COLUMN_NAMES[:-1],
    )

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert result.imported_count == 0
    assert _records(import_service) == []
    assert any(issue.row == 1 for issue in result.issues)
    assert any("缺少" in issue.reason for issue in result.issues)
    assert _failed_batches(import_service)


def test_header_extra_column_fails_entire_batch(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(
        tmp_path / "students.xlsx",
        [_values() + ["多余"]],
        headers=(*EXCEL_COLUMN_NAMES, "模板外列"),
    )

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert result.imported_count == 0
    assert _records(import_service) == []
    assert any("模板外列" in issue.reason for issue in result.issues)


def test_header_wrong_name_fails_entire_batch(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    headers = list(EXCEL_COLUMN_NAMES)
    headers[13] = "错误的学号列名"
    path = _write_rows(tmp_path / "students.xlsx", [_values()], headers=tuple(headers))

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert _records(import_service) == []
    assert any(issue.column == "学号" for issue in result.issues)


def test_header_swapped_columns_fail_entire_batch(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    headers = list(EXCEL_COLUMN_NAMES)
    headers[0], headers[1] = headers[1], headers[0]
    path = _write_rows(tmp_path / "students.xlsx", [_values()], headers=tuple(headers))

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert _records(import_service) == []
    assert any(issue.row == 1 for issue in result.issues)


def test_workbook_with_multiple_sheets_fails(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = tmp_path / "students.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(list(EXCEL_COLUMN_NAMES))
    sheet.append(_values())
    workbook.create_sheet("第二页")
    workbook.save(path)
    workbook.close()

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert _records(import_service) == []
    assert any("工作表" in issue.reason for issue in result.issues)


def test_workbook_without_worksheets_fails(
    import_service: ExcelImportService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_rows(tmp_path / "students.xlsx", [_values()])

    class _NoSheetWorkbook:
        worksheets = []
        sheetnames = []

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        openpyxl, "load_workbook", lambda *args, **kwargs: _NoSheetWorkbook()
    )

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert _records(import_service) == []
    assert any("工作表" in issue.reason for issue in result.issues)


def test_missing_file_fails(import_service: ExcelImportService, tmp_path: Path) -> None:
    result = import_service.import_file(tmp_path / "missing.xlsx")

    assert result.status == "failed"
    assert _records(import_service) == []
    assert result.issues
    assert result.batch_saved is True


def test_non_xlsx_suffix_fails(import_service: ExcelImportService, tmp_path: Path) -> None:
    path = tmp_path / "students.xls"
    path.write_bytes(b"not an xlsx")

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert _records(import_service) == []
    assert any("xlsx" in issue.reason for issue in result.issues)


@pytest.mark.parametrize("content", [b"", b"not an xlsx"])
def test_empty_or_corrupt_file_fails(
    import_service: ExcelImportService, tmp_path: Path, content: bytes
) -> None:
    path = tmp_path / "students.xlsx"
    path.write_bytes(content)

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert _records(import_service) == []
    assert result.issues
    assert _failed_batches(import_service)


def test_unreadable_file_fails_without_traceback(
    import_service: ExcelImportService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_rows(tmp_path / "students.xlsx", [_values()])

    def _boom(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(openpyxl, "load_workbook", _boom)

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert _records(import_service) == []
    assert any("读取" in issue.reason for issue in result.issues)


def test_header_only_file_fails_as_empty_data(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(tmp_path / "students.xlsx", [])

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert result.total_rows == 0
    assert result.imported_count == 0
    assert _records(import_service) == []
    assert any("没有可导入" in issue.reason for issue in result.issues)


# ---------------------------------------------------------------- 行级校验


def test_empty_student_no_fails(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(tmp_path / "students.xlsx", [_values(student_no=None)])

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert _records(import_service) == []
    assert any(
        issue.row == 2 and issue.field == "student_no" and issue.kind == "required"
        for issue in result.issues
    )


def test_duplicate_student_no_in_file_fails(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(
        tmp_path / "students.xlsx",
        [
            _values(student_no="00123"),
            _values(student_no="00124"),
            _values(student_no="00123"),
        ],
    )

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert result.imported_count == 0
    assert _records(import_service) == []
    assert any(
        issue.row == 4 and issue.kind == "duplicate_in_file" for issue in result.issues
    )


def test_existing_student_no_does_not_overwrite_record_or_photo(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    existing_id = import_service.records.create(
        {"student_no": "00123", "name": "原姓名", "photo_path": "old.jpg"}
    )
    path = _write_rows(
        tmp_path / "students.xlsx",
        [_values(student_no="00123", name="新姓名")],
    )

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert result.imported_count == 0
    existing = import_service.records.get(existing_id)
    assert existing["name"] == "原姓名"
    assert existing["photo_path"] == "old.jpg"
    assert len(_records(import_service)) == 1
    assert any(issue.kind == "duplicate_in_db" for issue in result.issues)


@pytest.mark.parametrize("field", CERTIFICATE_FIELDS)
def test_certificate_number_must_be_empty(
    import_service: ExcelImportService, tmp_path: Path, field: str
) -> None:
    path = _write_rows(
        tmp_path / "students.xlsx",
        [_values(**{field: "SECRET-123"})],
    )

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert _records(import_service) == []
    assert any(issue.field == field for issue in result.issues)
    batch = _failed_batches(import_service)[0]
    assert "SECRET-123" not in (batch["error_summary"] or "")


def test_multiple_rows_report_all_errors(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(
        tmp_path / "students.xlsx",
        [
            _values(student_no=None),
            _values(student_no="00124", certificate_no="X"),
            _values(student_no="00125", study_years=4),
            _values(student_no="00126"),
        ],
    )

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert result.total_rows == 4
    assert result.failed_count == 3
    assert result.imported_count == 0
    assert {issue.row for issue in result.issues} >= {2, 3, 4}
    assert _records(import_service) == []


def test_valid_and_invalid_rows_do_not_partially_import(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(
        tmp_path / "students.xlsx",
        [
            _values(student_no="00123"),
            _values(student_no="00124", study_years=4),
        ],
    )

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert result.total_rows == 2
    assert result.failed_count == 1
    assert _records(import_service) == []


def test_extra_data_column_fails(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(tmp_path / "students.xlsx", [_values() + ["多余"]])

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert _records(import_service) == []
    assert any(issue.kind == "extra_column" for issue in result.issues)


def test_numeric_student_no_is_not_coerced(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(tmp_path / "students.xlsx", [_values(student_no=123)])

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert _records(import_service) == []
    issue = next(issue for issue in result.issues if issue.field == "student_no")
    assert issue.kind == "type"
    assert "文本" in issue.reason
    assert "前导零" in issue.reason


def test_numeric_text_field_is_not_coerced(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(tmp_path / "students.xlsx", [_values(study_years=4)])

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert _records(import_service) == []
    assert any(
        issue.field == "study_years" and issue.kind == "type"
        for issue in result.issues
    )


@pytest.mark.parametrize("date_text", ["20260809", "2026.8.9", "2024/02/29", "2024-2-1"])
def test_date_text_is_accepted_and_stored_verbatim(
    import_service: ExcelImportService, tmp_path: Path, date_text: str
) -> None:
    path = _write_rows(
        tmp_path / "students.xlsx", [_values(birth_date=date_text)]
    )

    result = import_service.import_file(path)

    assert result.status == "success"
    assert result.imported_count == 1
    assert _records(import_service)[0]["birth_date"] == date_text


def test_datetime_with_non_zero_time_fails(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(
        tmp_path / "students.xlsx",
        [_values(birth_date=datetime(2024, 9, 1, 12, 30))],
    )

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert _records(import_service) == []
    assert any(issue.kind == "date" for issue in result.issues)


def test_numeric_date_cell_fails(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(tmp_path / "students.xlsx", [_values(birth_date=45292)])

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert _records(import_service) == []
    assert any(
        issue.field == "birth_date" and issue.kind == "type"
        for issue in result.issues
    )


def test_issue_columns_use_c2_template_names_not_ui_labels(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(
        tmp_path / "students.xlsx",
        [
            _values(
                college_number=123,
                education_level=4,
                enrollment_date=45292,
            )
        ],
    )

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert _records(import_service) == []
    located = {issue.field: issue.column for issue in result.issues}
    # 这些字段的界面标签（学院编号/学历层次/入学日期）与 C.2 列名不同，
    # 错误定位必须使用模板中的 C.2 列名。
    assert located["college_number"] == "院号"
    assert located["education_level"] == "层次"
    assert located["enrollment_date"] == "入学日期"
    assert "学院编号" not in located.values()
    assert "学历层次" not in located.values()


def test_sensitive_id_number_value_is_not_in_error_summary(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    secret = "110101199001011234"
    path = _write_rows(
        tmp_path / "students.xlsx",
        [_values(id_number=int(secret))],
    )

    result = import_service.import_file(path)

    assert result.status == "failed"
    batch = _failed_batches(import_service)[0]
    summary = batch["error_summary"] or ""
    assert secret not in summary
    assert all(secret not in issue.reason for issue in result.issues)


# ---------------------------------------------------------------- 事务与审计


def test_database_write_failure_rolls_back_all_records(
    import_service: ExcelImportService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_rows(
        tmp_path / "students.xlsx",
        [_values(student_no=f"00{index}") for index in range(1, 4)],
    )
    original = CertificateRecordRepository.create
    calls = {"count": 0}

    def _flaky(self, values, *, connection=None):
        calls["count"] += 1
        if calls["count"] == 2:
            raise sqlite3.OperationalError("disk I/O error")
        return original(self, values, connection=connection)

    monkeypatch.setattr(CertificateRecordRepository, "create", _flaky)

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert result.imported_count == 0
    assert result.batch_saved is True
    assert _records(import_service) == []
    assert any("回滚" in issue.reason for issue in result.issues)

    batches = _failed_batches(import_service)
    assert len(batches) == 1
    assert batches[0]["error_summary"]


def test_success_audit_failure_rolls_back_and_reports_warning(
    import_service: ExcelImportService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_rows(tmp_path / "students.xlsx", [_values()])

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("audit unavailable")

    monkeypatch.setattr(OperationLogRepository, "create", _boom)

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert result.imported_count == 0
    assert _records(import_service) == []
    assert result.batch_saved is True
    assert result.warnings
    assert any("回滚" in issue.reason for issue in result.issues)


def test_failure_batch_error_does_not_mask_original_error(
    import_service: ExcelImportService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_rows(tmp_path / "students.xlsx", [_values(student_no=None)])

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("batch unavailable")

    monkeypatch.setattr(ImportBatchRepository, "create", _boom)

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert result.batch_saved is False
    assert result.warnings
    assert any(issue.kind == "required" for issue in result.issues)
    assert "未写入任何学生记录" in result.message


def test_failed_audit_records_file_rows_and_error_counts(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(
        tmp_path / "students.xlsx",
        [
            _values(student_no="00123"),
            _values(student_no="00124", certificate_no="NO-1"),
            _values(student_no=None),
        ],
    )

    result = import_service.import_file(path)

    assert result.status == "failed"
    audit = [
        log
        for log in import_service.logs.list_logs(operation_type="excel_import")
        if log["result"] == "failure"
    ][0]
    message = audit["error_message"] or ""
    assert "students.xlsx" in message
    assert "总行数：3" in message
    assert "失败行数：2" in message
    assert "错误数：2" in message
    # 详细字段错误仍保存在批次摘要中。
    batch = _failed_batches(import_service)[0]
    assert "第 3 行" in batch["error_summary"]
    assert "第 4 行" in batch["error_summary"]


def test_file_level_failure_audit_records_zero_counts(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    result = import_service.import_file(tmp_path / "missing.xlsx")

    assert result.status == "failed"
    audit = [
        log
        for log in import_service.logs.list_logs(operation_type="excel_import")
        if log["result"] == "failure"
    ][0]
    message = audit["error_message"] or ""
    assert "missing.xlsx" in message
    assert "总行数：0" in message
    assert "失败行数：0" in message
    assert "错误数：1" in message


def test_failed_audit_write_error_preserves_original_import_error(
    import_service: ExcelImportService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_rows(tmp_path / "students.xlsx", [_values(student_no=None)])

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("audit unavailable")

    monkeypatch.setattr(OperationLogRepository, "create", _boom)

    result = import_service.import_file(path)

    assert result.status == "failed"
    assert result.batch_saved is True
    assert any("失败审计记录未能保存" in warning for warning in result.warnings)
    assert any(issue.kind == "required" for issue in result.issues)
    assert "未写入任何学生记录" in result.message
    assert "audit unavailable" not in result.message


def test_failed_batch_summary_contains_row_and_field(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    path = _write_rows(tmp_path / "students.xlsx", [_values(student_no=None)])

    result = import_service.import_file(path)

    assert result.status == "failed"
    batch = _failed_batches(import_service)[0]
    assert "第 2 行" in batch["error_summary"]
    assert "学号" in batch["error_summary"]


def test_import_does_not_generate_or_change_numbers(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    with import_service.database.connect() as connection:
        before_rules = [
            dict(row) for row in connection.execute("SELECT * FROM numbering_rules")
        ]

    path = _write_rows(tmp_path / "students.xlsx", [_values()])
    result = import_service.import_file(path)

    assert result.status == "success"
    with import_service.database.connect() as connection:
        after_rules = [
            dict(row) for row in connection.execute("SELECT * FROM numbering_rules")
        ]

    assert after_rules == before_rules
    record = _records(import_service)[0]
    for field in CERTIFICATE_FIELDS:
        assert record[field] is None


def test_existing_unrelated_record_is_unchanged(
    import_service: ExcelImportService, tmp_path: Path
) -> None:
    existing_id = import_service.records.create(
        {"student_no": "OLD-1", "name": "旧记录", "photo_path": "old.jpg"}
    )
    path = _write_rows(tmp_path / "students.xlsx", [_values(student_no="NEW-1")])

    result = import_service.import_file(path)

    assert result.status == "success"
    existing = import_service.records.get(existing_id)
    assert existing["name"] == "旧记录"
    assert existing["photo_path"] == "old.jpg"
    assert len(_records(import_service)) == 2
