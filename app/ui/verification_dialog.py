"""证书核验对话框（只读）。

职责边界：

- 进入时按学生页面传入的筛选与排序创建核验会话，后续不再读取页面状态；
- 只展示与导航，不修改学生信息、证书编号或任何业务数据；
- 状态文案只表达可确认事实，不出现「核验通过」「证书有效」「编号真实」；
- 记录已删除时保留位置；读取失败不自动禁用导航，导航只由集合边界决定。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services import CERTIFICATE_TYPES, VerificationService
from app.services.errors import ServiceError
from app.services.verification_service import (
    VERIFICATION_MISSING_NUMBER,
    VERIFICATION_MISSING_RECORD,
    VERIFICATION_READ_FAILED,
    VERIFICATION_READY,
    VerificationEntry,
    VerificationSession,
)

logger = logging.getLogger(__name__)

EMPTY_TEXT = "—"
READ_FAILED_MESSAGE = (
    "读取这条记录失败，可能是数据库暂时不可用。请稍后重试或查看日志了解详情。"
)
EMPTY_COLLECTION_MESSAGE = (
    "当前筛选结果没有可核验的记录；可关闭本窗口，调整筛选后重新进入核验。"
)

FIELD_LABELS: tuple[tuple[str, str], ...] = (
    ("student_no", "学号："),
    ("name", "姓名："),
    ("faculty_name", "学院："),
    ("major_name", "专业："),
    ("class_name", "班级："),
)

FILTER_LABELS: dict[str, str] = {
    "student_no": "学号",
    "name": "姓名",
    "class_name": "班级",
    "major_name": "专业",
    "faculty_name": "学院",
}
SORT_LABELS: dict[str, str] = {
    **FILTER_LABELS,
    "id": "记录 ID",
    "updated_at": "更新时间",
}

STATUS_COLORS: dict[str, str] = {
    VERIFICATION_READY: "#1a7f37",
    VERIFICATION_MISSING_NUMBER: "#8a6d00",
    VERIFICATION_MISSING_RECORD: "#b00020",
    VERIFICATION_READ_FAILED: "#b00020",
}

STATUS_HINTS: dict[str, str] = {
    VERIFICATION_READY: "记录存在且所选证书类型的编号非空，请与证书实物逐项核对。",
    VERIFICATION_MISSING_NUMBER: "该记录存在，但所选证书类型的证书编号为空。",
    VERIFICATION_MISSING_RECORD: "该记录已从数据库中删除；当前位置保留，可继续查看其他记录。",
    VERIFICATION_READ_FAILED: READ_FAILED_MESSAGE,
}


class VerificationDialog(QDialog):
    """按固定集合逐条核对证书编号的只读界面。"""

    def __init__(
        self,
        service: VerificationService,
        filters: Mapping[str, str],
        parent: QWidget | None = None,
        *,
        order_by: str = "student_no",
        descending: bool = False,
        initial_type: str = "graduation",
    ) -> None:
        super().__init__(parent)
        self.service = service
        self._filters = dict(filters)
        self._order_by = order_by
        self._descending = descending
        self._session: VerificationSession | None = None
        self._index = 0
        self._entry: VerificationEntry | None = None

        self.setWindowTitle("证书核验")
        self.resize(620, 460)
        self._build_ui()
        with QSignalBlocker(self.type_combo):
            self._select_type(initial_type)
        self._start_session(self.current_type())

    # ------------------------------------------------------------------ 构建

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self.snapshot_label = QLabel("", self)
        self.snapshot_label.setWordWrap(True)
        self.snapshot_label.setStyleSheet("color: #444444;")
        layout.addWidget(self.snapshot_label)

        form_box = QGroupBox("核验信息", self)
        form = QFormLayout(form_box)

        self.type_combo = QComboBox(self)
        for type_info in CERTIFICATE_TYPES:
            self.type_combo.addItem(type_info.label, type_info.key)
        self.type_combo.setToolTip("切换证书类型会按当前核验条件重新开始核验")
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("证书类型：", self.type_combo)

        self.value_labels: dict[str, QLabel] = {}
        for field, label in FIELD_LABELS:
            value_label = QLabel(EMPTY_TEXT, self)
            value_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self.value_labels[field] = value_label
            form.addRow(label, value_label)

        self.number_label = QLabel("证书编号：", self)
        self.number_value_label = QLabel(EMPTY_TEXT, self)
        self.number_value_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        form.addRow(self.number_label, self.number_value_label)

        self.status_value_label = QLabel(EMPTY_TEXT, self)
        self.status_value_label.setStyleSheet("font-weight: bold;")
        form.addRow("核验状态：", self.status_value_label)
        layout.addWidget(form_box)

        self.status_hint_label = QLabel("", self)
        self.status_hint_label.setWordWrap(True)
        self.status_hint_label.setStyleSheet("color: #444444;")
        layout.addWidget(self.status_hint_label)

        self.message_label = QLabel("", self)
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.message_label.setStyleSheet("color: #b00020;")
        layout.addWidget(self.message_label)
        layout.addStretch(1)

        navigation = QHBoxLayout()
        self.prev_button = QPushButton("上一条", self)
        self.prev_button.clicked.connect(self.go_previous)
        self.next_button = QPushButton("下一条", self)
        self.next_button.clicked.connect(self.go_next)
        self.position_label = QLabel("第 0 / 0 条", self)
        self.reload_button = QPushButton("重新载入", self)
        self.reload_button.setToolTip("按本会话原始的筛选与排序重新获取集合，并回到第一条")
        self.reload_button.clicked.connect(self.reload_session)
        self.close_button = QPushButton("关闭", self)
        self.close_button.clicked.connect(self.reject)

        navigation.addWidget(self.prev_button)
        navigation.addWidget(self.next_button)
        navigation.addWidget(self.position_label)
        navigation.addStretch(1)
        navigation.addWidget(self.reload_button)
        navigation.addWidget(self.close_button)
        layout.addLayout(navigation)

    # ------------------------------------------------------------------ 会话

    def _select_type(self, certificate_type: str) -> None:
        index = self.type_combo.findData(certificate_type)
        if index >= 0:
            self.type_combo.setCurrentIndex(index)

    def current_type(self) -> str:
        return str(self.type_combo.currentData())

    def _on_type_changed(self) -> None:
        certificate_type = self.current_type()
        if self._session is not None and self._session.certificate_type == certificate_type:
            return
        self._start_session(certificate_type)

    def _start_session(self, certificate_type: str) -> None:
        filters = dict(self._session.filters) if self._session is not None else dict(self._filters)
        order_by = self._session.order_by if self._session is not None else self._order_by
        descending = (
            self._session.descending if self._session is not None else self._descending
        )
        try:
            session = self.service.build_session(
                certificate_type,
                filters,
                order_by=order_by,
                descending=descending,
            )
        except ServiceError as exc:
            self._fail_session(str(exc))
            return
        except Exception:  # noqa: BLE001 - 未预期错误记录日志并提示
            logger.exception("创建核验会话失败")
            self._fail_session("无法开始核验：发生未预期错误，请查看日志后重试。")
            return

        self._session = session
        self._filters = dict(session.filters)
        self._index = 0
        self._entry = None
        self.message_label.clear()
        self.snapshot_label.setText(self._describe(session))
        self._load_current()

    def _fail_session(self, message: str) -> None:
        self._session = None
        self._index = 0
        self._entry = None
        self._clear_values()
        self.position_label.setText("第 0 / 0 条")
        self.status_value_label.setText(EMPTY_TEXT)
        self.status_value_label.setStyleSheet("")
        self.status_hint_label.clear()
        self.message_label.setText(message)
        self.prev_button.setEnabled(False)
        self.next_button.setEnabled(False)

    def reload_session(self) -> None:
        """按会话快照重新获取集合，并回到第一条。"""
        if self._session is None:
            self._start_session(self.current_type())
            return
        try:
            session = self.service.reload(self._session)
        except ServiceError as exc:
            self.message_label.setText(f"重新载入失败：{exc}")
            return
        except Exception:  # noqa: BLE001 - 未预期错误记录日志并提示
            logger.exception("重新载入核验集合失败")
            self.message_label.setText("重新载入失败：发生未预期错误，请查看日志后重试。")
            return

        self._session = session
        self._index = 0
        self._entry = None
        self.message_label.clear()
        self.snapshot_label.setText(self._describe(session))
        self._load_current()

    # ------------------------------------------------------------------ 展示

    def _load_current(self) -> None:
        session = self._session
        if session is None:
            return
        if session.total == 0:
            self._entry = None
            self._clear_values()
            self.position_label.setText("第 0 / 0 条")
            self.status_value_label.setText(EMPTY_TEXT)
            self.status_value_label.setStyleSheet("")
            self.status_hint_label.clear()
            self.message_label.setText(EMPTY_COLLECTION_MESSAGE)
            self._update_navigation()
            return

        try:
            entry = self.service.load_entry(session, self._index)
        except ServiceError as exc:
            self._entry = None
            self._clear_values()
            self.position_label.setText(f"第 {self._index + 1} / {session.total} 条")
            self.status_value_label.setText(EMPTY_TEXT)
            self.status_value_label.setStyleSheet("")
            self.status_hint_label.clear()
            self.message_label.setText(f"读取当前记录失败：{exc}")
            self._update_navigation()
            return

        self._entry = entry
        self._render_entry(entry, session)
        self._update_navigation()

    def _render_entry(self, entry: VerificationEntry, session: VerificationSession) -> None:
        self.number_label.setText(f"{session.certificate_type_label}编号：")
        self.position_label.setText(f"第 {entry.position} / {entry.total} 条")
        for field, _label in FIELD_LABELS:
            value = getattr(entry, field)
            self.value_labels[field].setText(value if value else EMPTY_TEXT)
        self.number_value_label.setText(entry.certificate_no or EMPTY_TEXT)

        self.status_value_label.setText(entry.status_label)
        self.status_value_label.setStyleSheet(
            f"font-weight: bold; color: {STATUS_COLORS.get(entry.status, '#444444')};"
        )
        self.status_hint_label.setText(STATUS_HINTS.get(entry.status, ""))
        self.message_label.setText(
            READ_FAILED_MESSAGE if entry.status == VERIFICATION_READ_FAILED else ""
        )

    def _clear_values(self) -> None:
        for label in self.value_labels.values():
            label.setText(EMPTY_TEXT)
        self.number_value_label.setText(EMPTY_TEXT)
        self.number_label.setText("证书编号：")

    def _update_navigation(self) -> None:
        session = self._session
        if session is None or session.total == 0:
            self.prev_button.setEnabled(False)
            self.next_button.setEnabled(False)
            return
        self.prev_button.setEnabled(self.service.can_previous(session, self._index))
        self.next_button.setEnabled(self.service.can_next(session, self._index))

    def _describe(self, session: VerificationSession) -> str:
        if session.filters:
            filter_text = "、".join(
                f"{FILTER_LABELS.get(key, key)}={value}"
                for key, value in session.filters.items()
            )
        else:
            filter_text = "无（全部记录）"
        order_text = SORT_LABELS.get(session.order_by, session.order_by)
        direction = "降序" if session.descending else "升序"
        return f"筛选：{filter_text}；排序：{order_text} {direction}；共 {session.total} 条"

    # ------------------------------------------------------------------ 导航

    def go_previous(self) -> None:
        if self._session is None or self._session.total == 0:
            return
        self._index = self.service.previous_index(self._session, self._index)
        self._load_current()

    def go_next(self) -> None:
        if self._session is None or self._session.total == 0:
            return
        self._index = self.service.next_index(self._session, self._index)
        self._load_current()

    def refresh_current(self) -> None:
        """重新读取当前位置的记录（集合不变），用于反映外部修改或删除。"""
        if self._session is None or self._session.total == 0:
            return
        self._load_current()

    # ------------------------------------------------------------------ 状态

    @property
    def session(self) -> VerificationSession | None:
        return self._session

    @property
    def current_index(self) -> int:
        return self._index

    @property
    def current_entry(self) -> VerificationEntry | None:
        return self._entry
