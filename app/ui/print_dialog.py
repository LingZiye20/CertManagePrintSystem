"""证书打印对话框。

只负责收集打印选择、展示预检与结果，不直接访问数据库、不自行渲染：

- 模板与证书类型由 ``PrintService`` 提供；
- 打印设备通过 ``target_factory`` 创建，默认弹出系统 ``QPrintDialog``；
- 结果措辞严格区分「渲染完成」「结果未知」「失败」与「未开始」，
  不声称物理打印成功。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from PySide6.QtCore import QMarginsF, QSizeF, Qt
from PySide6.QtGui import QPageLayout, QPageSize
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services import CERTIFICATE_TYPES, PrintService
from app.services.errors import PrintError
from app.services.print_service import PrintBatchResult, PrintPreflight, PrintTemplateOption
from app.services.print_target import PrintTarget, QtPrintTarget
from app.services.template_model import CertificateTemplate

logger = logging.getLogger(__name__)

TYPE_LABELS: dict[str, str] = {
    type_info.key: type_info.label for type_info in CERTIFICATE_TYPES
}


class PrintDialog(QDialog):
    """打印前的模板/纸张确认与结果展示。"""

    def __init__(
        self,
        service: PrintService,
        records: Sequence[Mapping[str, Any]],
        parent: QWidget | None = None,
        *,
        target_factory: Callable[[CertificateTemplate], PrintTarget | None] | None = None,
        initial_type: str = "graduation",
    ) -> None:
        super().__init__(parent)
        self.service = service
        # 记录快照：后续筛选、排序或勾选变化不影响本次任务。
        self.records: list[dict[str, Any]] = [dict(record) for record in records]
        self.target_factory = target_factory or self._default_target_factory
        self._target: PrintTarget | None = None
        self._template: CertificateTemplate | None = None
        self._template_id: int | None = None
        self._preflight: PrintPreflight | None = None
        self.result: PrintBatchResult | None = None

        self.setWindowTitle("打印证书")
        self.resize(640, 520)
        self._build_ui()
        self._select_type(initial_type)
        self.refresh_templates()
        if not self.records:
            self.status_label.setText("没有可打印的记录")

    # ------------------------------------------------------------------ 构建

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self.record_count_label = QLabel(
            f"待打印 {len(self.records)} 条（按当前顺序逐张提交）", self
        )
        self.record_count_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.record_count_label)

        form = QFormLayout()
        self.type_combo = QComboBox(self)
        for key, label in TYPE_LABELS.items():
            self.type_combo.addItem(label, key)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("证书类型：", self.type_combo)

        self.template_combo = QComboBox(self)
        self.template_combo.currentIndexChanged.connect(self._on_template_changed)
        form.addRow("模板：", self.template_combo)
        layout.addLayout(form)

        printer_row = QHBoxLayout()
        self.printer_label = QLabel("打印机：未选择", self)
        printer_row.addWidget(self.printer_label, 1)
        self.settings_button = QPushButton("打印设置…", self)
        self.settings_button.clicked.connect(self.configure_printer)
        printer_row.addWidget(self.settings_button)
        layout.addLayout(printer_row)

        warnings_box = QGroupBox("预检与限制", self)
        warnings_layout = QVBoxLayout(warnings_box)
        self.warnings_label = QLabel("", warnings_box)
        self.warnings_label.setWordWrap(True)
        self.warnings_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        warnings_layout.addWidget(self.warnings_label)
        layout.addWidget(warnings_box)

        buttons = QHBoxLayout()
        self.print_button = QPushButton("开始打印", self)
        self.print_button.setEnabled(False)
        self.print_button.clicked.connect(self.start_printing)
        self.close_button = QPushButton("关闭", self)
        self.close_button.clicked.connect(self.reject)
        buttons.addStretch(1)
        buttons.addWidget(self.print_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #444444;")
        layout.addWidget(self.status_label)

        self.summary_label = QLabel("", self)
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.summary_label, 1)

    # ------------------------------------------------------------------ 模板

    def _select_type(self, certificate_type: str) -> None:
        index = self.type_combo.findData(certificate_type)
        if index >= 0:
            self.type_combo.setCurrentIndex(index)

    def current_type(self) -> str:
        return str(self.type_combo.currentData())

    def current_option(self) -> PrintTemplateOption | None:
        return self.template_combo.currentData()

    def refresh_templates(self) -> None:
        """按证书类型刷新模板列表；数据库为空时回退内置只读资源。"""
        self._reset_target()
        self.template_combo.clear()
        options = self.service.available_templates(self.current_type())
        for option in options:
            self.template_combo.addItem(self._option_label(option), option)

        if not options:
            self.status_label.setText(
                "当前证书类型没有可用模板，请先在模板设计器中创建并保存模板。"
            )
            self.print_button.setEnabled(False)
            return

        self.status_label.clear()
        self.print_button.setEnabled(False)

    @staticmethod
    def _option_label(option: PrintTemplateOption) -> str:
        if option.source == "builtin_resource":
            suffix = "（内置资源，未入库）"
        elif option.is_builtin:
            suffix = "（内置）"
        else:
            suffix = "（自定义）"
        return f"{option.name}{suffix}"

    def _on_type_changed(self) -> None:
        self.refresh_templates()

    def _on_template_changed(self) -> None:
        self._reset_target()

    def _reset_target(self) -> None:
        self._target = None
        self._template = None
        self._template_id = None
        self._preflight = None
        self.printer_label.setText("打印机：未选择")
        self.print_button.setEnabled(False)

    # ------------------------------------------------------------------ 设置

    def configure_printer(self) -> bool:
        """打开打印设置并执行预检；取消或存在阻塞项时返回 ``False``。"""
        option = self.current_option()
        if option is None:
            self.status_label.setText("请先选择打印模板")
            return False
        if not self.records:
            self.status_label.setText("没有可打印的记录")
            return False

        try:
            template = self.service.load_template(self.current_type(), option.id)
        except PrintError as exc:
            self.status_label.setText(str(exc))
            self.warnings_label.setText(str(exc))
            return False

        target = self.target_factory(template)
        if target is None:
            self._reset_target()
            self.status_label.setText("已取消打印设置，未提交任何打印任务")
            self.warnings_label.clear()
            return False

        self._target = target
        self._template = template
        self._template_id = option.id
        geometry = target.page_geometry()
        self.printer_label.setText(
            f"打印机：{target.name()}（{target.resolution()} dpi，纸张 "
            f"{geometry.paper_width_mm:g}×{geometry.paper_height_mm:g}mm）"
        )

        preflight = self.service.preflight(
            self.records, template, self.current_type(), target
        )
        self._preflight = preflight
        lines = [*preflight.blocking_errors, *preflight.warnings]
        self.warnings_label.setText("\n".join(lines))
        self.warnings_label.setStyleSheet(
            "color: #b00020;" if preflight.blocking_errors else "color: #8a6d00;"
        )
        self.print_button.setEnabled(preflight.can_print)
        self.status_label.setText(
            "打印设置完成" if preflight.can_print else "存在阻塞问题，无法开始打印"
        )
        return preflight.can_print

    # ------------------------------------------------------------------ 打印

    def start_printing(self) -> PrintBatchResult | None:
        if self._target is None or self._template is None:
            self.status_label.setText("请先完成打印设置")
            return None
        if self._preflight is None or not self._preflight.can_print:
            self.status_label.setText("存在阻塞问题，无法开始打印")
            return None

        self.print_button.setEnabled(False)
        self.status_label.setText("正在逐张提交打印任务…")
        try:
            result = self.service.print_batch(
                self.records,
                self._template,
                self.current_type(),
                self._target,
                template_id=self._template_id,
            )
        except PrintError as exc:
            self.status_label.setText(str(exc))
            self.print_button.setEnabled(True)
            return None
        except Exception:  # noqa: BLE001 - 未预期错误记录日志并提示
            logger.exception("打印任务发生未预期错误")
            self.status_label.setText("打印任务发生未预期错误，请查看日志后重试。")
            self.print_button.setEnabled(True)
            return None

        self.result = result
        lines = [result.message]
        if result.warnings:
            lines.extend(result.warnings)
        self.summary_label.setText("\n".join(lines))
        self.summary_label.setStyleSheet(
            "color: #b00020;"
            if result.failed_count or result.audit_failed_count
            else "color: #444444;"
        )
        self.status_label.setText("打印任务已结束（提交状态见上方说明）")
        return result

    # ------------------------------------------------------------------ 设备

    def _default_target_factory(
        self, template: CertificateTemplate
    ) -> PrintTarget | None:
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        size = QPageSize(
            QSizeF(template.canvas_width_mm, template.canvas_height_mm),
            QPageSize.Unit.Millimeter,
            "证书模板",
        )
        printer.setPageLayout(
            QPageLayout(
                size,
                QPageLayout.Orientation.Portrait,
                QMarginsF(0.0, 0.0, 0.0, 0.0),
            )
        )
        dialog = QPrintDialog(printer, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return QtPrintTarget(printer)
