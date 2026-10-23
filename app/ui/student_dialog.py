"""学生记录新增/编辑对话框。

只负责收集与展示字段，不做业务写入；保存动作通过回调交给 Service，
校验失败时对话框保持打开并显示可读错误。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.data.db import EXCEL_FIELD_NAMES
from app.services import PhotoService, PhotoUpdateResult, ServiceError

logger = logging.getLogger(__name__)

PHOTO_PREVIEW_WIDTH = 160
PHOTO_PREVIEW_HEIGHT = 200
PHOTO_PLACEHOLDER_TEXT = "未上传\n毕业照片"
PHOTO_FILE_FILTER = "JPEG 图片 (*.jpg *.jpeg)"

FIELD_LABELS: dict[str, str] = {
    "graduation_batch": "毕业批次",
    "graduation_review": "毕业审查",
    "degree_review": "学位审查",
    "student_type": "学生类型",
    "college_number": "学院编号",
    "degree_category_code": "学位类别代码",
    "degree_category_name": "学位类别",
    "professional_category": "专业类别",
    "print_marker": "打印标记",
    "certificate_no": "毕业证书编号",
    "degree_certificate_no": "学位证书编号",
    "candidate_no": "考生号",
    "province_code": "省份代码",
    "student_no": "学号",
    "name": "姓名",
    "gender": "性别",
    "birth_date": "出生日期",
    "id_type": "证件类型",
    "id_number": "身份证号",
    "political_status": "政治面貌",
    "ethnicity": "民族",
    "institution_code": "学校代码",
    "institution_name": "学校名称",
    "major_code": "专业代码",
    "major_name": "专业名称",
    "branch": "分校",
    "faculty_name": "学院名称",
    "class_name": "班级",
    "education_level": "学历层次",
    "study_years": "学制",
    "study_mode": "学习形式",
    "current_grade": "年级",
    "enrollment_date": "入学日期",
    "graduation_date": "毕业日期",
    "registration_status": "注册状态",
    "remarks": "备注",
    "other_certificate_no": "其他证书编号",
}

FIELD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "基本信息",
        (
            "student_no",
            "name",
            "gender",
            "birth_date",
            "id_type",
            "id_number",
            "political_status",
            "ethnicity",
        ),
    ),
    (
        "学籍信息",
        (
            "student_type",
            "institution_code",
            "institution_name",
            "college_number",
            "faculty_name",
            "major_code",
            "major_name",
            "class_name",
            "branch",
            "education_level",
            "study_years",
            "study_mode",
            "current_grade",
            "enrollment_date",
            "graduation_date",
            "registration_status",
            "remarks",
        ),
    ),
    (
        "证书信息",
        (
            "graduation_batch",
            "graduation_review",
            "degree_review",
            "degree_category_code",
            "degree_category_name",
            "professional_category",
            "certificate_no",
            "degree_certificate_no",
            "candidate_no",
            "province_code",
            "print_marker",
            "other_certificate_no",
        ),
    ),
)

# 防御性检查：字段清单必须与 Schema 的唯一来源完全一致。
if sorted(FIELD_LABELS) != sorted(EXCEL_FIELD_NAMES):  # pragma: no cover - 开发期保护
    raise RuntimeError("学生表单字段与 EXCEL_FIELD_NAMES 不一致")

DATE_FIELDS: frozenset[str] = frozenset({"birth_date", "enrollment_date", "graduation_date"})


class StudentDialog(QDialog):
    """新增或编辑一条学生记录。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        record: Mapping[str, Any] | None = None,
        on_save: Callable[[dict[str, str]], None] | None = None,
        photo_service: PhotoService | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_save = on_save
        self._editing = record is not None
        self._record = dict(record) if record is not None else None
        self._saved_values: dict[str, str] | None = None
        self._editors: dict[str, QWidget] = {}
        self.photo_service = photo_service
        self.photo_result: PhotoUpdateResult | None = None
        self._pending_photo_source: Path | None = None
        self.photo_button: QPushButton | None = None
        self.photo_hint_label: QLabel | None = None
        self.photo_preview: QLabel | None = None

        self.setWindowTitle("编辑学生记录" if self._editing else "新增学生记录")
        self.resize(620, 680 if photo_service is not None else 560)
        self._build_ui()
        if record is not None:
            self._load_record(record)
        self._load_current_photo()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        if self.photo_service is not None:
            layout.addWidget(self._build_photo_box())

        tabs = QTabWidget(self)

        for title, fields in FIELD_GROUPS:
            form_host = QWidget()
            form = QFormLayout(form_host)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            for name in fields:
                editor = self._create_editor(name)
                self._editors[name] = editor
                form.addRow(f"{FIELD_LABELS[name]}：", editor)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(form_host)
            tabs.addTab(scroll, title)

        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_photo_box(self) -> QWidget:
        box = QGroupBox("毕业照片", self)
        layout = QHBoxLayout(box)

        self.photo_preview = QLabel(PHOTO_PLACEHOLDER_TEXT)
        self.photo_preview.setFixedSize(
            PHOTO_PREVIEW_WIDTH + 10, PHOTO_PREVIEW_HEIGHT + 10
        )
        self.photo_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_preview.setStyleSheet(
            "border: 1px solid #cccccc; background: #fafafa; color: #888888;"
        )
        layout.addWidget(self.photo_preview)

        side = QVBoxLayout()
        self.photo_hint_label = QLabel()
        self.photo_hint_label.setWordWrap(True)
        self.photo_hint_label.setStyleSheet("color: #555555;")
        side.addWidget(self.photo_hint_label)

        self.photo_button = QPushButton("选择照片…")
        self.photo_button.clicked.connect(self.choose_photo)
        side.addWidget(self.photo_button)
        side.addStretch(1)
        layout.addLayout(side, 1)
        return box

    def _create_editor(self, name: str) -> QWidget:
        if name == "remarks":
            editor = QPlainTextEdit()
            editor.setFixedHeight(80)
            return editor
        editor = QLineEdit()
        if name in DATE_FIELDS:
            editor.setPlaceholderText("YYYY-MM-DD")
        if name == "student_no":
            editor.setPlaceholderText("必填，保留前导零")
            if self._editing:
                editor.setReadOnly(True)
                editor.setToolTip("学号创建后不可修改")
        return editor

    def _load_record(self, record: Mapping[str, Any]) -> None:
        for name, editor in self._editors.items():
            value = record.get(name)
            text = "" if value is None else str(value)
            if isinstance(editor, QPlainTextEdit):
                editor.setPlainText(text)
            elif isinstance(editor, QLineEdit):
                editor.setText(text)

    # ------------------------------------------------------------------ 照片

    def _load_current_photo(self) -> None:
        """按数据库 ``photo_path`` 加载当前照片；不做学号推导。"""
        if self.photo_service is None:
            return
        if self._record is None:
            if self.photo_button is not None:
                self.photo_button.setEnabled(False)
            self._set_photo_placeholder("保存记录后可添加毕业照片")
            return

        stored = self._record.get("photo_path")
        if not stored:
            self._set_photo_placeholder("尚未上传毕业照片")
            return

        try:
            path = self.photo_service.resolve_photo(str(stored))
        except ServiceError:
            logger.warning("照片路径不安全，未加载：%s", stored)
            self._set_photo_placeholder(f"照片路径不安全，未加载：{stored}")
            return

        if path is None or not path.is_file():
            self._set_photo_placeholder(f"照片文件不存在：{stored}")
            return

        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._set_photo_placeholder(f"照片文件无法读取：{stored}")
            return
        self._apply_pixmap(pixmap, f"当前照片：{stored}")

    def choose_photo(self) -> None:
        """选择新的毕业照片；校验通过后仅预览，保存时再写入。"""
        if self.photo_service is None or self._record is None:
            return

        selected, _ = QFileDialog.getOpenFileName(
            self, "选择毕业照片", "", PHOTO_FILE_FILTER
        )
        if not selected:
            return

        try:
            source = self.photo_service.validate_source(selected)
        except ServiceError as exc:
            QMessageBox.warning(self, "照片不可用", str(exc))
            return

        pixmap = QPixmap(str(source))
        if pixmap.isNull():
            QMessageBox.warning(self, "照片不可用", "照片文件无法读取，请重新选择。")
            return

        self._pending_photo_source = source
        self._apply_pixmap(pixmap, f"待保存：{source.name}")

    def pending_photo_source(self) -> Path | None:
        """返回尚未保存的新照片路径；为空表示没有待保存修改。"""
        return self._pending_photo_source

    def preview_pixmap(self) -> QPixmap:
        """返回当前预览图；无照片或加载失败时为空 ``QPixmap``。"""
        if self.photo_preview is None:
            return QPixmap()
        pixmap = self.photo_preview.pixmap()
        return pixmap if pixmap is not None else QPixmap()

    def _set_photo_placeholder(self, text: str) -> None:
        self._set_photo_hint(text)
        if self.photo_preview is not None:
            self.photo_preview.clear()
            self.photo_preview.setText(PHOTO_PLACEHOLDER_TEXT)

    def _set_photo_hint(self, text: str) -> None:
        if self.photo_hint_label is not None:
            self.photo_hint_label.setText(text)

    def _apply_pixmap(self, pixmap: QPixmap, hint: str) -> None:
        if self.photo_preview is None:
            return
        scaled = pixmap.scaled(
            PHOTO_PREVIEW_WIDTH,
            PHOTO_PREVIEW_HEIGHT,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.photo_preview.setText("")
        self.photo_preview.setPixmap(scaled)
        self._set_photo_hint(hint)

    def values(self) -> dict[str, str]:
        """按 ``EXCEL_FIELD_NAMES`` 顺序返回表单原始文本。"""
        result: dict[str, str] = {}
        for name in EXCEL_FIELD_NAMES:
            editor = self._editors[name]
            if isinstance(editor, QPlainTextEdit):
                result[name] = editor.toPlainText()
            elif isinstance(editor, QLineEdit):
                result[name] = editor.text()
        return result

    def _save(self) -> None:
        if self._on_save is not None:
            values = self.values()
            # 照片失败后重试时，未变化的学生字段不再重复提交，避免重复成功审计。
            if self._saved_values is None or values != self._saved_values:
                try:
                    self._on_save(values)
                except ServiceError as exc:
                    QMessageBox.warning(self, "保存失败", str(exc))
                    return
                except Exception:  # noqa: BLE001 - 未预期错误不静默吞掉
                    logger.exception("保存学生记录时发生未预期错误")
                    QMessageBox.critical(
                        self, "保存失败", "发生未预期错误，请查看日志后重试。"
                    )
                    return
                self._saved_values = values
        if not self._save_pending_photo(
            fields_already_saved=self._saved_values is not None
        ):
            return
        self.accept()

    def _save_pending_photo(self, *, fields_already_saved: bool) -> bool:
        """保存已选择的照片；失败时对话框保持打开。"""
        if (
            self._pending_photo_source is None
            or self.photo_service is None
            or self._record is None
        ):
            return True

        note = (
            "\n\n学生字段修改已经保存，毕业照片尚未成功更新。"
            "取消对话框不会撤销已保存的字段修改。"
            if fields_already_saved
            else ""
        )
        try:
            self.photo_result = self.photo_service.set_photo(
                self._record["id"], self._pending_photo_source
            )
        except ServiceError as exc:
            QMessageBox.warning(self, "照片保存失败", f"{exc}{note}")
            return False
        except Exception:  # noqa: BLE001 - 未预期错误不静默吞掉
            logger.exception("保存毕业照片时发生未预期错误")
            QMessageBox.critical(
                self,
                "照片保存失败",
                f"发生未预期错误，请查看日志后重试。{note}",
            )
            return False

        self._pending_photo_source = None
        self._record["photo_path"] = self.photo_result.photo_path
        hint = f"已保存：{self.photo_result.photo_path}"
        if self.photo_result.cleanup_failed and self.photo_result.cleanup_message:
            hint += f"（{self.photo_result.cleanup_message}）"
        self._set_photo_hint(hint)
        return True
