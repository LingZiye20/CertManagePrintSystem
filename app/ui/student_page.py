"""学生数据管理页面。

表格只展示 Service 返回的分页快照，不直接访问数据库；筛选、排序、分页
参数均经过 Service 白名单校验。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QAction
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
    ExcelExportService,
    ExcelImportService,
    ExportResult,
    GeneratedNumber,
    ImportResult,
    NumberingService,
    PhotoService,
    PrintService,
    ServiceError,
    StudentPage as StudentPageResult,
    VerificationService,
)
from app.services import (
    DEFAULT_TABLE_COLUMN_KEYS,
    STUDENT_TABLE_FIELDS,
    StudentQuery,
    StudentService,
)
from app.ui.import_result_dialog import ImportResultDialog
from app.ui.numbering_dialog import NumberingDialog
from app.ui.print_dialog import PrintDialog
from app.ui.student_dialog import PHOTO_FILE_FILTER, StudentDialog
from app.ui.verification_dialog import VerificationDialog
from app.utils.excel_template import write_template

logger = logging.getLogger(__name__)

# 可配置字段池（38 项）与默认显示列（历史 10 列）分离：
# 模型与页面的默认值始终是 10 列，只有设置显式保存后才扩展到其他字段。
_STUDENT_TABLE_LABELS: dict[str, str] = dict(STUDENT_TABLE_FIELDS)

DEFAULT_TABLE_COLUMNS: tuple[tuple[str, str], ...] = tuple(
    (key, _STUDENT_TABLE_LABELS[key]) for key in DEFAULT_TABLE_COLUMN_KEYS
)

# 兼容既有引用：页面默认表格仍为 10 列。
TABLE_COLUMNS: tuple[tuple[str, str], ...] = DEFAULT_TABLE_COLUMNS


def normalize_table_columns(
    columns: Sequence[str | tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    """把字段键序列或（字段, 标签）序列统一为列定义；未知字段直接报错。"""
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in columns:
        key = item if isinstance(item, str) else item[0]
        if key in seen:
            continue
        label = _STUDENT_TABLE_LABELS.get(key)
        if label is None:
            raise ValueError(f"不支持的表格字段：{key}")
        seen.add(key)
        normalized.append((key, label))
    if not normalized:
        raise ValueError("主页表格至少需要保留一个字段")
    return tuple(normalized)


PAGE_SIZE_CHOICES: tuple[int, ...] = (20, 50, 100)


class StudentTableModel(QAbstractTableModel):
    """只读表格模型，持有一页记录的快照与跨页勾选集合。"""

    CHECK_COLUMN = 0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._columns: tuple[tuple[str, str], ...] = DEFAULT_TABLE_COLUMNS
        self._rows: list[dict[str, Any]] = []
        self._checked_ids: list[int] = []

    @property
    def columns(self) -> tuple[tuple[str, str], ...]:
        """当前激活的列定义（不含固定勾选列）。"""
        return self._columns

    def set_columns(self, columns: Sequence[tuple[str, str]]) -> None:
        """替换激活列；空列表视为非法，由调用方保证字段合法。"""
        normalized = tuple(columns)
        if not normalized:
            raise ValueError("主页表格至少需要保留一个字段")
        self.beginResetModel()
        self._columns = normalized
        self.endResetModel()

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
        return len(self._columns) + 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        if index.column() == self.CHECK_COLUMN:
            if role == Qt.ItemDataRole.CheckStateRole:
                record = self._rows[index.row()]
                return (
                    Qt.CheckState.Checked
                    if record["id"] in self._checked_ids
                    else Qt.CheckState.Unchecked
                )
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        field = self._columns[index.column() - 1][0]
        value = self._rows[index.row()].get(field)
        return "" if value is None else str(value)

    def setData(  # noqa: N802
        self,
        index: QModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.CheckStateRole,
    ) -> bool:
        if (
            not index.isValid()
            or index.column() != self.CHECK_COLUMN
            or role != Qt.ItemDataRole.CheckStateRole
        ):
            return False
        record = self._rows[index.row()]
        record_id = int(record["id"])
        checked = Qt.CheckState(value) == Qt.CheckState.Checked
        if checked and record_id not in self._checked_ids:
            self._checked_ids.append(record_id)
        elif not checked and record_id in self._checked_ids:
            self._checked_ids.remove(record_id)
        self.dataChanged.emit(
            index, index, [Qt.ItemDataRole.CheckStateRole]
        )
        return True

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:  # noqa: N802
        base = super().flags(index)
        if index.isValid() and index.column() == self.CHECK_COLUMN:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if section == self.CHECK_COLUMN:
                return "选择"
            return self._columns[section - 1][1]
        return section + 1

    # ------------------------------------------------------------------ 勾选

    def checked_ids(self) -> list[int]:
        """返回勾选顺序的证书记录 ID（跨页保留）。"""
        return list(self._checked_ids)

    def current_ids(self) -> list[int]:
        """返回当前页记录 ID，用于「全选本页」。"""
        return [int(record["id"]) for record in self._rows]

    def clear_checked(self) -> None:
        if not self._checked_ids:
            return
        self._checked_ids.clear()
        self._emit_check_changed()

    def check_rows(self, row_ids: list[int]) -> None:
        changed = False
        for record_id in row_ids:
            if record_id not in self._checked_ids:
                self._checked_ids.append(record_id)
                changed = True
        if changed:
            self._emit_check_changed()

    def _emit_check_changed(self) -> None:
        if not self._rows:
            return
        top = self.index(0, self.CHECK_COLUMN)
        bottom = self.index(len(self._rows) - 1, self.CHECK_COLUMN)
        self.dataChanged.emit(
            top, bottom, [Qt.ItemDataRole.CheckStateRole]
        )


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
        export_service: ExcelExportService | None = None,
        print_service: PrintService | None = None,
        verification_service: VerificationService | None = None,
        initial_page_size: int = PAGE_SIZE_CHOICES[0],
        default_certificate_type: str = "graduation",
        initial_table_columns: Sequence[str | tuple[str, str]] = DEFAULT_TABLE_COLUMNS,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.numbering_service = numbering_service
        self.photo_service = photo_service
        self.import_service = import_service
        self.import_action: QAction | None = None
        self.export_service = export_service
        self.print_service = print_service
        self.verification_service = verification_service
        self.default_certificate_type = default_certificate_type
        columns = normalize_table_columns(initial_table_columns)
        self.model = StudentTableModel(self)
        if columns != self.model.columns:
            self.model.set_columns(columns)
        self._page = 1
        self._page_count = 1
        self._total = 0
        self._order_by = (
            "student_no"
            if any(key == "student_no" for key, _label in columns)
            else columns[0][0]
        )
        self._descending = False
        self._page_size = (
            initial_page_size
            if initial_page_size in PAGE_SIZE_CHOICES
            else PAGE_SIZE_CHOICES[0]
        )
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
        self.table.setColumnWidth(0, 40)
        header = self.table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setStretchLastSection(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._on_header_clicked)
        self._sync_sort_indicator()
        self.table.doubleClicked.connect(lambda _index: self.edit_selected())
        self.table.selectionModel().selectionChanged.connect(self._sync_action_buttons)
        self.model.dataChanged.connect(lambda *args: self._sync_checked_label())
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
        self.edit_button = QPushButton("编辑")
        self.edit_button.clicked.connect(self.edit_selected)
        self.delete_button = QPushButton("删除")
        self.delete_button.clicked.connect(self.delete_selected)
        self.generate_number_button = QPushButton("生成编号")
        self.generate_number_button.clicked.connect(self.generate_number_for_selected)
        self.print_button = QPushButton("打印证书")
        self.print_button.clicked.connect(self.print_selected)
        self.verify_button = QPushButton("核验证书")
        self.verify_button.clicked.connect(self.verify_certificates)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(lambda: self.reload())

        row.addWidget(self.edit_button)
        row.addWidget(self.delete_button)
        row.addWidget(self.generate_number_button)
        row.addWidget(self.print_button)
        row.addWidget(self.verify_button)
        row.addWidget(self.refresh_button)
        row.addStretch(1)

        self.select_all_button = QPushButton("全选本页")
        self.select_all_button.clicked.connect(self.check_all_current_page)
        self.clear_checked_button = QPushButton("清除勾选")
        self.clear_checked_button.clicked.connect(self.clear_checked)
        self.checked_label = QLabel("已勾选 0 条")
        self.checked_label.setStyleSheet("color: #444444;")
        row.addWidget(self.select_all_button)
        row.addWidget(self.clear_checked_button)
        row.addWidget(self.checked_label)

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
        self.page_size_combo.setCurrentIndex(
            max(0, self.page_size_combo.findData(self._page_size))
        )
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
        self._reset_checks("筛选条件已变化，勾选已清空")
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
        self._reset_checks("筛选条件已变化，勾选已清空")
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
        if column == StudentTableModel.CHECK_COLUMN:
            return
        field = self.model.columns[column - 1][0]
        if field == self._order_by:
            self._descending = not self._descending
        else:
            self._order_by = field
            self._descending = False
        self._sync_sort_indicator()
        self._page = 1
        self._reset_checks("排序已变化，勾选已清空")
        self.reload()

    def _sort_indicator_position(self) -> int:
        """返回当前排序字段对应的表头列号；字段已隐藏时返回 -1。"""
        for position, (field, _label) in enumerate(self.model.columns, start=1):
            if field == self._order_by:
                return position
        return -1

    def _sync_sort_indicator(self) -> None:
        header = self.table.horizontalHeader()
        position = self._sort_indicator_position()
        if position <= 0:
            header.setSortIndicatorShown(False)
            return
        order = (
            Qt.SortOrder.DescendingOrder
            if self._descending
            else Qt.SortOrder.AscendingOrder
        )
        header.setSortIndicatorShown(True)
        header.setSortIndicator(position, order)

    def apply_table_columns(
        self, columns: Sequence[str | tuple[str, str]]
    ) -> None:
        """应用新的主页表格列配置；与当前配置相同时不做任何事。"""
        normalized = normalize_table_columns(columns)
        if normalized == self.model.columns:
            return

        self.model.set_columns(normalized)
        visible_fields = {field for field, _label in normalized}
        if self._order_by not in visible_fields:
            # 排序字段已被隐藏：回退默认排序并清理表头指示器。
            self._order_by = "student_no"
            self._descending = False
            self.table.horizontalHeader().setSortIndicatorShown(False)
        else:
            self._sync_sort_indicator()
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

    def set_photo_for_selected(self) -> None:
        """为当前选中记录设置或更换毕业照片。"""
        if self.photo_service is None:
            self._show_info("当前未启用照片功能")
            return
        record = self._selected_record()
        if record is None:
            self._show_info("请先选择要设置照片的记录")
            return
        selected, _ = QFileDialog.getOpenFileName(
            self, "选择毕业照片", "", PHOTO_FILE_FILTER
        )
        if not selected:
            return
        try:
            result = self.photo_service.set_photo(record["id"], selected)
        except ServiceError as exc:
            self._show_error("照片设置失败", str(exc))
            return
        except Exception:  # noqa: BLE001 - 界面不得因照片异常崩溃
            logger.exception("设置照片时发生未预期错误（记录 %s）", record.get("id"))
            self._show_error("照片设置失败", "发生未预期错误，请查看日志后重试。")
            return

        message = f"学号 {record.get('student_no')} 的毕业照片已更新"
        if result.cleanup_failed and result.cleanup_message:
            message += f"（{result.cleanup_message}）"
        self.message_label.setText(message)
        self.reload()
        QMessageBox.information(self, "照片设置成功", message)

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

    # ------------------------------------------------------------------ 勾选与打印

    def check_all_current_page(self) -> None:
        """把当前页全部记录加入勾选集合。"""
        self.model.check_rows(self.model.current_ids())
        self._sync_checked_label()

    def clear_checked(self) -> None:
        self.model.clear_checked()
        self._sync_checked_label()

    def _reset_checks(self, message: str | None = None) -> None:
        if not self.model.checked_ids():
            return
        self.model.clear_checked()
        self._sync_checked_label()
        if message:
            self.message_label.setText(message)

    def _sync_checked_label(self) -> None:
        self.checked_label.setText(f"已勾选 {len(self.model.checked_ids())} 条")

    def print_selected(self) -> None:
        """打印当前选中的单条记录。"""
        if self.print_service is None:
            self._show_info("当前未启用打印功能")
            return
        record = self._selected_record()
        if record is None:
            self._show_info("请先选择要打印的记录")
            return
        self._open_print_dialog([record])

    def print_filtered(self) -> None:
        """打印当前筛选与排序下的全部记录（点击瞬间取快照）。"""
        if self.print_service is None:
            self._show_info("当前未启用打印功能")
            return
        try:
            records = self.service.list_for_print(
                self.filters(),
                order_by=self._order_by,
                descending=self._descending,
            )
        except ServiceError as exc:
            self._show_error("读取打印数据失败", str(exc))
            return
        if not records:
            self._show_info("当前筛选结果没有可打印的记录")
            return
        if not self._confirm_batch(len(records), "当前筛选结果"):
            return
        self._open_print_dialog(records)

    def print_checked(self) -> None:
        """按勾选顺序打印被勾选的记录。"""
        if self.print_service is None:
            self._show_info("当前未启用打印功能")
            return
        record_ids = self.checked_ids()
        if not record_ids:
            self._show_info("请先勾选要打印的记录")
            return
        try:
            records = self.service.records_by_ids(record_ids)
        except ServiceError as exc:
            self._show_error("读取打印数据失败", str(exc))
            return
        if not records:
            self._show_info("勾选的记录已不存在，请刷新后重试")
            return
        if not self._confirm_batch(len(records), "勾选记录"):
            return
        self._open_print_dialog(records)

    def checked_ids(self) -> list[int]:
        return self.model.checked_ids()

    def _confirm_batch(self, count: int, scope: str) -> bool:
        reply = QMessageBox.question(
            self,
            "确认打印",
            f"将按当前顺序打印{scope}共 {count} 条记录，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _open_print_dialog(self, records: list[dict[str, Any]]) -> None:
        if self.print_service is None:
            self._show_info("当前未启用打印功能")
            return
        dialog = PrintDialog(
            self.print_service,
            records,
            self,
            initial_type=self.default_certificate_type,
        )
        dialog.exec()
        if dialog.result is not None:
            self.message_label.setText(dialog.result.message)

    def create_verification_dialog(self) -> VerificationDialog | None:
        """按当前筛选与排序创建核验窗口；集合在创建瞬间固定。"""
        if self.verification_service is None:
            self._show_info("当前未启用核验功能")
            return None
        return VerificationDialog(
            self.verification_service,
            self.filters(),
            self,
            order_by=self._order_by,
            descending=self._descending,
            initial_type=self.default_certificate_type,
        )

    def verify_certificates(self) -> None:
        """打开只读核验窗口（不修改任何业务数据）。"""
        dialog = self.create_verification_dialog()
        if dialog is not None:
            dialog.exec()

    def bind_import_action(self, action: QAction) -> None:
        """绑定菜单中的「导入 Excel」动作，导入期间禁用以防重复触发。"""
        self.import_action = action
        self._set_import_enabled(self.import_service is not None)

    def _set_import_enabled(self, enabled: bool) -> None:
        if self.import_action is not None:
            self.import_action.setEnabled(enabled)

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
        self._set_import_enabled(False)
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
            self._set_import_enabled(self.import_service is not None)

        self._show_import_result(result)
        return result

    def download_import_template(self) -> None:
        """选择保存位置并生成仅含表头行的固定 37 列导入模板。"""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "下载导入模板",
            "证书导入模板.xlsx",
            "Excel 工作簿 (*.xlsx)",
        )
        if not file_path:
            return

        target = Path(file_path)
        if target.suffix.lower() != ".xlsx":
            target = target.with_suffix(".xlsx")
        existed_before = target.exists()
        try:
            write_template(target)
        except OSError as exc:
            logger.warning("生成导入模板失败：%s（%s）", target, exc)
            self._remove_partial_template(target, existed_before)
            self._show_error(
                "下载模板失败",
                "无法写入模板文件，请检查保存位置与文件权限后重试。",
            )
            return
        except Exception:  # noqa: BLE001 - 界面不得因模板生成异常崩溃
            logger.exception("生成导入模板时发生未预期错误")
            self._remove_partial_template(target, existed_before)
            self._show_error(
                "下载模板失败", "生成模板时发生未预期错误，请查看日志后重试。"
            )
            return

        self.message_label.setText(f"导入模板已保存：{target}")
        QMessageBox.information(
            self, "下载导入模板", f"导入模板已保存到：\n{target}"
        )

    @staticmethod
    def _remove_partial_template(path: Path, existed_before: bool) -> None:
        """失败时尽力清理本次新建的模板文件；不删除用户原有文件。"""
        if existed_before:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("清理未完成的模板文件失败：%s", path)

    def export_data(self) -> None:
        """将当前筛选结果导出为固定 37 列 ``.xlsx`` 文件。"""
        if self.export_service is None:
            self._show_info("当前未启用数据导出功能")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出数据",
            "证书数据导出.xlsx",
            "Excel 工作簿 (*.xlsx)",
        )
        if not file_path:
            return

        target = Path(file_path)
        if target.suffix.lower() != ".xlsx":
            target = target.with_suffix(".xlsx")

        query = StudentQuery(
            filters=self.filters(),
            order_by=self._order_by,
            descending=self._descending,
            page=self._page,
            page_size=self._page_size,
        )

        self.message_label.setText("正在导出…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = self.export_service.export(query, target)
        except ServiceError as exc:
            self._show_error("导出失败", str(exc))
            return
        except Exception:  # noqa: BLE001 - 未预期错误记录日志并提示
            logger.exception("导出数据时发生未预期错误")
            self._show_error("导出失败", "发生未预期错误，请查看日志后重试。")
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.message_label.setText(f"已导出 {result.record_count} 条记录：{result.path}")
        QMessageBox.information(
            self,
            "导出成功",
            f"已导出 {result.record_count} 条记录到：\n{result.path}\n\n"
            "如需重新导入，请先清空三类证书编号列。",
        )

    def _show_import_result(self, result: ImportResult) -> None:
        self.message_label.setText(result.message)
        if result.status == "success":
            self.reload(reset_page=True)
        ImportResultDialog(result, self).exec()

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
        self._set_import_enabled(self.import_service is not None)
        self.print_button.setEnabled(self.print_service is not None)

    # ------------------------------------------------------------------ 反馈

    def _show_error(self, title: str, message: str) -> None:
        self.message_label.setText(message)
        QMessageBox.warning(self, title, message)

    def _show_info(self, message: str) -> None:
        self.message_label.setText(message)
        QMessageBox.information(self, "提示", message)
