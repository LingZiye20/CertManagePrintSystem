"""元素属性面板。

只负责收集与展示元素属性，并生成候选元素字典；真正的合法性判断由设计器
调用阶段 8 的 ``template_from_dict()`` 完成，本面板不会静默修正非法输入。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from app.services.template_model import (
    BINDABLE_FIELDS,
    TemplateElement,
    TextElement,
)
from app.ui.student_dialog import FIELD_LABELS

ALIGN_LABELS: tuple[tuple[str, str], ...] = (
    ("左对齐", "left"),
    ("居中", "center"),
    ("右对齐", "right"),
)
VERTICAL_ALIGN_LABELS: tuple[tuple[str, str], ...] = (
    ("顶部", "top"),
    ("垂直居中", "middle"),
    ("底部", "bottom"),
)
WRAP_LABELS: tuple[tuple[str, str], ...] = (
    ("单行（超出裁切）", "none"),
    ("自动换行", "word"),
)
FIT_LABELS: tuple[tuple[str, str], ...] = (
    ("填满元素并裁切", "cover"),
    ("完整显示（留白）", "contain"),
)

MAX_MM = 2000.0
MAX_FONT_SIZE_PT = 200.0


class TemplatePropertiesPanel(QWidget):
    """单个元素的属性编辑面板。"""

    apply_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._element_type: str | None = None
        self._read_only = False
        self._build_ui()
        self.set_element(None)

    # ------------------------------------------------------------------ 构建

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("元素属性", self)
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        header = QFormLayout()
        self.element_id_label = QLabel("-", self)
        header.addRow("元素 ID：", self.element_id_label)
        layout.addLayout(header)

        geometry = QGroupBox("位置与尺寸（毫米）", self)
        geometry_form = QFormLayout(geometry)
        self.x_spin = self._mm_spin()
        self.y_spin = self._mm_spin()
        self.width_spin = self._mm_spin()
        self.height_spin = self._mm_spin()
        geometry_form.addRow("X：", self.x_spin)
        geometry_form.addRow("Y：", self.y_spin)
        geometry_form.addRow("宽度：", self.width_spin)
        geometry_form.addRow("高度：", self.height_spin)
        layout.addWidget(geometry)

        self.text_group = QGroupBox("文本属性", self)
        text_form = QFormLayout(self.text_group)
        self.field_radio = QRadioButton("业务字段", self.text_group)
        self.text_radio = QRadioButton("静态文字", self.text_group)
        self.source_group = QButtonGroup(self)
        self.source_group.addButton(self.field_radio)
        self.source_group.addButton(self.text_radio)
        source_row = QHBoxLayout()
        source_row.addWidget(self.field_radio)
        source_row.addWidget(self.text_radio)
        text_form.addRow("内容来源：", source_row)

        self.field_combo = QComboBox(self.text_group)
        for field in BINDABLE_FIELDS:
            self.field_combo.addItem(FIELD_LABELS.get(field, field), field)
        text_form.addRow("业务字段：", self.field_combo)

        self.text_input = QLineEdit(self.text_group)
        self.text_input.setPlaceholderText("静态文字，如：姓　　名")
        text_form.addRow("静态文字：", self.text_input)

        self.font_combo = QFontComboBox(self.text_group)
        text_form.addRow("字体：", self.font_combo)

        self.size_spin = QDoubleSpinBox(self.text_group)
        self.size_spin.setRange(0.0, MAX_FONT_SIZE_PT)
        self.size_spin.setDecimals(1)
        self.size_spin.setSingleStep(1.0)
        self.size_spin.setSuffix(" pt")
        text_form.addRow("字号：", self.size_spin)

        style_row = QHBoxLayout()
        self.bold_check = QCheckBox("粗体", self.text_group)
        self.italic_check = QCheckBox("斜体", self.text_group)
        style_row.addWidget(self.bold_check)
        style_row.addWidget(self.italic_check)
        style_row.addStretch(1)
        text_form.addRow("样式：", style_row)

        self.align_combo = self._choice_combo(self.text_group, ALIGN_LABELS)
        text_form.addRow("水平对齐：", self.align_combo)
        self.vertical_align_combo = self._choice_combo(
            self.text_group, VERTICAL_ALIGN_LABELS
        )
        text_form.addRow("垂直对齐：", self.vertical_align_combo)
        self.wrap_combo = self._choice_combo(self.text_group, WRAP_LABELS)
        text_form.addRow("换行方式：", self.wrap_combo)
        layout.addWidget(self.text_group)

        self.photo_group = QGroupBox("照片属性", self)
        photo_form = QFormLayout(self.photo_group)
        self.fit_combo = self._choice_combo(self.photo_group, FIT_LABELS)
        photo_form.addRow("适配方式：", self.fit_combo)
        layout.addWidget(self.photo_group)

        self.apply_button = QPushButton("应用到画布", self)
        self.apply_button.clicked.connect(self.apply_requested.emit)
        layout.addWidget(self.apply_button)

        self.error_label = QLabel("", self)
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b00020;")
        layout.addWidget(self.error_label)
        layout.addStretch(1)

        self.field_radio.toggled.connect(self._sync_source_inputs)
        self.text_radio.toggled.connect(self._sync_source_inputs)

    def _mm_spin(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(self)
        spin.setRange(0.0, MAX_MM)
        spin.setDecimals(1)
        spin.setSingleStep(1.0)
        spin.setSuffix(" mm")
        return spin

    @staticmethod
    def _choice_combo(
        parent: QWidget, labels: tuple[tuple[str, str], ...]
    ) -> QComboBox:
        combo = QComboBox(parent)
        for text, value in labels:
            combo.addItem(text, value)
        return combo

    # ------------------------------------------------------------------ 载入

    def set_element(self, element: TemplateElement | None) -> None:
        """载入元素并刷新控件；``None`` 表示没有选中元素。"""
        self.clear_error()
        self._element_type = None

        if element is None:
            self.element_id_label.setText("-")
            self.text_group.setVisible(False)
            self.photo_group.setVisible(False)
            self.apply_button.setEnabled(False)
            return

        self.element_id_label.setText(element.id)
        self._set_geometry(element.x_mm, element.y_mm, element.width_mm, element.height_mm)

        if isinstance(element, TextElement):
            self._element_type = "text"
            self.text_group.setVisible(True)
            self.photo_group.setVisible(False)
            self.text_group.setEnabled(True)
            if element.field is not None:
                self.field_radio.setChecked(True)
                index = self.field_combo.findData(element.field)
                if index >= 0:
                    self.field_combo.setCurrentIndex(index)
            else:
                self.text_radio.setChecked(True)
            self.text_input.setText(element.text or "")
            self.font_combo.setEditText(element.font.family)
            self.size_spin.setValue(element.font.size_pt)
            self.bold_check.setChecked(element.font.bold)
            self.italic_check.setChecked(element.font.italic)
            self._select(self.align_combo, element.align)
            self._select(self.vertical_align_combo, element.vertical_align)
            self._select(self.wrap_combo, element.wrap)
        else:
            self._element_type = "photo"
            self.text_group.setVisible(False)
            self.photo_group.setVisible(True)
            self.photo_group.setEnabled(True)
            self._select(self.fit_combo, element.fit)

        self._sync_source_inputs()
        self._sync_enabled()

    def _set_geometry(
        self, x_mm: float, y_mm: float, width_mm: float, height_mm: float
    ) -> None:
        self.x_spin.setValue(x_mm)
        self.y_spin.setValue(y_mm)
        self.width_spin.setValue(width_mm)
        self.height_spin.setValue(height_mm)

    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    # ------------------------------------------------------------------ 输出

    def field_keys(self) -> list[str]:
        """返回字段下拉中的字段键，与 37 个业务字段一致。"""
        return [str(self.field_combo.itemData(index)) for index in range(self.field_combo.count())]

    def build_element_dict(self) -> dict[str, Any]:
        """按当前控件值生成候选元素字典，交由设计器整体校验。"""
        payload: dict[str, Any] = {
            "id": self.element_id_label.text(),
            "x_mm": float(self.x_spin.value()),
            "y_mm": float(self.y_spin.value()),
            "width_mm": float(self.width_spin.value()),
            "height_mm": float(self.height_spin.value()),
        }
        if self._element_type == "text":
            payload["type"] = "text"
            if self.field_radio.isChecked():
                payload["field"] = self.field_combo.currentData()
            else:
                payload["text"] = self.text_input.text()
            payload["font"] = {
                "family": self.font_combo.currentText().strip(),
                "size_pt": float(self.size_spin.value()),
                "bold": self.bold_check.isChecked(),
                "italic": self.italic_check.isChecked(),
            }
            payload["align"] = self.align_combo.currentData()
            payload["vertical_align"] = self.vertical_align_combo.currentData()
            payload["wrap"] = self.wrap_combo.currentData()
        else:
            payload["type"] = "photo"
            payload["fit"] = self.fit_combo.currentData()
        return payload

    # ------------------------------------------------------------------ 状态

    def show_error(self, message: str) -> None:
        self.error_label.setText(message)

    def clear_error(self) -> None:
        self.error_label.clear()

    def error_text(self) -> str:
        return self.error_label.text()

    def set_read_only(self, read_only: bool) -> None:
        """内置模板只读：禁用全部可编辑控件。"""
        self._read_only = bool(read_only)
        self._sync_enabled()

    def _sync_enabled(self) -> None:
        editable = not self._read_only
        for widget in (
            self.x_spin,
            self.y_spin,
            self.width_spin,
            self.height_spin,
            self.field_radio,
            self.text_radio,
            self.field_combo,
            self.text_input,
            self.font_combo,
            self.size_spin,
            self.bold_check,
            self.italic_check,
            self.align_combo,
            self.vertical_align_combo,
            self.wrap_combo,
            self.fit_combo,
            self.apply_button,
        ):
            widget.setEnabled(editable and self._element_type is not None)

        if editable:
            self._sync_source_inputs()

    def _sync_source_inputs(self) -> None:
        if self._element_type != "text" or self._read_only:
            return
        use_field = self.field_radio.isChecked()
        self.field_combo.setEnabled(use_field)
        self.text_input.setEnabled(not use_field)
