"""系统重置确认窗口（两步确认）。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services import ResetResult, ResetScope, ResetService, ServiceError


class SystemResetDialog(QDialog):
    """展示重置范围并要求两次确认；取消任一步都不修改任何数据。"""

    reset_completed = Signal(object)

    def __init__(self, service: ResetService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.result: ResetResult | None = None
        self.setWindowTitle("系统重置")
        self.resize(620, 460)
        self._build_ui()

    # ------------------------------------------------------------------ 构建

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        scope = self.service.describe_scope()

        warning = QLabel("警告：系统重置会清除业务数据，且无法通过本程序恢复。")
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #b91c1c; font-weight: bold;")
        layout.addWidget(warning)

        self.notice_label = QLabel(
            "请先手动备份 data/certificates.db、photos 与自定义模板等重要数据；"
            "备份不属于程序功能，需由你自行完成。"
        )
        self.notice_label.setWordWrap(True)
        layout.addWidget(self.notice_label)

        self.scope_label = QLabel(self._format_scope(scope))
        self.scope_label.setWordWrap(True)
        self.scope_label.setTextInteractionFlags(
            self.scope_label.textInteractionFlags()
        )
        layout.addWidget(self.scope_label, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.run_button = QPushButton("我已了解，继续…")
        self.run_button.clicked.connect(self.request_reset)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.run_button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

    @staticmethod
    def _format_scope(scope: ResetScope) -> str:
        clears = "\n".join(f"  - {item}" for item in scope.clears)
        keeps = "\n".join(f"  - {item}" for item in scope.keeps)
        return (
            "将被清除（不可撤销）：\n"
            f"{clears}\n\n"
            "将被保留：\n"
            f"{keeps}"
        )

    # ------------------------------------------------------------------ 执行

    def request_reset(self) -> bool:
        """第二次确认后执行重置；返回是否真正执行且成功。"""
        reply = QMessageBox.warning(
            self,
            "再次确认系统重置",
            "这是系统级数据清理，不是普通设置恢复。\n"
            "确认后将立即清除学生记录、打印记录、导入历史、操作日志与自定义模板，\n"
            "并删除受管理的学生照片。此操作无法通过本程序撤销。\n\n"
            "确定继续执行系统重置吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self.status_label.setStyleSheet("color: #555555;")
            self.status_label.setText("已取消，未做任何修改。")
            return False

        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = self.service.reset_system()
        except ServiceError as exc:
            self.status_label.setStyleSheet("color: #b91c1c;")
            self.status_label.setText(f"系统重置失败：{exc}")
            return False
        finally:
            QApplication.restoreOverrideCursor()
            self.run_button.setEnabled(True)
            self.cancel_button.setEnabled(True)

        self.result = result
        text = result.message
        if result.warnings:
            text += "\n\n注意：\n" + "\n".join(f"  - {item}" for item in result.warnings)
        if result.success:
            self.status_label.setStyleSheet("color: #15803d;")
        else:
            self.status_label.setStyleSheet("color: #b91c1c;")
        self.status_label.setText(text)
        self.reset_completed.emit(result)
        return result.success
