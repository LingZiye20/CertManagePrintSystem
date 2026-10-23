"""编号规则与编号生成界面测试（离屏渲染、临时数据库）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from app.bootstrap import bootstrap
from app.config.paths import AppPaths
from app.services import NumberingService, StudentService
from app.ui.main_window import MainWindow
from app.ui.numbering_dialog import NumberingDialog
from app.ui.numbering_rule_dialog import NumberingRuleDialog
from app.ui.student_page import StudentPage


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class _FakeMessageBox:
    """测试中屏蔽模态提示框，并可脚本化确认框返回值。"""

    StandardButton = QMessageBox.StandardButton
    question_reply = QMessageBox.StandardButton.Yes
    question_calls: list[tuple[str, str]] = []

    @classmethod
    def question(
        cls,
        parent: object,
        title: str,
        text: str,
        buttons: object,
        default: object,
    ) -> QMessageBox.StandardButton:
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
def silent_message_box(
    monkeypatch: pytest.MonkeyPatch,
) -> type[_FakeMessageBox]:
    _FakeMessageBox.question_reply = QMessageBox.StandardButton.Yes
    _FakeMessageBox.question_calls = []
    for module in (
        "app.ui.numbering_rule_dialog.QMessageBox",
        "app.ui.numbering_dialog.QMessageBox",
        "app.ui.student_page.QMessageBox",
    ):
        monkeypatch.setattr(module, _FakeMessageBox)
    return _FakeMessageBox


def _record(numbering_service: NumberingService, student_no: str, **values: object) -> dict:
    record_id = numbering_service.records.create(
        {"student_no": student_no, "name": "测试", **values}
    )
    record = numbering_service.records.get(record_id)
    assert record is not None
    return record


# ---------------------------------------------------------------- 规则对话框


def test_rule_dialog_loads_defaults_and_shows_read_only_sequence(
    numbering_service: NumberingService, qapp: QApplication
) -> None:
    dialog = NumberingRuleDialog(numbering_service)

    assert dialog.current_type() == "graduation"
    assert dialog.rule_name_input.text() == "毕结业证书"
    assert dialog.width_input.text() == "4"
    assert dialog.enabled_check.isChecked() is True
    assert "只读" in dialog.next_value_label.text()

    dialog.close()


def test_rule_dialog_saves_and_switches_types(
    numbering_service: NumberingService, qapp: QApplication, silent_message_box: type[_FakeMessageBox],
) -> None:
    dialog = NumberingRuleDialog(numbering_service)
    dialog.fixed_text_inputs[0].setText("  GX 学 ")
    dialog.fixed_text_inputs[1].setText("2026")
    dialog.width_input.setText("6")
    dialog.enabled_check.setChecked(False)

    assert dialog.save() is True

    view = numbering_service.get_rule("graduation")
    assert view.fixed_texts == ("GX 学", "2026", "", "")
    assert view.sequence_width == 6
    assert view.enabled is False
    # 保存规则不会递增流水。
    assert view.next_value == 1

    degree_index = dialog.type_combo.findData("degree")
    dialog.type_combo.setCurrentIndex(degree_index)
    assert dialog.rule_name_input.text() == "学位证书"
    assert dialog.width_input.text() == "4"

    dialog.close()


def test_rule_dialog_rejects_invalid_width(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: type[_FakeMessageBox],
) -> None:
    dialog = NumberingRuleDialog(numbering_service)
    validator = dialog.width_input.validator()
    assert validator.bottom() == 1
    assert validator.top() == 20

    dialog.width_input.setText("21")

    assert dialog.save() is False
    assert numbering_service.get_rule("graduation").sequence_width == 4
    assert "1 到 20" in dialog.status_label.text()

    dialog.close()


def test_rule_dialog_preview_uses_unsaved_form_values(
    numbering_service: NumberingService, qapp: QApplication
) -> None:
    dialog = NumberingRuleDialog(numbering_service)
    assert dialog.preview_button.text() == "预览当前编号"
    assert dialog.preview() is True
    assert "0001" in dialog.preview_label.text()

    dialog.fixed_text_inputs[0].setText(" GX ")
    dialog.fixed_text_inputs[2].setText("2026")
    dialog.width_input.setText("6")

    assert dialog.preview() is True
    assert "GX2026000001" in dialog.preview_label.text()
    assert "未保存" in dialog.status_label.text()

    # 预览不得保存规则、写入记录、写审计或递增流水。
    view = numbering_service.get_rule("graduation")
    assert view.fixed_texts == ("", "", "", "")
    assert view.sequence_width == 4
    assert view.next_value == 1
    assert numbering_service.records.count_records() == 0
    assert numbering_service.logs.count_logs() == 0
    assert numbering_service.sequences.get("graduation")["next_value"] == 1

    dialog.close()


def test_rule_dialog_preview_reports_invalid_width(
    numbering_service: NumberingService, qapp: QApplication
) -> None:
    dialog = NumberingRuleDialog(numbering_service)
    dialog.width_input.setText("21")

    assert dialog.preview() is False
    assert "无法计算" in dialog.preview_label.text()
    assert "1 到 20" in dialog.status_label.text()
    assert numbering_service.get_rule("graduation").sequence_width == 4
    assert numbering_service.sequences.get("graduation")["next_value"] == 1

    dialog.close()


# ---------------------------------------------------------------- 生成对话框


def test_numbering_dialog_preview_does_not_consume_sequence(
    numbering_service: NumberingService, qapp: QApplication
) -> None:
    record = _record(numbering_service, "S-1")

    dialog = NumberingDialog(numbering_service, record)

    assert dialog.preview_label.text() == "0001"
    assert dialog.generate_button.isEnabled() is True
    assert "尚未占用" in dialog.status_label.text()
    assert numbering_service.sequences.get("graduation")["next_value"] == 1
    assert numbering_service.records.get(record["id"])["certificate_no"] is None

    dialog.close()


def test_numbering_dialog_generate_writes_and_notifies(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: type[_FakeMessageBox],
) -> None:
    record = _record(numbering_service, "S-1")
    generated: list[object] = []

    dialog = NumberingDialog(
        numbering_service, record, on_generated=generated.append
    )

    assert dialog.generate() is True

    assert len(generated) == 1
    assert len(silent_message_box.question_calls) == 1
    assert silent_message_box.question_calls[0][0] == "确认生成编号"
    assert dialog.current_number_label.text() == "0001"
    assert dialog.preview_label.text() == "0001"
    assert dialog.generate_button.isEnabled() is False
    assert "已正式生成" in dialog.status_label.text()
    assert numbering_service.records.get(record["id"])["certificate_no"] == "0001"
    assert numbering_service.sequences.get("graduation")["next_value"] == 2

    dialog.close()


def test_numbering_dialog_generate_cancel_does_not_write(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: type[_FakeMessageBox],
) -> None:
    record = _record(numbering_service, "S-1")
    silent_message_box.question_reply = QMessageBox.StandardButton.No

    dialog = NumberingDialog(numbering_service, record)

    assert dialog.generate() is False

    title, text = silent_message_box.question_calls[0]
    assert title == "确认生成编号"
    assert "毕结业证书" in text
    assert "S-1" in text
    assert "0001" in text
    assert "递增流水" in text
    assert "已取消" in dialog.status_label.text()

    assert numbering_service.records.get(record["id"])["certificate_no"] is None
    assert numbering_service.sequences.get("graduation")["next_value"] == 1
    assert numbering_service.logs.count_logs() == 0

    dialog.close()


def test_numbering_dialog_disables_generate_for_existing_number(
    numbering_service: NumberingService, qapp: QApplication
) -> None:
    record = _record(numbering_service, "S-1", certificate_no="OLD-001")

    dialog = NumberingDialog(numbering_service, record)

    assert dialog.generate_button.isEnabled() is False
    assert dialog.current_number_label.text() == "OLD-001"
    assert "不能重复生成" in dialog.status_label.text()

    dialog.close()


def test_numbering_dialog_reports_conflict_without_consuming_sequence(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: None,
) -> None:
    numbering_service.update_rule("graduation", {"fixed_text_1": "GX"})
    _record(numbering_service, "S-1", certificate_no="GX0001")
    record = _record(numbering_service, "S-2")

    dialog = NumberingDialog(numbering_service, record)

    assert dialog.generate() is False
    assert "已存在" in dialog.status_label.text()
    assert numbering_service.records.get(record["id"])["certificate_no"] is None
    assert numbering_service.sequences.get("graduation")["next_value"] == 1

    dialog.close()


def test_numbering_dialog_reports_disabled_rule(
    numbering_service: NumberingService, qapp: QApplication
) -> None:
    numbering_service.update_rule("graduation", {"enabled": False})
    record = _record(numbering_service, "S-1")

    dialog = NumberingDialog(numbering_service, record)

    assert dialog.preview_label.text() == "无法预览"
    assert dialog.generate_button.isEnabled() is False

    dialog.close()


# ---------------------------------------------------------------- 页面接入


def test_student_page_generate_button_requires_selection(
    student_service: StudentService,
    numbering_service: NumberingService,
    qapp: QApplication,
) -> None:
    page = StudentPage(student_service, numbering_service=numbering_service)
    page.show()
    qapp.processEvents()

    assert page.generate_number_button.isEnabled() is False

    student_service.create_record({"student_no": "S-1"})
    page.reload()
    page.table.selectRow(0)
    qapp.processEvents()

    assert page.generate_number_button.isEnabled() is True

    page.close()


def test_student_page_without_numbering_service_disables_generate(
    student_service: StudentService, qapp: QApplication
) -> None:
    page = StudentPage(student_service)
    student_service.create_record({"student_no": "S-1"})
    page.reload()
    page.table.selectRow(0)
    qapp.processEvents()

    assert page.generate_number_button.isEnabled() is False

    page.close()


def test_main_window_wires_numbering_service_and_menu(
    qapp: QApplication, tmp_path: Path
) -> None:
    context = bootstrap(AppPaths.for_program(tmp_path))
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings, context=context)

    assert window.numbering_service is not None
    assert window.student_page is not None
    assert window.centralWidget() is window.student_page
    menu_titles = [action.text() for action in window.menuBar().actions()]
    assert "编号管理(&N)" in menu_titles

    window.close()
    settings.sync()
