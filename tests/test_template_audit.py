"""模板新增/修改/删除的操作审计测试。"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from app.config.paths import AppPaths
from app.data.db import Database
from app.repositories import OperationLogRepository
from app.services import AuditWriteError
from app.services.template_model import (
    CertificateTemplate,
    FontSpec,
    TextElement,
    template_to_json,
)
from app.services.template_service import BUILTIN_TEMPLATE_FILES, TemplateService

FONT_FAMILY = "Microsoft YaHei"


@pytest.fixture
def service(repository_database: Database, app_paths: AppPaths) -> TemplateService:
    return TemplateService(repository_database, app_paths)


@pytest.fixture
def logs(repository_database: Database) -> OperationLogRepository:
    return OperationLogRepository(repository_database)


def _template(name: str = "自定义模板", certificate_type: str = "graduation") -> CertificateTemplate:
    element = TextElement(
        id="name",
        x_mm=10.0,
        y_mm=10.0,
        width_mm=60.0,
        height_mm=12.0,
        font=FontSpec(family=FONT_FAMILY, size_pt=14.0),
        align="center",
        vertical_align="middle",
        wrap="none",
        field="name",
    )
    return CertificateTemplate(
        name=name,
        certificate_type=certificate_type,
        canvas_width_mm=210.0,
        canvas_height_mm=297.0,
        elements=(element,),
    )


def _write_builtin_resources(paths: AppPaths) -> None:
    directory = paths.resource_dir / "templates"
    directory.mkdir(parents=True, exist_ok=True)
    for certificate_type, file_name in BUILTIN_TEMPLATE_FILES.items():
        (directory / file_name).write_text(
            template_to_json(_template(f"内置-{certificate_type}", certificate_type)),
            encoding="utf-8",
        )


def test_create_writes_success_audit(service: TemplateService, logs: OperationLogRepository) -> None:
    template_id = service.create_template(_template("毕结业模板甲"))

    records = logs.list_logs(operation_type="template_create", limit=10)
    assert len(records) == 1
    record = records[0]
    assert record["result"] == "success"
    assert record["target_type"] == "certificate_template"
    assert record["target_id"] == str(template_id)
    assert "毕结业模板甲" in record["summary"]
    assert "210×297mm" in record["summary"]


def test_update_writes_success_audit(service: TemplateService, logs: OperationLogRepository) -> None:
    template_id = service.create_template(_template("旧名称"))

    service.update_template(template_id, _template("新名称"))

    records = logs.list_logs(operation_type="template_update", limit=10)
    assert len(records) == 1
    assert records[0]["target_id"] == str(template_id)
    assert "新名称" in records[0]["summary"]


def test_delete_writes_success_audit_with_name(
    service: TemplateService, logs: OperationLogRepository
) -> None:
    template_id = service.create_template(_template("待删除模板"))

    service.delete_template(template_id)

    records = logs.list_logs(operation_type="template_delete", limit=10)
    assert len(records) == 1
    assert "待删除模板" in records[0]["summary"]
    assert records[0]["target_id"] == str(template_id)


def test_builtin_rejection_writes_no_success_audit(
    service: TemplateService, logs: OperationLogRepository, app_paths: AppPaths
) -> None:
    _write_builtin_resources(app_paths)
    with service.database.transaction() as connection:
        service.seed_builtin_templates(connection)
    builtin = service.templates.get_builtin("graduation")
    assert builtin is not None

    from app.services.errors import TemplateReadOnlyError

    with pytest.raises(TemplateReadOnlyError):
        service.update_template(int(builtin["id"]), _template("改名"))
    with pytest.raises(TemplateReadOnlyError):
        service.delete_template(int(builtin["id"]))

    assert logs.count_logs(operation_type="template_update") == 0
    assert logs.count_logs(operation_type="template_delete") == 0


def test_seed_does_not_write_operation_audit(
    service: TemplateService, logs: OperationLogRepository, app_paths: AppPaths
) -> None:
    _write_builtin_resources(app_paths)

    with service.database.transaction() as connection:
        service.seed_builtin_templates(connection)

    assert logs.count_logs() == 0


def test_audit_failure_rolls_back_create(
    service: TemplateService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any, **kwargs: Any) -> int:
        raise sqlite3.Error("boom")

    monkeypatch.setattr(service.logs, "create", broken)

    with pytest.raises(AuditWriteError):
        service.create_template(_template("不会写入"))

    assert service.list_templates() == []


def test_audit_failure_rolls_back_delete(
    service: TemplateService, monkeypatch: pytest.MonkeyPatch
) -> None:
    template_id = service.create_template(_template("保留模板"))

    def broken(*args: Any, **kwargs: Any) -> int:
        raise sqlite3.Error("boom")

    monkeypatch.setattr(service.logs, "create", broken)

    with pytest.raises(AuditWriteError):
        service.delete_template(template_id)

    assert service.load_template(template_id).name == "保留模板"


def test_audit_summary_is_compact(service: TemplateService, logs: OperationLogRepository) -> None:
    service.create_template(_template("摘要模板"))

    record = logs.list_logs(operation_type="template_create", limit=1)[0]
    assert len(record["summary"]) < 200
    assert "template_json" not in record["summary"]
