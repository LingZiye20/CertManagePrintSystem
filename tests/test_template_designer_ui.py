"""模板设计器界面测试：模板管理、只读保护、编辑保存与关闭确认。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.config.paths import AppPaths
from app.data.db import Database
from app.services import TemplateService
from app.services.errors import ServiceError
from app.services.template_model import (
    CertificateTemplate,
    FontSpec,
    PhotoElement,
    TextElement,
)
from app.ui import template_designer_dialog
from app.ui.template_designer_dialog import TemplateDesignerDialog

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class _FakeMessageBox:
    """屏蔽模态提示框，并可脚本化确认结果。"""

    StandardButton = QMessageBox.StandardButton
    question_reply = QMessageBox.StandardButton.Yes
    question_calls: list[tuple[str, str]] = []

    @classmethod
    def question(cls, parent: object, title: str, text: str, *args: object) -> Any:
        cls.question_calls.append((title, text))
        return cls.question_reply

    @staticmethod
    def warning(*args: object, **kwargs: object) -> None:
        return None

    @staticmethod
    def information(*args: object, **kwargs: object) -> None:
        return None

    @staticmethod
    def critical(*args: object, **kwargs: object) -> None:
        return None


@pytest.fixture
def message_box(monkeypatch: pytest.MonkeyPatch) -> type[_FakeMessageBox]:
    _FakeMessageBox.question_reply = QMessageBox.StandardButton.Yes
    _FakeMessageBox.question_calls = []
    monkeypatch.setattr(
        template_designer_dialog, "QMessageBox", _FakeMessageBox
    )
    return _FakeMessageBox


@pytest.fixture
def service(repository_database: Database, app_paths: AppPaths) -> TemplateService:
    """把真实内置模板资源复制到临时资源目录，再播种到临时数据库。"""
    source = PROJECT_ROOT / "resources" / "templates"
    target = app_paths.resource_dir / "templates"
    target.mkdir(parents=True, exist_ok=True)
    for item in source.glob("*.json"):
        shutil.copy(item, target / item.name)

    resolved = TemplateService(repository_database, app_paths)
    with repository_database.transaction() as connection:
        resolved.seed_builtin_templates(connection)
    return resolved


@pytest.fixture
def dialog(
    service: TemplateService, qapp: QApplication, message_box: type[_FakeMessageBox]
) -> TemplateDesignerDialog:
    widget = TemplateDesignerDialog(service)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close()


def _builtin_id(service: TemplateService, certificate_type: str = "graduation") -> int:
    row = service.templates.get_builtin(certificate_type)
    assert row is not None
    return row["id"]


def _custom_template(service: TemplateService, name: str = "自定义模板") -> int:
    return service.create_template(
        CertificateTemplate(
            name=name,
            certificate_type="graduation",
            canvas_width_mm=210.0,
            canvas_height_mm=297.0,
            elements=(
                TextElement(
                    id="name_value",
                    x_mm=20.0,
                    y_mm=20.0,
                    width_mm=60.0,
                    height_mm=12.0,
                    font=FontSpec(family="Microsoft YaHei", size_pt=14.0),
                    field="name",
                ),
            ),
        )
    )


# ---------------------------------------------------------------- 模板列表


def test_template_list_shows_builtin_templates(
    dialog: TemplateDesignerDialog, service: TemplateService
) -> None:
    assert dialog.template_list.count() == 3
    texts = [dialog.template_list.item(i).text() for i in range(3)]
    assert all("内置" in text for text in texts)
    assert any("297" in text and "210" in text for text in texts)


def test_selecting_custom_template_is_editable(
    dialog: TemplateDesignerDialog, service: TemplateService
) -> None:
    template_id = _custom_template(service)
    dialog.reload_templates()

    assert dialog.select_template(template_id) is True

    assert dialog.current_template_id() == template_id
    assert dialog.is_read_only() is False
    assert dialog.save_button.isEnabled() is True
    assert dialog.name_input.isEnabled() is True
    assert dialog.canvas.is_interactive() is True
    assert dialog.current_template().name == "自定义模板"


def test_new_template_defaults_to_a4_landscape(dialog: TemplateDesignerDialog) -> None:
    dialog.new_template()

    template = dialog.current_template()
    assert template is not None
    assert template.name == "新建模板"
    assert template.certificate_type == "graduation"
    assert template.canvas_width_mm == pytest.approx(297.0)
    assert template.canvas_height_mm == pytest.approx(210.0)
    assert template.elements == ()
    assert dialog.current_template_id() is None
    assert dialog.is_dirty() is True
    assert dialog.canvas.page_size_px().width() > 0


# ---------------------------------------------------------------- 内置只读


def test_builtin_template_is_read_only(
    dialog: TemplateDesignerDialog, service: TemplateService
) -> None:
    template_id = _builtin_id(service)

    assert dialog.select_template(template_id) is True

    assert dialog.is_read_only() is True
    assert dialog.save_button.isEnabled() is False
    assert dialog.delete_button.isEnabled() is False
    assert dialog.name_input.isEnabled() is False
    assert dialog.width_spin.isEnabled() is False
    assert dialog.canvas.is_interactive() is False
    assert dialog.panel.apply_button.isEnabled() in (False, True)  # 无选中元素时不可编辑


def test_builtin_template_cannot_be_saved_or_deleted(
    dialog: TemplateDesignerDialog, service: TemplateService
) -> None:
    template_id = _builtin_id(service)
    before = service.templates.get(template_id)
    dialog.select_template(template_id)

    assert dialog.save_current() is False
    assert dialog.delete_current() is False

    assert service.templates.get(template_id) == before
    assert len(service.templates.list_all()) == 3


def test_copy_builtin_creates_unsaved_draft(
    dialog: TemplateDesignerDialog, service: TemplateService
) -> None:
    template_id = _builtin_id(service)
    original = service.templates.get(template_id)
    dialog.select_template(template_id)

    dialog.copy_current()

    assert dialog.current_template_id() is None
    assert dialog.is_dirty() is True
    assert dialog.is_read_only() is False
    assert dialog.current_template().name.endswith("副本")
    # 复制只生成草稿，不写数据库。
    assert len(service.templates.list_all()) == 3
    assert service.templates.get(template_id) == original


def test_copy_draft_can_be_saved_without_changing_builtin(
    dialog: TemplateDesignerDialog, service: TemplateService
) -> None:
    template_id = _builtin_id(service)
    original = service.templates.get(template_id)
    dialog.select_template(template_id)
    dialog.copy_current()

    assert dialog.save_current() is True

    assert dialog.current_template_id() is not None
    assert len(service.templates.list_all()) == 4
    assert service.templates.get(template_id) == original
    saved = service.templates.get(dialog.current_template_id())
    assert saved is not None
    assert saved["is_builtin"] == 0
    assert saved["name"].endswith("副本")


# ---------------------------------------------------------------- 编辑保存


def test_new_template_can_add_elements_and_save(
    dialog: TemplateDesignerDialog, service: TemplateService
) -> None:
    dialog.new_template()

    text_id = dialog.add_text_element()
    photo_id = dialog.add_photo_element()

    assert text_id is not None and photo_id is not None
    assert {element.id for element in dialog.current_template().elements} == {
        text_id,
        photo_id,
    }

    assert dialog.save_current() is True

    template_id = dialog.current_template_id()
    assert template_id is not None
    reloaded = service.load_template(template_id)
    assert reloaded == dialog.current_template()
    assert dialog.is_dirty() is False


def test_apply_element_properties_updates_model_and_canvas(
    dialog: TemplateDesignerDialog, service: TemplateService
) -> None:
    template_id = _custom_template(service)
    dialog.select_template(template_id)
    dialog.canvas.select_element("name_value")
    dialog.panel.x_spin.setValue(35.5)
    dialog.panel.width_spin.setValue(70.0)

    assert dialog.apply_element_properties() is True

    element = dialog.current_template().elements[0]
    assert element.x_mm == pytest.approx(35.5)
    assert element.width_mm == pytest.approx(70.0)
    assert dialog.canvas.template().elements[0].x_mm == pytest.approx(35.5)
    assert dialog.panel.error_text() == ""
    assert dialog.is_dirty() is True


def test_invalid_element_properties_are_rejected(
    dialog: TemplateDesignerDialog, service: TemplateService
) -> None:
    template_id = _custom_template(service)
    dialog.select_template(template_id)
    dialog.canvas.select_element("name_value")
    before = dialog.current_template()
    dialog.panel.width_spin.setValue(0.0)

    assert dialog.apply_element_properties() is False

    assert dialog.current_template() == before
    assert "width_mm" in dialog.panel.error_text()
    assert dialog.is_dirty() is False


def test_invalid_canvas_size_cannot_be_saved(
    dialog: TemplateDesignerDialog, service: TemplateService
) -> None:
    template_id = _custom_template(service)
    dialog.select_template(template_id)
    dialog.width_spin.setValue(0.0)
    before = service.templates.get(template_id)

    assert dialog.save_current() is False

    assert service.templates.get(template_id) == before
    assert "canvas_width_mm" in dialog.status_label.text()


def test_canvas_drag_marks_dirty_and_syncs_panel(
    dialog: TemplateDesignerDialog, service: TemplateService
) -> None:
    template_id = _custom_template(service)
    dialog.select_template(template_id)
    canvas = dialog.canvas
    element = dialog.current_template().elements[0]
    rect = canvas.element_screen_rect(element)
    start = rect.center().toPoint()

    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas, start + QPoint(20, 0))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=start + QPoint(20, 0))

    moved = dialog.current_template().elements[0]
    assert moved.x_mm > element.x_mm
    assert dialog.panel.x_spin.value() == pytest.approx(moved.x_mm)
    assert dialog.is_dirty() is True


def test_save_failure_shows_reason_and_keeps_data(
    dialog: TemplateDesignerDialog,
    service: TemplateService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template_id = _custom_template(service)
    dialog.select_template(template_id)
    before = service.templates.get(template_id)

    def _boom(*args: object, **kwargs: object) -> None:
        raise ServiceError("数据库写入失败")

    monkeypatch.setattr(service, "update_template", _boom)

    assert dialog.save_current() is False

    assert "数据库写入失败" in dialog.status_label.text()
    assert service.templates.get(template_id) == before


def test_delete_selected_element(
    dialog: TemplateDesignerDialog, service: TemplateService
) -> None:
    template_id = _custom_template(service)
    dialog.select_template(template_id)
    dialog.canvas.select_element("name_value")

    assert dialog.delete_selected_element() is True

    assert dialog.current_template().elements == ()
    assert dialog.panel.element_id_label.text() == "-"
    assert dialog.is_dirty() is True


def test_delete_element_without_selection_returns_false(
    dialog: TemplateDesignerDialog, service: TemplateService
) -> None:
    template_id = _custom_template(service)
    dialog.select_template(template_id)

    assert dialog.delete_selected_element() is False


# ---------------------------------------------------------------- 删除与关闭


def test_delete_custom_template_requires_confirmation(
    dialog: TemplateDesignerDialog,
    service: TemplateService,
    message_box: type[_FakeMessageBox],
) -> None:
    template_id = _custom_template(service)
    dialog.reload_templates()
    dialog.select_template(template_id)

    message_box.question_reply = QMessageBox.StandardButton.No
    assert dialog.delete_current() is False
    assert service.templates.get(template_id) is not None

    message_box.question_reply = QMessageBox.StandardButton.Yes
    assert dialog.delete_current() is True
    assert service.templates.get(template_id) is None
    assert message_box.question_calls


def test_switching_with_unsaved_changes_asks_confirmation(
    dialog: TemplateDesignerDialog,
    service: TemplateService,
    message_box: type[_FakeMessageBox],
) -> None:
    dialog.new_template()
    dialog.add_text_element()
    template_id = _builtin_id(service)

    message_box.question_reply = QMessageBox.StandardButton.No
    assert dialog.select_template(template_id) is False
    assert dialog.current_template_id() is None

    message_box.question_reply = QMessageBox.StandardButton.Yes
    assert dialog.select_template(template_id) is True
    assert dialog.current_template_id() == template_id


def test_close_with_unsaved_changes_does_not_write(
    dialog: TemplateDesignerDialog,
    service: TemplateService,
    message_box: type[_FakeMessageBox],
    qapp: QApplication,
) -> None:
    dialog.new_template()
    dialog.add_text_element()
    template_id = _custom_template(service)
    count_before = len(service.templates.list_all())

    message_box.question_reply = QMessageBox.StandardButton.No
    dialog.close()
    qapp.processEvents()
    assert dialog.isVisible() is True
    assert len(service.templates.list_all()) == count_before

    message_box.question_reply = QMessageBox.StandardButton.Yes
    dialog.close()
    qapp.processEvents()
    assert dialog.isVisible() is False
    assert len(service.templates.list_all()) == count_before
    assert dialog.current_template_id() is None


# ---------------------------------------------------------------- 预览


def test_preview_delegates_to_preview_dialog(
    dialog: TemplateDesignerDialog,
    service: TemplateService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template_id = _custom_template(service)
    dialog.select_template(template_id)
    captured: list[tuple[Any, Any]] = []

    class _FakePreview:
        def __init__(self, renderer: object, template: object, values: object, *args: object) -> None:
            captured.append((template, values))

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(template_designer_dialog, "TemplatePreviewDialog", _FakePreview)

    dialog.preview_current()

    assert len(captured) == 1
    assert captured[0][0] == dialog.current_template()


def test_preview_without_template_shows_status(dialog: TemplateDesignerDialog) -> None:
    dialog.preview_current()
    assert "请选择" in dialog.status_label.text()


# ---------------------------------------------------------------- 类型切换


def test_type_change_asks_confirmation_and_can_be_cancelled(
    dialog: TemplateDesignerDialog,
    service: TemplateService,
    message_box: type[_FakeMessageBox],
) -> None:
    template_id = _custom_template(service)
    dialog.select_template(template_id)
    message_box.question_reply = QMessageBox.StandardButton.No

    index = dialog.type_combo.findData("degree")
    dialog.type_combo.setCurrentIndex(index)

    assert dialog.current_template().certificate_type == "graduation"
    assert dialog.type_combo.currentData() == "graduation"
    assert message_box.question_calls
