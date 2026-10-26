"""系统重置 Service 测试：清理范围、事务回滚、文件暂存与审计。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.config.paths import AppPaths
from app.data.db import Database, get_schema_version
from app.repositories import (
    AppSettingsRepository,
    ImportBatchRepository,
    OperationLogRepository,
    PrintRecordRepository,
)
from app.services import NumberingService, StudentService, TemplateService
from app.services.reset_service import ResetService
from app.services.template_model import (
    CertificateTemplate,
    FontSpec,
    TextElement,
    template_to_json,
)
from app.services.template_service import BUILTIN_TEMPLATE_FILES

FONT = "Microsoft YaHei"


def _template(name: str, certificate_type: str) -> CertificateTemplate:
    return CertificateTemplate(
        name=name,
        certificate_type=certificate_type,
        canvas_width_mm=210.0,
        canvas_height_mm=297.0,
        elements=(
            TextElement(
                id="name",
                x_mm=10.0,
                y_mm=10.0,
                width_mm=60.0,
                height_mm=12.0,
                font=FontSpec(family=FONT, size_pt=14.0),
                align="center",
                vertical_align="middle",
                wrap="none",
                field="name",
            ),
        ),
    )


def _write_builtin_resources(paths: AppPaths) -> None:
    directory = paths.resource_dir / "templates"
    directory.mkdir(parents=True, exist_ok=True)
    for certificate_type, file_name in BUILTIN_TEMPLATE_FILES.items():
        (directory / file_name).write_text(
            template_to_json(_template(f"内置-{certificate_type}", certificate_type)),
            encoding="utf-8",
        )


@pytest.fixture
def service(
    repository_database: Database, app_paths: AppPaths
) -> ResetService:
    _write_builtin_resources(app_paths)
    return ResetService(repository_database, app_paths)


def _seed(database: Database, paths: AppPaths) -> None:
    (paths.photos_dir / "a.jpg").write_bytes(b"jpg-a")
    (paths.photos_dir / "b.jpg").write_bytes(b"jpg-b")
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO certificate_records (student_no, name, certificate_no, photo_path) "
            "VALUES ('00123', '张三', 'NO-1', 'a.jpg')"
        )
        record_id = connection.execute(
            "INSERT INTO certificate_records (student_no, name, photo_path) "
            "VALUES ('S-2', '李四', 'b.jpg')"
        ).lastrowid
        connection.execute(
            "INSERT INTO print_records (certificate_record_id, certificate_type, "
            "certificate_no_snapshot, result) VALUES (?, 'graduation', 'NO-1', 'unknown')",
            (record_id,),
        )
        connection.execute(
            "INSERT INTO import_batches (file_name, status, total_rows) "
            "VALUES ('students.xlsx', 'success', 2)"
        )
        connection.execute(
            "INSERT INTO app_settings (key, value) VALUES ('student.page_size', '50')"
        )
        connection.execute(
            "UPDATE numbering_rules SET fixed_text_1 = 'X', sequence_width = 6 "
            "WHERE rule_key = 'graduation'"
        )
        connection.execute(
            "INSERT INTO operation_logs (operation_type, target_type, summary, result) "
            "VALUES ('student_create', 'certificate_record', '旧审计', 'success')"
        )


def test_describe_scope_lists_cleared_and_kept(service: ResetService) -> None:
    scope = service.describe_scope()

    joined = " ".join(scope.clears)
    for keyword in ("学生", "打印", "导入", "操作日志", "照片", "自定义模板", "编号"):
        assert keyword in joined
    kept = " ".join(scope.keeps)
    assert "QSettings" in kept or "界面" in kept
    assert "内置模板" in kept
    assert "数据库结构" in kept


def test_success_clears_business_tables(
    service: ResetService, repository_database: Database, app_paths: AppPaths
) -> None:
    _seed(repository_database, app_paths)
    service.template_service.create_template(_template("自定义", "graduation"))

    result = service.reset_system()

    assert result.success is True
    with repository_database.connect() as connection:
        for table in (
            "certificate_records",
            "print_records",
            "import_batches",
            "app_settings",
        ):
            total = connection.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()
            assert total["total"] == 0, table
    summaries = service.template_service.list_templates()
    assert len(summaries) == 3
    assert all(item.is_builtin for item in summaries)


def test_builtin_templates_restored(
    service: ResetService, repository_database: Database, app_paths: AppPaths
) -> None:
    _seed(repository_database, app_paths)

    result = service.reset_system()

    assert result.success is True
    summaries = service.template_service.list_templates()
    assert len(summaries) == 3
    assert all(item.is_builtin for item in summaries)


def test_numbering_rules_restored(
    service: ResetService, repository_database: Database, app_paths: AppPaths
) -> None:
    _seed(repository_database, app_paths)

    result = service.reset_system()

    assert result.success is True
    with repository_database.connect() as connection:
        rule = connection.execute(
            "SELECT * FROM numbering_rules WHERE rule_key = 'graduation'"
        ).fetchone()
    assert rule["fixed_text_1"] == ""
    assert rule["sequence_width"] == 4
    assert rule["education_levels"] == "[]"

    # 重置不涉及任何流水计数器：手写起始值即可重新编号。
    numbering = NumberingService(repository_database)
    assert numbering.preview("graduation", 1).number == "0001"
    # app_settings 被清空后，上次证书类型回退到毕结业证书。
    assert numbering.get_last_type() == "graduation"


def test_photos_removed_and_unrelated_files_kept(
    service: ResetService, repository_database: Database, app_paths: AppPaths
) -> None:
    _seed(repository_database, app_paths)
    keep_file = app_paths.program_root / "keep.txt"
    keep_file.write_text("keep", encoding="utf-8")
    resource_file = app_paths.resource_dir / "templates" / BUILTIN_TEMPLATE_FILES["graduation"]
    resource_bytes = resource_file.read_bytes()

    result = service.reset_system()

    assert result.success is True
    assert result.photos_removed == 2
    assert list(app_paths.photos_dir.iterdir()) == []
    assert keep_file.read_text(encoding="utf-8") == "keep"
    assert resource_file.read_bytes() == resource_bytes


def test_non_file_items_in_photos_are_preserved_with_warning(
    service: ResetService, repository_database: Database, app_paths: AppPaths
) -> None:
    _seed(repository_database, app_paths)
    nested = app_paths.photos_dir / "nested"
    nested.mkdir()
    (nested / "keep.jpg").write_bytes(b"x")

    result = service.reset_system()

    assert result.success is True
    assert nested.is_dir()
    assert (nested / "keep.jpg").exists()
    assert any("非文件" in warning for warning in result.warnings)


def test_operation_logs_cleared_then_reset_audit_kept(
    service: ResetService, repository_database: Database, app_paths: AppPaths
) -> None:
    _seed(repository_database, app_paths)

    result = service.reset_system()

    assert result.success is True
    logs = OperationLogRepository(repository_database).list_logs()
    assert len(logs) == 1
    assert logs[0]["operation_type"] == "system_reset"
    assert logs[0]["result"] == "success"
    assert result.audit_log_id == logs[0]["id"]


def test_schema_and_metadata_preserved(
    service: ResetService, repository_database: Database, app_paths: AppPaths
) -> None:
    _seed(repository_database, app_paths)

    service.reset_system()

    assert repository_database.db_path.exists()
    assert get_schema_version(repository_database.db_path) == 4
    with repository_database.connect() as connection:
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    assert row["value"] == "4"


def test_staging_failure_leaves_everything_unchanged(
    service: ResetService,
    repository_database: Database,
    app_paths: AppPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(repository_database, app_paths)

    def broken(src: Path, dst: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("app.services.reset_service._move_file", broken)

    result = service.reset_system()

    assert result.success is False
    assert "未重置" in result.message
    with repository_database.connect() as connection:
        total = connection.execute(
            "SELECT COUNT(*) AS total FROM certificate_records"
        ).fetchone()
    assert total["total"] == 2
    assert (app_paths.photos_dir / "a.jpg").exists()
    assert (app_paths.photos_dir / "b.jpg").exists()


def test_database_failure_rolls_back_and_restores_photos(
    service: ResetService,
    repository_database: Database,
    app_paths: AppPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(repository_database, app_paths)
    custom_id = service.template_service.create_template(_template("保留模板", "degree"))

    def broken(*args: Any, **kwargs: Any) -> int:
        raise sqlite3.Error("boom")

    monkeypatch.setattr(service.prints, "delete_all", broken)

    result = service.reset_system()

    assert result.success is False
    with repository_database.connect() as connection:
        students = connection.execute(
            "SELECT COUNT(*) AS total FROM certificate_records"
        ).fetchone()
        batches = connection.execute(
            "SELECT COUNT(*) AS total FROM import_batches"
        ).fetchone()
    assert students["total"] == 2
    assert batches["total"] == 1
    assert service.template_service.load_template(custom_id).name == "保留模板"
    assert (app_paths.photos_dir / "a.jpg").exists()
    assert (app_paths.photos_dir / "b.jpg").exists()


def test_database_failure_writes_failure_audit(
    service: ResetService,
    repository_database: Database,
    app_paths: AppPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(repository_database, app_paths)

    def broken(*args: Any, **kwargs: Any) -> int:
        raise sqlite3.Error("boom")

    monkeypatch.setattr(service.records, "delete_all", broken)

    service.reset_system()

    failures = [
        record
        for record in OperationLogRepository(repository_database).list_logs()
        if record["operation_type"] == "system_reset"
    ]
    assert len(failures) == 1
    assert failures[0]["result"] == "failure"
    assert failures[0]["error_message"]


def test_failure_audit_error_does_not_mask_result(
    service: ResetService,
    repository_database: Database,
    app_paths: AppPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(repository_database, app_paths)

    def broken_delete(*args: Any, **kwargs: Any) -> int:
        raise sqlite3.Error("boom")

    def broken_log(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("audit down")

    monkeypatch.setattr(service.records, "delete_all", broken_delete)
    monkeypatch.setattr(service.logs, "create", broken_log)

    result = service.reset_system()

    assert result.success is False
    assert "重置失败" in result.message
    assert any("审计" in warning for warning in result.warnings)


def test_missing_builtin_resource_rolls_back(
    service: ResetService,
    repository_database: Database,
    app_paths: AppPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(repository_database, app_paths)
    custom_id = service.template_service.create_template(_template("保留模板", "other"))

    def broken() -> tuple[object, ...]:
        raise RuntimeError("resource missing")

    monkeypatch.setattr(service.template_service, "load_builtin_templates", broken)

    result = service.reset_system()

    assert result.success is False
    assert service.template_service.load_template(custom_id).name == "保留模板"
    assert (app_paths.photos_dir / "a.jpg").exists()


def test_staging_cleanup_failure_is_reported(
    service: ResetService,
    repository_database: Database,
    app_paths: AppPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(repository_database, app_paths)

    def broken(path: Path) -> None:
        raise OSError("locked")

    monkeypatch.setattr("app.services.reset_service._remove_tree", broken)

    result = service.reset_system()

    assert result.success is True
    assert result.staging_path is not None
    assert any("暂存" in warning for warning in result.warnings)


def test_log_truncate_failure_is_reported(
    service: ResetService,
    repository_database: Database,
    app_paths: AppPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(repository_database, app_paths)
    monkeypatch.setattr(
        "app.services.reset_service.truncate_log_file",
        lambda path: "日志文件正在使用，未能清空",
    )

    result = service.reset_system()

    assert result.success is True
    assert any("日志" in warning for warning in result.warnings)


def test_repeated_reset_is_stable(
    service: ResetService, repository_database: Database, app_paths: AppPaths
) -> None:
    _seed(repository_database, app_paths)

    first = service.reset_system()
    second = service.reset_system()

    assert first.success and second.success
    assert len(service.template_service.list_templates()) == 3
    with repository_database.connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "number_sequences" not in tables


def test_reset_keeps_print_repository_readable(
    service: ResetService, repository_database: Database, app_paths: AppPaths
) -> None:
    _seed(repository_database, app_paths)

    service.reset_system()

    assert PrintRecordRepository(repository_database).count_records() == 0
    assert ImportBatchRepository(repository_database).count_batches() == 0
    assert AppSettingsRepository(repository_database).list_all() == []


def test_reset_does_not_touch_qsettings(
    service: ResetService,
    repository_database: Database,
    app_paths: AppPaths,
    tmp_path: Path,
) -> None:
    from PySide6.QtCore import QSettings

    _seed(repository_database, app_paths)
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    settings.setValue("ui/main_window/geometry", b"geometry-bytes")
    settings.sync()

    service.reset_system()

    assert settings.value("ui/main_window/geometry") == b"geometry-bytes"


def test_app_can_start_and_use_core_features_after_reset(
    service: ResetService, repository_database: Database, app_paths: AppPaths
) -> None:
    from app.data.db import initialize_database

    _seed(repository_database, app_paths)
    service.reset_system()

    assert initialize_database(repository_database.db_path) == 4
    record_id = StudentService(repository_database).create_record(
        {"student_no": "S-AFTER", "name": "重置后"}
    )
    assert record_id > 0
    assert NumberingService(repository_database).preview("graduation", 1).sequence_value == 1
    assert len(service.template_service.list_templates()) == 3
