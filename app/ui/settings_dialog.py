"""系统设置对话框（业务配置）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services import (
    CERTIFICATE_TYPES,
    DEFAULT_TABLE_COLUMN_KEYS,
    SETTING_DEFINITIONS,
    STUDENT_TABLE_FIELDS,
    TABLE_COLUMNS_KEY,
    ServiceError,
    SettingsService,
)

PAGE_SIZE_CHOICES: tuple[int, ...] = (20, 50, 100)


class SettingsDialog(QDialog):
    """业务设置编辑窗口；系统重置不在此处提供。"""

    def __init__(self, service: SettingsService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("系统设置")
        self.setMinimumWidth(460)
        self._build_ui()
        self.reload()

    # ------------------------------------------------------------------ 构建

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.page_size_combo = QComboBox()
        for size in PAGE_SIZE_CHOICES:
            self.page_size_combo.addItem(str(size), size)
        form.addRow("学生列表每页条数：", self.page_size_combo)

        self.certificate_type_combo = QComboBox()
        for type_info in CERTIFICATE_TYPES:
            self.certificate_type_combo.addItem(type_info.label, type_info.key)
        form.addRow("默认证书类型：", self.certificate_type_combo)
        layout.addLayout(form)

        columns_box = QGroupBox("主页表格展示字段", self)
        columns_layout = QVBoxLayout(columns_box)
        self.columns_list = QListWidget(columns_box)
        self.columns_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        columns_layout.addWidget(self.columns_list)

        order_row = QHBoxLayout()
        self.move_up_button = QPushButton("上移", columns_box)
        self.move_up_button.clicked.connect(lambda: self._move_column(-1))
        order_row.addWidget(self.move_up_button)
        self.move_down_button = QPushButton("下移", columns_box)
        self.move_down_button.clicked.connect(lambda: self._move_column(1))
        order_row.addWidget(self.move_down_button)
        order_row.addStretch(1)
        self.select_all_columns_button = QPushButton("全选", columns_box)
        self.select_all_columns_button.clicked.connect(
            lambda: self._set_all_checked(True)
        )
        order_row.addWidget(self.select_all_columns_button)
        self.clear_all_columns_button = QPushButton("全不选", columns_box)
        self.clear_all_columns_button.clicked.connect(
            lambda: self._set_all_checked(False)
        )
        order_row.addWidget(self.clear_all_columns_button)
        columns_layout.addLayout(order_row)

        columns_hint = QLabel(
            "列表顺序即主页表格列顺序；第一列“选择”勾选框固定显示。"
            "至少保留一个字段，保存后立即生效。",
            columns_box,
        )
        columns_hint.setWordWrap(True)
        columns_hint.setStyleSheet("color: #555555;")
        columns_layout.addWidget(columns_hint)
        layout.addWidget(columns_box)

        self.hint_label = QLabel(
            "修改「每页条数」后需重启程序生效；「主页表格展示字段」保存后立即生效。"
        )
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #b45309;")
        layout.addWidget(self.warning_label)

        self.notice_label = QLabel(
            "「恢复默认值」只恢复业务配置，不会删除学生记录、编号或历史数据；"
            "如需清除业务数据，请使用「系统管理 → 系统重置」。"
        )
        self.notice_label.setWordWrap(True)
        self.notice_label.setStyleSheet("color: #555555;")
        layout.addWidget(self.notice_label)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

        buttons = QHBoxLayout()
        self.save_button = QPushButton("保存")
        self.save_button.clicked.connect(self.save_settings)
        self.restore_button = QPushButton("恢复默认值")
        self.restore_button.clicked.connect(self.restore_defaults)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.restore_button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------ 数据

    def reload(self) -> None:
        """把当前设置值加载到控件。"""
        snapshot = self.service.load()
        page_size = snapshot.values.get("student.page_size", PAGE_SIZE_CHOICES[0])
        index = self.page_size_combo.findData(page_size)
        self.page_size_combo.setCurrentIndex(index if index >= 0 else 0)

        certificate_type = snapshot.values.get("default.certificate_type", "graduation")
        index = self.certificate_type_combo.findData(certificate_type)
        self.certificate_type_combo.setCurrentIndex(index if index >= 0 else 0)

        column_keys = snapshot.values.get(TABLE_COLUMNS_KEY, DEFAULT_TABLE_COLUMN_KEYS)
        self._fill_columns(column_keys)

        self.warning_label.setText("\n".join(snapshot.warnings))
        self.detail_label.setText("")

    def _fill_columns(self, keys: tuple[str, ...]) -> None:
        """已配置字段按保存顺序在前并勾选，其余字段按字段池顺序追加且不勾选。"""
        labels = dict(STUDENT_TABLE_FIELDS)
        configured = [key for key in keys if key in labels]
        configured_set = set(configured)
        remaining = [
            key for key, _label in STUDENT_TABLE_FIELDS if key not in configured_set
        ]
        self.columns_list.clear()
        for key in (*configured, *remaining):
            item = QListWidgetItem(labels[key], self.columns_list)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if key in configured_set
                else Qt.CheckState.Unchecked
            )

    def selected_columns(self) -> tuple[str, ...]:
        """按列表顺序返回已勾选字段的键。"""
        keys: list[str] = []
        for row in range(self.columns_list.count()):
            item = self.columns_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                keys.append(item.data(Qt.ItemDataRole.UserRole))
        return tuple(keys)

    def _move_column(self, offset: int) -> None:
        row = self.columns_list.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= self.columns_list.count():
            return
        item = self.columns_list.takeItem(row)
        self.columns_list.insertItem(target, item)
        self.columns_list.setCurrentRow(target)

    def _set_all_checked(self, checked: bool) -> None:
        """全选/全不选只改变勾选状态，不调整列表顺序。"""
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.columns_list.count()):
            self.columns_list.item(row).setCheckState(state)

    def save_settings(self) -> bool:
        """保存设置；失败时保留窗口并显示原因。"""
        column_keys = self.selected_columns()
        if not column_keys:
            self.detail_label.setStyleSheet("color: #b91c1c;")
            self.detail_label.setText("请至少勾选一个主页表格展示字段。")
            return False

        changes = {
            "student.page_size": self.page_size_combo.currentData(),
            "default.certificate_type": self.certificate_type_combo.currentData(),
            TABLE_COLUMNS_KEY: column_keys,
        }
        try:
            self.service.update(changes)
        except ServiceError as exc:
            self.detail_label.setStyleSheet("color: #b91c1c;")
            self.detail_label.setText(str(exc))
            return False

        self.reload()
        self.detail_label.setStyleSheet("color: #15803d;")
        self.detail_label.setText(
            "已保存。「每页条数」需重启程序生效；主页表格展示字段已立即生效。"
        )
        return True

    def restore_defaults(self) -> bool:
        """恢复业务配置默认值；不影响业务数据。"""
        reply = QMessageBox.question(
            self,
            "恢复默认设置",
            "将恢复系统设置的默认值。\n"
            "此操作不会删除学生记录、证书编号、模板或历史数据。\n是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False

        try:
            self.service.reset_to_defaults()
        except ServiceError as exc:
            self.detail_label.setStyleSheet("color: #b91c1c;")
            self.detail_label.setText(str(exc))
            return False

        self.reload()
        self.detail_label.setStyleSheet("color: #15803d;")
        self.detail_label.setText("已恢复默认设置。")
        return True

    @staticmethod
    def definition_help() -> tuple[str, ...]:
        """返回各设置项的说明文本，供界面或文档复用。"""
        return tuple(
            f"{definition.label}：{definition.description}"
            for definition in SETTING_DEFINITIONS
        )
