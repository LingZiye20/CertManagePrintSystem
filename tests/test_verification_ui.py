"""证书核验界面测试：入口、类型选择、导航、状态展示与只读性。"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication

from app.data.db import Database
from app.services import StudentService, VerificationService
from app.ui import student_page as student_page_module
from app.ui.student_page import StudentPage
from app.ui.verification_dialog import (
    EMPTY_TEXT,
    READ_FAILED_MESSAGE,
    VerificationDialog,
)


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def students(repository_database: Database) -> StudentService:
    return StudentService(repository_database)


@pytest.fixture
def service(repository_database: Database) -> VerificationService:
    return VerificationService(repository_database)


def _create(students: StudentService, student_no: str, **values: Any) -> int:
    data = {"student_no": student_no, "name": f"学生{student_no}", **values}
    return students.create_record(data)


def _dialog(
    service: VerificationService,
    filters: dict[str, str] | None = None,
    *,
    order_by: str = "student_no",
    descending: bool = False,
    initial_type: str = "graduation",
) -> VerificationDialog:
    return VerificationDialog(
        service,
        filters or {},
        order_by=order_by,
        descending=descending,
        initial_type=initial_type,
    )


def _snapshot(database: Database) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
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


# ---------------------------------------------------------------- 打开与类型


def test_dialog_opens_with_selected_certificate_type(
    service: VerificationService, students: StudentService, qapp: QApplication
) -> None:
    _create(
        students,
        "S-001",
        certificate_no="BJ0001",
        degree_certificate_no="XW0001",
        other_certificate_no="QT0001",
    )

    dialog = _dialog(service, initial_type="degree")

    assert dialog.type_combo.currentData() == "degree"
    assert dialog.session is not None
    assert dialog.session.number_field == "degree_certificate_no"
    assert dialog.number_label.text() == "学位证书编号："
    assert dialog.number_value_label.text() == "XW0001"
    assert dialog.status_value_label.text() == "可核对"
    assert dialog.position_label.text() == "第 1 / 1 条"
    assert dialog.prev_button.isEnabled() is False
    assert dialog.next_button.isEnabled() is False
    assert "全部记录" in dialog.snapshot_label.text()
    assert "共 1 条" in dialog.snapshot_label.text()
    dialog.close()


def test_type_switch_restarts_with_same_session_conditions(
    service: VerificationService, students: StudentService, qapp: QApplication
) -> None:
    _create(students, "S-001", certificate_no="BJ0001", degree_certificate_no="XW0001")
    dialog = _dialog(service, order_by="student_no")

    dialog.type_combo.setCurrentIndex(dialog.type_combo.findData("degree"))

    assert dialog.session is not None
    assert dialog.session.certificate_type == "degree"
    assert dialog.number_value_label.text() == "XW0001"
    assert dialog.position_label.text() == "第 1 / 1 条"
    dialog.close()


# ---------------------------------------------------------------- 空集合


def test_empty_collection_disables_navigation(
    service: VerificationService, qapp: QApplication
) -> None:
    dialog = _dialog(service)

    assert dialog.session is not None
    assert dialog.session.total == 0
    assert dialog.position_label.text() == "第 0 / 0 条"
    assert dialog.prev_button.isEnabled() is False
    assert dialog.next_button.isEnabled() is False
    assert "没有可核验的记录" in dialog.message_label.text()
    assert dialog.value_labels["student_no"].text() == EMPTY_TEXT
    assert dialog.number_value_label.text() == EMPTY_TEXT
    dialog.close()


# ---------------------------------------------------------------- 导航


def test_navigation_boundaries(
    service: VerificationService, students: StudentService, qapp: QApplication
) -> None:
    for index in (1, 2, 3):
        _create(students, f"S-00{index}", certificate_no=f"BJ000{index}")
    dialog = _dialog(service)

    assert dialog.position_label.text() == "第 1 / 3 条"
    assert dialog.prev_button.isEnabled() is False
    assert dialog.next_button.isEnabled() is True

    dialog.next_button.click()
    assert dialog.position_label.text() == "第 2 / 3 条"
    assert dialog.prev_button.isEnabled() is True
    assert dialog.next_button.isEnabled() is True

    dialog.next_button.click()
    assert dialog.position_label.text() == "第 3 / 3 条"
    assert dialog.next_button.isEnabled() is False
    assert dialog.value_labels["student_no"].text() == "S-003"

    dialog.prev_button.click()
    assert dialog.position_label.text() == "第 2 / 3 条"
    dialog.close()


# ---------------------------------------------------------------- 四类状态


def test_status_missing_number(
    service: VerificationService, students: StudentService, qapp: QApplication
) -> None:
    _create(students, "S-001", certificate_no="BJ0001")
    dialog = _dialog(service, initial_type="degree")

    assert dialog.status_value_label.text() == "缺少证书编号"
    assert dialog.number_value_label.text() == EMPTY_TEXT
    assert "证书编号为空" in dialog.status_hint_label.text()
    dialog.close()


def test_deleted_record_keeps_position_and_navigation(
    service: VerificationService, students: StudentService, qapp: QApplication
) -> None:
    first = _create(students, "S-001", certificate_no="BJ0001")
    second = _create(students, "S-002", certificate_no="BJ0002")
    _create(students, "S-003", certificate_no="BJ0003")
    dialog = _dialog(service)
    dialog.go_next()
    assert dialog.position_label.text() == "第 2 / 3 条"

    students.delete_record(second)
    dialog.refresh_current()

    assert dialog.current_index == 1
    assert dialog.position_label.text() == "第 2 / 3 条"
    assert dialog.status_value_label.text() == "记录已删除"
    assert dialog.value_labels["student_no"].text() == EMPTY_TEXT
    assert dialog.prev_button.isEnabled() is True
    assert dialog.next_button.isEnabled() is True

    dialog.go_next()
    assert dialog.position_label.text() == "第 3 / 3 条"
    assert dialog.status_value_label.text() == "可核对"
    assert first == dialog.session.record_ids[0]
    dialog.close()


def test_read_failure_shows_friendly_message_and_keeps_navigation(
    service: VerificationService,
    students: StudentService,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(students, "S-001", certificate_no="BJ0001")
    _create(students, "S-002", certificate_no="BJ0002")
    dialog = _dialog(service)

    def _boom(record_id: int) -> dict[str, Any] | None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(service.records, "get", _boom)
    dialog.refresh_current()

    assert dialog.status_value_label.text() == "读取失败"
    assert dialog.message_label.text() == READ_FAILED_MESSAGE
    assert "读取这条记录失败" in dialog.message_label.text()
    assert "Traceback" not in dialog.message_label.text()
    # 导航只取决于集合边界，不因读取失败而禁用。
    assert dialog.prev_button.isEnabled() is False
    assert dialog.next_button.isEnabled() is True

    dialog.go_next()
    assert dialog.current_index == 1
    assert dialog.position_label.text() == "第 2 / 2 条"
    assert dialog.status_value_label.text() == "读取失败"
    assert dialog.next_button.isEnabled() is False
    dialog.close()


# ---------------------------------------------------------------- 重载


def test_reload_uses_session_snapshot_and_returns_to_first(
    service: VerificationService, students: StudentService, qapp: QApplication
) -> None:
    _create(students, "S-001", class_name="一班", certificate_no="BJ0001")
    _create(students, "S-002", class_name="一班", certificate_no="BJ0002")
    _create(students, "S-009", class_name="二班", certificate_no="BJ0009")

    dialog = _dialog(service, {"class_name": "一班"}, order_by="student_no", descending=True)
    assert dialog.session is not None
    assert dialog.session.total == 2
    dialog.go_next()
    assert dialog.position_label.text() == "第 2 / 2 条"

    _create(students, "S-000", class_name="一班", certificate_no="BJ0000")
    dialog.reload_session()

    assert dialog.session is not None
    assert dialog.session.total == 3
    assert dialog.current_index == 0
    assert dialog.position_label.text() == "第 1 / 3 条"
    assert dialog.value_labels["student_no"].text() == "S-002"
    assert dialog.session.filters == {"class_name": "一班"}
    assert dialog.session.descending is True
    dialog.close()


# ---------------------------------------------------------------- 页面入口


def test_page_entry_uses_current_filters_and_sort(
    students: StudentService,
    service: VerificationService,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(students, "S-001", class_name="一班")
    page = StudentPage(students, verification_service=service)
    page.class_name_input.setText("一班")
    page.apply_filters()

    captured: dict[str, Any] = {}

    class _FakeDialog:
        def __init__(self, service_arg, filters, parent=None, *, order_by, descending):
            captured.update(
                {
                    "service": service_arg,
                    "filters": dict(filters),
                    "order_by": order_by,
                    "descending": descending,
                }
            )

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(student_page_module, "VerificationDialog", _FakeDialog)

    assert page.verify_button.isEnabled() is True
    page.verify_button.click()

    assert captured["service"] is service
    assert captured["filters"] == {"class_name": "一班"}
    assert captured["order_by"] == "student_no"
    assert captured["descending"] is False
    page.close()


def test_page_filter_changes_do_not_affect_existing_session(
    students: StudentService, service: VerificationService, qapp: QApplication
) -> None:
    _create(students, "S-001", class_name="一班", certificate_no="BJ0001")
    _create(students, "S-002", class_name="一班", certificate_no="BJ0002")
    _create(students, "S-003", class_name="二班", certificate_no="BJ0003")

    page = StudentPage(students, verification_service=service)
    page.class_name_input.setText("一班")
    page.apply_filters()

    dialog = page.create_verification_dialog()
    assert dialog is not None
    assert dialog.session is not None
    session_ids = dialog.session.record_ids
    assert dialog.session.total == 2

    # 学生页面后续改变筛选，现有核验会话不随之变化。
    page.class_name_input.clear()
    page.apply_filters()
    assert page.model.rowCount() == 3

    assert dialog.session.record_ids == session_ids
    dialog.reload_session()
    assert dialog.session is not None
    assert dialog.session.record_ids == session_ids
    assert dialog.session.filters == {"class_name": "一班"}
    dialog.close()
    page.close()


# ---------------------------------------------------------------- 只读性


def test_dialog_never_changes_business_data(
    service: VerificationService, students: StudentService, qapp: QApplication
) -> None:
    _create(students, "S-001", certificate_no="BJ0001")
    _create(students, "S-002", degree_certificate_no="XW0001")
    before = _snapshot(service.database)

    dialog = _dialog(service)
    dialog.go_next()
    dialog.go_previous()
    dialog.type_combo.setCurrentIndex(dialog.type_combo.findData("degree"))
    dialog.reload_session()
    dialog.close()

    after = _snapshot(service.database)
    assert after == before
    assert after[2] == before[2], "核验不应新增 operation_logs"
    assert after[1] == before[1], "核验不应改变编号流水"
    assert after[3] == 0, "核验不应产生打印记录"
