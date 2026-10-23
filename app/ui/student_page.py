"""学生数据管理页面。

表格只展示 Service 返回的分页快照，不直接访问数据库；筛选、排序、分页
参数均经过 Service 白名单校验。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.services import (
    ExcelImportService,
    GeneratedNumber,
    ImportResult,
    NumberingService,
    PhotoService,
    ServiceError,
    StudentPage as StudentPageResult,
)
from app.services import StudentQuery, StudentService
from app.ui.import_result_dialog import ImportResultDialog
from app.ui.numbering_dialog import NumberingDialog
from app.ui.student_dialog import StudentDialog

logger = logging.getLogger(__name__)

TABLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("student_no", "学号"),
    ("name", "姓名"),
    ("gender", "性别"),
    ("faculty_name", "学院"),
    ("major_name", "专业"),
    ("class_name", "班级"),
    ("certificate_no", "毕业证书编号"),
    ("degree_certificate_no", "学位证书编号"),
    ("other_certificate_no", "其他证书编号"),
    ("updated_at", "更新时间"),
)

PAGE_SIZE_CHOICES: tuple[int, ...] = (20, 50, 100)


class StudentTableModel(QAbstractTableModel):
    """只读表格模型，持有一页记录的快照。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[dict[str, Any]] = []

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def record_at(self, row: int) -> dict[str, Any] | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        if parent is not None and parent.isValid():
            return 0
        return len(TABLE_COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        field = TABLE_COLUMNS[index.column()][0]
        value = self._rows[index.row()].get(field)
        return "" if value is None else str(value)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return TABLE_COLUMNS[section][1]
        return section + 1


class StudentPage(QWidget):
    """学生记录表格页面：筛选、排序、分页与单条增改删。"""

    def __init__(
        self,
        service: StudentService,
        parent: QWidget | None = None,
        *,
        numbering_service: NumberingService | None = None,
        photo_service: PhotoService | None = None,
        import_service: ExcelImportService | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.numbering_service = numbering_service
        self.photo_service = photo_service
        self.import_service = import_service
        self.model = StudentTableModel(self)
        self._page = 1
        self._page_count = 1
        self._total = 0
        self._order_by = "student_no"
        self._descending = False
        self._page_size = PAGE_SIZE_CHOICES[0]
        self._build_ui()
        self.reload()

    # ------------------------------------------------------------------ 构建

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(self._build_filter_box())
        layout.addLayout(self._build_toolbar())

        self.table = QTableView(self)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setStretchLastSection(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        header.sectionClicked.connect(self._on_header_clicked)
        self.table.doubleClicked.connect(lambda _index: self.edit_selected())
        self.table.selectionModel().selectionChanged.connect(self._sync_action_buttons)
        layout.addWidget(self.table, 1)

        layout.addLayout(self._build_pagination())
        self._sync_action_buttons()

    def _build_filter_box(self) -> QWidget:
        box = QGroupBox("筛选", self)
        grid = QGridLayout(box)

        self.student_no_input = QLineEdit()
        self.student_no_input.setPlaceholderText("学号精确查询")
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("包含匹配")
        self.class_name_input = QLineEdit()
        self.major_name_input = QLineEdit()
        self.faculty_name_input = QLineEdit()

        grid.addWidget(QLabel("学号："), 0, 0)
        grid.addWidget(self.student_no_input, 0, 1)
        grid.addWidget(QLabel("姓名："), 0, 2)
        grid.addWidget(self.name_input, 0, 3)
        grid.addWidget(QLabel("班级："), 1, 0)
        grid.addWidget(self.class_name_input, 1, 1)
        grid.addWidget(QLabel("专业："), 1, 2)
        grid.addWidget(self.major_name_input, 1, 3)
        grid.addWidget(QLabel("学院："), 1, 4)
        grid.addWidget(self.faculty_name_input, 1, 5)

        self.search_button = QPushButton("查询")
        self.search_button.clicked.connect(self.apply_filters)
        self.clear_button = QPushButton("清除筛选")
        self.clear_button.clicked.connect(self.clear_filters)
        grid.addWidget(self.search_button, 0, 6)
        grid.addWidget(self.clear_button, 1, 6)

        for line_edit in (
            self.student_no_input,
            self.name_input,
            self.class_name_input,
            self.major_name_input,
            self.faculty_name_input,
        ):
            line_edit.returnPressed.connect(self.apply_filters)
        return box

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.add_button = QPushButton("新增")
        self.add_button.clicked.connect(self.add_record)
        self.edit_button = QPushButton("编辑")
        self.edit_button.clicked.connect(self.edit_selected)
        self.delete_button = QPushButton("删除")
        self.delete_button.clicked.connect(self.delete_selected)
        self.generate_number_button = QPushButton("生成编号")
        self.generate_number_button.clicked.connect(self.generate_number_for_selected)
        self.import_button = QPushButton("导入 Excel")
        self.import_button.clicked.connect(self.import_excel)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(lambda: self.reload())

        row.addWidget(self.add_button)
        row.addWidget(self.edit_button)
        row.addWidget(self.delete_button)
        row.addWidget(self.generate_number_button)
        row.addWidget(self.import_button)
        row.addWidget(self.refresh_button)
        row.addStretch(1)

        self.message_label = QLabel("")
        self.message_label.setStyleSheet("color: #444444;")
        row.addWidget(self.message_label)
        return row

    def _build_pagination(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.summary_label = QLabel("共 0 条")
        row.addWidget(self.summary_label)
        row.addStretch(1)

        row.addWidget(QLabel("每页："))
        self.page_size_combo = QComboBox()
        for size in PAGE_SIZE_CHOICES:
            self.page_size_combo.addItem(str(size), size)
        self.page_size_combo.setCurrentIndex(0)
        self.page_size_combo.currentIndexChanged.connect(self._on_page_size_changed)
        row.addWidget(self.page_size_combo)

        self.prev_button = QPushButton("上一页")
        self.prev_button.clicked.connect(self.previous_page)
        self.next_button = QPushButton("下一页")
        self.next_button.clicked.connect(self.next_page)
        row.addWidget(self.prev_button)
        row.addWidget(self.next_button)
        return row

    # ------------------------------------------------------------------ 查询

    def filters(self) -> dict[str, str]:
        """收集非空筛选条件。"""
        values = {
            "student_no": self.student_no_input.text(),
            "name": self.name_input.text(),
            "class_name": self.class_name_input.text(),
            "major_name": self.major_name_input.text(),
            "faculty_name": self.faculty_name_input.text(),
        }
        return {key: value.strip() for key, value in values.items() if value.strip()}

    def reload(self, *, reset_page: bool = False) -> None:
        """按当前筛选、排序与页码刷新表格。"""
        if reset_page:
            self._page = 1
        query = StudentQuery(
            filters=self.filters(),
            order_by=self._order_by,
            descending=self._descending,
            page=self._page,
            page_size=self._page_size,
        )
        try:
            result = self.service.search(query)
        except ServiceError as exc:
            self._show_error("查询失败", str(exc))
            return
        except Exception:  # noqa: BLE001 - 未预期错误记录日志并提示
            logger.exception("刷新学生数据时发生未预期错误")
            self._show_error("查询失败", "发生未预期错误，请查看日志后重试。")
            return
        self._apply_result(result)

    def apply_filters(self) -> None:
        """应用筛选条件并从第一页开始查询。"""
        self._page = 1
        self.reload()

    def clear_filters(self) -> None:
        for line_edit in (
            self.student_no_input,
            self.name_input,
            self.class_name_input,
            self.major_name_input,
            self.faculty_name_input,
        ):
            line_edit.clear()
        self._page = 1
        self.reload()

    def previous_page(self) -> None:
        if self._page <= 1:
            return
        self._page -= 1
        self.reload()

    def next_page(self) -> None:
        if self._page >= self._page_count:
            return
        self._page += 1
        self.reload()

    def _on_page_size_changed(self) -> None:
        self._page_size = int(self.page_size_combo.currentData())
        self._page = 1
        self.reload()

    def _on_header_clicked(self, column: int) -> None:
        field = TABLE_COLUMNS[column][0]
        if field == self._order_by:
            self._descending = not self._descending
        else:
            self._order_by = field
            self._descending = False
        order = (
            Qt.SortOrder.DescendingOrder
            if self._descending
            else Qt.SortOrder.AscendingOrder
        )
        self.table.horizontalHeader().setSortIndicator(column, order)
        self._page = 1
        self.reload()

    def _apply_result(self, result: StudentPageResult) -> None:
        self._page = result.page
        self._page_count = result.page_count
        self._total = result.total
        self.model.set_rows(result.records)

        if result.total == 0:
            self.summary_label.setText("共 0 条 · 没有符合条件的记录")
        else:
            self.summary_label.setText(
                f"共 {result.total} 条 · 第 {result.page}/{result.page_count} 页"
            )
        self.prev_button.setEnabled(result.page > 1)
        self.next_button.setEnabled(result.page < result.page_count)
        self._sync_action_buttons()

    # ------------------------------------------------------------------ 增改删

    def add_record(self) -> None:
        dialog = StudentDialog(self, on_save=self._save_new_record)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.message_label.setText("新增记录成功")
            self.reload(reset_page=True)

    def edit_selected(self) -> None:
        record = self._selected_record()
        if record is None:
            self._show_info("请先选择要编辑的记录")
            return
        dialog = StudentDialog(
            self,
            record=record,
            on_save=lambda values: self.service.update_record(record["id"], values),
            photo_service=self.photo_service,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if dialog.photo_result is not None:
                self.message_label.setText("编辑记录成功，毕业照片已更新")
            else:
                self.message_label.setText("编辑记录成功")
            self.reload()

    def delete_selected(self) -> None:
        record = self._selected_record()
        if record is None:
            self._show_info("请先选择要删除的记录")
            return
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除学号 {record['student_no']} 的记录吗？此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.delete_record(record["id"])
        except ServiceError as exc:
            self._show_error("删除失败", str(exc))
            return
        except Exception:  # noqa: BLE001
            logger.exception("删除学生记录时发生未预期错误")
            self._show_error("删除失败", "发生未预期错误，请查看日志后重试。")
            return
        self.message_label.setText("删除记录成功")
        self.reload()

    def generate_number_for_selected(self) -> None:
        """为当前选中记录打开编号预览/生成对话框。"""
        if self.numbering_service is None:
            self._show_info("当前未启用证书编号功能")
            return
        record = self._selected_record()
        if record is None:
            self._show_info("请先选择要生成编号的记录")
            return

        def on_generated(result: GeneratedNumber) -> None:
            self.message_label.setText(f"编号生成成功：{result.number}")
            self.reload()

        dialog = NumberingDialog(
            self.numbering_service,
            record,
            self,
            on_generated=on_generated,
        )
        dialog.exec()

    def import_excel(self) -> ImportResult | None:
        """选择固定 37 列 Excel 文件并展示导入结果。"""
        if self.import_service is None:
            self._show_info("当前未启用 Excel 导入功能")
            return None

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择学生数据 Excel 文件",
            "",
            "Excel 工作簿 (*.xlsx)",
        )
        if not file_path:
            return None

        self.message_label.setText("正在导入…")
        self.import_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = self.import_service.import_file(Path(file_path))
        except ServiceError as exc:
            self._show_error("导入失败", str(exc))
            return None
        except Exception:  # noqa: BLE001 - 未预期错误记录日志并提示
            logger.exception("导入 Excel 时发生未预期错误")
            self._show_error("导入失败", "发生未预期错误，请查看日志后重试。")
            return None
        finally:
            QApplication.restoreOverrideCursor()
            self.import_button.setEnabled(self.import_service is not None)

        self._show_import_result(result)
        return result

    def _show_import_result(self, result: ImportResult) -> None:
        self.message_label.setText(result.message)
        if result.status == "success":
            self.reload(reset_page=True)
        ImportResultDialog(result, self).exec()

    def _save_new_record(self, values: dict[str, str]) -> None:
        self.service.create_record(values)

    def _selected_record(self) -> dict[str, Any] | None:
        selection = self.table.selectionModel()
        if selection is None:
            return None
        indexes = selection.selectedRows()
        if not indexes:
            return None
        return self.model.record_at(indexes[0].row())

    def _sync_action_buttons(self) -> None:
        has_selection = self._selected_record() is not None
        self.edit_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)
        self.generate_number_button.setEnabled(
            has_selection and self.numbering_service is not None
        )
        self.import_button.setEnabled(self.import_service is not None)

    # ------------------------------------------------------------------ 反馈

    def _show_error(self, title: str, message: str) -> None:
        self.message_label.setText(message)
        QMessageBox.warning(self, title, message)

    def _show_info(self, message: str) -> None:
        self.message_label.setText(message)
        QMessageBox.information(self, "提示", message)
