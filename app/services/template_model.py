"""证书模板 JSON v1 模型与严格校验。

设计约束：

- 所有持久化坐标、尺寸与字号均以毫米/点为单位，不保存屏幕像素；
- 结构版本固定为 ``FORMAT_VERSION``，未知字段一律拒绝，避免拼写错误被静默忽略；
- 文本元素只能绑定系统定义的 37 个业务字段，或保存静态文字；
  不执行任何表达式、模板语言或 Python 代码；
- 本模块不依赖 Qt、数据库与文件系统，便于独立测试。
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.data.db import EXCEL_FIELD_NAMES
from app.services.errors import TemplateDataError, TemplateValidationError
from app.services.numbering_service import CERTIFICATE_TYPES

FORMAT_VERSION = 1
MAX_CANVAS_MM = 2000.0
MIN_FONT_SIZE_PT = 1.0
MAX_FONT_SIZE_PT = 200.0

ELEMENT_TYPES: tuple[str, ...] = ("text", "photo")
ALIGNMENTS: tuple[str, ...] = ("left", "center", "right")
VERTICAL_ALIGNMENTS: tuple[str, ...] = ("top", "middle", "bottom")
WRAP_MODES: tuple[str, ...] = ("none", "word")
PHOTO_FITS: tuple[str, ...] = ("cover", "contain")

CERTIFICATE_TYPE_KEYS: tuple[str, ...] = tuple(
    type_info.key for type_info in CERTIFICATE_TYPES
)
# 文本元素可绑定的字段清单：严格等于 37 个业务字段，不含 photo_path。
BINDABLE_FIELDS: tuple[str, ...] = EXCEL_FIELD_NAMES

_ROOT_KEYS = frozenset(
    {
        "format_version",
        "name",
        "certificate_type",
        "canvas_width_mm",
        "canvas_height_mm",
        "elements",
    }
)
_TEXT_KEYS = frozenset(
    {
        "id",
        "type",
        "field",
        "text",
        "x_mm",
        "y_mm",
        "width_mm",
        "height_mm",
        "font",
        "align",
        "vertical_align",
        "wrap",
    }
)
_PHOTO_KEYS = frozenset(
    {
        "id",
        "type",
        "x_mm",
        "y_mm",
        "width_mm",
        "height_mm",
        "fit",
    }
)
_FONT_KEYS = frozenset({"family", "size_pt", "bold", "italic"})


@dataclass(frozen=True, slots=True)
class FontSpec:
    """文本字体配置；``size_pt`` 为磅值。"""

    family: str
    size_pt: float
    bold: bool = False
    italic: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "size_pt": self.size_pt,
            "bold": self.bold,
            "italic": self.italic,
        }


@dataclass(frozen=True, slots=True)
class TextElement:
    """文本元素：绑定业务字段或绘制静态文字。"""

    id: str
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    font: FontSpec
    align: str = "left"
    vertical_align: str = "top"
    wrap: str = "none"
    field: str | None = None
    text: str | None = None

    def source_text(self, values: Mapping[str, Any]) -> str:
        """返回待绘制文本：静态文字直接返回，字段绑定取记录值。"""
        if self.text is not None:
            return self.text
        raw = values.get(self.field) if self.field is not None else None
        return "" if raw is None else str(raw)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": self.id, "type": "text"}
        if self.field is not None:
            payload["field"] = self.field
        else:
            payload["text"] = self.text
        payload.update(
            {
                "x_mm": self.x_mm,
                "y_mm": self.y_mm,
                "width_mm": self.width_mm,
                "height_mm": self.height_mm,
                "font": self.font.to_dict(),
                "align": self.align,
                "vertical_align": self.vertical_align,
                "wrap": self.wrap,
            }
        )
        return payload


@dataclass(frozen=True, slots=True)
class PhotoElement:
    """照片元素：按元素矩形绘制记录关联的毕业照片。"""

    id: str
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    fit: str = "cover"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "photo",
            "x_mm": self.x_mm,
            "y_mm": self.y_mm,
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "fit": self.fit,
        }


TemplateElement = TextElement | PhotoElement


@dataclass(frozen=True, slots=True)
class CertificateTemplate:
    """一份可版本化的证书模板。"""

    name: str
    certificate_type: str
    canvas_width_mm: float
    canvas_height_mm: float
    elements: tuple[TemplateElement, ...]
    format_version: int = FORMAT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "name": self.name,
            "certificate_type": self.certificate_type,
            "canvas_width_mm": self.canvas_width_mm,
            "canvas_height_mm": self.canvas_height_mm,
            "elements": [element.to_dict() for element in self.elements],
        }

    def to_record_values(self) -> dict[str, Any]:
        """派生 ``certificate_templates`` 列值，宽高与名称只取自本模型。"""
        return {
            "name": self.name,
            "certificate_type": self.certificate_type,
            "canvas_width_mm": self.canvas_width_mm,
            "canvas_height_mm": self.canvas_height_mm,
            "template_json": template_to_json(self),
        }

    def text_elements(self) -> tuple[TextElement, ...]:
        return tuple(
            element for element in self.elements if isinstance(element, TextElement)
        )

    def photo_elements(self) -> tuple[PhotoElement, ...]:
        return tuple(
            element for element in self.elements if isinstance(element, PhotoElement)
        )

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> CertificateTemplate:
        """从数据库行还原模板，并校验数据库列与 JSON 顶层字段一致。

        Raises:
            TemplateDataError: 数据库列与 JSON 内容不一致。
        """
        payload = record.get("template_json")
        if isinstance(payload, str):
            template = template_from_json(payload)
        elif isinstance(payload, Mapping):
            template = template_from_dict(payload)
        else:
            raise TemplateDataError("模板数据缺少 template_json")

        for column in ("name", "certificate_type", "canvas_width_mm", "canvas_height_mm"):
            if column not in record:
                continue
            expected = getattr(template, column)
            actual = record[column]
            if isinstance(expected, float) and isinstance(actual, (int, float)):
                if math.isclose(float(actual), expected, rel_tol=1e-9, abs_tol=1e-9):
                    continue
            elif actual == expected:
                continue
            raise TemplateDataError(
                f"模板数据不一致：{column} 列与 template_json 不一致"
            )
        return template


# ---------------------------------------------------------------- 序列化入口


def template_from_json(text: str) -> CertificateTemplate:
    """解析 JSON 文本；任何结构或取值问题都转为 ``TemplateValidationError``。"""
    if not isinstance(text, str):
        raise TemplateValidationError("模板内容必须是 JSON 文本")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TemplateValidationError(f"模板 JSON 格式错误：{exc.msg}") from exc
    return template_from_dict(payload)


def template_from_dict(payload: object) -> CertificateTemplate:
    """从已解析的 JSON 对象构建模板。"""
    data = _require_mapping(payload, "模板根节点")
    _reject_unknown_keys(data, _ROOT_KEYS, "模板根节点")

    format_version = _require_int(data.get("format_version"), "format_version")
    if format_version != FORMAT_VERSION:
        raise TemplateValidationError(
            f"format_version 不受支持：{format_version}，当前仅支持 {FORMAT_VERSION}"
        )

    name = _require_text(data.get("name"), "name")
    certificate_type = _require_text(data.get("certificate_type"), "certificate_type")
    if certificate_type not in CERTIFICATE_TYPE_KEYS:
        raise TemplateValidationError(
            f"certificate_type 取值不合法：{certificate_type!r}，"
            f"可选值：{'、'.join(CERTIFICATE_TYPE_KEYS)}"
        )

    width_mm = _require_number(
        data.get("canvas_width_mm"), "canvas_width_mm", positive=True, maximum=MAX_CANVAS_MM
    )
    height_mm = _require_number(
        data.get("canvas_height_mm"), "canvas_height_mm", positive=True, maximum=MAX_CANVAS_MM
    )

    raw_elements = data.get("elements")
    if not isinstance(raw_elements, Sequence) or isinstance(raw_elements, (str, bytes)):
        raise TemplateValidationError("elements 必须是数组")

    elements: list[TemplateElement] = []
    seen_ids: set[str] = set()
    for index, raw_element in enumerate(raw_elements):
        element = _parse_element(raw_element, f"elements[{index}]", width_mm, height_mm)
        if element.id in seen_ids:
            raise TemplateValidationError(f"elements[{index}].id 重复：{element.id}")
        seen_ids.add(element.id)
        elements.append(element)

    return CertificateTemplate(
        name=name,
        certificate_type=certificate_type,
        canvas_width_mm=width_mm,
        canvas_height_mm=height_mm,
        elements=tuple(elements),
        format_version=format_version,
    )


def template_to_json(template: CertificateTemplate) -> str:
    """序列化为稳定的 JSON 文本，只包含毫米坐标。"""
    return json.dumps(template.to_dict(), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 元素解析


def _parse_element(
    raw: object,
    path: str,
    canvas_width_mm: float,
    canvas_height_mm: float,
) -> TemplateElement:
    data = _require_mapping(raw, path)
    element_type = data.get("type")
    if element_type == "text":
        return _parse_text_element(data, path, canvas_width_mm, canvas_height_mm)
    if element_type == "photo":
        return _parse_photo_element(data, path, canvas_width_mm, canvas_height_mm)
    raise TemplateValidationError(
        f"{path}.type 取值不合法：{element_type!r}，可选值：{'、'.join(ELEMENT_TYPES)}"
    )


def _parse_text_element(
    data: Mapping[str, Any],
    path: str,
    canvas_width_mm: float,
    canvas_height_mm: float,
) -> TextElement:
    _reject_unknown_keys(data, _TEXT_KEYS, path)
    element_id = _require_text(data.get("id"), f"{path}.id")
    x_mm, y_mm, width_mm, height_mm = _parse_geometry(
        data, path, canvas_width_mm, canvas_height_mm
    )

    has_field = "field" in data
    has_text = "text" in data
    if has_field == has_text:
        raise TemplateValidationError(
            f"{path} 必须且只能提供 field（字段绑定）或 text（静态文字）之一"
        )

    field: str | None = None
    text: str | None = None
    if has_field:
        field = _require_text(data.get("field"), f"{path}.field")
        if field not in BINDABLE_FIELDS:
            raise TemplateValidationError(
                f"{path}.field 不是可绑定字段：{field!r}"
            )
    else:
        text = _require_text(data.get("text"), f"{path}.text")

    font = _parse_font(data.get("font"), f"{path}.font")
    align = _require_choice(data.get("align", "left"), f"{path}.align", ALIGNMENTS)
    vertical_align = _require_choice(
        data.get("vertical_align", "top"), f"{path}.vertical_align", VERTICAL_ALIGNMENTS
    )
    wrap = _require_choice(data.get("wrap", "none"), f"{path}.wrap", WRAP_MODES)

    return TextElement(
        id=element_id,
        x_mm=x_mm,
        y_mm=y_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        font=font,
        align=align,
        vertical_align=vertical_align,
        wrap=wrap,
        field=field,
        text=text,
    )


def _parse_photo_element(
    data: Mapping[str, Any],
    path: str,
    canvas_width_mm: float,
    canvas_height_mm: float,
) -> PhotoElement:
    _reject_unknown_keys(data, _PHOTO_KEYS, path)
    element_id = _require_text(data.get("id"), f"{path}.id")
    x_mm, y_mm, width_mm, height_mm = _parse_geometry(
        data, path, canvas_width_mm, canvas_height_mm
    )
    fit = _require_choice(data.get("fit", "cover"), f"{path}.fit", PHOTO_FITS)
    return PhotoElement(
        id=element_id,
        x_mm=x_mm,
        y_mm=y_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        fit=fit,
    )


def _parse_geometry(
    data: Mapping[str, Any],
    path: str,
    canvas_width_mm: float,
    canvas_height_mm: float,
) -> tuple[float, float, float, float]:
    x_mm = _require_number(data.get("x_mm"), f"{path}.x_mm")
    y_mm = _require_number(data.get("y_mm"), f"{path}.y_mm")
    width_mm = _require_number(data.get("width_mm"), f"{path}.width_mm", positive=True)
    height_mm = _require_number(data.get("height_mm"), f"{path}.height_mm", positive=True)

    if x_mm + width_mm > canvas_width_mm + 1e-9:
        raise TemplateValidationError(
            f"{path} 超出画布宽度：x_mm + width_mm = {x_mm + width_mm}mm，"
            f"画布宽度 {canvas_width_mm}mm"
        )
    if y_mm + height_mm > canvas_height_mm + 1e-9:
        raise TemplateValidationError(
            f"{path} 超出画布高度：y_mm + height_mm = {y_mm + height_mm}mm，"
            f"画布高度 {canvas_height_mm}mm"
        )
    return x_mm, y_mm, width_mm, height_mm


def _parse_font(raw: object, path: str) -> FontSpec:
    data = _require_mapping(raw, path)
    _reject_unknown_keys(data, _FONT_KEYS, path)
    family = _require_text(data.get("family"), f"{path}.family")
    size_pt = _require_number(
        data.get("size_pt"),
        f"{path}.size_pt",
        positive=True,
        maximum=MAX_FONT_SIZE_PT,
    )
    if size_pt < MIN_FONT_SIZE_PT:
        raise TemplateValidationError(
            f"{path}.size_pt 不能小于 {MIN_FONT_SIZE_PT}"
        )
    bold = _require_bool(data.get("bold", False), f"{path}.bold")
    italic = _require_bool(data.get("italic", False), f"{path}.italic")
    return FontSpec(family=family, size_pt=size_pt, bold=bold, italic=italic)


# ---------------------------------------------------------------- 基础校验


def _require_mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TemplateValidationError(f"{path} 必须是 JSON 对象")
    return value


def _reject_unknown_keys(
    data: Mapping[str, Any], allowed: frozenset[str], path: str
) -> None:
    unknown = sorted(key for key in data if key not in allowed)
    if unknown:
        raise TemplateValidationError(f"{path} 包含未知字段：{'、'.join(unknown)}")


def _require_text(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise TemplateValidationError(f"{path} 必须是文本")
    text = value.strip()
    if not text:
        raise TemplateValidationError(f"{path} 不能为空")
    return text


def _require_int(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TemplateValidationError(f"{path} 必须是整数")
    return value


def _require_bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise TemplateValidationError(f"{path} 必须是布尔值")
    return value


def _require_number(
    value: object,
    path: str,
    *,
    positive: bool = False,
    maximum: float | None = None,
) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TemplateValidationError(f"{path} 必须是数值")
    number = float(value)
    if not math.isfinite(number):
        raise TemplateValidationError(f"{path} 必须是有限数值")
    if positive and number <= 0:
        raise TemplateValidationError(f"{path} 必须大于 0")
    if not positive and number < 0:
        raise TemplateValidationError(f"{path} 不能为负数")
    if maximum is not None and number > maximum:
        raise TemplateValidationError(f"{path} 不能超过 {maximum}")
    return number


def _require_choice(value: object, path: str, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise TemplateValidationError(
            f"{path} 取值不合法：{value!r}，可选值：{'、'.join(allowed)}"
        )
    return value
