"""照片界面测试：占位、按数据库路径加载、选择/取消与保存接线。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from app.bootstrap import bootstrap
from app.config.paths import AppPaths
from app.services import PhotoService, PhotoStorageError, StudentService
from app.ui.main_window import MainWindow
from app.ui.student_dialog import StudentDialog
from app.ui.student_page import StudentPage


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def student_service(repository_database) -> StudentService:
    return StudentService(repository_database)


def _write_jpeg(path: Path, *, width: int = 8, height: int = 8) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.blue)
    assert image.save(str(path), "JPEG") is True
    return path


def _record_with_photo(
    photo_service: PhotoService, student_no: str, photo_path: str | None
) -> dict:
    record_id = photo_service.records.create(
        {"student_no": student_no, "name": "照片测试", "photo_path": photo_path}
    )
    return photo_service.records.get(record_id)


@pytest.fixture
def record(photo_service: PhotoService) -> dict:
    return _record_with_photo(photo_service, "00123", None)


def test_dialog_without_photo_shows_placeholder(
    photo_service: PhotoService, record: dict, qapp: QApplication
) -> None:
    dialog = StudentDialog(photo_service=photo_service, record=record)

    assert dialog.photo_button.isEnabled() is True
    assert "尚未" in dialog.photo_hint_label.text() or "未上传" in dialog.photo_hint_label.text()
    assert dialog.preview_pixmap().isNull()
    dialog.close()


def test_add_mode_disables_photo_until_record_saved(
    photo_service: PhotoService, qapp: QApplication
) -> None:
    dialog = StudentDialog(photo_service=photo_service)

    assert dialog.photo_button.isEnabled() is False
    assert "保存记录后" in dialog.photo_hint_label.text()
    dialog.close()


def test_dialog_loads_photo_from_database_path_only(
    photo_service: PhotoService, qapp: QApplication
) -> None:
    photos_dir = photo_service.paths.photos_dir
    _write_jpeg(photos_dir / "S-1.jpg")
    custom = _write_jpeg(photos_dir / "custom-name.jpg", width=40, height=20)
    record = _record_with_photo(photo_service, "S-1", custom.name)

    dialog = StudentDialog(photo_service=photo_service, record=record)

    assert "custom-name.jpg" in dialog.photo_hint_label.text()
    pixmap = dialog.preview_pixmap()
    assert not pixmap.isNull()
    # 宽高比保持 2:1
    assert abs(pixmap.width() / pixmap.height() - 2.0) < 0.05
    dialog.close()


def test_unregistered_same_name_file_is_not_auto_loaded(
    photo_service: PhotoService, qapp: QApplication
) -> None:
    _write_jpeg(photo_service.paths.photos_dir / "D-1.jpg")
    record = _record_with_photo(photo_service, "D-1", None)

    dialog = StudentDialog(photo_service=photo_service, record=record)

    assert "尚未" in dialog.photo_hint_label.text()
    assert dialog.preview_pixmap().isNull()
    dialog.close()


def test_missing_or_corrupt_photo_falls_back_to_placeholder(
    photo_service: PhotoService, qapp: QApplication
) -> None:
    missing = _record_with_photo(photo_service, "M-1", "missing.jpg")
    missing_dialog = StudentDialog(photo_service=photo_service, record=missing)
    assert "不存在" in missing_dialog.photo_hint_label.text()
    assert missing_dialog.preview_pixmap().isNull()
    missing_dialog.close()

    broken_path = photo_service.paths.photos_dir / "broken.jpg"
    broken_path.write_bytes(b"not a jpeg")
    broken = _record_with_photo(photo_service, "B-1", "broken.jpg")
    broken_dialog = StudentDialog(photo_service=photo_service, record=broken)
    assert "无法读取" in broken_dialog.photo_hint_label.text()
    assert broken_dialog.preview_pixmap().isNull()
    broken_dialog.close()


def test_unsafe_stored_path_is_not_loaded(
    photo_service: PhotoService, qapp: QApplication
) -> None:
    record = _record_with_photo(photo_service, "U-1", "../escape.jpg")

    dialog = StudentDialog(photo_service=photo_service, record=record)

    assert "不安全" in dialog.photo_hint_label.text()
    assert dialog.preview_pixmap().isNull()
    dialog.close()


def test_choose_photo_cancel_keeps_current_state(
    photo_service: PhotoService,
    record: dict,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = StudentDialog(photo_service=photo_service, record=record)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *args, **kwargs: ("", "")
    )

    dialog.choose_photo()

    assert dialog.pending_photo_source() is None
    assert dialog.photo_result is None
    dialog.close()


def test_choose_photo_rejects_invalid_format_with_warning(
    photo_service: PhotoService,
    record: dict,
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = tmp_path / "source.png"
    invalid.write_bytes(b"\x89PNG\r\n\x1a\n")
    warnings: list[str] = []
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(invalid), "JPEG 图片 (*.jpg *.jpeg)"),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda parent, title, message, *args, **kwargs: warnings.append(message),
    )
    dialog = StudentDialog(photo_service=photo_service, record=record)

    dialog.choose_photo()

    assert dialog.pending_photo_source() is None
    assert warnings and "JPG" in warnings[0]
    dialog.close()


def test_choose_then_save_persists_photo_and_keeps_source(
    photo_service: PhotoService,
    record: dict,
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_jpeg(tmp_path / "source.jpg", width=30, height=40)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(source), "JPEG 图片 (*.jpg *.jpeg)"),
    )
    dialog = StudentDialog(photo_service=photo_service, record=record)

    dialog.choose_photo()
    assert dialog.pending_photo_source() == source.resolve()
    assert "待保存" in dialog.photo_hint_label.text()
    assert not dialog.preview_pixmap().isNull()

    dialog._save()

    assert dialog.photo_result is not None
    assert dialog.photo_result.photo_path == "00123.jpg"
    assert photo_service.records.get(record["id"])["photo_path"] == "00123.jpg"
    assert (photo_service.paths.photos_dir / "00123.jpg").is_file()
    assert source.is_file()
    assert dialog.result() == QDialog.DialogCode.Accepted
    dialog.close()


def test_page_and_main_window_receive_photo_service(
    student_service: StudentService,
    photo_service: PhotoService,
    qapp: QApplication,
) -> None:
    page = StudentPage(student_service, photo_service=photo_service)
    assert page.photo_service is photo_service
    page.close()


def test_photo_failure_warns_fields_saved_and_retry_skips_unchanged_fields(
    student_service: StudentService,
    photo_service: PhotoService,
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record_id = student_service.create_record(
        {"student_no": "00123", "name": "原姓名"}
    )
    source = _write_jpeg(tmp_path / "source.jpg")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(source), "JPEG")
    )
    calls: list[dict[str, str]] = []

    def on_save(values: dict[str, str]) -> None:
        calls.append(dict(values))
        student_service.update_record(record_id, values)

    dialog = StudentDialog(
        photo_service=photo_service,
        record=photo_service.records.get(record_id),
        on_save=on_save,
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda parent, title, message, *args, **kwargs: warnings.append(message),
    )
    original_set_photo = photo_service.set_photo

    def _fail(*args, **kwargs):
        raise PhotoStorageError("模拟照片写入失败")

    monkeypatch.setattr(photo_service, "set_photo", _fail)
    dialog.choose_photo()
    dialog._save()

    assert len(calls) == 1
    assert dialog.photo_result is None
    assert warnings
    assert "学生字段修改已经保存" in warnings[-1]
    assert "毕业照片尚未成功更新" in warnings[-1]
    assert "取消对话框不会撤销" in warnings[-1]
    assert photo_service.records.get(record_id)["photo_path"] is None
    assert list(photo_service.paths.photos_dir.iterdir()) == []

    monkeypatch.setattr(photo_service, "set_photo", original_set_photo)
    dialog._save()

    assert len(calls) == 1
    assert dialog.photo_result is not None
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert photo_service.records.get(record_id)["photo_path"] == "00123.jpg"
    success_logs = [
        log
        for log in student_service.logs.list_logs(operation_type="student_update")
        if log["result"] == "success"
    ]
    assert len(success_logs) == 1
    dialog.close()


def test_photo_failure_then_field_change_saves_new_values(
    student_service: StudentService,
    photo_service: PhotoService,
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record_id = student_service.create_record(
        {"student_no": "00123", "name": "原姓名"}
    )
    source = _write_jpeg(tmp_path / "source.jpg")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(source), "JPEG")
    )
    calls: list[dict[str, str]] = []

    def on_save(values: dict[str, str]) -> None:
        calls.append(dict(values))
        student_service.update_record(record_id, values)

    dialog = StudentDialog(
        photo_service=photo_service,
        record=photo_service.records.get(record_id),
        on_save=on_save,
    )
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kwargs: None
    )
    original_set_photo = photo_service.set_photo

    def _fail(*args, **kwargs):
        raise PhotoStorageError("模拟照片写入失败")

    monkeypatch.setattr(photo_service, "set_photo", _fail)
    dialog.choose_photo()
    dialog._save()

    dialog._editors["name"].setText("新姓名")
    monkeypatch.setattr(photo_service, "set_photo", original_set_photo)
    dialog._save()

    assert len(calls) == 2
    assert calls[-1]["name"] == "新姓名"
    assert student_service.records.get(record_id)["name"] == "新姓名"
    assert student_service.records.get(record_id)["photo_path"] == "00123.jpg"
    assert dialog.result() == QDialog.DialogCode.Accepted
    success_logs = [
        log
        for log in student_service.logs.list_logs(operation_type="student_update")
        if log["result"] == "success"
    ]
    assert len(success_logs) == 2
    dialog.close()


def test_cancel_after_photo_failure_keeps_committed_fields(
    student_service: StudentService,
    photo_service: PhotoService,
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record_id = student_service.create_record(
        {"student_no": "00123", "name": "原姓名"}
    )
    source = _write_jpeg(tmp_path / "source.jpg")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(source), "JPEG")
    )
    dialog = StudentDialog(
        photo_service=photo_service,
        record=photo_service.records.get(record_id),
        on_save=lambda values: student_service.update_record(record_id, values),
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    dialog._editors["name"].setText("已提交姓名")

    original_set_photo = photo_service.set_photo

    def _fail(*args, **kwargs):
        raise PhotoStorageError("模拟照片写入失败")

    monkeypatch.setattr(photo_service, "set_photo", _fail)
    dialog.choose_photo()
    dialog._save()

    assert student_service.records.get(record_id)["name"] == "已提交姓名"
    assert photo_service.records.get(record_id)["photo_path"] is None

    dialog.reject()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert student_service.records.get(record_id)["name"] == "已提交姓名"
    assert photo_service.records.get(record_id)["photo_path"] is None
    assert list(photo_service.paths.photos_dir.iterdir()) == []
    monkeypatch.setattr(photo_service, "set_photo", original_set_photo)
    dialog.close()


def test_main_window_builds_photo_service(
    qapp: QApplication, tmp_path: Path
) -> None:
    context = bootstrap(AppPaths.for_program(tmp_path))
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings, context=context)

    assert window.photo_service is not None
    assert window.student_page is not None
    assert window.student_page.photo_service is window.photo_service

    window.close()
    settings.sync()
