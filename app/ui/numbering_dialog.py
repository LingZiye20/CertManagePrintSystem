"""证书编号预览与正式生成对话框。

预览只读，不占用流水；正式生成由 Service 在单个事务内完成写入与递增。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services import (
    GeneratedNumber,
    NumberingService,
    NumberPreview,
    ServiceError,
)

logger = logging.getLogger(__name__)


class NumberingDialog(QDialog):
    """为一条学生记录预览并生成指定类型的证书编号。"""

    def __init__(
        self,
        service: NumberingService,
        record: Mapping[str, Any],
        parent: QWidget | None = None,
        *,
        initial_type: str = "graduation",
        on_generated: Callable[[GeneratedNumber], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.record = dict(record)
        self._on_generated = on_generated

        self.setWindowTitle("生成证书编号")
        self.resize(520, 420)
        self._build_ui()
        index = self.type_combo.findData(initial_type)
        if index >= 0:
            self.type_combo.setCurrentIndex(index)
        self.refresh()

    # ------------------------------------------------------------------ 构建

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        info_box = QGroupBox("学生记录", self)
        info_form = QFormLayout(info_box)
        self.student_no_label = QLabel(str(self.record.get("student_no") or "（空）"))
        self.student_name_label = QLabel(str(self.record.get("name") or "（空）"))
        info_form.addRow("学号：", self.student_no_label)
        info_form.addRow("姓名：", self.student_name_label)
        layout.addWidget(info_box)

        rule_box = QGroupBox("编号规则", self)
        rule_form = QFormLayout(rule_box)
        self.type_combo = QComboBox(rule_box)
        for type_info in self.service.available_types():
            self.type_combo.addItem(type_info.label, type_info.key)
        self.type_combo.currentIndexChanged.connect(self.refresh)
        rule_form.addRow("证书类型：", self.type_combo)

        self.current_number_label = QLabel("（尚未生成）", rule_box)
        rule_form.addRow("当前编号：", self.current_number_label)
        self.next_sequence_label = QLabel("-", rule_box)
        rule_form.addRow("下一流水号：", self.next_sequence_label)
        layout.addWidget(rule_box)

        preview_box = QGroupBox("编号预览", self)
        preview_layout = QVBoxLayout(preview_box)
        self.preview_label = QLabel("-", preview_box)
        self.preview_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.preview_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        preview_layout.addWidget(self.preview_label)
        hint = QLabel("预览不会写入数据库、不会递增流水号，也不占用编号。", preview_box)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666666;")
        preview_layout.addWidget(hint)
        layout.addWidget(preview_box)

        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.preview_button = QPushButton("预览编号", self)
        self.preview_button.clicked.connect(lambda: self.preview())
        buttons.addWidget(self.preview_button)
        self.generate_button = QPushButton("正式生成", self)
        self.generate_button.clicked.connect(lambda: self.generate())
        buttons.addWidget(self.generate_button)
        buttons.addStretch(1)
        self.close_button = QPushButton("关闭", self)
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------ 状态

    def current_type(self) -> str:
        return str(self.type_combo.currentData())

    def refresh(self) -> None:
        """按当前证书类型刷新只读信息并自动预览。"""
        self.status_label.clear()
        try:
            view = self.service.get_rule(self.current_type())
        except ServiceError as exc:
            self._show_error(str(exc))
            self.generate_button.setEnabled(False)
            return
        except Exception:  # noqa: BLE001
            logger.exception("读取编号规则时发生未预期错误")
            self._show_error("读取编号规则失败，请查看日志后重试。")
            self.generate_button.setEnabled(False)
            return

        type_info = next(
            item for item in self.service.available_types() if item.key == view.rule_key
        )
        current = self.record.get(type_info.number_field)
        self.current_number_label.setText(current or "（尚未生成）")
        self.next_sequence_label.setText(
            f"{view.next_value}（只读；正式生成后自动递增）"
        )
        if current:
            self.generate_button.setEnabled(False)
            self.preview_label.setText(current)
            self.status_label.setText(f"该记录已有{type_info.label}编号，不能重复生成。")
            return

        self.generate_button.setEnabled(view.enabled)
        self.preview(show_dialog=False)

    # ------------------------------------------------------------------ 操作

    def preview(self, *, show_dialog: bool = True) -> bool:
        """刷新预览；成功返回 ``True``。"""
        try:
            result = self.service.preview(self.current_type())
        except ServiceError as exc:
            self.preview_label.setText("无法预览")
            self._show_error(str(exc), title="预览失败", show_dialog=show_dialog)
            return False
        except Exception:  # noqa: BLE001
            logger.exception("预览证书编号时发生未预期错误")
            self.preview_label.setText("无法预览")
            self._show_error(
                "预览证书编号失败，请查看日志后重试。",
                title="预览失败",
                show_dialog=show_dialog,
            )
            return False

        self.preview_label.setText(result.number)
        self.status_label.setText(
            f"预览基于第 {result.sequence_value} 个流水号计算，尚未占用。"
        )
        return True

    def generate(self) -> bool:
        """确认后正式生成编号并刷新界面；成功返回 ``True``。"""
        try:
            preview = self.service.preview(self.current_type())
        except ServiceError as exc:
            self.refresh()
            self._show_error(str(exc), title="生成失败")
            return False
        except Exception:  # noqa: BLE001
            logger.exception("正式生成前预览编号时发生未预期错误")
            self._show_error("生成证书编号失败，请查看日志后重试。", title="生成失败")
            return False

        if not self._confirm_generation(preview):
            self.status_label.setText(
                "已取消生成；未写入编号、未递增流水、未写入成功审计。"
            )
            return False

        try:
            result = self.service.generate_number(
                self.record["id"], self.current_type()
            )
        except ServiceError as exc:
            self.refresh()
            self._show_error(str(exc), title="生成失败")
            return False
        except Exception:  # noqa: BLE001
            logger.exception("正式生成证书编号时发生未预期错误")
            self.refresh()
            self._show_error("生成证书编号失败，请查看日志后重试。", title="生成失败")
            return False

        type_info = next(
            item for item in self.service.available_types() if item.key == result.rule_key
        )
        self.record[type_info.number_field] = result.number
        self.refresh()
        self.status_label.setText(
            f"已正式生成{result.label}编号 {result.number}；下一流水号为 {result.next_value}。"
        )
        if self._on_generated is not None:
            self._on_generated(result)
        return True

    def _confirm_generation(self, preview: NumberPreview) -> bool:
        """显示正式生成确认框；用户取消时返回 ``False``。"""
        student_no = self.record.get("student_no") or "（空）"
        message = (
            f"证书类型：{preview.label}\n"
            f"学生学号：{student_no}\n"
            f"当前预览编号：{preview.number}\n\n"
            "正式生成将写入该编号并递增流水号，操作不可撤销。是否继续？"
        )
        reply = QMessageBox.question(
            self,
            "确认生成编号",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _show_error(
        self, message: str, *, title: str = "编号生成", show_dialog: bool = True
    ) -> None:
        self.status_label.setText(message)
        if show_dialog:
            QMessageBox.warning(self, title, message)
