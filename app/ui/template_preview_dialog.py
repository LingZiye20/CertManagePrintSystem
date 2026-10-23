"""证书模板预览对话框。

只调用 ``CertificateRenderer.render_to_image()`` 展示结果，不实现第二套文本或
照片绘制逻辑；预览使用示例数据与屏幕 DPI，不写回模板，也不访问学生记录。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.services.template_model import CertificateTemplate
from app.services.template_renderer import CertificateRenderer, RenderResult

PREVIEW_DPI = 96
MAX_PREVIEW_WIDTH = 900
MAX_PREVIEW_HEIGHT = 620

# 明确标记的示例数据；日期等字段保持原始字符串，不做格式转换。
SAMPLE_VALUES: Mapping[str, Any] = {
    "graduation_batch": "2026届",
    "graduation_review": "通过",
    "degree_review": "通过",
    "student_type": "普通",
    "college_number": "示例院号",
    "degree_category_name": "示例学位类别",
    "professional_category": "示例专业类别",
    "certificate_no": "GX2026000001",
    "degree_certificate_no": "XW2026000001",
    "candidate_no": "264500000001",
    "province_code": "45",
    "student_no": "0020240001",
    "name": "示例姓名",
    "gender": "女",
    "birth_date": "2004-05-20",
    "id_type": "居民身份证",
    "political_status": "共青团员",
    "ethnicity": "汉族",
    "institution_code": "12345",
    "institution_name": "示例院校",
    "major_code": "080901",
    "major_name": "示例专业",
    "branch": "示例分院",
    "faculty_name": "示例学院",
    "class_name": "示例班级",
    "education_level": "本科",
    "study_years": "4",
    "study_mode": "普通全日制",
    "current_grade": "2022",
    "enrollment_date": "2022-09-01",
    "graduation_date": "2026-07-01",
    "registration_status": "已注册",
    "remarks": "示例备注",
    "other_certificate_no": "QT2026000001",
}


class TemplatePreviewDialog(QDialog):
    """展示一份模板的渲染结果与告警。"""

    def __init__(
        self,
        renderer: CertificateRenderer,
        template: CertificateTemplate,
        values: Mapping[str, Any] | None = None,
        parent: QWidget | None = None,
        *,
        dpi: int = PREVIEW_DPI,
    ) -> None:
        super().__init__(parent)
        self.template = template
        self.values = dict(values) if values is not None else dict(SAMPLE_VALUES)
        self.setWindowTitle("模板预览")
        self.resize(940, 760)

        self.result: RenderResult = renderer.render_to_image(
            template, self.values, photo_file=None, dpi=dpi
        )

        layout = QVBoxLayout(self)

        self.hint_label = QLabel(
            "示例数据预览：不读取学生记录、不写入数据库；"
            "日期等业务字段按原始字符串显示，照片元素在无当前记录时显示缺失告警。",
            self,
        )
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: #444444;")
        layout.addWidget(self.hint_label)

        self.image_label = QLabel(self)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scaled = QPixmap.fromImage(self.result.image).scaled(
            MAX_PREVIEW_WIDTH,
            MAX_PREVIEW_HEIGHT,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)
        self.image_label.setMinimumSize(scaled.size())

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.image_label)
        layout.addWidget(scroll, 1)

        self.warnings_label = QLabel(self)
        self.warnings_label.setWordWrap(True)
        self.warnings_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        if self.result.warnings:
            self.warnings_label.setStyleSheet("color: #b00020;")
            self.warnings_label.setText(
                "\n".join(
                    f"{warning.element_id}｜{warning.code}｜{warning.message}"
                    for warning in self.result.warnings
                )
            )
        else:
            self.warnings_label.setStyleSheet("color: #2e7d32;")
            self.warnings_label.setText("渲染无告警。")
        layout.addWidget(self.warnings_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
