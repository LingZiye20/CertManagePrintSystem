"""设计画布测试：毫米换算、缩放、选中、拖动与尺寸调整。"""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.services.template_model import (
    CertificateTemplate,
    FontSpec,
    PhotoElement,
    TextElement,
)
from app.ui.template_canvas import BASE_DPI, MIN_ELEMENT_MM, TemplateCanvas

MM_PER_PX = 25.4 / BASE_DPI


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _text(
    *,
    element_id: str = "text",
    x_mm: float = 10.0,
    y_mm: float = 10.0,
    width_mm: float = 40.0,
    height_mm: float = 20.0,
) -> TextElement:
    return TextElement(
        id=element_id,
        x_mm=x_mm,
        y_mm=y_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        font=FontSpec(family="Microsoft YaHei", size_pt=12.0),
        field="name",
    )


def _photo(**overrides: Any) -> PhotoElement:
    values: dict[str, Any] = {
        "id": "photo",
        "x_mm": 60.0,
        "y_mm": 10.0,
        "width_mm": 30.0,
        "height_mm": 40.0,
    }
    values.update(overrides)
    return PhotoElement(**values)


def _template(*elements: TextElement | PhotoElement) -> CertificateTemplate:
    return CertificateTemplate(
        name="测试模板",
        certificate_type="graduation",
        canvas_width_mm=100.0,
        canvas_height_mm=60.0,
        elements=tuple(elements) or (_text(),),
    )


@pytest.fixture
def canvas(qapp: QApplication, tmp_path) -> TemplateCanvas:
    widget = TemplateCanvas()
    widget.set_template(_template(_text(), _photo()))
    widget.resize(widget.page_size_px())
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close()


def _center(element: TextElement | PhotoElement, canvas: TemplateCanvas) -> QPoint:
    rect = canvas.element_screen_rect(element)
    return QPoint(round(rect.center().x()), round(rect.center().y()))


def _element(canvas: TemplateCanvas, element_id: str) -> TextElement | PhotoElement:
    template = canvas.template()
    assert template is not None
    for element in template.elements:
        if element.id == element_id:
            return element
    raise AssertionError(f"元素不存在：{element_id}")


def _drag(canvas: TemplateCanvas, start: QPoint, end: QPoint) -> None:
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas, end)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end)


# ---------------------------------------------------------------- 尺寸与缩放


def test_page_size_follows_mm_and_zoom(qapp: QApplication) -> None:
    widget = TemplateCanvas()
    widget.set_template(_template())
    widget.set_zoom(1.0)

    assert widget.page_size_px().width() == round(100.0 / 25.4 * BASE_DPI)
    assert widget.page_size_px().height() == round(60.0 / 25.4 * BASE_DPI)

    widget.set_zoom(0.5)
    page = widget.page_size_px()
    assert page.width() == round(100.0 / 25.4 * BASE_DPI * 0.5)
    assert page.height() == round(60.0 / 25.4 * BASE_DPI * 0.5)


def test_zoom_does_not_modify_template(canvas: TemplateCanvas) -> None:
    original = canvas.template()

    canvas.set_zoom(0.5)
    canvas.set_zoom(2.0)

    assert canvas.template() == original


def test_zoom_rejects_invalid_values(canvas: TemplateCanvas) -> None:
    for invalid in (0, -1.0):
        with pytest.raises(ValueError):
            canvas.set_zoom(invalid)


# ---------------------------------------------------------------- 选中


def test_element_at_hits_inside_only(canvas: TemplateCanvas) -> None:
    text = _element(canvas, "text")

    inside = _center(text, canvas)
    assert canvas.element_at(inside) == "text"
    assert canvas.element_at(QPoint(1, 1)) is None


def test_click_selects_and_background_clears(canvas: TemplateCanvas) -> None:
    text = _element(canvas, "text")

    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=_center(text, canvas))
    assert canvas.selected_element_id() == "text"

    QTest.mouseClick(
        canvas, Qt.MouseButton.LeftButton, pos=QPoint(canvas.width() - 2, canvas.height() - 2)
    )
    assert canvas.selected_element_id() is None


def test_selection_signal_reports_element(canvas: TemplateCanvas) -> None:
    seen: list[object] = []
    canvas.selection_changed.connect(seen.append)

    text = _element(canvas, "text")
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=_center(text, canvas))

    assert seen == ["text"]


# ---------------------------------------------------------------- 拖动


def test_drag_moves_element_by_mm_at_zoom_one(canvas: TemplateCanvas) -> None:
    text = _element(canvas, "text")
    dx = 38
    start = _center(text, canvas)

    _drag(canvas, start, QPoint(start.x() + dx, start.y()))

    moved = _element(canvas, "text")
    assert moved.x_mm == pytest.approx(round((10.0 + dx * MM_PER_PX) * 10) / 10, abs=1e-6)
    assert moved.y_mm == pytest.approx(10.0)
    assert moved.width_mm == pytest.approx(text.width_mm)
    assert moved.height_mm == pytest.approx(text.height_mm)


def test_drag_uses_zoom_in_mm_conversion(canvas: TemplateCanvas) -> None:
    canvas.set_zoom(2.0)
    text = _element(canvas, "text")
    dx = 76
    start = _center(text, canvas)

    _drag(canvas, start, QPoint(start.x() + dx, start.y()))

    moved = _element(canvas, "text")
    expected = round((10.0 + dx * MM_PER_PX / 2.0) * 10) / 10
    assert moved.x_mm == pytest.approx(expected, abs=1e-6)


def test_drag_is_clamped_at_canvas_edges(canvas: TemplateCanvas) -> None:
    canvas.set_template(_template(_text(x_mm=0.0, y_mm=0.0)))
    text = _element(canvas, "text")
    start = _center(text, canvas)

    _drag(canvas, start, QPoint(start.x() - 60, start.y() - 60))
    moved = _element(canvas, "text")
    assert moved.x_mm == 0.0
    assert moved.y_mm == 0.0

    _drag(canvas, _center(moved, canvas), QPoint(canvas.width(), canvas.height()))
    moved = _element(canvas, "text")
    assert moved.x_mm == pytest.approx(100.0 - moved.width_mm)
    assert moved.y_mm == pytest.approx(60.0 - moved.height_mm)


def test_drag_emits_element_changed(canvas: TemplateCanvas) -> None:
    changed: list[str] = []
    canvas.element_changed.connect(changed.append)

    text = _element(canvas, "text")
    start = _center(text, canvas)
    _drag(canvas, start, QPoint(start.x() + 20, start.y()))

    assert changed == ["text"]


# ---------------------------------------------------------------- 尺寸调整


def test_resize_from_bottom_right_corner(canvas: TemplateCanvas) -> None:
    text = _element(canvas, "text")
    canvas.select_element("text")
    rect = canvas.element_screen_rect(text)
    corner = QPoint(round(rect.right()), round(rect.bottom()))
    dx = dy = 38

    _drag(canvas, corner, QPoint(corner.x() + dx, corner.y() + dy))

    resized = _element(canvas, "text")
    assert resized.x_mm == pytest.approx(text.x_mm)
    assert resized.y_mm == pytest.approx(text.y_mm)
    assert resized.width_mm == pytest.approx(
        round((text.width_mm + dx * MM_PER_PX) * 10) / 10, abs=1e-6
    )
    assert resized.height_mm == pytest.approx(
        round((text.height_mm + dy * MM_PER_PX) * 10) / 10, abs=1e-6
    )


def test_resize_from_top_left_corner_keeps_bottom_right(canvas: TemplateCanvas) -> None:
    canvas.set_template(_template(_text(x_mm=20.0, y_mm=20.0, width_mm=30.0, height_mm=20.0)))
    text = _element(canvas, "text")
    canvas.select_element("text")
    rect = canvas.element_screen_rect(text)
    corner = QPoint(round(rect.left()), round(rect.top()))
    dx = dy = 19

    _drag(canvas, corner, QPoint(corner.x() + dx, corner.y() + dy))

    resized = _element(canvas, "text")
    assert resized.x_mm == pytest.approx(round((20.0 + dx * MM_PER_PX) * 10) / 10, abs=1e-6)
    assert resized.y_mm == pytest.approx(round((20.0 + dy * MM_PER_PX) * 10) / 10, abs=1e-6)
    assert resized.x_mm + resized.width_mm == pytest.approx(50.0, abs=0.11)
    assert resized.y_mm + resized.height_mm == pytest.approx(40.0, abs=0.11)


def test_resize_is_clamped_to_minimum_size(canvas: TemplateCanvas) -> None:
    canvas.set_template(_template(_text(x_mm=10.0, y_mm=10.0, width_mm=5.0, height_mm=5.0)))
    text = _element(canvas, "text")
    canvas.select_element("text")
    rect = canvas.element_screen_rect(text)
    corner = QPoint(round(rect.right()), round(rect.bottom()))

    _drag(canvas, corner, QPoint(corner.x() - 200, corner.y() - 200))

    resized = _element(canvas, "text")
    assert resized.width_mm == pytest.approx(MIN_ELEMENT_MM)
    assert resized.height_mm == pytest.approx(MIN_ELEMENT_MM)


# ---------------------------------------------------------------- 只读


def test_read_only_ignores_mouse_editing(canvas: TemplateCanvas) -> None:
    original = canvas.template()
    text = _element(canvas, "text")
    canvas.set_interactive(False)

    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=_center(text, canvas))
    start = _center(text, canvas)
    _drag(canvas, start, QPoint(start.x() + 40, start.y() + 40))

    assert canvas.template() == original
    assert canvas.selected_element_id() is None
    assert canvas.is_interactive() is False
