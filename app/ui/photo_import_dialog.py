"""照片批量导入对话框。

交互保持简洁：选择文件夹与关联字段 → 扫描匹配（不写库）→ 确认导入 → 展示结果。
既有照片的覆盖/跳过在扫描之后、写入之前询问。
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from app.services import (
    PhotoImportError,
    PhotoImportMode,
    PhotoImportReport,
    PhotoImportService,
    PhotoScanResult,
)

logger = logging.getLogger(__name__)

FIELD_OPTIONS: tuple[tuple[str, str], ...] = (
    ("student_no", "学号"),
    ("id_number", "证件号码"),
    ("candidate_no", "考生号"),
)


class PhotoImportDialog(QDialog):
    """扫描文件夹、按字段匹配学生并逐条导入照片。"""

    def __init__(
        self,
        service: PhotoImportService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.scan_result: PhotoScanResult | None = None
        self.last_report: PhotoImportReport | None = None

        self.setWindowTitle("导入照片")
        self.resize(720, 520)

        layout = QVBoxLayout(self)

        source_box = QGroupBox("导入范围", self)
        source_layout = QGridLayout(source_box)

        source_layout.addWidget(QLabel("照片文件夹：", source_box), 0, 0)
        self.folder_edit = QLineEdit(source_box)
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setPlaceholderText("请选择包含照片文件的文件夹")
        source_layout.addWidget(self.folder_edit, 0, 1)
        self.browse_button = QPushButton("选择文件夹…", source_box)
        self.browse_button.clicked.connect(self.choose_folder)
        source_layout.addWidget(self.browse_button, 0, 2)

        source_layout.addWidget(QLabel("关联字段：", source_box), 1, 0)
        field_row = QHBoxLayout()
        self.field_group = QButtonGroup(self)
        self._field_buttons: dict[str, QRadioButton] = {}
        for index, (field, label) in enumerate(FIELD_OPTIONS):
            button = QRadioButton(label, source_box)
            self.field_group.addButton(button, index)
            self._field_buttons[field] = button
            field_row.addWidget(button)
            button.toggled.connect(self._reset_scan)
        field_row.addStretch(1)
        source_layout.addLayout(field_row, 1, 1, 1, 2)
        layout.addWidget(source_box)

        actions = QHBoxLayout()
        self.scan_button = QPushButton("扫描匹配", self)
        self.scan_button.clicked.connect(self.scan)
        actions.addWidget(self.scan_button)
        self.import_button = QPushButton("开始导入", self)
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self.start_import)
        actions.addWidget(self.import_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        # 默认选中必须在 _reset_scan 引用的控件全部创建完成后再设置，
        # 否则 setChecked() 会同步触发 toggled 信号并访问尚不存在的 import_button。
        self._field_buttons["student_no"].setChecked(True)

        self.summary_label = QLabel("请选择照片文件夹与关联字段后点击“扫描匹配”。", self)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.detail_view = QPlainTextEdit(self)
        self.detail_view.setReadOnly(True)
        layout.addWidget(self.detail_view, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ 交互

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择照片文件夹")
        if not folder:
            return
        self.folder_edit.setText(folder)
        self._reset_scan()

    def current_field(self) -> str:
        for field, button in self._field_buttons.items():
            if button.isChecked():
                return field
        return "student_no"

    def _reset_scan(self) -> None:
        self.scan_result = None
        self.import_button.setEnabled(False)

    def scan(self) -> None:
        folder = self.folder_edit.text().strip()
        if not folder:
            self._warn("请先选择照片文件夹")
            return
        try:
            result = self.service.scan(folder, self.current_field())
        except PhotoImportError as exc:
            self._warn(str(exc))
            return
        except Exception:  # noqa: BLE001 - 界面不得因扫描异常崩溃
            logger.exception("扫描照片文件夹时发生未预期错误")
            self._warn("扫描照片文件夹失败，请查看日志后重试")
            return

        self.scan_result = result
        self.import_button.setEnabled(result.has_candidates)
        self._render_scan(result)

    def start_import(self) -> None:
        scan = self.scan_result
        if scan is None or not scan.has_candidates:
            self._warn("没有可导入的照片，请先扫描匹配")
            return

        mode = PhotoImportMode.OVERWRITE
        if scan.existing_photo_count:
            choice = self._ask_existing_photo(scan.existing_photo_count)
            if choice is None:
                self.summary_label.setText("已取消导入，未写入任何数据。")
                return
            mode = choice

        try:
            report = self.service.import_photos(scan, mode=mode)
        except PhotoImportError as exc:
            self._warn(str(exc))
            return
        except Exception:  # noqa: BLE001 - 界面不得因导入异常崩溃
            logger.exception("批量导入照片时发生未预期错误")
            self._warn("批量导入照片失败，请查看日志后重试")
            return

        self.last_report = report
        # 本轮候选已消费，再次导入需要重新扫描，避免重复写入。
        self.import_button.setEnabled(False)
        self._render_report(report)
        QMessageBox.information(self, "照片导入结果", report.message)

    def _ask_existing_photo(self, count: int) -> PhotoImportMode | None:
        box = QMessageBox(self)
        box.setWindowTitle("已有照片")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            f"匹配到 {count} 名已有照片的学生，请选择处理方式。"
        )
        overwrite = box.addButton("覆盖已有照片", QMessageBox.ButtonRole.AcceptRole)
        skip = box.addButton(
            "跳过已有照片的学生", QMessageBox.ButtonRole.AcceptRole
        )
        cancel = box.addButton("取消导入", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()

        clicked = box.clickedButton()
        if clicked is overwrite:
            return PhotoImportMode.OVERWRITE
        if clicked is skip:
            return PhotoImportMode.SKIP_EXISTING
        return None

    # ------------------------------------------------------------------ 展示

    def _render_scan(self, result: PhotoScanResult) -> None:
        lines = [
            f"照片文件夹：{result.folder}",
            f"关联字段：{result.field_label}",
            f"可导入学生：{result.candidate_count}"
            f"（其中已有照片 {result.existing_photo_count} 人）",
            f"无匹配文件：{len(result.unmatched_files)}",
            f"字段值歧义：{len(result.ambiguous_files)}",
            f"文件无效：{len(result.invalid_files)}",
            f"同一学生多文件：{len(result.duplicate_files)}",
            f"已忽略文件：{len(result.ignored_files)}",
        ]
        self.summary_label.setText("\n".join(lines))

        details: list[str] = []
        if result.unmatched_files:
            details.append("【无匹配文件】")
            details.extend(issue.describe() for issue in result.unmatched_files)
        if result.ambiguous_files:
            details.append("【字段值歧义】")
            details.extend(issue.describe() for issue in result.ambiguous_files)
        if result.invalid_files:
            details.append("【文件无效】")
            details.extend(issue.describe() for issue in result.invalid_files)
        if result.duplicate_files:
            details.append("【同一学生多文件】")
            details.extend(issue.describe() for issue in result.duplicate_files)
        if result.ignored_files:
            details.append("【已忽略文件】")
            details.extend(
                f"{name}：不是 .jpg/.jpeg 文件，已忽略"
                for name in result.ignored_files
            )
        if not details:
            details.append("全部照片文件均已匹配，未发现冲突。")
        self.detail_view.setPlainText("\n".join(details))

    def _render_report(self, report: PhotoImportReport) -> None:
        lines = [
            "照片导入结果",
            f"成功：{report.imported_count}",
            f"失败：{report.failed_count}",
            f"跳过：{report.skipped_count}",
            f"忽略文件：{report.ignored_count}",
        ]
        self.summary_label.setText("\n".join(lines))

        details: list[str] = []
        if report.imported:
            details.append("【成功】")
            for item in report.imported:
                line = (
                    f"学号 {item.student_no}（{item.student_name}）："
                    f"{item.file_name} → {item.photo_path}"
                )
                if item.cleanup_failed and item.cleanup_message:
                    line += f"（{item.cleanup_message}）"
                details.append(line)
        if report.failed:
            details.append("【失败】")
            details.extend(issue.describe() for issue in report.failed)
        if report.skipped:
            details.append("【跳过】")
            details.extend(issue.describe() for issue in report.skipped)
        if not report.imported and not report.failed and not report.skipped:
            details.append("没有执行任何照片导入。")
        self.detail_view.setPlainText("\n".join(details))

    def _warn(self, message: str) -> None:
        self.summary_label.setText(message)
        QMessageBox.warning(self, "导入照片", message)
