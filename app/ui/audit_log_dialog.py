"""操作审计只读查询窗口。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services import AuditService, ServiceError

LIMIT_CHOICES: tuple[int, ...] = (100, 200, 500)
COLUMNS: tuple[str, ...] = ("时间", "操作类型", "目标", "结果", "摘要")


class AuditLogDialog(QDialog):
    """按时间倒序查看操作审计；不提供任何修改或删除能力。"""

    def __init__(self, service: AuditService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("操作审计")
        self.resize(860, 480)
        self._build_ui()
        self._load_operation_types()
        self.refresh()

    # ------------------------------------------------------------------ 构建

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("操作类型："))
        self.operation_filter = QComboBox()
        self.operation_filter.addItem("全部", None)
        self.operation_filter.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.operation_filter)

        filters.addWidget(QLabel("显示条数："))
        self.limit_combo = QComboBox()
        for limit in LIMIT_CHOICES:
            self.limit_combo.addItem(str(limit), limit)
        self.limit_combo.setCurrentIndex(1)
        self.limit_combo.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.limit_combo)
        filters.addStretch(1)

        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh)
        filters.addWidget(self.refresh_button)
        layout.addLayout(filters)

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------ 数据

    def _load_operation_types(self) -> None:
        try:
            types = self.service.operation_types()
        except ServiceError:
            types = ()
        for operation_type in types:
            self.operation_filter.addItem(operation_type, operation_type)

    def refresh(self) -> None:
        """重新读取审计记录；失败时保留空表并显示原因。"""
        operation_type = self.operation_filter.currentData()
        limit = self.limit_combo.currentData() or LIMIT_CHOICES[1]
        self.table.setRowCount(0)

        try:
            entries = self.service.list_recent(
                operation_type=operation_type, limit=int(limit)
            )
        except ServiceError as exc:
            self.status_label.setStyleSheet("color: #b91c1c;")
            self.status_label.setText(str(exc))
            return

        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            target = entry.target_type
            if entry.target_id:
                target = f"{target} #{entry.target_id}"
            values = (
                entry.created_at.replace("T", " ").rstrip("Z"),
                entry.operation_type,
                target,
                entry.result,
                entry.summary,
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 4 and entry.error_message:
                    item.setToolTip(entry.error_message)
                self.table.setItem(row, column, item)

        self.status_label.setStyleSheet("color: #555555;")
        self.status_label.setText(f"共 {len(entries)} 条（只读，按时间倒序）")
