"""打印设备抽象与 Qt QPrinter 实现。

设计要点：

- 打印服务只依赖 ``PrintTarget``，测试可用 QImage 画布替身，不连接真实打印机；
- 页面几何统一以毫米描述；设备原点固定为纸张左上角（``setFullPage(True)``），
  逻辑单位为设备像素，毫米到像素只由渲染器按 ``resolution()`` 换算一次；
- ``QPageLayout.paintRect()`` 只是**布局可绘制区域**，不是硬件可打印边距的可靠测量；
  ``acceptance()`` 在 Qt 下恒为 ``None``，表示无法确认驱动是否接受任务。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPageLayout, QPainter
from PySide6.QtPrintSupport import QPrinter

STATE_IDLE = "idle"
STATE_ACTIVE = "active"
STATE_ERROR = "error"
STATE_ABORTED = "aborted"
STATE_UNKNOWN = "unknown"

UNNAMED_PRINTER = "（未命名打印机）"


@dataclass(frozen=True, slots=True)
class PageGeometry:
    """纸张与布局可绘制区域的毫米快照。"""

    paper_width_mm: float
    paper_height_mm: float
    printable_x_mm: float | None
    printable_y_mm: float | None
    printable_width_mm: float | None
    printable_height_mm: float | None
    margins_mm: tuple[float, float, float, float]

    @property
    def printable_available(self) -> bool:
        return (
            self.printable_x_mm is not None
            and self.printable_y_mm is not None
            and self.printable_width_mm is not None
            and self.printable_height_mm is not None
            and self.printable_width_mm > 0
            and self.printable_height_mm > 0
        )


class PrintTarget:
    """一次打印会话使用的设备接口。"""

    def name(self) -> str:
        raise NotImplementedError

    def is_valid(self) -> bool:
        raise NotImplementedError

    def resolution(self) -> int:
        raise NotImplementedError

    def page_geometry(self) -> PageGeometry:
        raise NotImplementedError

    def begin(self) -> QPainter | None:
        raise NotImplementedError

    def end(self) -> None:
        raise NotImplementedError

    def abort(self) -> None:
        raise NotImplementedError

    def state(self) -> str:
        raise NotImplementedError

    def acceptance(self) -> bool | None:
        """驱动是否明确接受任务；``None`` 表示无法确认（Qt 的实际情况）。"""
        raise NotImplementedError


class QtPrintTarget(PrintTarget):
    """包装 ``QPrinter`` 的真实打印设备。

    使用前应通过 ``QPrintDialog`` 让用户确认打印机、纸张、方向与份数；
    本类不弹出任何对话框，也不声称驱动接受或物理输出成功。
    """

    def __init__(self, printer: QPrinter) -> None:
        self.printer = printer
        # 固定原点为纸张左上角，避免依赖驱动可打印区域原点。
        self.printer.setFullPage(True)
        self._painter: QPainter | None = None

    def name(self) -> str:
        return self.printer.printerName() or UNNAMED_PRINTER

    def is_valid(self) -> bool:
        return bool(self.printer.isValid())

    def resolution(self) -> int:
        return int(self.printer.resolution())

    def page_geometry(self) -> PageGeometry:
        layout = self.printer.pageLayout()
        paper: QRectF = layout.fullRect(QPageLayout.Unit.Millimeter)
        margins = layout.margins(QPageLayout.Unit.Millimeter)
        printable: QRectF = layout.paintRect(QPageLayout.Unit.Millimeter)

        available = printable.width() > 0 and printable.height() > 0
        return PageGeometry(
            paper_width_mm=float(paper.width()),
            paper_height_mm=float(paper.height()),
            printable_x_mm=float(printable.x()) if available else None,
            printable_y_mm=float(printable.y()) if available else None,
            printable_width_mm=float(printable.width()) if available else None,
            printable_height_mm=float(printable.height()) if available else None,
            margins_mm=(
                float(margins.left()),
                float(margins.top()),
                float(margins.right()),
                float(margins.bottom()),
            ),
        )

    def begin(self) -> QPainter | None:
        painter = QPainter(self.printer)
        if not painter.isActive():
            return None
        self._painter = painter
        return painter

    def end(self) -> None:
        painter = self._painter
        self._painter = None
        if painter is not None and painter.isActive():
            painter.end()

    def abort(self) -> None:
        painter = self._painter
        self._painter = None
        if painter is not None and painter.isActive():
            painter.end()
        abort = getattr(self.printer, "abort", None)
        if callable(abort):
            abort()

    def state(self) -> str:
        try:
            state = self.printer.printerState()
        except Exception:  # noqa: BLE001 - 驱动异常时按未知处理
            return STATE_UNKNOWN
        return {
            QPrinter.PrinterState.Idle: STATE_IDLE,
            QPrinter.PrinterState.Active: STATE_ACTIVE,
            QPrinter.PrinterState.Aborted: STATE_ABORTED,
            QPrinter.PrinterState.Error: STATE_ERROR,
        }.get(state, STATE_UNKNOWN)

    def acceptance(self) -> bool | None:
        # Qt 不提供可确认的“驱动已接受任务”信号。
        return None
