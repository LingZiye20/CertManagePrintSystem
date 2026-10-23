"""模板设计画布。

职责边界：

- 只负责把模板以缩放后的屏幕坐标显示，并处理选中、拖动和尺寸调整；
- 页面与元素绘制一律调用 ``CertificateRenderer.paint()``，不重复实现文本或照片渲染；
- 内部模型始终保存毫米坐标，缩放比例只影响绘制与命中测试的换算；
- 只读模式（内置模板）下忽略全部鼠标编辑，不产生任何模型修改。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from app.services.template_model import (
    CertificateTemplate,
    TemplateElement,
)
from app.services.template_renderer import CertificateRenderer

BASE_DPI = 96
MIN_ELEMENT_MM = 1.0
GEOMETRY_PRECISION_MM = 0.1
HANDLE_SIZE_PX = 8

BORDER_COLOR = QColor(70, 110, 180)
SELECTION_COLOR = QColor(200, 80, 40)
HANDLE_COLOR = QColor(255, 255, 255)

_HANDLES: tuple[str, ...] = ("tl", "tr", "bl", "br")
_MIN_ZOOM = 0.05
_MAX_ZOOM = 8.0


def _round_mm(value: float) -> float:
    return round(value / GEOMETRY_PRECISION_MM) * GEOMETRY_PRECISION_MM


class TemplateCanvas(QWidget):
    """可缩放、可拖动的模板设计画布。"""

    selection_changed = Signal(object)
    element_changed = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        renderer: CertificateRenderer | None = None,
    ) -> None:
        super().__init__(parent)
        self._renderer = renderer if renderer is not None else CertificateRenderer()
        self._template: CertificateTemplate | None = None
        self._values: Mapping[str, Any] = {}
        self._photo_file = None
        self._zoom = 1.0
        self._selected_id: str | None = None
        self._interactive = True
        self._mode: str | None = None
        self._active_handle: str | None = None
        self._press_pos = QPointF()
        self._origin_geometry: tuple[float, float, float, float] | None = None
        self._geometry_changed = False
        self.setMouseTracking(True)

    # ------------------------------------------------------------------ 状态

    def template(self) -> CertificateTemplate | None:
        return self._template

    def set_template(self, template: CertificateTemplate | None) -> None:
        """载入模板；选中状态在元素仍存在时保留。"""
        self._template = template
        if template is None:
            self._selected_id = None
        elif self._selected_id is not None and all(
            element.id != self._selected_id for element in template.elements
        ):
            self._selected_id = None
        self._mode = None
        self.updateGeometry()
        self.resize(self.page_size_px())
        self.update()

    def set_preview_context(
        self,
        values: Mapping[str, Any] | None = None,
        photo_file: Any = None,
    ) -> None:
        """设置画布上用于绘制的示例数据与照片路径（不写入模板）。"""
        self._values = dict(values) if values is not None else {}
        self._photo_file = photo_file
        self.update()

    def set_interactive(self, enabled: bool) -> None:
        """只读模式关闭全部鼠标编辑与选中。"""
        self._interactive = bool(enabled)
        if not self._interactive:
            self._mode = None
            self._active_handle = None
            self._set_selected(None)

    def is_interactive(self) -> bool:
        return self._interactive

    def selected_element_id(self) -> str | None:
        return self._selected_id

    def select_element(self, element_id: str | None) -> None:
        if not self._interactive:
            return
        if element_id is not None and self._find(element_id) is None:
            return
        self._set_selected(element_id)

    def selected_element(self) -> TemplateElement | None:
        return self._find(self._selected_id)

    # ------------------------------------------------------------------ 几何

    def set_zoom(self, zoom: float) -> None:
        """设置显示缩放；只改变屏幕显示，不修改模板毫米坐标。"""
        if (
            not isinstance(zoom, (int, float))
            or isinstance(zoom, bool)
            or not math.isfinite(float(zoom))
        ):
            raise ValueError("画布缩放必须是有限数值")
        resolved = float(zoom)
        if resolved <= 0:
            raise ValueError("画布缩放必须大于 0")
        self._zoom = min(_MAX_ZOOM, max(_MIN_ZOOM, resolved))
        self.resize(self.page_size_px())
        self.updateGeometry()
        self.update()

    def zoom(self) -> float:
        return self._zoom

    def page_size_px(self) -> QSize:
        if self._template is None:
            return QSize(1, 1)
        scale = BASE_DPI / 25.4 * self._zoom
        return QSize(
            max(1, round(self._template.canvas_width_mm * scale)),
            max(1, round(self._template.canvas_height_mm * scale)),
        )

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return self.page_size_px()

    def element_screen_rect(self, element: TemplateElement) -> QRectF:
        scale = BASE_DPI / 25.4 * self._zoom
        return QRectF(
            element.x_mm * scale,
            element.y_mm * scale,
            element.width_mm * scale,
            element.height_mm * scale,
        )

    def element_at(self, pos: QPointF | Any) -> str | None:
        """返回命中的顶层元素 ID；空白返回 ``None``。"""
        if self._template is None:
            return None
        point = QPointF(pos)
        for element in reversed(self._template.elements):
            if self.element_screen_rect(element).contains(point):
                return element.id
        return None

    # ------------------------------------------------------------------ 绘制

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.fillRect(self.rect(), QColor(120, 120, 120))

            if self._template is None:
                return

            painter.save()
            try:
                painter.scale(self._zoom, self._zoom)
                self._renderer.paint(
                    painter,
                    self._template,
                    self._values,
                    photo_file=self._photo_file,
                    dpi=BASE_DPI,
                )
            finally:
                painter.restore()

            self._paint_outlines(painter)
        finally:
            painter.end()

    def _paint_outlines(self, painter: QPainter) -> None:
        if self._template is None:
            return
        for element in self._template.elements:
            rect = self.element_screen_rect(element)
            selected = element.id == self._selected_id
            pen = QPen(SELECTION_COLOR if selected else BORDER_COLOR)
            pen.setWidth(2 if selected else 1)
            pen.setStyle(Qt.PenStyle.SolidLine if selected else Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)
            if selected:
                for point in self._handle_points(rect).values():
                    painter.fillRect(
                        QRectF(
                            point.x() - HANDLE_SIZE_PX / 2,
                            point.y() - HANDLE_SIZE_PX / 2,
                            HANDLE_SIZE_PX,
                            HANDLE_SIZE_PX,
                        ),
                        HANDLE_COLOR,
                    )
                    painter.drawRect(
                        QRectF(
                            point.x() - HANDLE_SIZE_PX / 2,
                            point.y() - HANDLE_SIZE_PX / 2,
                            HANDLE_SIZE_PX,
                            HANDLE_SIZE_PX,
                        )
                    )

    # ------------------------------------------------------------------ 鼠标

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        if not self._interactive or self._template is None:
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        pos = event.position()
        self._press_pos = pos
        self._geometry_changed = False

        selected = self.selected_element()
        if selected is not None:
            handle = self._handle_at(selected, pos)
            if handle is not None:
                self._begin_drag("resize", handle, selected)
                return

        element_id = self.element_at(pos)
        if element_id is None:
            self._set_selected(None)
            return

        self._set_selected(element_id)
        element = self._find(element_id)
        if element is not None:
            self._begin_drag("move", None, element)

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        if (
            not self._interactive
            or self._mode is None
            or self._origin_geometry is None
            or self._template is None
            or self._selected_id is None
        ):
            return
        self._apply_drag(event.position())

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        if not self._interactive:
            return
        emit = self._mode is not None and self._geometry_changed and self._selected_id
        self._mode = None
        self._active_handle = None
        self._origin_geometry = None
        if emit and self._selected_id is not None:
            self.element_changed.emit(self._selected_id)

    # ------------------------------------------------------------------ 内部

    def _begin_drag(
        self,
        mode: str,
        handle: str | None,
        element: TemplateElement,
    ) -> None:
        self._mode = mode
        self._active_handle = handle
        self._origin_geometry = (
            element.x_mm,
            element.y_mm,
            element.width_mm,
            element.height_mm,
        )

    def _apply_drag(self, pos: QPointF) -> None:
        assert self._template is not None
        assert self._origin_geometry is not None

        mm_scale = 25.4 / BASE_DPI / self._zoom
        dx = (pos.x() - self._press_pos.x()) * mm_scale
        dy = (pos.y() - self._press_pos.y()) * mm_scale

        x, y, width, height = self._origin_geometry
        canvas_width = self._template.canvas_width_mm
        canvas_height = self._template.canvas_height_mm

        if self._mode == "move":
            left = min(max(x + dx, 0.0), canvas_width - width)
            top = min(max(y + dy, 0.0), canvas_height - height)
            new_geometry = (left, top, width, height)
        else:
            new_geometry = self._resized_geometry(
                (x, y, width, height),
                self._active_handle,
                dx,
                dy,
                canvas_width,
                canvas_height,
            )

        self._update_geometry(new_geometry)

    @staticmethod
    def _resized_geometry(
        geometry: tuple[float, float, float, float],
        handle: str | None,
        dx: float,
        dy: float,
        canvas_width: float,
        canvas_height: float,
    ) -> tuple[float, float, float, float]:
        x, y, width, height = geometry
        left, top = x, y
        right, bottom = x + width, y + height

        if handle in ("tl", "bl"):
            left = x + dx
        if handle in ("tr", "br"):
            right = x + width + dx
        if handle in ("tl", "tr"):
            top = y + dy
        if handle in ("bl", "br"):
            bottom = y + height + dy

        left = min(max(left, 0.0), canvas_width - MIN_ELEMENT_MM)
        top = min(max(top, 0.0), canvas_height - MIN_ELEMENT_MM)
        right = min(max(right, left + MIN_ELEMENT_MM), canvas_width)
        bottom = min(max(bottom, top + MIN_ELEMENT_MM), canvas_height)
        return (left, top, right - left, bottom - top)

    def _update_geometry(self, geometry: tuple[float, float, float, float]) -> None:
        if self._template is None or self._selected_id is None:
            return
        x, y, width, height = (_round_mm(value) for value in geometry)
        elements = tuple(
            replace(
                element,
                x_mm=x,
                y_mm=y,
                width_mm=width,
                height_mm=height,
            )
            if element.id == self._selected_id
            else element
            for element in self._template.elements
        )
        updated = replace(self._template, elements=elements)
        if updated == self._template:
            return
        self._template = updated
        self._geometry_changed = True
        self.update()

    def _set_selected(self, element_id: str | None) -> None:
        if self._selected_id == element_id:
            return
        self._selected_id = element_id
        self.update()
        self.selection_changed.emit(element_id)

    def _find(self, element_id: str | None) -> TemplateElement | None:
        if element_id is None or self._template is None:
            return None
        for element in self._template.elements:
            if element.id == element_id:
                return element
        return None

    @staticmethod
    def _handle_points(rect: QRectF) -> dict[str, QPointF]:
        return {
            "tl": QPointF(rect.left(), rect.top()),
            "tr": QPointF(rect.right(), rect.top()),
            "bl": QPointF(rect.left(), rect.bottom()),
            "br": QPointF(rect.right(), rect.bottom()),
        }

    def _handle_at(self, element: TemplateElement, pos: QPointF) -> str | None:
        rect = self.element_screen_rect(element)
        half = HANDLE_SIZE_PX / 2
        for name, point in self._handle_points(rect).items():
            area = QRectF(
                point.x() - half, point.y() - half, HANDLE_SIZE_PX, HANDLE_SIZE_PX
            )
            if area.contains(pos):
                return name
        return None
