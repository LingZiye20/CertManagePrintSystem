"""模板预览对话框测试：复用渲染器、示例数据提示与 warning 展示。"""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.services.template_model import (
    CertificateTemplate,
    FontSpec,
    PhotoElement,
    TextElement,
)
from app.services.template_renderer import CertificateRenderer, RenderResult
from app.ui.template_preview_dialog import SAMPLE_VALUES, TemplatePreviewDialog


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _template(*elements: TextElement | PhotoElement) -> CertificateTemplate:
    return CertificateTemplate(
        name="预览模板",
        certificate_type="graduation",
        canvas_width_mm=297.0,
        canvas_height_mm=210.0,
        elements=elements,
    )


def _text(field: str = "name") -> TextElement:
    return TextElement(
        id="name_value",
        x_mm=20.0,
        y_mm=20.0,
        width_mm=60.0,
        height_mm=12.0,
        font=FontSpec(family="Microsoft YaHei", size_pt=14.0),
        field=field,
    )


def _photo() -> PhotoElement:
    return PhotoElement(
        id="photo", x_mm=200.0, y_mm=60.0, width_mm=35.0, height_mm=45.0
    )


def test_preview_renders_through_certificate_renderer(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = _template(_text())
    calls: list[dict[str, Any]] = []

    def _fake_render(self, template_arg, values, *, photo_file=None, dpi=300):
        calls.append(
            {
                "template": template_arg,
                "values": values,
                "photo_file": photo_file,
                "dpi": dpi,
            }
        )
        image = QImage(100, 80, QImage.Format.Format_ARGB32)
        image.fill(0xFFFFFFFF)
        return RenderResult(
            image=image, width_px=100, height_px=80, dpi=dpi, warnings=()
        )

    monkeypatch.setattr(CertificateRenderer, "render_to_image", _fake_render)

    dialog = TemplatePreviewDialog(CertificateRenderer(), template, SAMPLE_VALUES)

    assert len(calls) == 1
    assert calls[0]["template"] == template
    assert calls[0]["values"] == SAMPLE_VALUES
    assert calls[0]["photo_file"] is None
    assert calls[0]["dpi"] == 96
    assert dialog.result.width_px == 100
    assert dialog.image_label.pixmap().isNull() is False
    dialog.close()


def test_preview_image_matches_mm_at_screen_dpi(qapp: QApplication) -> None:
    dialog = TemplatePreviewDialog(CertificateRenderer(), _template(_text()), SAMPLE_VALUES)

    assert dialog.result.width_px == round(297.0 / 25.4 * 96)
    assert dialog.result.height_px == round(210.0 / 25.4 * 96)
    dialog.close()


def test_preview_shows_missing_photo_warning(qapp: QApplication) -> None:
    dialog = TemplatePreviewDialog(CertificateRenderer(), _template(_photo()), SAMPLE_VALUES)

    text = dialog.warnings_label.text()
    assert "missing_photo" in text
    assert "photo" in text
    assert dialog.warnings_label.isVisibleTo(dialog) is True
    dialog.close()


def test_preview_shows_missing_field_warning(qapp: QApplication) -> None:
    dialog = TemplatePreviewDialog(
        CertificateRenderer(), _template(_text(field="name")), {}
    )

    assert "missing_field" in dialog.warnings_label.text()
    assert "name" in dialog.warnings_label.text()
    dialog.close()


def test_preview_marks_sample_data(qapp: QApplication) -> None:
    dialog = TemplatePreviewDialog(CertificateRenderer(), _template(_text()), SAMPLE_VALUES)

    assert "示例" in dialog.hint_label.text()
    dialog.close()


def test_preview_does_not_modify_template(qapp: QApplication) -> None:
    template = _template(_text(), _photo())
    dialog = TemplatePreviewDialog(CertificateRenderer(), template, SAMPLE_VALUES)

    assert dialog.result.image.width() > 0
    # dataclass 不可变，预览只读取模板。
    assert template.elements[0].x_mm == 20.0
    dialog.close()


def test_preview_without_warnings_reports_none(qapp: QApplication) -> None:
    template = _template(_text())
    result_values = {"name": "张三"}

    dialog = TemplatePreviewDialog(CertificateRenderer(), template, result_values)

    assert dialog.result.warnings == ()
    assert "无" in dialog.warnings_label.text()
    dialog.close()
