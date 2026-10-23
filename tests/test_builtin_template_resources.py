"""真实内置模板资源测试：文件存在、结构有效、与数据库模型兼容。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.data.db import EXCEL_FIELD_NAMES, Database
from app.services.template_model import (
    FORMAT_VERSION,
    CertificateTemplate,
    PhotoElement,
    TextElement,
)
from app.services.template_service import BUILTIN_TEMPLATE_FILES, TemplateService

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESOURCE_DIR = PROJECT_ROOT / "resources" / "templates"


@pytest.fixture
def service(repository_database: Database) -> TemplateService:
    from app.config.paths import AppPaths

    return TemplateService(repository_database, AppPaths.for_program(PROJECT_ROOT))


def test_all_builtin_resource_files_exist() -> None:
    for file_name in BUILTIN_TEMPLATE_FILES.values():
        assert (RESOURCE_DIR / file_name).is_file(), f"缺少内置模板 {file_name}"


def test_builtin_resources_parse_and_match_declared_types(
    service: TemplateService,
) -> None:
    templates = service.load_builtin_templates()

    assert [template.certificate_type for template in templates] == list(
        BUILTIN_TEMPLATE_FILES
    )
    assert len({template.name for template in templates}) == len(templates)


def test_builtin_templates_use_mm_and_format_version(
    service: TemplateService,
) -> None:
    for template in service.load_builtin_templates():
        assert template.format_version == FORMAT_VERSION
        assert template.canvas_width_mm > 0
        assert template.canvas_height_mm > 0

        payload = json.loads((RESOURCE_DIR / BUILTIN_TEMPLATE_FILES[template.certificate_type]).read_text(encoding="utf-8"))
        assert payload["format_version"] == FORMAT_VERSION
        for element in payload["elements"]:
            assert "x_mm" in element and "y_mm" in element
            assert "x_px" not in element and "y_px" not in element


def test_builtin_elements_stay_inside_canvas(service: TemplateService) -> None:
    for template in service.load_builtin_templates():
        for element in template.elements:
            assert element.x_mm >= 0
            assert element.y_mm >= 0
            assert element.x_mm + element.width_mm <= template.canvas_width_mm + 1e-9
            assert element.y_mm + element.height_mm <= template.canvas_height_mm + 1e-9


def test_builtin_text_bindings_are_whitelisted(service: TemplateService) -> None:
    for template in service.load_builtin_templates():
        for element in template.text_elements():
            assert element.field is None or element.field in EXCEL_FIELD_NAMES
            assert element.text is None or "{{" not in element.text
            assert element.field != "photo_path"


def test_builtin_templates_contain_text_and_photo(service: TemplateService) -> None:
    for template in service.load_builtin_templates():
        assert isinstance(template.text_elements(), tuple)
        assert template.text_elements(), f"{template.certificate_type} 缺少文本元素"
        assert template.photo_elements(), f"{template.certificate_type} 缺少照片元素"
        assert all(isinstance(item, (TextElement, PhotoElement)) for item in template.elements)


def test_builtin_templates_can_be_seeded_into_database(
    service: TemplateService,
) -> None:
    with service.database.transaction() as connection:
        result = service.seed_builtin_templates(connection)

    assert result.created == len(BUILTIN_TEMPLATE_FILES)

    resources = {
        template.certificate_type: template
        for template in service.load_builtin_templates()
    }
    for certificate_type, expected in resources.items():
        row = service.templates.get_builtin(certificate_type)
        assert row is not None
        assert row["is_builtin"] == 1
        reloaded = CertificateTemplate.from_record(row)
        assert reloaded == expected


def test_builtin_templates_use_a4_landscape(service: TemplateService) -> None:
    for template in service.load_builtin_templates():
        assert template.canvas_width_mm == pytest.approx(297.0)
        assert template.canvas_height_mm == pytest.approx(210.0)
