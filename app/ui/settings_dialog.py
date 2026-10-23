"""系统设置对话框（业务配置）。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services import (
    CERTIFICATE_TYPES,
    SETTING_DEFINITIONS,
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

        self.hint_label = QLabel("修改「每页条数」后需重启程序生效。")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #b45309;")
        layout.addWidget(self.warning_label)

        self.notice_label = QLabel(
            "「恢复默认值」只恢复业务配置，不会删除学生记录、编号流水或历史数据；"
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

        self.warning_label.setText("\n".join(snapshot.warnings))
        self.detail_label.setText("")

    def save_settings(self) -> bool:
        """保存设置；失败时保留窗口并显示原因。"""
        changes = {
            "student.page_size": self.page_size_combo.currentData(),
            "default.certificate_type": self.certificate_type_combo.currentData(),
        }
        try:
            self.service.update(changes)
        except ServiceError as exc:
            self.detail_label.setStyleSheet("color: #b91c1c;")
            self.detail_label.setText(str(exc))
            return False

        self.reload()
        self.detail_label.setStyleSheet("color: #15803d;")
        self.detail_label.setText("已保存。重启程序后「每页条数」生效。")
        return True

    def restore_defaults(self) -> bool:
        """恢复业务配置默认值；不影响业务数据。"""
        reply = QMessageBox.question(
            self,
            "恢复默认设置",
            "将恢复系统设置的默认值。\n"
            "此操作不会删除学生记录、编号流水、模板或历史数据。\n是否继续？",
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
