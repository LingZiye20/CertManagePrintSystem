"""TemplateService 测试：保存加载、内置只读、资源加载、幂等播种与渲染编排。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from app.config.paths import AppPaths
from app.data.db import Database
from app.repositories import CertificateTemplateRepository
from app.services import PhotoPathError, TemplateDataError
from app.services.errors import (
    TemplateNotFoundError,
    TemplateReadOnlyError,
    TemplateResourceError,
    TemplateValidationError,
)
from app.services.template_model import (
    CertificateTemplate,
    FontSpec,
    PhotoElement,
    TextElement,
    template_to_json,
)
from app.services.template_service import (
    BUILTIN_TEMPLATE_FILES,
    TemplateService,
    TemplateSummary,
)

FONT_FAMILY = "Microsoft YaHei"


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def service(repository_database: Database, app_paths: AppPaths) -> TemplateService:
    return TemplateService(repository_database, app_paths)


def _text(**overrides: Any) -> TextElement:
    values: dict[str, Any] = {
        "id": "name",
        "x_mm": 10.0,
        "y_mm": 10.0,
        "width_mm": 60.0,
        "height_mm": 12.0,
        "font": FontSpec(family=FONT_FAMILY, size_pt=14.0),
        "align": "center",
        "vertical_align": "middle",
        "wrap": "none",
        "field": "name",
    }
    values.update(overrides)
    return TextElement(**values)


def _template(
    certificate_type: str = "graduation",
    *,
    name: str = "自定义模板",
    elements: tuple[TextElement | PhotoElement, ...] | None = None,
) -> CertificateTemplate:
    return CertificateTemplate(
        name=name,
        certificate_type=certificate_type,
        canvas_width_mm=210.0,
        canvas_height_mm=297.0,
        elements=elements if elements is not None else (_text(),),
    )


def _write_builtin_resources(paths: AppPaths) -> Path:
    """在临时资源目录写入三份内置模板，避免依赖真实 resources/。"""
    directory = paths.resource_dir / "templates"
    directory.mkdir(parents=True, exist_ok=True)
    for certificate_type, file_name in BUILTIN_TEMPLATE_FILES.items():
        template = _template(
            certificate_type,
            name=f"内置-{certificate_type}",
            elements=(_text(), PhotoElement(
                id="photo",
                x_mm=10.0,
                y_mm=40.0,
                width_mm=35.0,
                height_mm=45.0,
                fit="cover",
            )),
        )
        (directory / file_name).write_text(template_to_json(template), encoding="utf-8")
    return directory


# ---------------------------------------------------------------- 保存与加载


def test_create_and_load_round_trip(service: TemplateService) -> None:
    template = _template()
    template_id = service.create_template(template)

    loaded = service.load_template(template_id)

    assert loaded == template
    row = service.templates.get(template_id)
    assert row is not None
    assert row["is_builtin"] == 0
    assert row["canvas_width_mm"] == 210.0
    assert json.loads(row["template_json"])["canvas_height_mm"] == 297.0


def test_saved_columns_derive_from_model(service: TemplateService) -> None:
    template = _template()
    template_id = service.create_template(template)

    row = service.templates.get(template_id)

    assert row is not None
    payload = json.loads(row["template_json"])
    for column in ("name", "certificate_type", "canvas_width_mm", "canvas_height_mm"):
        assert row[column] == payload[column]


def test_update_replaces_json_and_columns(service: TemplateService) -> None:
    template_id = service.create_template(_template())
    updated = _template(name="改名模板")

    service.update_template(template_id, updated)

    row = service.templates.get(template_id)
    assert row is not None
    assert row["name"] == "改名模板"
    assert service.load_template(template_id) == updated


def test_update_missing_template_raises(service: TemplateService) -> None:
    with pytest.raises(TemplateNotFoundError):
        service.update_template(999999, _template())


def test_delete_custom_template(service: TemplateService) -> None:
    template_id = service.create_template(_template())

    service.delete_template(template_id)

    assert service.templates.get(template_id) is None
    with pytest.raises(TemplateNotFoundError):
        service.delete_template(template_id)


def test_load_missing_template_raises(service: TemplateService) -> None:
    with pytest.raises(TemplateNotFoundError):
        service.load_template(999999)


def test_list_templates_by_type(service: TemplateService) -> None:
    graduation_id = service.create_template(_template("graduation"))
    degree_id = service.create_template(_template("degree", name="学位模板"))

    summaries = service.list_templates("graduation")

    assert [item.id for item in summaries] == [graduation_id]
    assert isinstance(summaries[0], TemplateSummary)
    assert summaries[0].name == "自定义模板"
    assert summaries[0].is_builtin is False
    assert service.list_templates("degree")[0].id == degree_id
    assert service.list_templates("other") == []


# ---------------------------------------------------------------- 数据一致性


def test_inconsistent_columns_raise_data_error(service: TemplateService) -> None:
    template_id = service.create_template(_template())
    with service.database.transaction() as connection:
        connection.execute(
            "UPDATE certificate_templates SET canvas_width_mm = 100 WHERE id = ?",
            (template_id,),
        )

    with pytest.raises(TemplateDataError):
        service.load_template(template_id)


def test_corrupted_template_json_raises_validation_error(
    service: TemplateService,
) -> None:
    repository = CertificateTemplateRepository(service.database)
    template_id = repository.create(
        name="坏模板",
        certificate_type="graduation",
        canvas_width_mm=210.0,
        canvas_height_mm=297.0,
        template_json="{not json",
    )

    with pytest.raises(TemplateValidationError):
        service.load_template(template_id)


# ---------------------------------------------------------------- 内置只读


def test_builtin_template_cannot_be_updated_or_deleted(service: TemplateService) -> None:
    template = _template()
    with service.database.transaction() as connection:
        template_id = service.templates.create(
            is_builtin=1,
            connection=connection,
            **template.to_record_values(),
        )

    with pytest.raises(TemplateReadOnlyError):
        service.update_template(template_id, _template(name="改内置"))
    with pytest.raises(TemplateReadOnlyError):
        service.delete_template(template_id)

    row = service.templates.get(template_id)
    assert row is not None
    assert row["name"] == "自定义模板"


# ---------------------------------------------------------------- 内置资源


def test_load_builtin_templates_from_resources(service: TemplateService, app_paths: AppPaths) -> None:
    directory = _write_builtin_resources(app_paths)

    templates = service.load_builtin_templates()

    assert [item.certificate_type for item in templates] == list(BUILTIN_TEMPLATE_FILES)
    assert all(item.name.startswith("内置-") for item in templates)
    assert directory.is_dir()


def test_missing_builtin_resource_raises(service: TemplateService) -> None:
    with pytest.raises(TemplateResourceError):
        service.load_builtin_templates()


def test_builtin_resource_type_mismatch_raises(
    service: TemplateService, app_paths: AppPaths
) -> None:
    directory = _write_builtin_resources(app_paths)
    graduation_file = directory / BUILTIN_TEMPLATE_FILES["graduation"]
    graduation_file.write_text(
        template_to_json(_template("degree")), encoding="utf-8"
    )

    with pytest.raises(TemplateResourceError):
        service.load_builtin_templates()


def test_invalid_builtin_resource_json_raises(
    service: TemplateService, app_paths: AppPaths
) -> None:
    directory = _write_builtin_resources(app_paths)
    (directory / BUILTIN_TEMPLATE_FILES["degree"]).write_text("{oops", encoding="utf-8")

    with pytest.raises(TemplateResourceError):
        service.load_builtin_templates()


# ---------------------------------------------------------------- 幂等播种


def test_seed_builtin_templates_inserts_three(
    service: TemplateService, app_paths: AppPaths
) -> None:
    _write_builtin_resources(app_paths)

    with service.database.transaction() as connection:
        result = service.seed_builtin_templates(connection)

    assert result.created == 3
    assert result.skipped == 0
    for certificate_type in BUILTIN_TEMPLATE_FILES:
        row = service.templates.get_builtin(certificate_type)
        assert row is not None
        assert row["is_builtin"] == 1
        assert row["name"] == f"内置-{certificate_type}"


def test_seed_is_idempotent_and_keeps_existing(
    service: TemplateService, app_paths: AppPaths
) -> None:
    _write_builtin_resources(app_paths)
    with service.database.transaction() as connection:
        service.seed_builtin_templates(connection)

    with service.database.transaction() as connection:
        connection.execute(
            "UPDATE certificate_templates SET name = '用户改名' WHERE certificate_type = 'degree'"
        )

    with service.database.transaction() as connection:
        result = service.seed_builtin_templates(connection)

    assert result.created == 0
    assert result.skipped == 3
    row = service.templates.get_builtin("degree")
    assert row is not None
    assert row["name"] == "用户改名"
    assert len(service.templates.list_all()) == 3


def test_seed_requires_explicit_transaction(
    service: TemplateService, app_paths: AppPaths
) -> None:
    _write_builtin_resources(app_paths)
    with service.database.connect() as connection:
        with pytest.raises(ValueError):
            service.seed_builtin_templates(connection)


# ---------------------------------------------------------------- 记录渲染


def _record(photo_path: str | None = None) -> dict[str, Any]:
    return {
        "student_no": "00123",
        "name": "张三",
        "graduation_date": "2026-07-01",
        "photo_path": photo_path,
    }


def test_render_record_without_photo_returns_warning(
    service: TemplateService, qapp: QApplication
) -> None:
    template = _template(
        elements=(
            _text(),
            PhotoElement(id="photo", x_mm=10.0, y_mm=40.0, width_mm=35.0, height_mm=45.0),
        )
    )
    template_id = service.create_template(template)

    result = service.render_record(template_id, _record(), dpi=96)

    assert result.warnings[0].code == "missing_photo"


def test_render_record_with_photo_file(
    service: TemplateService, app_paths: AppPaths, qapp: QApplication
) -> None:
    template = _template(
        elements=(PhotoElement(id="photo", x_mm=10.0, y_mm=40.0, width_mm=35.0, height_mm=45.0),)
    )
    template_id = service.create_template(template)
    photo = QImage(20, 20, QImage.Format.Format_RGB32)
    photo.fill(QColor("#303030"))
    assert photo.save(str(app_paths.photos_dir / "00123.jpg"), "JPG")

    result = service.render_record(template_id, _record("00123.jpg"), dpi=96)

    assert result.warnings == ()


def test_render_record_rejects_unsafe_photo_path(
    service: TemplateService, qapp: QApplication
) -> None:
    template_id = service.create_template(_template())

    with pytest.raises(PhotoPathError):
        service.render_record(template_id, _record("../secret.jpg"), dpi=96)


def test_render_record_missing_template(service: TemplateService, qapp: QApplication) -> None:
    with pytest.raises(TemplateNotFoundError):
        service.render_record(999999, _record(), dpi=96)
