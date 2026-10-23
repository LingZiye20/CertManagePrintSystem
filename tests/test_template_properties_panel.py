"""元素属性面板测试：字段白名单、二选一来源、毫米数值与只读状态。"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from app.data.db import EXCEL_FIELD_NAMES
from app.services.template_model import FontSpec, PhotoElement, TextElement
from app.ui.template_properties_panel import TemplatePropertiesPanel


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qapp: QApplication) -> TemplatePropertiesPanel:
    widget = TemplatePropertiesPanel()
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close()


def _text_element() -> TextElement:
    return TextElement(
        id="name_value",
        x_mm=12.5,
        y_mm=20.0,
        width_mm=40.0,
        height_mm=10.0,
        font=FontSpec(family="Microsoft YaHei", size_pt=14.0, bold=True, italic=False),
        align="center",
        vertical_align="middle",
        wrap="none",
        field="name",
    )


def _photo_element() -> PhotoElement:
    return PhotoElement(
        id="photo",
        x_mm=200.0,
        y_mm=30.0,
        width_mm=35.0,
        height_mm=45.0,
        fit="contain",
    )


# ---------------------------------------------------------------- 字段清单


def test_field_combo_contains_all_business_fields(panel: TemplatePropertiesPanel) -> None:
    assert panel.field_keys() == list(EXCEL_FIELD_NAMES)
    assert "photo_path" not in panel.field_keys()
    assert panel.field_combo.count() == 37


def test_source_switch_enables_only_selected_input(panel: TemplatePropertiesPanel) -> None:
    panel.set_element(_text_element())

    panel.field_radio.setChecked(True)
    assert panel.field_combo.isEnabled() is True
    assert panel.text_input.isEnabled() is False

    panel.text_radio.setChecked(True)
    assert panel.field_combo.isEnabled() is False
    assert panel.text_input.isEnabled() is True


# ---------------------------------------------------------------- 载入元素


def test_set_text_element_populates_controls(panel: TemplatePropertiesPanel) -> None:
    panel.set_element(_text_element())

    assert panel.element_id_label.text() == "name_value"
    assert panel.x_spin.value() == pytest.approx(12.5)
    assert panel.y_spin.value() == pytest.approx(20.0)
    assert panel.width_spin.value() == pytest.approx(40.0)
    assert panel.height_spin.value() == pytest.approx(10.0)
    assert panel.size_spin.value() == pytest.approx(14.0)
    assert panel.bold_check.isChecked() is True
    assert panel.italic_check.isChecked() is False
    assert panel.align_combo.currentData() == "center"
    assert panel.vertical_align_combo.currentData() == "middle"
    assert panel.wrap_combo.currentData() == "none"
    assert panel.field_radio.isChecked() is True
    assert panel.field_combo.currentData() == "name"
    assert panel.text_group.isVisibleTo(panel) is True


def test_set_photo_element_populates_controls(panel: TemplatePropertiesPanel) -> None:
    panel.set_element(_photo_element())

    assert panel.element_id_label.text() == "photo"
    assert panel.x_spin.value() == pytest.approx(200.0)
    assert panel.fit_combo.currentData() == "contain"
    assert panel.photo_group.isVisibleTo(panel) is True


def test_no_element_disables_apply(panel: TemplatePropertiesPanel) -> None:
    panel.set_element(None)
    assert panel.apply_button.isEnabled() is False


# ---------------------------------------------------------------- 生成字典


def test_build_text_dict_uses_field_only(panel: TemplatePropertiesPanel) -> None:
    panel.set_element(_text_element())
    panel.field_radio.setChecked(True)
    panel.field_combo.setCurrentIndex(panel.field_combo.findData("student_no"))
    panel.x_spin.setValue(15.0)

    candidate = panel.build_element_dict()

    assert candidate["type"] == "text"
    assert candidate["field"] == "student_no"
    assert "text" not in candidate
    assert candidate["x_mm"] == pytest.approx(15.0)
    assert candidate["align"] == "center"
    assert candidate["vertical_align"] == "middle"
    assert candidate["wrap"] == "none"
    assert candidate["font"]["bold"] is True


def test_build_text_dict_uses_static_text_only(panel: TemplatePropertiesPanel) -> None:
    panel.set_element(_text_element())
    panel.text_radio.setChecked(True)
    panel.text_input.setText("  证书  ")

    candidate = panel.build_element_dict()

    assert candidate["text"] == "  证书  "
    assert "field" not in candidate


def test_build_photo_dict_omits_text_keys(panel: TemplatePropertiesPanel) -> None:
    panel.set_element(_photo_element())
    panel.fit_combo.setCurrentIndex(panel.fit_combo.findData("cover"))

    candidate = panel.build_element_dict()

    assert candidate == {
        "id": "photo",
        "type": "photo",
        "x_mm": 200.0,
        "y_mm": 30.0,
        "width_mm": 35.0,
        "height_mm": 45.0,
        "fit": "cover",
    }


def test_apply_button_emits_signal(panel: TemplatePropertiesPanel) -> None:
    panel.set_element(_text_element())
    calls: list[bool] = []
    panel.apply_requested.connect(lambda: calls.append(True))

    panel.apply_button.click()

    assert calls == [True]


# ---------------------------------------------------------------- 错误与只读


def test_error_text_is_visible_and_clearable(panel: TemplatePropertiesPanel) -> None:
    panel.show_error("elements[0].width_mm 必须大于 0")
    assert "width_mm" in panel.error_text()
    assert panel.error_label.isVisibleTo(panel)

    panel.clear_error()
    assert panel.error_text() == ""


def test_read_only_disables_editing_controls(panel: TemplatePropertiesPanel) -> None:
    panel.set_element(_text_element())
    panel.set_read_only(True)

    for widget in (
        panel.x_spin,
        panel.y_spin,
        panel.width_spin,
        panel.height_spin,
        panel.size_spin,
        panel.field_combo,
        panel.text_input,
        panel.bold_check,
        panel.align_combo,
        panel.apply_button,
    ):
        assert widget.isEnabled() is False, widget

    panel.set_read_only(False)
    assert panel.apply_button.isEnabled() is True


def test_numeric_inputs_use_mm_with_one_decimal(panel: TemplatePropertiesPanel) -> None:
    for spin in (panel.x_spin, panel.y_spin, panel.width_spin, panel.height_spin):
        assert spin.decimals() == 1
        assert spin.suffix() == " mm"
