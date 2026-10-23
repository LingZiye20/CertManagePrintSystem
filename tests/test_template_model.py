"""模板 JSON v1 模型与校验测试：结构、白名单字段、几何、序列化。"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest

from app.data.db import EXCEL_FIELD_NAMES
from app.services.errors import TemplateDataError, TemplateValidationError
from app.services.template_model import (
    FORMAT_VERSION,
    MAX_CANVAS_MM,
    MAX_FONT_SIZE_PT,
    CertificateTemplate,
    PhotoElement,
    TextElement,
    template_from_json,
    template_to_json,
)


def _text_element(**overrides: Any) -> dict[str, Any]:
    element: dict[str, Any] = {
        "id": "title",
        "type": "text",
        "field": "name",
        "x_mm": 10.0,
        "y_mm": 20.0,
        "width_mm": 100.0,
        "height_mm": 15.0,
        "font": {
            "family": "Microsoft YaHei",
            "size_pt": 16.0,
            "bold": False,
            "italic": False,
        },
        "align": "center",
        "vertical_align": "middle",
        "wrap": "none",
    }
    element.update(overrides)
    return element


def _photo_element(**overrides: Any) -> dict[str, Any]:
    element: dict[str, Any] = {
        "id": "photo",
        "type": "photo",
        "x_mm": 10.0,
        "y_mm": 40.0,
        "width_mm": 35.0,
        "height_mm": 45.0,
        "fit": "cover",
    }
    element.update(overrides)
    return element


def _template(**overrides: Any) -> dict[str, Any]:
    template: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "name": "测试模板",
        "certificate_type": "graduation",
        "canvas_width_mm": 297.0,
        "canvas_height_mm": 210.0,
        "elements": [_text_element(), _photo_element()],
    }
    template.update(overrides)
    return template


def _json(**overrides: Any) -> str:
    return json.dumps(_template(**overrides), ensure_ascii=False)


def _error(**overrides: Any) -> str:
    with pytest.raises(TemplateValidationError) as excinfo:
        template_from_json(_json(**overrides))
    return str(excinfo.value)


# ---------------------------------------------------------------- 正常解析


def test_parse_valid_template() -> None:
    template = template_from_json(_json())

    assert template.format_version == FORMAT_VERSION
    assert template.name == "测试模板"
    assert template.certificate_type == "graduation"
    assert template.canvas_width_mm == 297.0
    assert template.canvas_height_mm == 210.0
    assert len(template.elements) == 2

    text, photo = template.elements
    assert isinstance(text, TextElement)
    assert text.field == "name"
    assert text.text is None
    assert text.font.family == "Microsoft YaHei"
    assert text.font.size_pt == 16.0
    assert text.align == "center"
    assert text.vertical_align == "middle"
    assert text.wrap == "none"

    assert isinstance(photo, PhotoElement)
    assert photo.fit == "cover"


def test_parse_accepts_static_text() -> None:
    element = _text_element()
    element.pop("field")
    element["text"] = "  证书  "

    template = template_from_json(_json(elements=[element]))

    text = template.elements[0]
    assert isinstance(text, TextElement)
    assert text.field is None
    assert text.text == "证书"


@pytest.mark.parametrize("field", EXCEL_FIELD_NAMES)
def test_every_business_field_can_be_bound(field: str) -> None:
    template = template_from_json(_json(elements=[_text_element(field=field)]))
    assert template.elements[0].field == field


def test_serialization_round_trip_is_stable() -> None:
    template = template_from_json(_json())

    dumped = template_to_json(template)
    reloaded = template_from_json(dumped)

    assert reloaded == template
    assert json.loads(dumped)["format_version"] == FORMAT_VERSION
    # 持久化坐标只保存毫米，不出现像素字段。
    assert "x_px" not in dumped
    assert "x_mm" in dumped


def test_template_record_values_derive_from_model() -> None:
    template = template_from_json(_json(name="  内置模板  "))

    values = template.to_record_values()

    assert values["name"] == "内置模板"
    assert values["certificate_type"] == "graduation"
    assert values["canvas_width_mm"] == 297.0
    assert values["canvas_height_mm"] == 210.0
    assert json.loads(values["template_json"])["name"] == "内置模板"


def test_from_record_rejects_inconsistent_columns() -> None:
    template = template_from_json(_json())
    record = {
        "name": template.name,
        "certificate_type": "degree",
        "canvas_width_mm": template.canvas_width_mm,
        "canvas_height_mm": template.canvas_height_mm,
        "template_json": template_to_json(template),
    }

    with pytest.raises(TemplateDataError) as excinfo:
        CertificateTemplate.from_record(record)

    assert "certificate_type" in str(excinfo.value)


# ---------------------------------------------------------------- 顶层结构


def test_root_must_be_object() -> None:
    for payload in ("[]", '"text"', "1", "null"):
        with pytest.raises(TemplateValidationError):
            template_from_json(payload)


def test_invalid_json_text_is_reported() -> None:
    with pytest.raises(TemplateValidationError):
        template_from_json("{not json")


@pytest.mark.parametrize("version", [2, 0, "1", 1.0, None, True])
def test_format_version_is_strict(version: object) -> None:
    assert "format_version" in _error(format_version=version)


@pytest.mark.parametrize("name", ["", "   ", None, 123])
def test_name_must_be_non_empty_text(name: object) -> None:
    assert "name" in _error(name=name)


@pytest.mark.parametrize("certificate_type", ["bachelor", "", None])
def test_certificate_type_is_restricted(certificate_type: object) -> None:
    assert "certificate_type" in _error(certificate_type=certificate_type)


@pytest.mark.parametrize("size", [0, -1, "297", True, math.nan, math.inf, MAX_CANVAS_MM + 1])
def test_canvas_size_must_be_positive_finite_mm(size: object) -> None:
    message = _error(canvas_width_mm=size)
    assert "canvas_width_mm" in message


def test_canvas_height_is_validated() -> None:
    assert "canvas_height_mm" in _error(canvas_height_mm=0)


def test_elements_must_be_list() -> None:
    assert "elements" in _error(elements={})


def test_unknown_root_key_is_rejected() -> None:
    payload = _template()
    payload["width_px"] = 1000

    with pytest.raises(TemplateValidationError) as excinfo:
        template_from_json(json.dumps(payload, ensure_ascii=False))

    assert "width_px" in str(excinfo.value)


# ---------------------------------------------------------------- 元素通用


def test_element_must_be_object() -> None:
    assert "elements[0]" in _error(elements=["text"])


def test_element_id_must_be_unique_and_non_empty() -> None:
    assert "id" in _error(elements=[_text_element(id="")])
    assert "重复" in _error(
        elements=[_text_element(id="dup"), _text_element(id="dup", field="name")]
    )


def test_unknown_element_type_is_rejected() -> None:
    assert "type" in _error(elements=[_text_element(type="barcode")])


@pytest.mark.parametrize("key", ["x_mm", "y_mm"])
def test_element_position_must_be_non_negative(key: str) -> None:
    assert key in _error(elements=[_text_element(**{key: -1.0})])


@pytest.mark.parametrize("key", ["width_mm", "height_mm"])
def test_element_size_must_be_positive(key: str) -> None:
    assert key in _error(elements=[_text_element(**{key: 0})])


def test_element_must_stay_inside_canvas() -> None:
    message = _error(elements=[_text_element(x_mm=250.0, width_mm=100.0)])
    assert "elements[0]" in message


def test_element_geometry_rejects_non_numeric() -> None:
    assert "width_mm" in _error(elements=[_text_element(width_mm="100")])


def test_unknown_element_key_is_rejected() -> None:
    assert "x_px" in _error(elements=[_text_element(x_px=10)])


# ---------------------------------------------------------------- 文本元素


def test_text_element_requires_field_or_text() -> None:
    element = _text_element()
    element.pop("field")
    assert "field" in _error(elements=[element])


def test_text_element_rejects_field_and_text_together() -> None:
    element = _text_element(text="证书")
    assert "text" in _error(elements=[element])


@pytest.mark.parametrize(
    "field",
    ["photo_path", "unknown_field", "name + 1", "__import__('os')", "{{name}}", ""],
)
def test_text_field_must_be_whitelisted(field: str) -> None:
    assert "field" in _error(elements=[_text_element(field=field)])


@pytest.mark.parametrize("family", ["", "   ", None])
def test_font_family_required(family: object) -> None:
    element = _text_element()
    element["font"]["family"] = family
    assert "font.family" in _error(elements=[element])


@pytest.mark.parametrize("size", [0, -1, MAX_FONT_SIZE_PT + 1, "12", True, math.nan])
def test_font_size_is_bounded(size: object) -> None:
    element = _text_element()
    element["font"]["size_pt"] = size
    assert "font.size_pt" in _error(elements=[element])


@pytest.mark.parametrize("flag", ["true", 1, None])
def test_font_flags_must_be_boolean(flag: object) -> None:
    element = _text_element()
    element["font"]["bold"] = flag
    assert "font.bold" in _error(elements=[element])


def test_unknown_font_key_is_rejected() -> None:
    element = _text_element()
    element["font"]["color"] = "#000000"
    assert "color" in _error(elements=[element])


@pytest.mark.parametrize("key", ["align", "vertical_align", "wrap"])
def test_text_style_values_are_restricted(key: str) -> None:
    assert key in _error(elements=[_text_element(**{key: "justify"})])


def test_text_element_rejects_photo_fit() -> None:
    assert "fit" in _error(elements=[_text_element(fit="cover")])


# ---------------------------------------------------------------- 照片元素


def test_photo_element_parses_defaults() -> None:
    template = template_from_json(_json(elements=[_photo_element()]))
    photo = template.elements[0]
    assert isinstance(photo, PhotoElement)
    assert photo.fit == "cover"


@pytest.mark.parametrize("fit", ["fill", "", None])
def test_photo_fit_is_restricted(fit: object) -> None:
    assert "fit" in _error(elements=[_photo_element(fit=fit)])


def test_photo_element_rejects_text_only_keys() -> None:
    assert "field" in _error(elements=[_photo_element(field="name")])
    assert "font" in _error(elements=[_photo_element(font={})])


# ---------------------------------------------------------------- 错误定位


def test_error_message_contains_element_path() -> None:
    message = _error(elements=[_text_element(), _text_element(id="second", width_mm=0)])
    assert "elements[1].width_mm" in message
