"""证书编号规则配置对话框。

只负责收集规则字段并调用 Service 保存；下一流水号为只读展示，
本对话框不提供手动调整入口，也不会递增流水。
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services import (
    MAX_SEQUENCE_WIDTH,
    MIN_SEQUENCE_WIDTH,
    NumberingService,
    ServiceError,
    compose_certificate_number,
)

logger = logging.getLogger(__name__)


class NumberingRuleDialog(QDialog):
    """编辑三类证书编号规则中的一类。"""

    def __init__(
        self,
        service: NumberingService,
        parent: QWidget | None = None,
        *,
        initial_type: str = "graduation",
    ) -> None:
        super().__init__(parent)
        self.service = service

        self.setWindowTitle("编号规则设置")
        self.resize(520, 380)
        self._build_ui()
        self._select_type(initial_type)
        self.load_rule()

    # ------------------------------------------------------------------ 构建

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.type_combo = QComboBox(self)
        for type_info in self.service.available_types():
            self.type_combo.addItem(type_info.label, type_info.key)
        self.type_combo.currentIndexChanged.connect(self.load_rule)
        form.addRow("证书类型：", self.type_combo)

        self.rule_name_input = QLineEdit(self)
        form.addRow("规则名称：", self.rule_name_input)

        self.width_input = QLineEdit(self)
        self.width_input.setValidator(
            QIntValidator(MIN_SEQUENCE_WIDTH, MAX_SEQUENCE_WIDTH, self)
        )
        self.width_input.setPlaceholderText(
            f"{MIN_SEQUENCE_WIDTH}–{MAX_SEQUENCE_WIDTH}，默认 4"
        )
        form.addRow("序号位数：", self.width_input)

        self.enabled_check = QCheckBox("启用该类编号规则", self)
        self.enabled_check.setChecked(True)
        form.addRow("", self.enabled_check)
        layout.addLayout(form)

        fragments_box = QGroupBox("固定文本片段（按顺序直接拼接，不自动添加分隔符）", self)
        fragments_layout = QVBoxLayout(fragments_box)
        self.fixed_text_inputs: list[QLineEdit] = []
        for index in range(1, 5):
            row = QHBoxLayout()
            row.addWidget(QLabel(f"片段 {index}："))
            editor = QLineEdit(fragments_box)
            editor.setPlaceholderText("可留空；去除首尾空白后参与拼接")
            self.fixed_text_inputs.append(editor)
            row.addWidget(editor)
            fragments_layout.addLayout(row)
        layout.addWidget(fragments_box)

        self.next_value_label = QLabel("下一流水号：-", self)
        self.next_value_label.setStyleSheet("color: #444444;")
        layout.addWidget(self.next_value_label)

        self.preview_label = QLabel("预览编号：-", self)
        self.preview_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.preview_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.preview_label)

        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.preview_button = QPushButton("预览当前编号", self)
        self.preview_button.clicked.connect(lambda: self.preview())
        buttons.addWidget(self.preview_button)
        buttons.addStretch(1)
        self.save_button = QPushButton("保存规则", self)
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(self.save_button)
        self.close_button = QPushButton("关闭", self)
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    def _select_type(self, rule_key: str) -> None:
        index = self.type_combo.findData(rule_key)
        if index >= 0:
            self.type_combo.setCurrentIndex(index)

    # ------------------------------------------------------------------ 读写

    def current_type(self) -> str:
        return str(self.type_combo.currentData())

    def values(self) -> dict[str, Any]:
        """按 Service 可接受的字段名返回当前表单输入。"""
        width_text = self.width_input.text().strip()
        return {
            "rule_name": self.rule_name_input.text(),
            "fixed_text_1": self.fixed_text_inputs[0].text(),
            "fixed_text_2": self.fixed_text_inputs[1].text(),
            "fixed_text_3": self.fixed_text_inputs[2].text(),
            "fixed_text_4": self.fixed_text_inputs[3].text(),
            "sequence_width": int(width_text) if width_text else None,
            "enabled": self.enabled_check.isChecked(),
        }

    def load_rule(self) -> None:
        """按当前证书类型加载规则与只读流水值。"""
        self.status_label.clear()
        self.preview_label.setText("预览编号：-")
        try:
            view = self.service.get_rule(self.current_type())
        except ServiceError as exc:
            self._show_error(str(exc))
            return
        except Exception:  # noqa: BLE001 - 未预期错误记录日志并提示
            logger.exception("加载编号规则时发生未预期错误")
            self._show_error("加载编号规则失败，请查看日志后重试。")
            return

        self.rule_name_input.setText(view.rule_name)
        for editor, text in zip(self.fixed_text_inputs, view.fixed_texts):
            editor.setText(text)
        self.width_input.setText(str(view.sequence_width))
        self.enabled_check.setChecked(view.enabled)
        self.next_value_label.setText(
            f"下一流水号：{view.next_value}（只读；仅正式生成后自动递增）"
        )

    def preview(self) -> bool:
        """按当前表单值计算预览；不保存规则、不写数据库、不递增流水。"""
        self.status_label.clear()
        try:
            view = self.service.get_rule(self.current_type())
        except ServiceError as exc:
            self._show_error(str(exc), title="预览失败")
            return False
        except Exception:  # noqa: BLE001
            logger.exception("预览编号规则时发生未预期错误")
            self._show_error("预览失败，请查看日志后重试。", title="预览失败")
            return False

        width_text = self.width_input.text().strip()
        try:
            width: int | None = int(width_text)
        except ValueError:
            width = None

        try:
            number = compose_certificate_number(
                [editor.text() for editor in self.fixed_text_inputs],
                view.next_value,
                width,  # type: ignore[arg-type] - 非法值由拼接函数统一拒绝
            )
        except ValueError as exc:
            self.preview_label.setText("预览编号：无法计算")
            self.status_label.setText(str(exc))
            return False

        self.preview_label.setText(f"预览编号：{number}")
        self.status_label.setText(
            f"预览基于当前表单值与下一流水号 {view.next_value}；"
            "未保存、不会占用流水。"
        )
        return True

    def save(self) -> bool:
        """保存当前规则；成功返回 ``True``。"""
        try:
            self.service.update_rule(self.current_type(), self.values())
        except ServiceError as exc:
            self._show_error(str(exc), title="保存失败")
            return False
        except Exception:  # noqa: BLE001
            logger.exception("保存编号规则时发生未预期错误")
            self._show_error("保存编号规则失败，请查看日志后重试。", title="保存失败")
            return False

        self.load_rule()
        self.status_label.setText("规则已保存。下一流水号保持不变。")
        return True

    def _show_error(self, message: str, *, title: str = "编号规则") -> None:
        self.status_label.setText(message)
        QMessageBox.warning(self, title, message)
