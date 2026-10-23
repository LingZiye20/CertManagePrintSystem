"""证书模板统一渲染器。

预览与后续打印必须共用同一个 ``CertificateRenderer.paint()``：

- ``paint()`` 接收任意 ``QPainter``（离屏 QImage、预览控件或 QPrinter 均可）；
- ``render_to_image()`` 只是创建离屏画布并调用 ``paint()`` 的薄封装；
- 模板坐标始终以毫米保存，绘制时按 DPI 换算为像素，不把像素写回模板。

渲染器不访问数据库，也不决定照片所在目录；照片绝对路径由调用方（Service）
通过既有的照片路径安全校验后传入。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QImageReader,
    QPainter,
    QTextDocument,
    QTextOption,
)
from app.services.template_model import (
    CertificateTemplate,
    PhotoElement,
    TextElement,
)
from app.utils.mm_units import MM_PER_INCH, pt_to_px, validate_dpi

DEFAULT_DPI = 300
TEXT_COLOR = QColor(0, 0, 0)

_HORIZONTAL_FLAGS = {
    "left": Qt.AlignmentFlag.AlignLeft,
    "center": Qt.AlignmentFlag.AlignHCenter,
    "right": Qt.AlignmentFlag.AlignRight,
}
_VERTICAL_FLAGS = {
    "top": Qt.AlignmentFlag.AlignTop,
    "middle": Qt.AlignmentFlag.AlignVCenter,
    "bottom": Qt.AlignmentFlag.AlignBottom,
}


@dataclass(frozen=True, slots=True)
class RenderWarning:
    """单个元素的可渲染性问题；不阻断其余元素绘制。"""

    element_id: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class RenderResult:
    """离屏渲染结果。"""

    image: QImage
    width_px: int
    height_px: int
    dpi: int
    warnings: tuple[RenderWarning, ...]


class CertificateRenderer:
    """证书模板的统一绘制入口。"""

    def canvas_size(self, template: CertificateTemplate, dpi: int) -> tuple[int, int]:
        """返回画布像素尺寸，遵循毫米换算并按 DPI 校验。"""
        resolved = validate_dpi(dpi)
        return (
            self._mm_to_px(template.canvas_width_mm, resolved),
            self._mm_to_px(template.canvas_height_mm, resolved),
        )

    def paint(
        self,
        painter: QPainter,
        template: CertificateTemplate,
        values: Mapping[str, Any],
        *,
        photo_file: str | Path | None = None,
        dpi: int = DEFAULT_DPI,
    ) -> tuple[RenderWarning, ...]:
        """把模板绘制到给定 painter，返回逐元素告警。

        调用方负责 painter 的生命周期。传入的 painter 逻辑坐标应与 ``dpi``
        一致，即 1 单位对应 1 像素。
        """
        resolved_dpi = validate_dpi(dpi)
        width_px, height_px = self.canvas_size(template, resolved_dpi)

        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.fillRect(
                QRectF(0, 0, width_px, height_px), QColor(255, 255, 255)
            )

            warnings: list[RenderWarning] = []
            for element in template.elements:
                if isinstance(element, TextElement):
                    warnings.extend(
                        self._paint_text(painter, element, values, resolved_dpi)
                    )
                elif isinstance(element, PhotoElement):
                    warnings.extend(
                        self._paint_photo(painter, element, photo_file, resolved_dpi)
                    )
            return tuple(warnings)
        finally:
            painter.restore()

    def render_to_image(
        self,
        template: CertificateTemplate,
        values: Mapping[str, Any],
        *,
        photo_file: str | Path | None = None,
        dpi: int = DEFAULT_DPI,
    ) -> RenderResult:
        """渲染到离屏 QImage；内部复用 ``paint()``，与打印共用同一路径。"""
        resolved_dpi = validate_dpi(dpi)
        width_px, height_px = self.canvas_size(template, resolved_dpi)

        image = QImage(
            max(1, width_px), max(1, height_px), QImage.Format.Format_ARGB32
        )
        image.fill(QColor(255, 255, 255))

        painter = QPainter(image)
        try:
            warnings = self.paint(
                painter,
                template,
                values,
                photo_file=photo_file,
                dpi=resolved_dpi,
            )
        finally:
            painter.end()

        return RenderResult(
            image=image,
            width_px=image.width(),
            height_px=image.height(),
            dpi=resolved_dpi,
            warnings=warnings,
        )

    # ------------------------------------------------------------------ 元素

    def _paint_text(
        self,
        painter: QPainter,
        element: TextElement,
        values: Mapping[str, Any],
        dpi: int,
    ) -> list[RenderWarning]:
        warnings: list[RenderWarning] = []
        if element.field is not None and element.field not in values:
            warnings.append(
                RenderWarning(
                    element_id=element.id,
                    code="missing_field",
                    message=f"记录中缺少字段 {element.field}，已按空文本绘制",
                )
            )

        text = element.source_text(values)
        if not text:
            return warnings

        rect = self._element_rect(element, dpi)
        font = self._build_font(element, dpi)

        painter.save()
        try:
            painter.setClipRect(rect)
            painter.setPen(TEXT_COLOR)
            painter.setFont(font)
            if element.wrap == "word":
                self._draw_wrapped_text(painter, rect, element, text, font)
            else:
                flags = (
                    _HORIZONTAL_FLAGS[element.align]
                    | _VERTICAL_FLAGS[element.vertical_align]
                )
                # 单行模式：显式换行降级为空格，不做换行也不缩小字号。
                painter.drawText(rect, int(flags), text.replace("\r", " ").replace("\n", " "))
        finally:
            painter.restore()
        return warnings

    def _draw_wrapped_text(
        self,
        painter: QPainter,
        rect: QRectF,
        element: TextElement,
        text: str,
        font: QFont,
    ) -> None:
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(font)
        document.setPlainText(text)

        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WordWrap)
        option.setAlignment(_HORIZONTAL_FLAGS[element.align])
        document.setDefaultTextOption(option)
        document.setTextWidth(rect.width())

        content_height = document.size().height()
        free_space = rect.height() - content_height
        if element.vertical_align == "middle":
            offset = max(0.0, free_space / 2)
        elif element.vertical_align == "bottom":
            offset = max(0.0, free_space)
        else:
            offset = 0.0

        painter.save()
        try:
            painter.setClipRect(rect)
            painter.translate(rect.left(), rect.top() + offset)
            document.drawContents(
                painter, QRectF(0, 0, rect.width(), rect.height())
            )
        finally:
            painter.restore()

    def _paint_photo(
        self,
        painter: QPainter,
        element: PhotoElement,
        photo_file: str | Path | None,
        dpi: int,
    ) -> list[RenderWarning]:
        rect = self._element_rect(element, dpi)
        if photo_file is None:
            return [
                RenderWarning(
                    element_id=element.id,
                    code="missing_photo",
                    message="该记录没有照片，已跳过照片元素",
                )
            ]

        path = Path(photo_file)
        if not path.exists():
            return [
                RenderWarning(
                    element_id=element.id,
                    code="photo_not_found",
                    message=f"照片文件不存在：{path.name}",
                )
            ]

        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read() if reader.canRead() else QImage()
        if image.isNull() or image.width() <= 0 or image.height() <= 0:
            return [
                RenderWarning(
                    element_id=element.id,
                    code="photo_unreadable",
                    message=f"照片无法解码或已损坏：{path.name}",
                )
            ]

        painter.save()
        try:
            painter.setClipRect(rect)
            if element.fit == "contain":
                self._draw_contain(painter, rect, image)
            else:
                self._draw_cover(painter, rect, image)
        finally:
            painter.restore()
        return []

    @staticmethod
    def _draw_cover(painter: QPainter, rect: QRectF, image: QImage) -> None:
        """等比放大填满元素，居中裁切多余部分。"""
        source_aspect = image.width() / image.height()
        target_aspect = rect.width() / rect.height()

        if source_aspect > target_aspect:
            crop_height = image.height()
            crop_width = crop_height * target_aspect
            source = QRectF(
                (image.width() - crop_width) / 2, 0.0, crop_width, crop_height
            )
        else:
            crop_width = image.width()
            crop_height = crop_width / target_aspect
            source = QRectF(
                0.0, (image.height() - crop_height) / 2, crop_width, crop_height
            )
        painter.drawImage(rect, image, source)

    @staticmethod
    def _draw_contain(painter: QPainter, rect: QRectF, image: QImage) -> None:
        """等比缩放至元素内部，居中绘制并保留空白边。"""
        scale = min(rect.width() / image.width(), rect.height() / image.height())
        width = image.width() * scale
        height = image.height() * scale
        target = QRectF(
            rect.left() + (rect.width() - width) / 2,
            rect.top() + (rect.height() - height) / 2,
            width,
            height,
        )
        painter.drawImage(target, image)

    # ------------------------------------------------------------------ 换算

    @staticmethod
    def _mm_to_px(value: float, dpi: int) -> int:
        return max(1, int(round(value / MM_PER_INCH * dpi)))

    @staticmethod
    def _element_rect(element: TextElement | PhotoElement, dpi: int) -> QRectF:
        scale = dpi / MM_PER_INCH
        return QRectF(
            element.x_mm * scale,
            element.y_mm * scale,
            element.width_mm * scale,
            element.height_mm * scale,
        )

    @staticmethod
    def _build_font(element: TextElement, dpi: int) -> QFont:
        font = QFont(element.font.family)
        font.setPixelSize(max(1, pt_to_px(element.font.size_pt, dpi)))
        font.setBold(element.font.bold)
        font.setItalic(element.font.italic)
        return font
