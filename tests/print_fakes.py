"""打印相关测试共用的模拟设备与模板构造工具。"""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QColor, QImage, QPainter

from app.services.print_target import STATE_IDLE, PageGeometry
from app.services.template_model import (
    CertificateTemplate,
    FontSpec,
    PhotoElement,
    TextElement,
)


class FakePrintTarget:
    """以 QImage 作为打印设备的测试替身，可注入设备与结束状态。"""

    def __init__(
        self,
        *,
        dpi: int = 300,
        paper_mm: tuple[float, float] = (100.0, 50.0),
        printable: tuple[float, float, float, float] | None = None,
        valid: bool = True,
        acceptance: bool | None = None,
        state: str = STATE_IDLE,
        fail_begin: bool = False,
        begin_error: str | None = None,
        geometry_error: str | None = None,
    ) -> None:
        self._dpi = dpi
        self._paper = paper_mm
        self._printable = printable
        self._valid = valid
        self._acceptance = acceptance
        self._state = state
        self.fail_begin = fail_begin
        self.begin_error = begin_error
        self.geometry_error = geometry_error
        self.end_error: str | None = None
        self.images: list[QImage] = []
        self.painters: list[QPainter] = []
        self.begin_count = 0
        self.end_count = 0
        self.abort_count = 0

    def name(self) -> str:
        return "测试打印机"

    def is_valid(self) -> bool:
        return self._valid

    def resolution(self) -> int:
        return self._dpi

    def page_geometry(self) -> PageGeometry:
        if self.geometry_error is not None:
            raise RuntimeError(self.geometry_error)
        width, height = self._paper
        if self._printable is None:
            return PageGeometry(
                paper_width_mm=width,
                paper_height_mm=height,
                printable_x_mm=None,
                printable_y_mm=None,
                printable_width_mm=None,
                printable_height_mm=None,
                margins_mm=(0.0, 0.0, 0.0, 0.0),
            )
        x, y, printable_width, printable_height = self._printable
        return PageGeometry(
            paper_width_mm=width,
            paper_height_mm=height,
            printable_x_mm=x,
            printable_y_mm=y,
            printable_width_mm=printable_width,
            printable_height_mm=printable_height,
            margins_mm=(
                x,
                y,
                width - x - printable_width,
                height - y - printable_height,
            ),
        )

    def begin(self) -> QPainter | None:
        self.begin_count += 1
        if self.begin_error is not None:
            raise RuntimeError(self.begin_error)
        if self.fail_begin:
            return None
        width = max(1, round(self._paper[0] / 25.4 * self._dpi))
        height = max(1, round(self._paper[1] / 25.4 * self._dpi))
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        self.images.append(image)
        self.painters.append(painter)
        return painter

    def end(self) -> None:
        self.end_count += 1
        painter = self.painters[-1]
        if painter.isActive():
            painter.end()
        if self.end_error is not None:
            raise RuntimeError(self.end_error)

    def abort(self) -> None:
        self.abort_count += 1
        painter = self.painters[-1]
        if painter.isActive():
            painter.end()

    def state(self) -> str:
        return self._state

    def acceptance(self) -> bool | None:
        return self._acceptance


def simple_template(
    *,
    width_mm: float = 100.0,
    height_mm: float = 50.0,
    with_photo: bool = False,
    name: str = "打印模板",
    certificate_type: str = "graduation",
) -> CertificateTemplate:
    elements: list[TextElement | PhotoElement] = [
        TextElement(
            id="name_value",
            x_mm=10.0,
            y_mm=10.0,
            width_mm=40.0,
            height_mm=10.0,
            font=FontSpec(family="Microsoft YaHei", size_pt=12.0),
            field="name",
        )
    ]
    if with_photo:
        elements.append(
            PhotoElement(
                id="photo",
                x_mm=60.0,
                y_mm=10.0,
                width_mm=30.0,
                height_mm=40.0,
            )
        )
    return CertificateTemplate(
        name=name,
        certificate_type=certificate_type,
        canvas_width_mm=width_mm,
        canvas_height_mm=height_mm,
        elements=tuple(elements),
    )


def record_dict(index: int, **values: Any) -> dict[str, Any]:
    return {
        "id": index,
        "student_no": f"S-{index:03d}",
        "name": "张三",
        "certificate_no": f"BJ{index:04d}",
        "photo_path": None,
        **values,
    }
