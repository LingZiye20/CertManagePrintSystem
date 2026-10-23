"""PhotoImportService 测试：扫描匹配、冲突分类、逐条导入与旧照片清理。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from app.services import (
    PhotoImportError,
    PhotoImportMode,
    PhotoImportService,
    PhotoService,
)


def _write_jpeg(path: Path, *, width: int = 8, height: int = 8) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    assert image.save(str(path), "JPEG") is True
    return path


def _services(repository_database, app_paths) -> tuple[PhotoService, PhotoImportService]:
    photos = PhotoService(repository_database, app_paths)
    return photos, PhotoImportService(photos)


def _create_record(
    photos: PhotoService, student_no: str, name: str = "测试", **values: object
) -> int:
    return photos.records.create({"student_no": student_no, "name": name, **values})


def test_import_by_student_no_updates_photo_and_writes_audit(
    repository_database, app_paths, tmp_path: Path
) -> None:
    photos, importer = _services(repository_database, app_paths)
    record_id = _create_record(photos, "00123", "张三")
    folder = tmp_path / "batch"
    folder.mkdir()
    _write_jpeg(folder / "00123.jpg")

    scan = importer.scan(folder, "student_no")

    assert scan.candidate_count == 1
    assert scan.candidates[0].file_name == "00123.jpg"
    assert scan.candidates[0].student_no == "00123"
    assert scan.existing_photo_count == 0
    assert list(app_paths.photos_dir.iterdir()) == []
    assert photos.records.get(record_id)["photo_path"] is None

    report = importer.import_photos(scan)

    assert report.imported_count == 1
    assert report.failed_count == 0
    assert report.skipped_count == 0
    assert photos.records.get(record_id)["photo_path"] == "00123.jpg"
    assert (app_paths.photos_dir / "00123.jpg").is_file()
    logs = photos.logs.list_logs(operation_type="student_photo_update")
    assert len(logs) == 1
    assert logs[0]["result"] == "success"
    assert "成功 1" in report.message


def test_filename_whitespace_is_stripped_and_non_jpeg_files_are_ignored(
    repository_database, app_paths, tmp_path: Path
) -> None:
    photos, importer = _services(repository_database, app_paths)
    record_id = _create_record(photos, "00123", "张三")
    folder = tmp_path / "batch"
    folder.mkdir()
    _write_jpeg(folder / " 00123 .jpg")
    (folder / "notes.txt").write_text("hello", encoding="utf-8")
    _write_jpeg(folder / "extra.png")

    scan = importer.scan(folder, "student_no")

    assert scan.candidate_count == 1
    assert scan.candidates[0].file_name == " 00123 .jpg"
    assert scan.ignored_files == ("extra.png", "notes.txt")

    report = importer.import_photos(scan)

    assert report.imported_count == 1
    assert report.ignored_count == 2
    assert photos.records.get(record_id)["photo_path"] == "00123.jpg"


def test_unmatched_files_and_blank_field_values_are_reported(
    repository_database, app_paths, tmp_path: Path
) -> None:
    photos, importer = _services(repository_database, app_paths)
    _create_record(photos, "S-1", "空证件号", id_number="   ")
    folder = tmp_path / "batch"
    folder.mkdir()
    _write_jpeg(folder / "   .jpg")
    _write_jpeg(folder / "ID-9.jpg")

    scan = importer.scan(folder, "id_number")

    assert scan.candidate_count == 0
    assert scan.ambiguous_files == ()
    assert {issue.file_name for issue in scan.unmatched_files} == {
        "   .jpg",
        "ID-9.jpg",
    }
    assert all("没有学生" in issue.reason for issue in scan.unmatched_files)


def test_ambiguous_field_value_skips_all_matching_files(
    repository_database, app_paths, tmp_path: Path
) -> None:
    photos, importer = _services(repository_database, app_paths)
    _create_record(photos, "S-1", "甲", id_number="ID-1")
    _create_record(photos, "S-2", "乙", id_number="ID-1")
    folder = tmp_path / "batch"
    folder.mkdir()
    _write_jpeg(folder / "ID-1.jpg")

    scan = importer.scan(folder, "id_number")

    assert scan.candidate_count == 0
    assert len(scan.ambiguous_files) == 1
    assert "字段值不唯一" in scan.ambiguous_files[0].reason
    assert "2 条学生记录" in scan.ambiguous_files[0].reason


def test_two_files_for_one_student_uses_first_by_name(
    repository_database, app_paths, tmp_path: Path
) -> None:
    photos, importer = _services(repository_database, app_paths)
    record_id = _create_record(photos, "00123", "张三")
    folder = tmp_path / "batch"
    folder.mkdir()
    _write_jpeg(folder / "00123 .jpeg")
    _write_jpeg(folder / "00123.jpg", width=12, height=12)

    scan = importer.scan(folder, "student_no")

    assert scan.candidate_count == 1
    assert scan.candidates[0].file_name == "00123 .jpeg"
    assert len(scan.duplicate_files) == 1
    assert scan.duplicate_files[0].file_name == "00123.jpg"
    assert "已由其他文件匹配" in scan.duplicate_files[0].reason

    report = importer.import_photos(scan)

    assert report.imported_count == 1
    assert photos.records.get(record_id)["photo_path"] == "00123.jpg"
    assert len(photos.logs.list_logs(operation_type="student_photo_update")) == 1


def test_skip_existing_photo_keeps_association_and_file(
    repository_database, app_paths, tmp_path: Path
) -> None:
    photos, importer = _services(repository_database, app_paths)
    record_id = _create_record(photos, "00123", "张三")
    old_result = photos.set_photo(record_id, _write_jpeg(tmp_path / "old.jpg"))
    old_file = app_paths.photos_dir / old_result.photo_path

    folder = tmp_path / "batch"
    folder.mkdir()
    _write_jpeg(folder / "00123.jpg")
    scan = importer.scan(folder, "student_no")

    assert scan.existing_photo_count == 1
    report = importer.import_photos(scan, mode=PhotoImportMode.SKIP_EXISTING)

    assert report.imported_count == 0
    assert report.skipped_count == 1
    assert "已有照片" in report.skipped[0].reason
    assert photos.records.get(record_id)["photo_path"] == old_result.photo_path
    assert old_file.is_file()
    assert len(photos.logs.list_logs(operation_type="student_photo_update")) == 1


def test_overwrite_existing_photo_removes_old_file(
    repository_database, app_paths, tmp_path: Path
) -> None:
    photos, importer = _services(repository_database, app_paths)
    record_id = _create_record(photos, "00123", "张三")
    old_result = photos.set_photo(record_id, _write_jpeg(tmp_path / "old.jpg"))
    old_file = app_paths.photos_dir / old_result.photo_path

    folder = tmp_path / "batch"
    folder.mkdir()
    _write_jpeg(folder / "00123.jpg", width=16, height=16)
    scan = importer.scan(folder, "student_no")

    report = importer.import_photos(scan, mode=PhotoImportMode.OVERWRITE)

    assert report.imported_count == 1
    assert report.imported[0].cleanup_failed is False
    new_path = report.imported[0].photo_path
    assert new_path != old_result.photo_path
    assert (app_paths.photos_dir / new_path).is_file()
    assert not old_file.exists()
    assert photos.records.get(record_id)["photo_path"] == new_path
    assert len(photos.logs.list_logs(operation_type="student_photo_update")) == 2


def test_overwrite_reports_cleanup_failure_but_keeps_new_association(
    repository_database, app_paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photos, importer = _services(repository_database, app_paths)
    record_id = _create_record(photos, "00123", "张三")
    old_result = photos.set_photo(record_id, _write_jpeg(tmp_path / "old.jpg"))
    old_file = app_paths.photos_dir / old_result.photo_path

    folder = tmp_path / "batch"
    folder.mkdir()
    _write_jpeg(folder / "00123.jpg", width=16, height=16)
    scan = importer.scan(folder, "student_no")

    original_unlink = Path.unlink

    def _fail_old(self, *args, **kwargs):
        if self == old_file:
            raise PermissionError("locked")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _fail_old)

    report = importer.import_photos(scan, mode=PhotoImportMode.OVERWRITE)

    assert report.imported_count == 1
    assert report.imported[0].cleanup_failed is True
    assert report.imported[0].cleanup_message
    assert report.cleanup_warnings
    assert old_file.is_file()
    assert photos.records.get(record_id)["photo_path"] == report.imported[0].photo_path
    assert (app_paths.photos_dir / report.imported[0].photo_path).is_file()


def test_corrupt_jpeg_is_reported_without_blocking_other_files(
    repository_database, app_paths, tmp_path: Path
) -> None:
    photos, importer = _services(repository_database, app_paths)
    good_id = _create_record(photos, "001", "甲")
    bad_id = _create_record(photos, "002", "乙")
    folder = tmp_path / "batch"
    folder.mkdir()
    _write_jpeg(folder / "001.jpg")
    (folder / "002.jpg").write_bytes(b"not a jpeg")

    scan = importer.scan(folder, "student_no")

    assert scan.candidate_count == 1
    assert len(scan.invalid_files) == 1
    assert scan.invalid_files[0].file_name == "002.jpg"
    assert scan.invalid_files[0].student_no == "002"

    report = importer.import_photos(scan)

    assert report.imported_count == 1
    assert report.failed_count == 0
    assert photos.records.get(good_id)["photo_path"] == "001.jpg"
    assert photos.records.get(bad_id)["photo_path"] is None


def test_single_item_failure_does_not_stop_remaining_imports(
    repository_database, app_paths, tmp_path: Path
) -> None:
    photos, importer = _services(repository_database, app_paths)
    first_id = _create_record(photos, "001", "甲")
    second_id = _create_record(photos, "002", "乙")
    folder = tmp_path / "batch"
    folder.mkdir()
    _write_jpeg(folder / "001.jpg")
    _write_jpeg(folder / "002.jpg")
    scan = importer.scan(folder, "student_no")

    photos.records.delete(second_id)
    report = importer.import_photos(scan)

    assert report.imported_count == 1
    assert report.failed_count == 1
    assert report.failed[0].student_no == "002"
    assert "不存在" in report.failed[0].reason
    assert photos.records.get(first_id)["photo_path"] == "001.jpg"


@pytest.mark.parametrize("field", ["name", "photo_path", ""])
def test_unsupported_match_field_is_rejected(
    repository_database, app_paths, tmp_path: Path, field: str
) -> None:
    _photos, importer = _services(repository_database, app_paths)
    folder = tmp_path / "batch"
    folder.mkdir()

    with pytest.raises(PhotoImportError):
        importer.scan(folder, field)


def test_missing_folder_is_rejected(repository_database, app_paths, tmp_path: Path) -> None:
    _photos, importer = _services(repository_database, app_paths)

    with pytest.raises(PhotoImportError):
        importer.scan(tmp_path / "missing", "student_no")


def test_missing_source_between_scan_and_import_fails_only_that_item(
    repository_database, app_paths, tmp_path: Path
) -> None:
    photos, importer = _services(repository_database, app_paths)
    record_id = _create_record(photos, "001", "甲")
    folder = tmp_path / "batch"
    folder.mkdir()
    source = _write_jpeg(folder / "001.jpg")
    scan = importer.scan(folder, "student_no")

    source.unlink()
    report = importer.import_photos(scan)

    assert report.imported_count == 0
    assert report.failed_count == 1
    assert report.failed[0].student_no == "001"
    assert photos.records.get(record_id)["photo_path"] is None
