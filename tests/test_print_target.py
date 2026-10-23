"""QtPrintTarget 测试：页面几何、原点与接受状态语义（不连接真实打印机）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QMarginsF, QSizeF
from PySide6.QtGui import QColor, QPageLayout, QPageSize
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication

from app.services.print_target import STATE_ABORTED, STATE_ERROR, STATE_IDLE, QtPrintTarget


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _pdf_target(tmp_path: Path) -> tuple[QPrinter, QtPrintTarget]:
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(tmp_path / "print.pdf"))
    return printer, QtPrintTarget(printer)


def _set_layout(printer: QPrinter, width_mm: float, height_mm: float) -> None:
    size = QPageSize(QSizeF(width_mm, height_mm), QPageSize.Unit.Millimeter, "证书模板")
    layout = QPageLayout(
        size, QPageLayout.Orientation.Portrait, QMarginsF(0, 0, 0, 0)
    )
    printer.setPageLayout(layout)


def test_target_forces_full_page_origin(tmp_path: Path, qapp: QApplication) -> None:
    printer, _ = _pdf_target(tmp_path)

    QtPrintTarget(printer)

    assert printer.fullPage() is True


def test_page_geometry_reports_millimeters(tmp_path: Path, qapp: QApplication) -> None:
    printer, target = _pdf_target(tmp_path)
    _set_layout(printer, 297.0, 210.0)

    geometry = target.page_geometry()

    assert geometry.paper_width_mm == pytest.approx(297.0, abs=0.1)
    assert geometry.paper_height_mm == pytest.approx(210.0, abs=0.1)
    assert geometry.margins_mm[0] == pytest.approx(0.0, abs=0.1)


def test_acceptance_is_never_claimed(tmp_path: Path, qapp: QApplication) -> None:
    _, target = _pdf_target(tmp_path)

    # Qt 无法确认驱动是否接受任务，必须返回 None。
    assert target.acceptance() is None


def test_state_is_explicit_string(tmp_path: Path, qapp: QApplication) -> None:
    _, target = _pdf_target(tmp_path)

    assert target.state() in {STATE_IDLE, "active", STATE_ERROR, STATE_ABORTED, "unknown"}


def test_painter_session_produces_output(tmp_path: Path, qapp: QApplication) -> None:
    printer, target = _pdf_target(tmp_path)
    _set_layout(printer, 100.0, 50.0)

    painter = target.begin()
    assert painter is not None
    painter.fillRect(painter.device().width() // 4, painter.device().height() // 4, 10, 10, QColor("black"))
    target.end()

    output = tmp_path / "print.pdf"
    assert output.is_file()
    assert output.stat().st_size > 0


def test_end_without_begin_is_safe(tmp_path: Path, qapp: QApplication) -> None:
    _, target = _pdf_target(tmp_path)

    target.end()
    target.abort()
