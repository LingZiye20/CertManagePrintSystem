"""证书编码规则配置对话框。

只负责收集规则字段并调用 Service 保存；起始流水号由用户手动输入，
不随规则保存、不落库，编号完成后自动清空。

「编号 / 清空编号」按当前筛选结果与层次条件操作：记录 ID、层次候选值
与列表刷新均通过构造函数注入的回调获得，对话框不直接持有学生页或主窗口。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtCore import QRegularExpression, QSignalBlocker, Qt
from PySide6.QtGui import QIntValidator, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services import (
    MAX_SEQUENCE_WIDTH,
    MIN_SEQUENCE_WIDTH,
    NumberingPlan,
    NumberingService,
    ServiceError,
    compose_certificate_number,
)

logger = logging.getLogger(__name__)

LEVEL_HINT_EMPTY = "请先选择编号应用的层次"
START_SEQUENCE_HINT = "请输入起始流水号（大于等于 1 的整数）"


class NumberingRuleDialog(QDialog):
    """编辑三类证书编码规则中的一类，并按当前筛选结果批量编号。"""

    def __init__(
        self,
        service: NumberingService,
        parent: QWidget | None = None,
        *,
        initial_type: str | None = None,
        record_ids_provider: Callable[[], Sequence[int]] | None = None,
        education_level_provider: Callable[[str], Sequence[str]] | None = None,
        on_numbered: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self._record_ids_provider = record_ids_provider
        self._education_level_provider = education_level_provider
        self._on_numbered = on_numbered
        self._loading_levels = False
        self._candidate_levels: tuple[str, ...] = ()
        self._candidate_error: str | None = None
        self._current_type_key = "graduation"

        self.setWindowTitle("证书编码")
        self.resize(560, 520)
        self._build_ui()
        selected_type = initial_type if initial_type is not None else service.get_last_type()
        self._select_type(selected_type)
        self._current_type_key = self.current_type()
        self._refresh_candidates()
        self.load_rule()
        # 初始化完成后再连接，避免构造期间触发的保存覆盖数据库。
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)

    # ------------------------------------------------------------------ 构建

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.type_combo = QComboBox(self)
        for type_info in self.service.available_types():
            self.type_combo.addItem(type_info.label, type_info.key)
        form.addRow("证书类型：", self.type_combo)

        self.width_input = QLineEdit(self)
        self.width_input.setValidator(
            QIntValidator(MIN_SEQUENCE_WIDTH, MAX_SEQUENCE_WIDTH, self)
        )
        self.width_input.setPlaceholderText(
            f"{MIN_SEQUENCE_WIDTH}–{MAX_SEQUENCE_WIDTH}，默认 4"
        )
        form.addRow("序号位数：", self.width_input)
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

        sequence_row = QHBoxLayout()
        sequence_row.addWidget(QLabel("流水号："))
        self.sequence_input = QLineEdit(fragments_box)
        self.sequence_input.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"[1-9][0-9]*"), self)
        )
        self.sequence_input.setPlaceholderText(
            "必填；本批编号的起始流水号，按列表顺序逐条递增"
        )
        sequence_row.addWidget(self.sequence_input)
        fragments_layout.addLayout(sequence_row)
        layout.addWidget(fragments_box)

        levels_box = QGroupBox("编号应用条件（仅对选中的层次编号）", self)
        levels_layout = QVBoxLayout(levels_box)
        self.level_list = QListWidget(levels_box)
        self.level_list.setMaximumHeight(130)
        self.level_list.itemChanged.connect(self._on_level_selection_changed)
        levels_layout.addWidget(self.level_list)

        level_buttons = QHBoxLayout()
        self.select_all_levels_button = QPushButton("全选", levels_box)
        self.select_all_levels_button.clicked.connect(
            lambda: self._set_all_levels(True)
        )
        level_buttons.addWidget(self.select_all_levels_button)
        self.clear_all_levels_button = QPushButton("全不选", levels_box)
        self.clear_all_levels_button.clicked.connect(
            lambda: self._set_all_levels(False)
        )
        level_buttons.addWidget(self.clear_all_levels_button)
        level_buttons.addStretch(1)
        levels_layout.addLayout(level_buttons)

        self.level_hint_label = QLabel("", levels_box)
        self.level_hint_label.setWordWrap(True)
        self.level_hint_label.setStyleSheet("color: #a33a2c;")
        levels_layout.addWidget(self.level_hint_label)
        layout.addWidget(levels_box)

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
        self.number_button = QPushButton("编号", self)
        self.number_button.setToolTip("按当前筛选结果批量生成该类证书编号")
        self.number_button.clicked.connect(lambda: self.number_all())
        buttons.addWidget(self.number_button)
        self.clear_numbers_button = QPushButton("清空编号", self)
        self.clear_numbers_button.setToolTip("清空该类型全部记录的编号")
        self.clear_numbers_button.clicked.connect(lambda: self.clear_numbers())
        buttons.addWidget(self.clear_numbers_button)
        buttons.addStretch(1)
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
            "fixed_text_1": self.fixed_text_inputs[0].text(),
            "fixed_text_2": self.fixed_text_inputs[1].text(),
            "fixed_text_3": self.fixed_text_inputs[2].text(),
            "fixed_text_4": self.fixed_text_inputs[3].text(),
            "sequence_width": int(width_text) if width_text else None,
            "education_levels": self.selected_education_levels(),
        }

    def selected_education_levels(self) -> list[str]:
        """返回当前勾选的层次，保持列表顺序。"""
        return [
            self.level_list.item(row).text()
            for row in range(self.level_list.count())
            if self.level_list.item(row).checkState() == Qt.CheckState.Checked
        ]

    def _refresh_candidates(self) -> None:
        """打开对话框时刷新一次层次候选值。"""
        provider = self._education_level_provider
        if provider is None:
            self._candidate_levels = ()
            return
        try:
            self._candidate_levels = tuple(provider(self.current_type()))
            self._candidate_error = None
        except ServiceError as exc:
            self._candidate_levels = ()
            self._candidate_error = str(exc)
        except Exception:  # noqa: BLE001
            logger.exception("读取层次候选值失败")
            self._candidate_levels = ()
            self._candidate_error = "读取层次候选值失败，请查看日志后重试。"

    def _load_education_levels(self, selected: Sequence[str]) -> None:
        """按候选值与已保存选择重建可勾选列表。"""
        values = list(self._candidate_levels)
        for level in selected:
            if level and level not in values:
                values.append(level)

        self._loading_levels = True
        try:
            self.level_list.clear()
            selected_set = set(selected)
            for level in values:
                item = QListWidgetItem(level, self.level_list)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked
                    if level in selected_set
                    else Qt.CheckState.Unchecked
                )
        finally:
            self._loading_levels = False
        self._on_level_selection_changed()

    def _set_all_levels(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._loading_levels = True
        try:
            for row in range(self.level_list.count()):
                self.level_list.item(row).setCheckState(state)
        finally:
            self._loading_levels = False
        self._on_level_selection_changed()

    def _on_level_selection_changed(self, *_args: object) -> None:
        if self._loading_levels:
            return
        selected = self.selected_education_levels()
        if selected:
            self.level_hint_label.setText(f"已选择 {len(selected)} 个层次。")
        else:
            self.level_hint_label.setText(LEVEL_HINT_EMPTY)
        self._refresh_number_button()

    def _on_type_changed(self, index: int) -> None:
        """切换证书类型：先静默保存当前规则，失败则回退选择。"""
        target = (
            str(self.type_combo.itemData(index))
            if index >= 0
            else self._current_type_key
        )
        if target == self._current_type_key:
            return
        if not self._autosave(self._current_type_key):
            blocker = QSignalBlocker(self.type_combo)
            restore = self.type_combo.findData(self._current_type_key)
            if restore >= 0:
                self.type_combo.setCurrentIndex(restore)
            del blocker
            return

        self._current_type_key = target
        self.sequence_input.clear()
        self.preview_label.setText("预览编号：-")
        self.load_rule()
        self.status_label.setText("规则已自动保存")

    def _autosave(self, rule_key: str | None = None) -> bool:
        """静默保存当前表单；失败时弹窗说明原因并返回 ``False``。"""
        key = self.current_type() if rule_key is None else rule_key
        try:
            self.service.update_rule(key, self.values())
        except ServiceError as exc:
            self._show_error(str(exc), title="自动保存失败")
            return False
        except Exception:  # noqa: BLE001
            logger.exception("自动保存编号规则时发生未预期错误")
            self._show_error(
                "自动保存失败，请查看日志后重试。", title="自动保存失败"
            )
            return False

        self.status_label.setText("规则已自动保存")
        self.service.remember_last_type(key)
        return True

    def reject(self) -> None:
        """关闭前保存当前规则；保存失败时不关闭对话框。"""
        if not self._autosave(self._current_type_key):
            return
        super().reject()


    def start_sequence(self) -> int | None:
        """返回当前手动输入的起始流水号；为空或非法时返回 ``None``。"""
        text = self.sequence_input.text().strip()
        if not text.isdigit():
            return None
        value = int(text)
        return value if value >= 1 else None

    def load_rule(self) -> None:
        """按当前证书类型加载规则与层次选择。"""
        self.status_label.clear()
        self.preview_label.setText("预览编号：-")
        try:
            view = self.service.get_rule(self.current_type())
        except ServiceError as exc:
            self.number_button.setEnabled(False)
            self.clear_numbers_button.setEnabled(False)
            self._show_error(str(exc))
            return
        except Exception:  # noqa: BLE001 - 未预期错误记录日志并提示
            logger.exception("加载编号规则时发生未预期错误")
            self.number_button.setEnabled(False)
            self.clear_numbers_button.setEnabled(False)
            self._show_error("加载编号规则失败，请查看日志后重试。")
            return

        for editor, text in zip(self.fixed_text_inputs, view.fixed_texts):
            editor.setText(text)
        self.width_input.setText(str(view.sequence_width))
        self._load_education_levels(view.education_levels)
        self.clear_numbers_button.setEnabled(True)
        if self._candidate_error:
            self.level_hint_label.setText(self._candidate_error)
        self._refresh_number_button()

    def preview(self) -> bool:
        """按当前表单值与手动流水号计算预览；不写数据库、不占用编号。"""
        self.status_label.clear()
        start = self.start_sequence()
        if start is None:
            self.preview_label.setText("预览编号：无法计算")
            self.status_label.setText(START_SEQUENCE_HINT)
            return False

        width_text = self.width_input.text().strip()
        try:
            width: int | None = int(width_text)
        except ValueError:
            width = None

        try:
            number = compose_certificate_number(
                [editor.text() for editor in self.fixed_text_inputs],
                start,
                width,  # type: ignore[arg-type] - 非法值由拼接函数统一拒绝
            )
        except ValueError as exc:
            self.preview_label.setText("预览编号：无法计算")
            self.status_label.setText(str(exc))
            return False

        self.preview_label.setText(f"预览编号：{number}")
        self.status_label.setText(
            f"预览基于当前表单值与手动流水号 {start}；"
            "未保存、不会占用编号。"
        )
        return True

    # ------------------------------------------------------------------ 批量编号

    def number_all(self) -> bool:
        """按当前筛选结果批量编号；成功返回 ``True``。"""
        self.status_label.clear()
        provider = self._record_ids_provider
        if provider is None:
            self._show_error("未连接学生列表，无法批量编号。", title="编号失败")
            return False

        if not self.selected_education_levels():
            self._show_error(LEVEL_HINT_EMPTY, title="编号失败")
            return False

        start_sequence = self.start_sequence()
        if start_sequence is None:
            self._show_error(START_SEQUENCE_HINT, title="编号失败")
            return False

        # 编号前先保存当前表单，保存失败则中止。
        if not self._autosave(self._current_type_key):
            return False

        try:
            record_ids = list(provider())
        except ServiceError as exc:
            self._show_error(str(exc), title="编号失败")
            return False
        except Exception:  # noqa: BLE001
            logger.exception("读取当前筛选结果失败")
            self._show_error(
                "读取当前筛选结果失败，请查看日志后重试。", title="编号失败"
            )
            return False

        if not record_ids:
            self._show_info("当前筛选结果为空，无可编号记录", title="无需编号")
            return False

        try:
            plan = self.service.plan_numbers(
                record_ids, self.current_type(), start_sequence
            )
        except ServiceError as exc:
            self._show_error(str(exc), title="编号失败")
            return False
        except Exception:  # noqa: BLE001
            logger.exception("计算批量编号计划时发生未预期错误")
            self._show_error("计算编号计划失败，请查看日志后重试。", title="编号失败")
            return False

        if plan.matched_count == 0:
            self._show_info(
                "当前筛选结果中没有符合层次条件"
                f"（{'、'.join(plan.education_levels)}）的记录，无需编号。",
                title="无需编号",
            )
            return False

        if plan.pending_count == 0:
            self._show_info(
                f"符合层次条件的 {plan.matched_count} 条记录都已有"
                f"{plan.label}编号，无需编号。",
                title="无需编号",
            )
            return False

        if not self._confirm_batch(plan):
            self.status_label.setText(
                "已取消编号；未写入编号、未写入成功审计。"
            )
            return False

        self.status_label.setText(f"正在为 {plan.pending_count} 条记录编号…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            result = self.service.generate_numbers(
                record_ids, self.current_type(), start_sequence
            )
        except ServiceError as exc:
            self.load_rule()
            self._show_error(str(exc), title="编号失败")
            return False
        except Exception:  # noqa: BLE001
            logger.exception("批量生成证书编号时发生未预期错误")
            self.load_rule()
            self._show_error(
                "批量生成证书编号失败，请查看日志后重试。", title="编号失败"
            )
            return False
        finally:
            QApplication.restoreOverrideCursor()

        self.load_rule()
        # 编号完成后自动清空流水号输入，并把焦点留在该输入框。
        self.sequence_input.clear()
        self.preview_label.setText("预览编号：-")
        self.status_label.setText(
            f"批量编号完成：编号 {result.numbered_count} 条，"
            f"跳过 {result.skipped_count} 条，"
            f"编号区间 {result.first_number}–{result.last_number}；"
            "流水号输入已清空。"
        )
        self.sequence_input.setFocus()
        if self._on_numbered is not None:
            self._on_numbered()

        QMessageBox.information(
            self,
            "编号完成",
            f"证书类型：{result.label}\n"
            f"应用层次：{'、'.join(result.education_levels)}\n"
            f"成功编号：{result.numbered_count} 条\n"
            f"跳过（已有该类编号）：{result.skipped_count} 条\n"
            f"跳过（层次不符）：{result.level_skipped_count} 条\n"
            f"编号起始值：{result.first_number or '（无）'}\n"
            f"编号结束值：{result.last_number or '（无）'}\n"
            "流水号输入已清空，下次编号请重新输入起始流水号。",
        )
        return True

    def _confirm_batch(self, plan: NumberingPlan) -> bool:
        """显示批量编号确认框；用户取消时返回 ``False``。"""
        message = (
            f"证书类型：{plan.label}\n"
            f"应用层次：{'、'.join(plan.education_levels)}\n"
            f"当前筛选结果：{plan.total_count} 条\n"
            f"命中层次条件：{plan.matched_count} 条\n"
            f"已有该类编号将跳过：{plan.skipped_count} 条\n"
            f"本次实际编号：{plan.pending_count} 条\n"
            f"起始流水号（手动输入）：{plan.start_sequence}\n"
            f"首个编号：{plan.first_number}\n"
            f"末个编号：{plan.last_number}\n\n"
            "将按列表顺序逐条递增写入编号，操作不可撤销。是否继续？"
        )
        reply = QMessageBox.question(
            self,
            "确认批量编号",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _refresh_number_button(self) -> None:
        """按依赖与层次选择刷新「编号」按钮。"""
        if self._record_ids_provider is None:
            self.number_button.setEnabled(False)
            self.number_button.setToolTip("未连接学生列表，无法按当前筛选结果批量编号")
            return
        if not self.selected_education_levels():
            self.number_button.setEnabled(False)
            self.number_button.setToolTip(LEVEL_HINT_EMPTY)
            return
        self.number_button.setEnabled(True)
        self.number_button.setToolTip("按当前筛选结果批量生成该类证书编号")

    # ------------------------------------------------------------------ 清空编号

    def clear_numbers(self) -> bool:
        """清空当前证书类型的全部编号；成功返回 ``True``。"""
        self.status_label.clear()
        # 清空前先保存当前表单，保存失败则中止。
        if not self._autosave(self._current_type_key):
            return False

        try:
            total = self.service.count_numbers(self.current_type())
        except ServiceError as exc:
            self._show_error(str(exc), title="清空失败")
            return False
        except Exception:  # noqa: BLE001
            logger.exception("统计待清空编号时发生未预期错误")
            self._show_error("读取证书编号失败，请查看日志后重试。", title="清空失败")
            return False

        if total == 0:
            self._show_info("没有可清空的编号", title="无需清空")
            return False

        reply = QMessageBox.question(
            self,
            "确认清空编号",
            f"证书类型：{self.type_combo.currentText()}\n"
            f"将清空全部 {total} 条记录的该类编号（含当前筛选范围外的记录）。\n"
            "编号清空后如需重新编号，请在「流水号」中手动输入起始值。\n"
            "已打印记录中的编号快照不受影响。\n"
            "操作不可撤销。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self.status_label.setText("已取消清空编号；未做任何修改。")
            return False

        self.status_label.setText("正在清空编号…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            result = self.service.clear_numbers(self.current_type())
        except ServiceError as exc:
            self.load_rule()
            self._show_error(str(exc), title="清空失败")
            return False
        except Exception:  # noqa: BLE001
            logger.exception("清空证书编号时发生未预期错误")
            self.load_rule()
            self._show_error("清空证书编号失败，请查看日志后重试。", title="清空失败")
            return False
        finally:
            QApplication.restoreOverrideCursor()

        self.load_rule()
        # 清空编号不清空用户已输入的流水号，按当前输入刷新预览。
        if self.start_sequence() is not None:
            self.preview()
        self.status_label.setText(
            f"已清空{result.label}编号 {result.cleared_count} 条；"
            "如需重新编号，请手动输入起始流水号。"
        )
        if self._on_numbered is not None:
            self._on_numbered()

        QMessageBox.information(
            self,
            "清空完成",
            f"证书类型：{result.label}\n"
            f"清空条数：{result.cleared_count} 条\n"
            "如需重新编号，请在「流水号」中手动输入起始值。",
        )
        return True

    def _show_error(self, message: str, *, title: str = "证书编码") -> None:
        self.status_label.setText(message)
        QMessageBox.warning(self, title, message)

    def _show_info(self, message: str, *, title: str = "证书编码") -> None:
        self.status_label.setText(message)
        QMessageBox.information(self, title, message)
