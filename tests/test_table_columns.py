"""主页表格动态列测试：模型、页面、排序回退与主窗口生效链路。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from app.bootstrap import bootstrap
from app.config.paths import AppPaths
from app.services import (
    DEFAULT_TABLE_COLUMN_KEYS,
    STUDENT_TABLE_FIELDS,
    TABLE_COLUMNS_KEY,
    SettingsService,
    StudentService,
)
from app.ui.main_window import MainWindow
from app.ui.student_page import DEFAULT_TABLE_COLUMNS, StudentPage

POOL_KEYS: tuple[str, ...] = tuple(key for key, _label in STUDENT_TABLE_FIELDS)


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


def _create(service: StudentService, student_no: str, name: str) -> int:
    return service.create_record(
        {"student_no": student_no, "name": name, "gender": "女"}
    )


def test_default_model_columns_and_checkbox_column(page: StudentPage) -> None:
    model = page.model

    assert model.columns == DEFAULT_TABLE_COLUMNS
    assert len(model.columns) == 10
    assert tuple(key for key, _label in model.columns) == DEFAULT_TABLE_COLUMN_KEYS
    assert model.columnCount() == 11
    assert model.headerData(0, Qt.Orientation.Horizontal) == "选择"
    assert model.headerData(1, Qt.Orientation.Horizontal) == "学号"


def test_apply_table_columns_updates_headers_and_data(
    page: StudentPage, student_service: StudentService
) -> None:
    _create(student_service, "00123", "张三")
    page.reload()

    page.apply_table_columns(("name", "student_no"))

    model = page.model
    assert model.columnCount() == 3
    assert [key for key, _label in model.columns] == ["name", "student_no"]
    assert model.headerData(0, Qt.Orientation.Horizontal) == "选择"
    assert model.headerData(1, Qt.Orientation.Horizontal) == "姓名"
    assert model.headerData(2, Qt.Orientation.Horizontal) == "学号"
    assert model.data(model.index(0, 1)) == "张三"
    assert model.data(model.index(0, 2)) == "00123"
    assert page.table.horizontalHeader().sortIndicatorSection() == 2


def test_header_click_uses_active_columns(
    page: StudentPage, student_service: StudentService
) -> None:
    _create(student_service, "00123", "张三")
    page.apply_table_columns(("name", "student_no"))

    page._on_header_clicked(1)  # 姓名
    assert page._order_by == "name"
    assert page._descending is False
    assert page.table.horizontalHeader().sortIndicatorSection() == 1

    page._on_header_clicked(1)
    assert page._descending is True


def test_hiding_current_sort_field_resets_default(
    page: StudentPage, student_service: StudentService
) -> None:
    _create(student_service, "00123", "张三")
    page.apply_table_columns(("name", "student_no"))
    page._on_header_clicked(1)
    assert page._order_by == "name"

    page.apply_table_columns(("student_no", "gender"))

    assert page._order_by == "student_no"
    assert page._descending is False
    assert page.table.horizontalHeader().isSortIndicatorShown() is False


def test_reapplying_same_columns_is_noop_and_keeps_checked_rows(
    page: StudentPage, student_service: StudentService
) -> None:
    _create(student_service, "00123", "张三")
    page.reload()
    page.model.setData(
        page.model.index(0, 0),
        Qt.CheckState.Checked.value,
        Qt.ItemDataRole.CheckStateRole,
    )
    checked = page.checked_ids()

    page.apply_table_columns(("name", "student_no"))
    page.apply_table_columns(("name", "student_no"))

    assert page.checked_ids() == checked
    assert page.model.columnCount() == 3

    page.apply_table_columns(("student_no",))
    assert page.checked_ids() == checked
    assert page.model.columnCount() == 2


def test_apply_rejects_empty_columns(page: StudentPage) -> None:
    with pytest.raises(ValueError):
        page.apply_table_columns(())

    assert page.model.columnCount() == 11


def test_all_pool_fields_can_be_displayed_and_sorted(
    page: StudentPage, student_service: StudentService
) -> None:
    _create(student_service, "00123", "张三")
    page.apply_table_columns(POOL_KEYS)

    model = page.model
    assert model.columnCount() == 39
    assert model.headerData(0, Qt.Orientation.Horizontal) == "选择"

    birth_position = POOL_KEYS.index("birth_date") + 1
    assert model.headerData(birth_position, Qt.Orientation.Horizontal) == "出生日期"
    page._on_header_clicked(birth_position)
    assert page._order_by == "birth_date"
    assert page.table.horizontalHeader().sortIndicatorSection() == birth_position

    id_position = POOL_KEYS.index("id_number") + 1
    assert model.headerData(id_position, Qt.Orientation.Horizontal) == "证件号码"
    page._on_header_clicked(id_position)
    assert page._order_by == "id_number"
    assert page.table.horizontalHeader().sortIndicatorSection() == id_position


def test_startup_uses_stored_table_columns(
    qapp: QApplication, tmp_path
) -> None:
    context = bootstrap(AppPaths.for_program(tmp_path))
    SettingsService(context.database).update(
        {TABLE_COLUMNS_KEY: ("name", "student_no")}
    )
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)

    window = MainWindow(settings=settings, context=context)
    window.show()
    qapp.processEvents()

    page = window.student_page
    assert page is not None
    assert [key for key, _label in page.model.columns] == ["name", "student_no"]
    assert page.model.columnCount() == 3
    window.close()


def test_open_settings_applies_saved_columns_without_restart(
    qapp: QApplication, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ui import main_window as main_window_module

    context = bootstrap(AppPaths.for_program(tmp_path))
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings, context=context)
    window.show()
    qapp.processEvents()
    page = window.student_page
    assert page is not None

    class _SavingDialog:
        def __init__(self, service: SettingsService, parent=None) -> None:
            service.update({TABLE_COLUMNS_KEY: ("updated_at", "name")})

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(main_window_module, "SettingsDialog", _SavingDialog)

    window.open_settings()

    assert [key for key, _label in page.model.columns] == ["updated_at", "name"]
    assert page.model.columnCount() == 3
    window.close()


def test_open_settings_with_full_pool_shows_all_columns(
    qapp: QApplication, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ui import main_window as main_window_module

    context = bootstrap(AppPaths.for_program(tmp_path))
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings, context=context)
    window.show()
    qapp.processEvents()
    page = window.student_page
    assert page is not None

    class _SelectAllDialog:
        def __init__(self, service: SettingsService, parent=None) -> None:
            service.update({TABLE_COLUMNS_KEY: POOL_KEYS})

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(main_window_module, "SettingsDialog", _SelectAllDialog)

    window.open_settings()

    assert page.model.columnCount() == 39
    assert page.model.headerData(0, Qt.Orientation.Horizontal) == "选择"
    assert tuple(key for key, _label in page.model.columns) == POOL_KEYS
    window.close()


def test_open_settings_cancel_does_not_apply_ui_changes(
    qapp: QApplication, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ui import main_window as main_window_module

    context = bootstrap(AppPaths.for_program(tmp_path))
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings, context=context)
    window.show()
    qapp.processEvents()
    page = window.student_page
    assert page is not None
    before = page.model.columns

    class _CancelledDialog:
        def __init__(self, service: SettingsService, parent=None) -> None:
            self.service = service

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(main_window_module, "SettingsDialog", _CancelledDialog)

    window.open_settings()

    assert page.model.columns == before
    window.close()
