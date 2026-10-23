"""PhotoService 测试：新增、替换、路径安全、事务补偿与日志。"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

import app.services.photo_service as photo_service_module
from app.config.paths import AppPaths
from app.services import (
    AuditWriteError,
    DataAccessError,
    InvalidPhotoError,
    PhotoPathError,
    PhotoService,
    PhotoStorageError,
    StudentService,
)


def _write_jpeg(path: Path, *, width: int = 8, height: int = 8) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    assert image.save(str(path), "JPEG") is True
    return path


def _photo_service(
    repository_database,
    tmp_path: Path,
    *,
    photos_dirname: str = "photos",
) -> PhotoService:
    paths = AppPaths.for_program(tmp_path)
    if photos_dirname == "photos":
        paths.ensure_user_dirs()
    return PhotoService(repository_database, paths)


def _create_record(service: PhotoService, student_no: str = "00123") -> int:
    return service.records.create({"student_no": student_no, "name": "测试"})


def test_add_photo_uses_student_number_filename_and_relative_db_value(
    repository_database, tmp_path: Path
) -> None:
    service = _photo_service(repository_database, tmp_path)
    record_id = _create_record(service)
    source = _write_jpeg(tmp_path / "source.jpg")

    result = service.set_photo(record_id, source)

    assert result.photo_path == "00123.jpg"
    assert (tmp_path / "photos" / "00123.jpg").is_file()
    assert service.records.get(record_id)["photo_path"] == "00123.jpg"
    assert source.is_file()
    assert "absolute" not in result.photo_path.lower()

    logs = service.logs.list_logs(operation_type="student_photo_update")
    assert len(logs) == 1
    assert logs[0]["result"] == "success"
    assert logs[0]["target_id"] == str(record_id)


def test_resolve_photo_uses_stored_relative_name_only(
    repository_database, tmp_path: Path
) -> None:
    service = _photo_service(repository_database, tmp_path)
    record_id = _create_record(service, "S-1")
    _write_jpeg(tmp_path / "photos" / "S-1.jpg")
    _write_jpeg(tmp_path / "photos" / "custom-name.jpg")
    service.records.update(record_id, {"photo_path": "custom-name.jpg"})

    resolved = service.resolve_photo(
        service.records.get(record_id)["photo_path"]
    )
    assert resolved == (tmp_path / "photos" / "custom-name.jpg").resolve()
    assert resolved.name != "S-1.jpg"


def test_replace_photo_keeps_old_file_until_new_one_is_committed(
    repository_database, tmp_path: Path
) -> None:
    service = _photo_service(repository_database, tmp_path)
    record_id = _create_record(service)
    first = _write_jpeg(tmp_path / "first.jpg")
    first_result = service.set_photo(record_id, first)
    old_path = tmp_path / "photos" / first_result.photo_path

    second = _write_jpeg(tmp_path / "second.jpg", width=12, height=12)
    second_result = service.set_photo(record_id, second)

    assert second_result.photo_path != first_result.photo_path
    assert second_result.photo_path.startswith("00123_")
    assert (tmp_path / "photos" / second_result.photo_path).is_file()
    assert not old_path.exists()
    assert service.records.get(record_id)["photo_path"] == second_result.photo_path


def test_existing_orphan_primary_name_is_not_overwritten(
    repository_database, tmp_path: Path
) -> None:
    service = _photo_service(repository_database, tmp_path)
    record_id = _create_record(service)
    orphan = _write_jpeg(tmp_path / "photos" / "00123.jpg")
    before = orphan.read_bytes()
    source = _write_jpeg(tmp_path / "source.jpg")

    result = service.set_photo(record_id, source)

    assert result.photo_path != "00123.jpg"
    assert orphan.read_bytes() == before


@pytest.mark.parametrize("suffix", [".png", ".txt", ".jpeg"])
def test_invalid_extension_is_rejected_but_jpeg_is_allowed(
    repository_database, tmp_path: Path, suffix: str
) -> None:
    service = _photo_service(repository_database, tmp_path)
    record_id = _create_record(service)
    source = _write_jpeg(tmp_path / f"source{suffix}")

    if suffix == ".jpeg":
        assert service.set_photo(record_id, source).photo_path == "00123.jpg"
        return

    with pytest.raises(InvalidPhotoError):
        service.set_photo(record_id, source)
    assert service.records.get(record_id)["photo_path"] is None
    assert list((tmp_path / "photos").iterdir()) == []


def test_corrupt_jpeg_is_rejected(repository_database, tmp_path: Path) -> None:
    service = _photo_service(repository_database, tmp_path)
    record_id = _create_record(service)
    source = tmp_path / "broken.jpg"
    source.write_bytes(b"not a jpeg")

    with pytest.raises(InvalidPhotoError):
        service.set_photo(record_id, source)
    assert service.records.get(record_id)["photo_path"] is None


def test_missing_source_is_rejected(repository_database, tmp_path: Path) -> None:
    service = _photo_service(repository_database, tmp_path)
    record_id = _create_record(service)

    with pytest.raises(InvalidPhotoError):
        service.set_photo(record_id, tmp_path / "missing.jpg")
    assert service.records.get(record_id)["photo_path"] is None


def test_database_failure_keeps_old_photo_and_cleans_new_file(
    repository_database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _photo_service(repository_database, tmp_path)
    record_id = _create_record(service)
    first = _write_jpeg(tmp_path / "first.jpg")
    old_result = service.set_photo(record_id, first)
    old_file = tmp_path / "photos" / old_result.photo_path
    second = _write_jpeg(tmp_path / "second.jpg")

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(service.records, "update", _boom)

    with pytest.raises(DataAccessError):
        service.set_photo(record_id, second)

    assert service.records.get(record_id)["photo_path"] == old_result.photo_path
    assert old_file.is_file()
    new_files = [
        path
        for path in (tmp_path / "photos").iterdir()
        if path.name != old_result.photo_path
    ]
    assert new_files == []


def test_non_sqlite_transaction_failure_cleans_new_file_and_reraises(
    repository_database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _photo_service(repository_database, tmp_path)
    record_id = _create_record(service)
    first = _write_jpeg(tmp_path / "first.jpg")
    old_result = service.set_photo(record_id, first)
    old_file = tmp_path / "photos" / old_result.photo_path
    second = _write_jpeg(tmp_path / "second.jpg")

    def _boom(*args, **kwargs):
        raise RuntimeError("unexpected transaction failure")

    monkeypatch.setattr(service.records, "update", _boom)

    with pytest.raises(RuntimeError, match="unexpected transaction failure"):
        service.set_photo(record_id, second)

    assert service.records.get(record_id)["photo_path"] == old_result.photo_path
    assert old_file.is_file()
    new_files = [
        path
        for path in (tmp_path / "photos").iterdir()
        if path.name != old_result.photo_path
    ]
    assert new_files == []


def test_partial_copy_runtime_error_cleans_new_file_and_reraises(
    repository_database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _photo_service(repository_database, tmp_path)
    record_id = _create_record(service)
    first = _write_jpeg(tmp_path / "first.jpg")
    old_result = service.set_photo(record_id, first)
    old_file = tmp_path / "photos" / old_result.photo_path
    second = _write_jpeg(tmp_path / "second.jpg")

    def _partial_copy(source_handle, target_handle, *args, **kwargs):
        target_handle.write(source_handle.read(64))
        target_handle.flush()
        raise RuntimeError("copy interrupted")

    monkeypatch.setattr(shutil, "copyfileobj", _partial_copy)

    with pytest.raises(RuntimeError, match="copy interrupted"):
        service.set_photo(record_id, second)

    assert service.records.get(record_id)["photo_path"] == old_result.photo_path
    assert old_file.is_file()
    assert [
        path for path in (tmp_path / "photos").iterdir() if path != old_file
    ] == []


def test_audit_failure_rolls_back_and_cleans_new_file(
    repository_database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _photo_service(repository_database, tmp_path)
    record_id = _create_record(service)
    first = _write_jpeg(tmp_path / "first.jpg")
    old_result = service.set_photo(record_id, first)
    second = _write_jpeg(tmp_path / "second.jpg")

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("audit unavailable")

    monkeypatch.setattr(service.logs, "create", _boom)

    with pytest.raises(AuditWriteError):
        service.set_photo(record_id, second)

    assert service.records.get(record_id)["photo_path"] == old_result.photo_path
    assert (tmp_path / "photos" / old_result.photo_path).is_file()
    assert len(list((tmp_path / "photos").iterdir())) == 1


def test_new_file_cleanup_failure_does_not_mask_database_error(
    repository_database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = _photo_service(repository_database, tmp_path)
    record_id = _create_record(service)
    source = _write_jpeg(tmp_path / "source.jpg")

    def _write_failure(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(service.records, "update", _write_failure)

    original_unlink = Path.unlink

    def _unlink_failure(self, *args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "unlink", _unlink_failure)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(DataAccessError):
            service.set_photo(record_id, source)

    assert service.records.get(record_id)["photo_path"] is None
    assert "清理新照片失败" in caplog.text or "清理新照片" in caplog.text

    monkeypatch.setattr(Path, "unlink", original_unlink)


def test_old_file_cleanup_failure_keeps_new_association(
    repository_database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = _photo_service(repository_database, tmp_path)
    record_id = _create_record(service)
    first = _write_jpeg(tmp_path / "first.jpg")
    old_result = service.set_photo(record_id, first)
    old_file = tmp_path / "photos" / old_result.photo_path
    second = _write_jpeg(tmp_path / "second.jpg")

    original_unlink = Path.unlink

    def _fail_old(self, *args, **kwargs):
        if self == old_file:
            raise PermissionError("locked")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _fail_old)

    with caplog.at_level(logging.WARNING):
        result = service.set_photo(record_id, second)

    assert result.cleanup_failed is True
    assert result.cleanup_message
    assert service.records.get(record_id)["photo_path"] == result.photo_path
    assert (tmp_path / "photos" / result.photo_path).is_file()
    assert old_file.is_file()
    assert "旧照片清理失败" in caplog.text


def test_old_file_shared_by_other_record_is_not_deleted(
    repository_database, tmp_path: Path
) -> None:
    service = _photo_service(repository_database, tmp_path)
    first_record = _create_record(service, "S-1")
    second_record = _create_record(service, "S-2")
    first = _write_jpeg(tmp_path / "first.jpg")
    old_result = service.set_photo(first_record, first)
    old_file = tmp_path / "photos" / old_result.photo_path
    service.records.update(second_record, {"photo_path": old_result.photo_path})

    second = _write_jpeg(tmp_path / "second.jpg")
    result = service.set_photo(first_record, second)

    assert old_file.is_file()
    assert service.records.get(second_record)["photo_path"] == old_result.photo_path
    assert service.records.get(first_record)["photo_path"] == result.photo_path


def test_delete_record_keeps_photo_file(
    repository_database, tmp_path: Path
) -> None:
    service = _photo_service(repository_database, tmp_path)
    record_id = _create_record(service)
    source = _write_jpeg(tmp_path / "source.jpg")
    result = service.set_photo(record_id, source)
    file_path = tmp_path / "photos" / result.photo_path

    StudentService(repository_database).delete_record(record_id)

    assert not service.records.get(record_id)
    assert file_path.is_file()


def test_unsafe_stored_path_is_not_deleted_or_accessed(
    repository_database, tmp_path: Path
) -> None:
    service = _photo_service(repository_database, tmp_path)
    record_id = _create_record(service)
    outside = _write_jpeg(tmp_path / "escape.jpg")
    service.records.update(record_id, {"photo_path": "../escape.jpg"})

    with pytest.raises(PhotoPathError):
        service.resolve_photo("../escape.jpg")

    source = _write_jpeg(tmp_path / "source.jpg")
    result = service.set_photo(record_id, source)

    assert outside.is_file()
    assert service.records.get(record_id)["photo_path"] == result.photo_path


def test_filename_collision_retries_and_then_reports_storage_error(
    repository_database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _photo_service(repository_database, tmp_path)
    record_id = _create_record(service)
    _write_jpeg(tmp_path / "photos" / "00123.jpg")
    _write_jpeg(tmp_path / "photos" / "00123_deadbeef.jpg")
    source = _write_jpeg(tmp_path / "source.jpg")

    class _FakeUuid:
        hex = "deadbeef"

    monkeypatch.setattr(photo_service_module, "uuid4", lambda: _FakeUuid())

    with pytest.raises(PhotoStorageError):
        service.set_photo(record_id, source)

    assert service.records.get(record_id)["photo_path"] is None
    assert (tmp_path / "photos" / "00123.jpg").is_file()
    assert (tmp_path / "photos" / "00123_deadbeef.jpg").is_file()


def test_orphan_files_are_not_removed_on_success(
    repository_database, tmp_path: Path
) -> None:
    service = _photo_service(repository_database, tmp_path)
    record_id = _create_record(service)
    orphan = _write_jpeg(tmp_path / "photos" / "unrelated.jpg")
    source = _write_jpeg(tmp_path / "source.jpg")

    service.set_photo(record_id, source)

    assert orphan.is_file()


def test_photos_dir_blocked_by_file_reports_storage_error(
    repository_database, tmp_path: Path
) -> None:
    program_root = tmp_path / "blocked"
    program_root.mkdir()
    (program_root / "photos").write_text("not a directory", encoding="utf-8")
    service = PhotoService(repository_database, AppPaths.for_program(program_root))
    record_id = _create_record(service)
    source = _write_jpeg(tmp_path / "source.jpg")

    with pytest.raises(PhotoStorageError):
        service.set_photo(record_id, source)

    assert service.records.get(record_id)["photo_path"] is None


def test_missing_record_does_not_leave_new_file(
    repository_database, tmp_path: Path
) -> None:
    service = _photo_service(repository_database, tmp_path)
    source = _write_jpeg(tmp_path / "source.jpg")

    with pytest.raises(Exception):
        service.set_photo(999, source)

    assert list((tmp_path / "photos").iterdir()) == []
