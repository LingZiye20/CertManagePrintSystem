"""Excel 导入结果对话框。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services import ImportResult


class ImportResultDialog(QDialog):
    """展示一次导入的总行数、成功/失败条数与行级错误。"""

    def __init__(
        self,
        result: ImportResult,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.result = result
        self.setWindowTitle("导入结果")
        self.resize(760, 420)

        layout = QVBoxLayout(self)
        self.summary_label = QLabel(self)
        self.summary_label.setWordWrap(True)
        lines = [
            f"文件：{result.file_name}",
            f"总行数：{result.total_rows}",
        ]
        if result.status == "success":
            lines.append(f"成功：{result.imported_count}")
        else:
            lines.append(f"失败：{result.failed_count}")
            lines.append("未写入任何学生记录")
        if result.batch_id is not None:
            state = "已保存" if result.batch_saved else "未保存"
            lines.append(f"批次记录：{result.batch_id}（{state}）")
        self.summary_label.setText("\n".join(lines))
        layout.addWidget(self.summary_label)

        self.issues_table: QTableWidget | None = None
        if result.issues:
            table = QTableWidget(len(result.issues), 4, self)
            table.setHorizontalHeaderLabels(["Excel 行号", "列名", "字段", "错误原因"])
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setWordWrap(True)
            table.verticalHeader().setVisible(False)
            for row_index, issue in enumerate(result.issues):
                values = (
                    "" if issue.row is None else str(issue.row),
                    issue.column or "",
                    issue.field or "",
                    issue.reason,
                )
                for column_index, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    table.setItem(row_index, column_index, item)
            header = table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            layout.addWidget(table, 1)
            self.issues_table = table
        else:
            message = QLabel(result.message, self)
            message.setWordWrap(True)
            message.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            )
            layout.addWidget(message, 1)

        self.warnings_label = QLabel(self)
        self.warnings_label.setWordWrap(True)
        self.warnings_label.setStyleSheet("color: #b00020;")
        if result.warnings:
            self.warnings_label.setText("\n".join(result.warnings))
            layout.addWidget(self.warnings_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
