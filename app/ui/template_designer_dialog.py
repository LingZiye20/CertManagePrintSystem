"""证书模板设计器对话框。

职责边界：

- 模板列表、基本属性、画布与属性面板的编排；
- 所有模板数据读写都经 ``TemplateService``，不直接访问数据库；
- 每次属性修改都通过阶段 8 的 ``template_from_dict()`` 整体校验，非法值不写入模型；
- 内置模板只读：界面控件禁用，Service 层同时拒绝修改；
- 预览只调用 ``CertificateRenderer``，不实现第二套渲染逻辑。
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.services import CERTIFICATE_TYPES, TemplateService
from app.services.errors import ServiceError, TemplateValidationError
from app.services.template_model import (
    CertificateTemplate,
    FontSpec,
    PhotoElement,
    TextElement,
    template_from_dict,
)
from app.services.template_renderer import CertificateRenderer
from app.ui.template_canvas import BASE_DPI, TemplateCanvas
from app.ui.template_preview_dialog import SAMPLE_VALUES, TemplatePreviewDialog
from app.ui.template_properties_panel import TemplatePropertiesPanel

logger = logging.getLogger(__name__)

DEFAULT_CANVAS_WIDTH_MM = 297.0
DEFAULT_CANVAS_HEIGHT_MM = 210.0
DEFAULT_TEMPLATE_NAME = "新建模板"
COPY_SUFFIX = " 副本"
SIDEBAR_WIDTH = 280
PANEL_WIDTH = 320
MAX_MM = 2000.0

TYPE_LABELS: dict[str, str] = {
    type_info.key: type_info.label for type_info in CERTIFICATE_TYPES
}


class TemplateDesignerDialog(QDialog):
    """模板列表 + 设计画布 + 属性面板的轻量设计器。"""

    def __init__(
        self,
        service: TemplateService,
        parent: QWidget | None = None,
        *,
        renderer: CertificateRenderer | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.renderer = renderer if renderer is not None else CertificateRenderer()
        self._template: CertificateTemplate | None = None
        self._template_id: int | None = None
        self._read_only = False
        self._dirty = False
        self._syncing = False

        self.setWindowTitle("模板设计器")
        self.resize(1280, 820)
        self._build_ui()
        self._sync_buttons()
        self.reload_templates()
        self.status_label.setText("请选择或新建模板")

    # ------------------------------------------------------------------ 构建

    def _build_ui(self) -> None:
        main = QVBoxLayout(self)
        main.addWidget(self._build_template_fields())

        body = QHBoxLayout()
        body.addWidget(self._build_sidebar())
        body.addWidget(self._build_canvas_area(), 1)
        body.addWidget(self._build_properties_area())
        main.addLayout(body, 1)

        bottom = QHBoxLayout()
        self.add_text_button = QPushButton("新增文本元素", self)
        self.add_text_button.clicked.connect(self.add_text_element)
        self.add_photo_button = QPushButton("新增照片元素", self)
        self.add_photo_button.clicked.connect(self.add_photo_element)
        self.delete_element_button = QPushButton("删除选中元素", self)
        self.delete_element_button.clicked.connect(self.delete_selected_element)
        bottom.addWidget(self.add_text_button)
        bottom.addWidget(self.add_photo_button)
        bottom.addWidget(self.delete_element_button)
        bottom.addStretch(1)

        self.save_button = QPushButton("保存", self)
        self.save_button.clicked.connect(self.save_current)
        self.preview_button = QPushButton("预览", self)
        self.preview_button.clicked.connect(self.preview_current)
        self.close_button = QPushButton("关闭", self)
        self.close_button.clicked.connect(self.close)
        bottom.addWidget(self.save_button)
        bottom.addWidget(self.preview_button)
        bottom.addWidget(self.close_button)
        main.addLayout(bottom)

        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #444444;")
        main.addWidget(self.status_label)

    def _build_template_fields(self) -> QWidget:
        box = QGroupBox("模板属性", self)
        row = QHBoxLayout(box)

        row.addWidget(QLabel("名称："))
        self.name_input = QLineEdit(box)
        self.name_input.setMaxLength(100)
        self.name_input.editingFinished.connect(self._on_template_fields_edited)
        row.addWidget(self.name_input, 2)

        row.addWidget(QLabel("证书类型："))
        self.type_combo = QComboBox(box)
        for key, label in TYPE_LABELS.items():
            self.type_combo.addItem(label, key)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        row.addWidget(self.type_combo)

        row.addWidget(QLabel("画布宽："))
        self.width_spin = self._mm_spin(box)
        self.width_spin.editingFinished.connect(self._on_template_fields_edited)
        row.addWidget(self.width_spin)

        row.addWidget(QLabel("高："))
        self.height_spin = self._mm_spin(box)
        self.height_spin.editingFinished.connect(self._on_template_fields_edited)
        row.addWidget(self.height_spin)

        row.addWidget(QLabel("mm"))
        return box

    @staticmethod
    def _mm_spin(parent: QWidget) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(parent)
        spin.setRange(0.0, MAX_MM)
        spin.setDecimals(1)
        spin.setSingleStep(1.0)
        spin.setSuffix(" mm")
        return spin

    def _build_sidebar(self) -> QWidget:
        container = QWidget(self)
        container.setFixedWidth(SIDEBAR_WIDTH)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("模板列表", container))
        self.template_list = QListWidget(container)
        # 避免窗口显示时列表获得焦点后自动选中首项并隐式打开模板；
        # 鼠标点击仍可正常选择，打开动作始终由用户触发。
        self.template_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.template_list.setWordWrap(True)
        self.template_list.currentItemChanged.connect(self._on_list_selection)
        layout.addWidget(self.template_list, 1)

        buttons = QHBoxLayout()
        self.new_button = QPushButton("新建模板", container)
        self.new_button.clicked.connect(self.new_template)
        self.copy_button = QPushButton("复制模板", container)
        self.copy_button.clicked.connect(self.copy_current)
        self.delete_button = QPushButton("删除模板", container)
        self.delete_button.clicked.connect(self.delete_current)
        buttons.addWidget(self.new_button)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.delete_button)
        layout.addLayout(buttons)
        return container

    def _build_canvas_area(self) -> QWidget:
        self.canvas = TemplateCanvas(self, renderer=self.renderer)
        self.canvas.selection_changed.connect(self._on_selection_changed)
        self.canvas.element_changed.connect(self._on_element_changed)

        self.canvas_scroll = QScrollArea(self)
        self.canvas_scroll.setWidgetResizable(False)
        self.canvas_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas_scroll.setWidget(self.canvas)
        self.canvas_scroll.setStyleSheet("background-color: #787878;")
        return self.canvas_scroll

    def _build_properties_area(self) -> QWidget:
        self.panel = TemplatePropertiesPanel(self)
        self.panel.apply_requested.connect(self.apply_element_properties)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(PANEL_WIDTH)
        container = QWidget(scroll)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.panel)
        scroll.setWidget(container)
        return scroll

    # ------------------------------------------------------------------ 状态

    def current_template(self) -> CertificateTemplate | None:
        return self._template

    def current_template_id(self) -> int | None:
        return self._template_id

    def is_read_only(self) -> bool:
        return self._read_only

    def is_dirty(self) -> bool:
        return self._dirty

    def _editable(self) -> bool:
        return self._template is not None and not self._read_only

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = bool(dirty)

    def _mark_dirty(self) -> None:
        if not self._read_only:
            self._dirty = True
            self.status_label.setText("有未保存的修改")

    def _sync_buttons(self) -> None:
        editable = self._editable()
        has_template = self._template is not None
        self.name_input.setEnabled(editable)
        self.type_combo.setEnabled(editable)
        self.width_spin.setEnabled(editable)
        self.height_spin.setEnabled(editable)
        self.add_text_button.setEnabled(editable)
        self.add_photo_button.setEnabled(editable)
        self.delete_element_button.setEnabled(bool(editable and self.canvas.selected_element()))
        self.save_button.setEnabled(editable)
        self.delete_button.setEnabled(editable and self._template_id is not None)
        self.copy_button.setEnabled(has_template)
        self.preview_button.setEnabled(has_template)
        self.panel.set_read_only(not editable)

    # ------------------------------------------------------------------ 列表

    def reload_templates(self) -> None:
        """重新读取模板列表，并尽量保持当前选中项。"""
        keep_id = self._template_id
        try:
            summaries = self.service.list_templates()
        except ServiceError as exc:
            self.status_label.setText(str(exc))
            summaries = []

        blocker = QSignalBlocker(self.template_list)
        try:
            self.template_list.clear()
            selected_row = -1
            for index, summary in enumerate(summaries):
                label = TYPE_LABELS.get(summary.certificate_type, summary.certificate_type)
                kind = "内置" if summary.is_builtin else "自定义"
                text = (
                    f"{summary.name}\n{label}｜"
                    f"{summary.canvas_width_mm:g}×{summary.canvas_height_mm:g}mm｜{kind}"
                )
                item = QListWidgetItem(text)
                item.setToolTip(text.replace("\n", " "))
                item.setData(Qt.ItemDataRole.UserRole, summary.id)
                self.template_list.addItem(item)
                if summary.id == keep_id:
                    selected_row = index
            if selected_row >= 0:
                self.template_list.setCurrentRow(selected_row)
            else:
                # 显式清除：QListWidget 在插入首项时可能自行设置当前项。
                self.template_list.setCurrentRow(-1)
                self.template_list.clearSelection()
        finally:
            del blocker

    def _on_list_selection(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        if current is None or self._syncing:
            return
        template_id = current.data(Qt.ItemDataRole.UserRole)
        if template_id == self._template_id:
            return
        if not self.select_template(int(template_id)):
            # 放弃切换时恢复列表选中项。
            self._select_list_row(self._template_id)

    def _select_list_row(self, template_id: int | None) -> None:
        blocker = QSignalBlocker(self.template_list)
        try:
            if template_id is None:
                self.template_list.clearSelection()
                return
            for row in range(self.template_list.count()):
                item = self.template_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == template_id:
                    self.template_list.setCurrentRow(row)
                    return
            self.template_list.clearSelection()
        finally:
            del blocker

    # ------------------------------------------------------------------ 打开

    def select_template(self, template_id: int) -> bool:
        """打开指定模板；有未保存修改时先确认。"""
        if not self._confirm_discard():
            return False
        try:
            template = self.service.load_template(template_id)
            row = self.service.templates.get(template_id)
        except ServiceError as exc:
            self.status_label.setText(str(exc))
            return False
        except TemplateValidationError as exc:
            self.status_label.setText(str(exc))
            return False

        self._apply_template(
            template,
            template_id=template_id,
            read_only=bool(row["is_builtin"]) if row is not None else False,
            dirty=False,
        )
        self._select_list_row(template_id)
        self.status_label.setText(
            "内置模板为只读，可预览或复制" if self._read_only else "模板已打开"
        )
        return True

    def new_template(self) -> None:
        if not self._confirm_discard():
            return
        template = CertificateTemplate(
            name=DEFAULT_TEMPLATE_NAME,
            certificate_type="graduation",
            canvas_width_mm=DEFAULT_CANVAS_WIDTH_MM,
            canvas_height_mm=DEFAULT_CANVAS_HEIGHT_MM,
            elements=(),
        )
        self._apply_template(template, template_id=None, read_only=False, dirty=True)
        self._select_list_row(None)
        self.status_label.setText("新建模板（未保存）")

    def copy_current(self) -> None:
        if self._template is None:
            self.status_label.setText("请先选择要复制的模板")
            return
        if not self._confirm_discard():
            return
        draft = replace(self._template, name=f"{self._template.name}{COPY_SUFFIX}")
        self._apply_template(draft, template_id=None, read_only=False, dirty=True)
        self._select_list_row(None)
        self.status_label.setText("已复制为未保存草稿，点击保存后写入数据库")

    def _apply_template(
        self,
        template: CertificateTemplate | None,
        *,
        template_id: int | None,
        read_only: bool,
        dirty: bool,
    ) -> None:
        self._syncing = True
        try:
            self._template = template
            self._template_id = template_id
            self._read_only = read_only

            if template is None:
                self.canvas.set_template(None)
                self.canvas.set_interactive(False)
                self.name_input.clear()
                self.width_spin.setValue(0.0)
                self.height_spin.setValue(0.0)
            else:
                self.canvas.set_template(template)
                self.canvas.set_preview_context(SAMPLE_VALUES)
                self.canvas.set_interactive(not read_only)
                self.name_input.setText(template.name)
                index = self.type_combo.findData(template.certificate_type)
                if index >= 0:
                    self.type_combo.setCurrentIndex(index)
                self.width_spin.setValue(template.canvas_width_mm)
                self.height_spin.setValue(template.canvas_height_mm)
        finally:
            self._syncing = False

        self.panel.set_element(None)
        self._set_dirty(dirty)
        self._sync_buttons()
        self._fit_canvas()

    # ------------------------------------------------------------------ 属性

    def _on_template_fields_edited(self) -> None:
        if self._syncing or not self._editable():
            return
        self._mark_dirty()

    def _on_type_changed(self, index: int) -> None:
        if self._syncing or self._template is None or self._read_only:
            return
        new_type = self.type_combo.currentData()
        if new_type == self._template.certificate_type:
            return

        answer = QMessageBox.question(
            self,
            "切换证书类型",
            "切换证书类型可能导致当前字段绑定不再适用，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            blocker = QSignalBlocker(self.type_combo)
            try:
                previous = self.type_combo.findData(self._template.certificate_type)
                if previous >= 0:
                    self.type_combo.setCurrentIndex(previous)
            finally:
                del blocker
            return
        self._mark_dirty()

    def apply_template_properties(self) -> bool:
        """校验并应用模板名称、类型与画布尺寸。"""
        if self._template is None or self._read_only:
            return False

        payload = self._template.to_dict()
        payload["name"] = self.name_input.text()
        payload["certificate_type"] = self.type_combo.currentData()
        payload["canvas_width_mm"] = float(self.width_spin.value())
        payload["canvas_height_mm"] = float(self.height_spin.value())
        try:
            updated = template_from_dict(payload)
        except TemplateValidationError as exc:
            self.status_label.setText(str(exc))
            return False

        changed = updated != self._template
        self._template = updated
        self.canvas.set_template(updated)
        if changed:
            self._mark_dirty()
        self._fit_canvas()
        return True

    def apply_element_properties(self) -> bool:
        """把属性面板内容应用到选中元素，非法值只显示错误。"""
        element = self.canvas.selected_element()
        if element is None:
            self.status_label.setText("请先在画布上选中元素")
            return False
        if self._template is None or self._read_only:
            return False

        candidate = self.panel.build_element_dict()
        payload = self._template.to_dict()
        payload["elements"] = [
            candidate if item.get("id") == element.id else item
            for item in payload["elements"]
        ]
        try:
            updated = template_from_dict(payload)
        except TemplateValidationError as exc:
            self.panel.show_error(str(exc))
            self.status_label.setText(str(exc))
            return False

        self._template = updated
        self.canvas.set_template(updated)
        self.canvas.select_element(element.id)
        self.panel.set_element(self.canvas.selected_element())
        self.panel.clear_error()
        self._mark_dirty()
        self.status_label.setText("属性已应用（未保存）")
        return True

    # ------------------------------------------------------------------ 元素

    def _next_element_id(self, prefix: str) -> str:
        assert self._template is not None
        used = {element.id for element in self._template.elements}
        if prefix not in used:
            return prefix
        index = 2
        while f"{prefix}_{index}" in used:
            index += 1
        return f"{prefix}_{index}"

    def add_text_element(self) -> str | None:
        if not self._editable():
            return None
        assert self._template is not None
        element = TextElement(
            id=self._next_element_id("text"),
            x_mm=20.0,
            y_mm=20.0,
            width_mm=min(60.0, self._template.canvas_width_mm - 2.0),
            height_mm=min(12.0, self._template.canvas_height_mm - 2.0),
            font=FontSpec(family="Microsoft YaHei", size_pt=12.0),
            field="name",
        )
        if not self._append_element(element):
            return None
        return element.id

    def add_photo_element(self) -> str | None:
        if not self._editable():
            return None
        assert self._template is not None
        width = min(35.0, self._template.canvas_width_mm - 2.0)
        height = min(45.0, self._template.canvas_height_mm - 2.0)
        element = PhotoElement(
            id=self._next_element_id("photo"),
            x_mm=max(0.0, self._template.canvas_width_mm - width - 10.0),
            y_mm=10.0,
            width_mm=width,
            height_mm=height,
        )
        if not self._append_element(element):
            return None
        return element.id

    def _append_element(self, element: TextElement | PhotoElement) -> bool:
        assert self._template is not None
        payload = self._template.to_dict()
        payload["elements"] = [*payload["elements"], element.to_dict()]
        try:
            updated = template_from_dict(payload)
        except TemplateValidationError as exc:
            self.status_label.setText(str(exc))
            return False

        self._template = updated
        self.canvas.set_template(updated)
        self.canvas.select_element(element.id)
        self.panel.set_element(self.canvas.selected_element())
        self._mark_dirty()
        self._sync_buttons()
        self.status_label.setText("元素已新增（未保存）")
        return True

    def delete_selected_element(self) -> bool:
        element_id = self.canvas.selected_element_id()
        if element_id is None or not self._editable():
            return False
        assert self._template is not None

        payload = self._template.to_dict()
        payload["elements"] = [
            item for item in payload["elements"] if item.get("id") != element_id
        ]
        try:
            updated = template_from_dict(payload)
        except TemplateValidationError as exc:
            self.status_label.setText(str(exc))
            return False

        self._template = updated
        self.canvas.set_template(updated)
        self.panel.set_element(None)
        self._mark_dirty()
        self._sync_buttons()
        self.status_label.setText("元素已删除（未保存）")
        return True

    # ------------------------------------------------------------------ 保存

    def save_current(self) -> bool:
        """校验并保存当前模板；失败时在状态栏显示原因。"""
        if self._template is None:
            self.status_label.setText("请先选择或新建模板")
            return False
        if self._read_only:
            self.status_label.setText("内置模板为只读，不能保存修改")
            return False
        if not self.apply_template_properties():
            return False

        try:
            if self._template_id is None:
                template_id = self.service.create_template(self._template)
            else:
                self.service.update_template(self._template_id, self._template)
                template_id = self._template_id
        except ServiceError as exc:
            self.status_label.setText(str(exc))
            return False
        except Exception:  # noqa: BLE001 - 未预期错误记录日志并提示
            logger.exception("保存模板时发生未预期错误")
            self.status_label.setText("保存模板失败，请查看日志后重试。")
            return False

        self._template_id = template_id
        self._set_dirty(False)
        self.reload_templates()
        self._select_list_row(template_id)
        self._sync_buttons()
        self.status_label.setText("模板已保存")
        return True

    def delete_current(self) -> bool:
        """删除当前自定义模板；内置模板只读，不允许删除。"""
        if self._template_id is None or self._template is None:
            self.status_label.setText("当前模板尚未保存，无需删除")
            return False
        if self._read_only:
            self.status_label.setText("内置模板为只读，不能删除")
            return False

        answer = QMessageBox.question(
            self,
            "删除模板",
            f"确定删除模板「{self._template.name}」？该操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False

        try:
            self.service.delete_template(self._template_id)
        except ServiceError as exc:
            self.status_label.setText(str(exc))
            return False
        except Exception:  # noqa: BLE001
            logger.exception("删除模板时发生未预期错误")
            self.status_label.setText("删除模板失败，请查看日志后重试。")
            return False

        self._apply_template(None, template_id=None, read_only=False, dirty=False)
        self.reload_templates()
        self.status_label.setText("模板已删除")
        return True

    # ------------------------------------------------------------------ 预览

    def preview_current(self) -> None:
        if self._template is None:
            self.status_label.setText("请选择或新建模板")
            return
        if self._editable() and not self.apply_template_properties():
            return
        TemplatePreviewDialog(
            self.renderer, self._template, SAMPLE_VALUES, self
        ).exec()

    # ------------------------------------------------------------------ 信号

    def _on_selection_changed(self, element_id: object) -> None:
        self.panel.set_element(self.canvas.selected_element())
        self._sync_buttons()

    def _on_element_changed(self, element_id: str) -> None:
        if self._read_only or self._template is None:
            return
        self._template = self.canvas.template()
        self.panel.set_element(self.canvas.selected_element())
        self._mark_dirty()
        self.status_label.setText("元素位置/尺寸已修改（未保存）")

    # ------------------------------------------------------------------ 关闭

    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "未保存的修改",
            "当前模板有未保存的修改，确定放弃并继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        if not self._confirm_discard():
            event.ignore()
            return
        event.accept()

    # ------------------------------------------------------------------ 缩放

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._fit_canvas()

    def _fit_canvas(self) -> None:
        if self._template is None:
            return
        viewport = self.canvas_scroll.viewport().size()
        if viewport.width() <= 0 or viewport.height() <= 0:
            return
        base_width = round(self._template.canvas_width_mm / 25.4 * BASE_DPI)
        base_height = round(self._template.canvas_height_mm / 25.4 * BASE_DPI)
        if base_width <= 0 or base_height <= 0:
            return
        zoom = min(viewport.width() / base_width, viewport.height() / base_height)
        self.canvas.set_zoom(max(0.05, zoom * 0.95))
