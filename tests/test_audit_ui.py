"""操作审计查询界面测试。"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QAbstractItemView

from app.data.db import Database
from app.repositories import OperationLogRepository
from app.services import AuditService, DataAccessError
from app.ui.audit_log_dialog import AuditLogDialog


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def service(repository_database: Database) -> AuditService:
    return AuditService(repository_database)


@pytest.fixture
def logs(repository_database: Database) -> OperationLogRepository:
    return OperationLogRepository(repository_database)


def test_table_shows_newest_logs_first(
    qapp: QApplication, service: AuditService, logs: OperationLogRepository
) -> None:
    logs.create("student_create", "certificate_record", "1", "新增学生 1", "success")
    logs.create("template_update", "certificate_template", "2", "修改模板 2", "failure")

    dialog = AuditLogDialog(service)

    assert dialog.table.rowCount() == 2
    assert dialog.table.item(0, 1).text() == "template_update"
    assert dialog.table.item(1, 1).text() == "student_create"
    assert "修改模板 2" in dialog.table.item(0, 4).text()
    assert dialog.table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers


def test_filter_by_operation_type(
    qapp: QApplication, service: AuditService, logs: OperationLogRepository
) -> None:
    logs.create("student_create", "certificate_record", "1", "新增", "success")
    logs.create("settings_update", "app_setting", "x", "设置", "success")
    dialog = AuditLogDialog(service)
    assert dialog.table.rowCount() == 2

    index = dialog.operation_filter.findData("settings_update")
    dialog.operation_filter.setCurrentIndex(index)

    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 1).text() == "settings_update"


def test_refresh_reloads_data(
    qapp: QApplication, service: AuditService, logs: OperationLogRepository
) -> None:
    dialog = AuditLogDialog(service)
    assert dialog.table.rowCount() == 0

    logs.create("student_create", "certificate_record", "1", "新增", "success")
    dialog.refresh()

    assert dialog.table.rowCount() == 1


def test_load_error_is_reported(
    qapp: QApplication, service: AuditService, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = AuditLogDialog(service)

    def broken(*args: object, **kwargs: object) -> tuple[object, ...]:
        raise DataAccessError("读取失败")

    monkeypatch.setattr(service, "list_recent", broken)
    dialog.refresh()

    assert dialog.table.rowCount() == 0
    assert "读取失败" in dialog.status_label.text()


def test_dialog_has_no_mutation_controls(
    qapp: QApplication, service: AuditService
) -> None:
    dialog = AuditLogDialog(service)

    button_texts = {button.text() for button in dialog.findChildren(type(dialog.close_button))}
    assert not any("删除" in text or "修改" in text for text in button_texts)
    assert dialog.table.isSortingEnabled() is False
