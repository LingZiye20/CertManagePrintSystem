"""学生数据页面与主窗口接入测试（离屏渲染、临时数据库）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from app.bootstrap import bootstrap
from app.config.paths import AppPaths
from app.services import StudentService
from app.ui.main_window import MainWindow
from app.ui.student_dialog import StudentDialog
from app.ui.student_page import StudentPage


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(student_service: StudentService, qapp: QApplication) -> StudentPage:
    widget = StudentPage(student_service)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close()


def _seed(service: StudentService, count: int) -> list[int]:
    ids = []
    for index in range(count):
        ids.append(
            service.create_record(
                {"student_no": f"S-{index:02d}", "name": "同名", "class_name": "一班"}
            )
        )
    return ids


def test_empty_state_page(page: StudentPage) -> None:
    assert page.model.rowCount() == 0
    assert "没有符合条件的记录" in page.summary_label.text()
    assert page.prev_button.isEnabled() is False
    assert page.next_button.isEnabled() is False
    assert page.edit_button.isEnabled() is False
    assert page.delete_button.isEnabled() is False


def test_pagination_buttons_and_row_counts(
    student_service: StudentService, page: StudentPage, qapp: QApplication
) -> None:
    _seed(student_service, 25)
    page.reload()
    qapp.processEvents()

    assert page.model.rowCount() == 20
    assert "共 25 条" in page.summary_label.text()
    assert "第 1/2 页" in page.summary_label.text()
    assert page.next_button.isEnabled() is True
    assert page.prev_button.isEnabled() is False

    page.next_button.click()
    qapp.processEvents()
    assert page.model.rowCount() == 5
    assert "第 2/2 页" in page.summary_label.text()
    assert page.prev_button.isEnabled() is True
    assert page.next_button.isEnabled() is False

    page.prev_button.click()
    qapp.processEvents()
    assert page.model.rowCount() == 20


def test_filter_search_and_clear(
    student_service: StudentService, page: StudentPage, qapp: QApplication
) -> None:
    student_service.create_record({"student_no": "S-1", "name": "张三", "class_name": "一班"})
    student_service.create_record({"student_no": "S-2", "name": "李四", "class_name": "二班"})
    page.reload()

    page.name_input.setText("张")
    page.search_button.click()
    qapp.processEvents()

    assert page.model.rowCount() == 1
    assert page.model.data(page.model.index(0, 1)) == "S-1"
    assert "共 1 条" in page.summary_label.text()

    page.clear_button.click()
    qapp.processEvents()
    assert page.model.rowCount() == 2
    assert "共 2 条" in page.summary_label.text()


def test_header_click_toggles_sort(
    student_service: StudentService, page: StudentPage, qapp: QApplication
) -> None:
    for student_no in ("S-2", "S-1", "S-3"):
        student_service.create_record({"student_no": student_no})
    page.reload()
    assert page.model.data(page.model.index(0, 1)) == "S-1"

    page._on_header_clicked(1)
    qapp.processEvents()
    assert page.model.data(page.model.index(0, 1)) == "S-3"
    assert page.table.horizontalHeader().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder

    page._on_header_clicked(1)
    qapp.processEvents()
    assert page.model.data(page.model.index(0, 1)) == "S-1"


def test_selection_enables_actions(
    student_service: StudentService, page: StudentPage, qapp: QApplication
) -> None:
    _seed(student_service, 3)
    page.reload()
    assert page.edit_button.isEnabled() is False

    page.table.selectRow(0)
    qapp.processEvents()
    assert page.edit_button.isEnabled() is True
    assert page.delete_button.isEnabled() is True


def test_reload_clamps_page_after_deletion(
    student_service: StudentService, page: StudentPage, qapp: QApplication
) -> None:
    ids = _seed(student_service, 21)
    page.reload()
    page.next_button.click()
    qapp.processEvents()
    assert "第 2/2 页" in page.summary_label.text()

    student_service.delete_record(ids[0])
    student_service.delete_record(ids[1])
    page.reload()
    qapp.processEvents()

    assert page.model.rowCount() == 19
    assert "第 1/1 页" in page.summary_label.text()


def test_dialog_round_trips_all_fields(qapp: QApplication) -> None:
    record = {
        "student_no": "0020240001",
        "name": "张三",
        "birth_date": "2000-01-02",
        "remarks": "第一行\n第二行",
    }
    dialog = StudentDialog(record=record)

    values = dialog.values()
    assert values["student_no"] == "0020240001"
    assert values["name"] == "张三"
    assert values["birth_date"] == "2000-01-02"
    assert values["remarks"] == "第一行\n第二行"
    assert values["certificate_no"] == ""


def test_student_no_read_only_when_editing_but_editable_when_adding(
    qapp: QApplication,
) -> None:
    edit_dialog = StudentDialog(record={"student_no": "00123", "name": "张三"})
    edit_editor = edit_dialog._editors["student_no"]
    assert edit_editor.isReadOnly() is True
    assert edit_editor.text() == "00123"

    add_dialog = StudentDialog()
    add_editor = add_dialog._editors["student_no"]
    assert add_editor.isReadOnly() is False

    edit_dialog.close()
    add_dialog.close()


def test_main_window_builds_student_page_with_context(
    qapp: QApplication, tmp_path: Path
) -> None:
    context = bootstrap(AppPaths.for_program(tmp_path))
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings, context=context)

    assert window.student_page is not None
    assert window.centralWidget() is window.student_page
    assert "数据管理(&D)" in [action.text() for action in window.menuBar().actions()]

    window.close()
    settings.sync()
