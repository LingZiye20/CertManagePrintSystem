"""CertificateRenderer 测试：毫米坐标、文本行为、照片适配、告警与统一路径。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from app.services.template_model import (
    CertificateTemplate,
    FontSpec,
    PhotoElement,
    TextElement,
)
from app.services.template_renderer import (
    CertificateRenderer,
    RenderResult,
    RenderWarning,
)

FONT_FAMILY = "Microsoft YaHei"


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _template(
    elements: list[TextElement | PhotoElement],
    *,
    width_mm: float = 100.0,
    height_mm: float = 50.0,
) -> CertificateTemplate:
    return CertificateTemplate(
        name="测试模板",
        certificate_type="graduation",
        canvas_width_mm=width_mm,
        canvas_height_mm=height_mm,
        elements=tuple(elements),
    )


def _text(
    *,
    element_id: str = "text",
    field: str | None = "name",
    text: str | None = None,
    x_mm: float = 0.0,
    y_mm: float = 0.0,
    width_mm: float = 40.0,
    height_mm: float = 10.0,
    size_pt: float = 12.0,
    align: str = "left",
    vertical_align: str = "top",
    wrap: str = "none",
) -> TextElement:
    return TextElement(
        id=element_id,
        x_mm=x_mm,
        y_mm=y_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        font=FontSpec(family=FONT_FAMILY, size_pt=size_pt),
        align=align,
        vertical_align=vertical_align,
        wrap=wrap,
        field=field,
        text=text,
    )


def _photo(
    *,
    element_id: str = "photo",
    x_mm: float = 0.0,
    y_mm: float = 0.0,
    width_mm: float = 20.0,
    height_mm: float = 20.0,
    fit: str = "cover",
) -> PhotoElement:
    return PhotoElement(
        id=element_id,
        x_mm=x_mm,
        y_mm=y_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        fit=fit,
    )


def _is_dark(color: QColor, threshold: int = 200) -> bool:
    return (color.red() + color.green() + color.blue()) / 3 < threshold


def _dark_bbox(
    image: QImage, rect: QRectF
) -> tuple[int, int, int, int] | None:
    """返回区域内暗色像素的外接矩形 ``(left, top, right, bottom)``。"""
    left = top = None
    right = bottom = -1
    for y in range(int(rect.top()), int(rect.bottom()) + 1):
        for x in range(int(rect.left()), int(rect.right()) + 1):
            if x < 0 or y < 0 or x >= image.width() or y >= image.height():
                continue
            if not _is_dark(image.pixelColor(x, y)):
                continue
            left = x if left is None else min(left, x)
            top = y if top is None else min(top, y)
            right = max(right, x)
            bottom = max(bottom, y)
    if left is None or top is None:
        return None
    return left, top, right, bottom


def _dark_row_count(image: QImage, rect: QRectF) -> int:
    rows = 0
    for y in range(int(rect.top()), int(rect.bottom()) + 1):
        if any(
            _is_dark(image.pixelColor(x, y))
            for x in range(int(rect.left()), int(rect.right()) + 1)
            if 0 <= x < image.width() and 0 <= y < image.height()
        ):
            rows += 1
    return rows


def _write_jpeg(path: Path, width: int, height: int) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#202020"))
    assert image.save(str(path), "JPG")
    return path


# ---------------------------------------------------------------- 画布


def test_canvas_size_follows_mm_and_dpi(qapp: QApplication) -> None:
    result = CertificateRenderer().render_to_image(
        _template([], width_mm=210.0, height_mm=297.0), {}, dpi=96
    )

    assert isinstance(result, RenderResult)
    assert result.width_px == 794
    assert result.height_px == 1123
    assert result.image.width() == 794
    assert result.image.height() == 1123
    assert result.dpi == 96


def test_canvas_background_is_white(qapp: QApplication) -> None:
    result = CertificateRenderer().render_to_image(_template([]), {}, dpi=96)
    assert result.image.pixelColor(0, 0) == QColor("white")
    assert result.image.pixelColor(result.width_px - 1, result.height_px - 1) == QColor("white")


def test_invalid_dpi_is_rejected(qapp: QApplication) -> None:
    for dpi in (0, -96, 601, 96.5, "96"):
        with pytest.raises(ValueError):
            CertificateRenderer().render_to_image(_template([]), {}, dpi=dpi)


# ---------------------------------------------------------------- 文本


def test_bound_field_text_is_rendered(qapp: QApplication) -> None:
    element = _text(x_mm=5.0, y_mm=5.0, width_mm=40.0, height_mm=12.0, size_pt=14)
    result = CertificateRenderer().render_to_image(
        _template([element]), {"name": "张三"}, dpi=96
    )

    bbox = _dark_bbox(
        result.image,
        QRectF(5 / 25.4 * 96, 5 / 25.4 * 96, 40 / 25.4 * 96, 12 / 25.4 * 96),
    )
    assert bbox is not None
    assert result.warnings == ()


def test_static_text_is_rendered(qapp: QApplication) -> None:
    element = _text(field=None, text="证书", size_pt=16)
    result = CertificateRenderer().render_to_image(_template([element]), {}, dpi=96)

    assert _dark_bbox(result.image, QRectF(0, 0, result.width_px, result.height_px))
    assert result.warnings == ()


def test_business_strings_are_drawn_as_is(qapp: QApplication) -> None:
    for value in ("00123", "2026-07-01", "2026/7/1"):
        element = _text(field="student_no", size_pt=12)
        assert element.source_text({"student_no": value}) == value


def test_text_outside_element_is_clipped(qapp: QApplication) -> None:
    element = _text(
        field=None,
        text="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        width_mm=10.0,
        height_mm=6.0,
        size_pt=20,
    )
    result = CertificateRenderer().render_to_image(_template([element]), {}, dpi=96)

    element_rect = QRectF(0, 0, 10 / 25.4 * 96, 6 / 25.4 * 96)
    assert _dark_bbox(result.image, element_rect) is not None
    outside = QRectF(0, element_rect.height() + 2, result.width_px, 30)
    assert _dark_bbox(result.image, outside) is None

    # 水平方向同样被裁切：元素右侧区域不应出现文字。
    right_side = QRectF(element_rect.width() + 2, 0, result.width_px - element_rect.width() - 2, 40)
    assert _dark_bbox(result.image, right_side) is None


def test_wrap_none_keeps_single_line(qapp: QApplication) -> None:
    text = "alpha beta gamma delta epsilon zeta eta theta"
    element = _text(
        field=None,
        text=text,
        width_mm=30.0,
        height_mm=30.0,
        size_pt=10,
        wrap="none",
    )
    result = CertificateRenderer().render_to_image(_template([element]), {}, dpi=96)

    rows = _dark_row_count(result.image, QRectF(0, 0, 30 / 25.4 * 96, 30 / 25.4 * 96))
    # 单行文本只占用一行字高，不会铺满整个元素高度。
    assert 0 < rows < 30 / 25.4 * 96 / 2


def test_wrap_word_uses_multiple_lines(qapp: QApplication) -> None:
    text = "alpha beta gamma delta epsilon zeta eta theta"
    element = _text(
        field=None,
        text=text,
        width_mm=30.0,
        height_mm=30.0,
        size_pt=10,
        wrap="word",
    )
    result = CertificateRenderer().render_to_image(_template([element]), {}, dpi=96)

    rows = _dark_row_count(result.image, QRectF(0, 0, 30 / 25.4 * 96, 30 / 25.4 * 96))
    assert rows > 30 / 25.4 * 96 / 3


def test_horizontal_alignment_positions_text(qapp: QApplication) -> None:
    element_width_px = 60 / 25.4 * 96
    positions: dict[str, float] = {}
    for align in ("left", "center", "right"):
        element = _text(
            field=None, text="II", width_mm=60.0, height_mm=12.0, size_pt=12, align=align
        )
        result = CertificateRenderer().render_to_image(_template([element]), {}, dpi=96)
        bbox = _dark_bbox(result.image, QRectF(0, 0, element_width_px, 12 / 25.4 * 96))
        assert bbox is not None
        positions[align] = (bbox[0] + bbox[2]) / 2

    assert positions["left"] < positions["center"] < positions["right"]


def test_vertical_alignment_positions_text(qapp: QApplication) -> None:
    element_height_px = 30 / 25.4 * 96
    positions: dict[str, float] = {}
    for vertical_align in ("top", "middle", "bottom"):
        element = _text(
            field=None,
            text="II",
            width_mm=30.0,
            height_mm=30.0,
            size_pt=10,
            vertical_align=vertical_align,
        )
        result = CertificateRenderer().render_to_image(_template([element]), {}, dpi=96)
        bbox = _dark_bbox(result.image, QRectF(0, 0, 30 / 25.4 * 96, element_height_px))
        assert bbox is not None
        positions[vertical_align] = (bbox[1] + bbox[3]) / 2

    assert positions["top"] < positions["middle"] < positions["bottom"]


def test_missing_field_returns_warning(qapp: QApplication) -> None:
    element = _text(field="name")
    result = CertificateRenderer().render_to_image(_template([element]), {}, dpi=96)

    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert isinstance(warning, RenderWarning)
    assert warning.code == "missing_field"
    assert warning.element_id == "text"
    assert "name" in warning.message


def test_blank_field_value_is_not_a_warning(qapp: QApplication) -> None:
    element = _text(field="name")
    result = CertificateRenderer().render_to_image(
        _template([element]), {"name": None}, dpi=96
    )
    assert result.warnings == ()


# ---------------------------------------------------------------- 照片


def test_photo_is_drawn_with_cover(qapp: QApplication, tmp_path: Path) -> None:
    photo_path = _write_jpeg(tmp_path / "photo.jpg", 40, 20)
    element = _photo(width_mm=20.0, height_mm=20.0, fit="cover")
    result = CertificateRenderer().render_to_image(
        _template([element]), {}, photo_file=photo_path, dpi=96
    )

    rect = QRectF(0, 0, 20 / 25.4 * 96, 20 / 25.4 * 96)
    bbox = _dark_bbox(result.image, rect)
    assert bbox is not None
    height = bbox[3] - bbox[1] + 1
    assert abs(height - rect.height()) <= 3
    assert result.warnings == ()


def test_photo_contain_leaves_letterbox(qapp: QApplication, tmp_path: Path) -> None:
    photo_path = _write_jpeg(tmp_path / "photo.jpg", 40, 20)
    element = _photo(width_mm=20.0, height_mm=20.0, fit="contain")
    result = CertificateRenderer().render_to_image(
        _template([element]), {}, photo_file=photo_path, dpi=96
    )

    rect = QRectF(0, 0, 20 / 25.4 * 96, 20 / 25.4 * 96)
    bbox = _dark_bbox(result.image, rect)
    assert bbox is not None
    height = bbox[3] - bbox[1] + 1
    assert height < rect.height() * 0.75
    assert result.warnings == ()


def test_missing_photo_returns_warning(qapp: QApplication) -> None:
    result = CertificateRenderer().render_to_image(
        _template([_photo()]), {}, photo_file=None, dpi=96
    )

    assert [warning.code for warning in result.warnings] == ["missing_photo"]
    assert result.warnings[0].element_id == "photo"


def test_nonexistent_photo_file_returns_warning(qapp: QApplication, tmp_path: Path) -> None:
    result = CertificateRenderer().render_to_image(
        _template([_photo()]), {}, photo_file=tmp_path / "absent.jpg", dpi=96
    )

    assert [warning.code for warning in result.warnings] == ["photo_not_found"]


def test_corrupt_photo_returns_warning(qapp: QApplication, tmp_path: Path) -> None:
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not a jpeg")

    result = CertificateRenderer().render_to_image(
        _template([_photo()]), {}, photo_file=broken, dpi=96
    )

    assert [warning.code for warning in result.warnings] == ["photo_unreadable"]


# ---------------------------------------------------------------- 告警不中断渲染


def test_missing_photo_keeps_rendering_other_elements(qapp: QApplication) -> None:
    text_element = _text(
        field=None, text="证书", x_mm=0.0, y_mm=0.0, width_mm=40.0, height_mm=12.0
    )
    photo_element = _photo(x_mm=50.0, y_mm=10.0, width_mm=20.0, height_mm=20.0)

    result = CertificateRenderer().render_to_image(
        _template([text_element, photo_element]), {}, photo_file=None, dpi=96
    )

    assert [warning.code for warning in result.warnings] == ["missing_photo"]
    assert "照片" in result.warnings[0].message
    # 照片缺失不会中断整张证书：文本元素仍然完成绘制。
    assert _dark_bbox(result.image, QRectF(0, 0, 40 / 25.4 * 96, 12 / 25.4 * 96))


def test_nonexistent_photo_keeps_rendering_other_elements(
    qapp: QApplication, tmp_path: Path
) -> None:
    text_element = _text(
        field=None, text="证书", x_mm=0.0, y_mm=0.0, width_mm=40.0, height_mm=12.0
    )
    photo_element = _photo(x_mm=50.0, y_mm=10.0, width_mm=20.0, height_mm=20.0)

    result = CertificateRenderer().render_to_image(
        _template([text_element, photo_element]),
        {},
        photo_file=tmp_path / "absent.jpg",
        dpi=96,
    )

    assert [warning.code for warning in result.warnings] == ["photo_not_found"]
    assert "照片" in result.warnings[0].message
    assert _dark_bbox(result.image, QRectF(0, 0, 40 / 25.4 * 96, 12 / 25.4 * 96))


def test_corrupt_photo_keeps_rendering_other_elements(
    qapp: QApplication, tmp_path: Path
) -> None:
    text_element = _text(
        field=None, text="证书", x_mm=0.0, y_mm=0.0, width_mm=40.0, height_mm=12.0
    )
    photo_element = _photo(x_mm=50.0, y_mm=10.0, width_mm=20.0, height_mm=20.0)
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not a jpeg")

    result = CertificateRenderer().render_to_image(
        _template([text_element, photo_element]), {}, photo_file=broken, dpi=96
    )

    assert [warning.code for warning in result.warnings] == ["photo_unreadable"]
    assert "损坏" in result.warnings[0].message
    assert _dark_bbox(result.image, QRectF(0, 0, 40 / 25.4 * 96, 12 / 25.4 * 96))


def test_missing_field_keeps_rendering_other_elements(qapp: QApplication) -> None:
    bound_element = _text(field="name", x_mm=0.0, y_mm=0.0, width_mm=40.0, height_mm=12.0)
    static_element = _text(
        element_id="static",
        field=None,
        text="证书",
        x_mm=0.0,
        y_mm=20.0,
        width_mm=40.0,
        height_mm=12.0,
    )

    result = CertificateRenderer().render_to_image(
        _template([bound_element, static_element]), {}, dpi=96
    )

    assert [warning.code for warning in result.warnings] == ["missing_field"]
    assert result.warnings[0].element_id == "text"
    assert "name" in result.warnings[0].message
    # 缺失字段只影响该元素，后续静态文字仍然绘制。
    static_rect = QRectF(0, 20 / 25.4 * 96, 40 / 25.4 * 96, 12 / 25.4 * 96)
    assert _dark_bbox(result.image, static_rect)


# ---------------------------------------------------------------- 统一渲染路径


def test_render_to_image_uses_same_paint_path(qapp: QApplication, tmp_path: Path) -> None:
    photo_path = _write_jpeg(tmp_path / "photo.jpg", 40, 20)
    template = _template(
        [
            _text(field=None, text="证书", size_pt=16, width_mm=40.0, height_mm=12.0),
            _photo(x_mm=50.0, y_mm=10.0, width_mm=20.0, height_mm=20.0),
        ]
    )
    renderer = CertificateRenderer()

    result = renderer.render_to_image(
        template, {}, photo_file=photo_path, dpi=96
    )

    manual = QImage(
        result.width_px, result.height_px, QImage.Format.Format_ARGB32
    )
    manual.fill(QColor("white"))
    painter = QPainter(manual)
    try:
        warnings = renderer.paint(
            painter, template, {}, photo_file=photo_path, dpi=96
        )
    finally:
        painter.end()

    assert warnings == result.warnings
    assert manual == result.image
