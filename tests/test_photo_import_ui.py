"""照片管理菜单与导入对话框 UI 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QMenu

from app.config.paths import AppPaths
from app.services import PhotoImportService, PhotoService


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


def _write_jpeg(path: Path, *, width: int = 8, height: int = 8) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.blue)
    assert image.save(str(path), "JPEG") is True
    return path


def _build_window(tmp_path, qapp: QApplication):
    from app.bootstrap import bootstrap
    from app.ui.main_window import MainWindow

    paths = AppPaths.for_program(tmp_path)
    context = bootstrap(paths)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings, context=context)
    window.show()
    qapp.processEvents()
    return window, context


def _photo_menu(window) -> QMenu:
    return next(
        menu for menu in window.findChildren(QMenu) if menu.title() == "照片管理(&Z)"
    )


def test_photo_menu_has_expected_actions(qapp: QApplication, tmp_path) -> None:
    window, _context = _build_window(tmp_path, qapp)

    assert window.photo_service is not None
    assert window.photo_import_service is not None
    menu = _photo_menu(window)
    assert [action.text() for action in menu.actions()] == [
        "导入照片(&I)…",
        "为选中记录设置照片(&S)…",
        "打开照片目录(&O)",
    ]

    window.close()


def test_open_photos_directory_opens_existing_directory(
    qapp: QApplication, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, context = _build_window(tmp_path, qapp)

    opened: list[str] = []

    class _FakeDesktopServices:
        @staticmethod
        def openUrl(url) -> bool:
            opened.append(url.toLocalFile())
            return True

    from app.ui import main_window as main_window_module

    monkeypatch.setattr(
        main_window_module, "QDesktopServices", _FakeDesktopServices
    )

    window.open_photos_directory()

    assert context.paths.photos_dir.is_dir()
    assert [Path(item) for item in opened] == [context.paths.photos_dir]
    window.close()


def test_photo_import_dialog_scans_and_imports(
    qapp: QApplication,
    tmp_path,
    repository_database,
    app_paths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ui import photo_import_dialog as dialog_module
    from app.ui.photo_import_dialog import PhotoImportDialog

    photos = PhotoService(repository_database, app_paths)
    record_id = photos.records.create({"student_no": "00123", "name": "张三"})
    folder = tmp_path / "batch"
    folder.mkdir()
    _write_jpeg(folder / "00123.jpg")

    dialog = PhotoImportDialog(PhotoImportService(photos))
    dialog.folder_edit.setText(str(folder))
    dialog.scan()

    assert dialog.scan_result is not None
    assert dialog.scan_result.candidate_count == 1
    assert dialog.import_button.isEnabled() is True
    assert "可导入学生：1" in dialog.summary_label.text()

    monkeypatch.setattr(
        dialog_module.QMessageBox,
        "information",
        staticmethod(lambda *args, **kwargs: None),
    )
    dialog.start_import()

    assert dialog.last_report is not None
    assert dialog.last_report.imported_count == 1
    assert photos.records.get(record_id)["photo_path"] == "00123.jpg"
    assert dialog.import_button.isEnabled() is False
    dialog.close()


def test_dialog_initialisation_does_not_hit_slot_errors(
    qapp: QApplication,
    repository_database,
    app_paths,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """默认选中会同步触发 toggled；构造期不得因控件尚未创建而打印槽异常。"""
    from app.ui.photo_import_dialog import PhotoImportDialog

    photos = PhotoService(repository_database, app_paths)
    dialog = PhotoImportDialog(PhotoImportService(photos))
    qapp.processEvents()

    captured = capfd.readouterr()
    assert "Traceback" not in captured.err
    assert "AttributeError" not in captured.err
    assert dialog.import_button.isEnabled() is False
    assert dialog.scan_result is None
    dialog.close()


def test_set_photo_for_selected_updates_record(
    qapp: QApplication, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import StudentService
    from app.ui import student_page as student_page_module

    window, context = _build_window(tmp_path, qapp)
    page = window.student_page
    assert page is not None

    record_id = StudentService(context.database).create_record(
        {"student_no": "00123", "name": "张三"}
    )
    page.reload(reset_page=True)
    page.table.selectRow(0)
    source = _write_jpeg(tmp_path / "source.jpg")

    monkeypatch.setattr(
        student_page_module.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(source), "")),
    )
    monkeypatch.setattr(
        student_page_module.QMessageBox,
        "information",
        staticmethod(lambda *args, **kwargs: None),
    )

    page.set_photo_for_selected()

    assert window.photo_service is not None
    assert (
        window.photo_service.records.get(record_id)["photo_path"] == "00123.jpg"
    )
    assert "照片已更新" in page.message_label.text()
    window.close()


def test_set_photo_without_selection_shows_readable_message(
    qapp: QApplication, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ui import student_page as student_page_module

    window, _context = _build_window(tmp_path, qapp)
    page = window.student_page
    assert page is not None

    messages: list[str] = []
    monkeypatch.setattr(
        student_page_module.QMessageBox,
        "information",
        staticmethod(lambda parent, title, message, *args, **kwargs: messages.append(message)),
    )

    page.set_photo_for_selected()

    assert messages == ["请先选择要设置照片的记录"]
    window.close()
