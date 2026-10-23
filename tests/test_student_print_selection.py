"""学生页勾选与打印入口测试：勾选顺序、分页保留、筛选清空与记录集合。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from app.config.paths import AppPaths
from app.data.db import Database
from app.services import PrintService, StudentService, TemplateService
from app.ui import student_page as student_page_module
from app.ui.student_page import TABLE_COLUMNS, StudentPage


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def print_service(
    repository_database: Database, app_paths: AppPaths
) -> PrintService:
    return PrintService(
        repository_database, TemplateService(repository_database, app_paths), app_paths
    )


@pytest.fixture
def page(
    student_service: StudentService,
    print_service: PrintService,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> StudentPage:
    monkeypatch.setattr(
        student_page_module,
        "QMessageBox",
        _SilentMessageBox,
    )
    widget = StudentPage(student_service, print_service=print_service)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close()


class _SilentMessageBox:
    StandardButton = QMessageBox.StandardButton
    question_reply = QMessageBox.StandardButton.Yes

    @classmethod
    def question(cls, *args: object, **kwargs: object) -> QMessageBox.StandardButton:
        return cls.question_reply

    @staticmethod
    def warning(*args: object, **kwargs: object) -> None:
        return None

    @staticmethod
    def information(*args: object, **kwargs: object) -> None:
        return None


def _create(student_service: StudentService, index: int, **values: object) -> dict:
    record_id = student_service.create_record(
        {"student_no": f"S-{index:03d}", "name": "打印", **values}
    )
    record = student_service.records.get(record_id)
    assert record is not None
    return record


def _check(page: StudentPage, row: int, checked: bool = True) -> None:
    model = page.table.model()
    index = model.index(row, 0)
    state = (
        Qt.CheckState.Checked.value if checked else Qt.CheckState.Unchecked.value
    )
    model.setData(index, state, Qt.ItemDataRole.CheckStateRole)


# ---------------------------------------------------------------- 勾选列


def test_checkbox_column_exists(page: StudentPage) -> None:
    model = page.table.model()
    assert model.columnCount() == len(TABLE_COLUMNS) + 1
    assert model.headerData(0, Qt.Orientation.Horizontal) == "选择"
    flags = model.flags(model.index(0, 0)) if model.rowCount() else None
    if flags is not None:
        assert flags & Qt.ItemFlag.ItemIsUserCheckable


def test_checked_ids_follow_check_order(page: StudentPage, student_service: StudentService) -> None:
    first = _create(student_service, 1)
    second = _create(student_service, 2)
    page.reload()

    _check(page, 1)
    _check(page, 0)

    assert page.checked_ids() == [second["id"], first["id"]]
    assert "已勾选 2 条" in page.checked_label.text()

    _check(page, 0, checked=False)
    assert page.checked_ids() == [second["id"]]


def test_checked_ids_survive_page_change(
    page: StudentPage, student_service: StudentService, qapp: QApplication
) -> None:
    for index in range(25):
        _create(student_service, index)
    page.reload()

    _check(page, 0)
    first_page_checked = page.checked_ids()

    page.next_page()
    qapp.processEvents()
    _check(page, 0)

    assert len(page.checked_ids()) == 2
    assert first_page_checked[0] in page.checked_ids()


def test_select_all_current_page(page: StudentPage, student_service: StudentService) -> None:
    for index in range(3):
        _create(student_service, index)
    page.reload()

    page.check_all_current_page()

    assert len(page.checked_ids()) == 3
    page.clear_checked()
    assert page.checked_ids() == []


def test_filter_change_clears_checks(
    page: StudentPage, student_service: StudentService
) -> None:
    _create(student_service, 1)
    page.reload()
    _check(page, 0)
    assert page.checked_ids()

    page.name_input.setText("打印")
    page.apply_filters()

    assert page.checked_ids() == []
    assert "已勾选 0 条" in page.checked_label.text()


def test_sort_change_clears_checks(
    page: StudentPage, student_service: StudentService
) -> None:
    _create(student_service, 1)
    page.reload()
    _check(page, 0)

    page._on_header_clicked(1)

    assert page.checked_ids() == []


# ---------------------------------------------------------------- 打印入口


def test_print_selected_passes_single_record(
    page: StudentPage,
    student_service: StudentService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _create(student_service, 1)
    page.reload()
    page.table.selectRow(0)
    captured: list[list[dict]] = []
    monkeypatch.setattr(page, "_open_print_dialog", captured.append)

    page.print_selected()

    assert len(captured) == 1
    assert [row["id"] for row in captured[0]] == [record["id"]]


def test_print_filtered_uses_all_filtered_records(
    page: StudentPage,
    student_service: StudentService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(25):
        _create(student_service, index)
    page.name_input.setText("打印")
    page.apply_filters()
    captured: list[list[dict]] = []
    monkeypatch.setattr(page, "_open_print_dialog", captured.append)

    page.print_filtered()

    assert len(captured) == 1
    assert len(captured[0]) == 25
    assert [row["student_no"] for row in captured[0]] == [
        f"S-{index:03d}" for index in range(25)
    ]


def test_print_checked_uses_checked_order(
    page: StudentPage,
    student_service: StudentService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _create(student_service, 1)
    second = _create(student_service, 2)
    page.reload()
    _check(page, 1)
    _check(page, 0)
    captured: list[list[dict]] = []
    monkeypatch.setattr(page, "_open_print_dialog", captured.append)

    page.print_checked()

    assert [row["id"] for row in captured[0]] == [second["id"], first["id"]]


def test_print_selected_without_selection_shows_info(
    page: StudentPage, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages: list[str] = []
    monkeypatch.setattr(page, "_show_info", messages.append)
    captured: list[list[dict]] = []
    monkeypatch.setattr(page, "_open_print_dialog", captured.append)

    page.print_selected()

    assert messages
    assert captured == []


def test_print_entries_disabled_without_print_service(
    student_service: StudentService, qapp: QApplication
) -> None:
    widget = StudentPage(student_service)
    widget.show()
    qapp.processEvents()

    assert widget.print_button.isEnabled() is False

    widget.close()
