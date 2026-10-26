"""证书编码界面测试（离屏渲染、临时数据库）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from app.bootstrap import bootstrap
from app.config.paths import AppPaths
from app.services import NumberingService
from app.ui.main_window import MainWindow
from app.ui.numbering_rule_dialog import (
    LEVEL_HINT_EMPTY,
    START_SEQUENCE_HINT,
    NumberingRuleDialog,
)


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class _FakeMessageBox:
    """测试中屏蔽模态提示框，并可脚本化确认框返回值。"""

    StandardButton = QMessageBox.StandardButton
    question_reply = QMessageBox.StandardButton.Yes
    question_calls: list[tuple[str, str]] = []
    information_calls: list[tuple[str, str]] = []
    warning_calls: list[tuple[str, str]] = []

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

    @classmethod
    def warning(cls, parent: object, title: str, text: str) -> None:
        cls.warning_calls.append((title, text))

    @classmethod
    def information(cls, parent: object, title: str, text: str) -> None:
        cls.information_calls.append((title, text))

    @staticmethod
    def critical(*args: object, **kwargs: object) -> None:
        return None


@pytest.fixture
def silent_message_box(
    monkeypatch: pytest.MonkeyPatch,
) -> type[_FakeMessageBox]:
    _FakeMessageBox.question_reply = QMessageBox.StandardButton.Yes
    _FakeMessageBox.question_calls = []
    _FakeMessageBox.information_calls = []
    _FakeMessageBox.warning_calls = []
    monkeypatch.setattr(
        "app.ui.numbering_rule_dialog.QMessageBox", _FakeMessageBox
    )
    return _FakeMessageBox


def _record(numbering_service: NumberingService, student_no: str, **values: object) -> dict:
    record_id = numbering_service.records.create(
        {
            "student_no": student_no,
            "name": "测试",
            "education_level": "本科",
            **values,
        }
    )
    record = numbering_service.records.get(record_id)
    assert record is not None
    return record


def _all_ids(numbering_service: NumberingService) -> list[int]:
    return numbering_service.records.list_ids(order_by="student_no")


def _all_ids_closure(numbering_service: NumberingService):
    return lambda: _all_ids(numbering_service)


def _success_logs(numbering_service: NumberingService) -> list[dict]:
    return numbering_service.logs.list_logs(
        operation_type="certificate_number_generate"
    )


def _dialog(
    numbering_service: NumberingService,
    *,
    on_numbered=None,
    ids_provider=None,
) -> NumberingRuleDialog:
    return NumberingRuleDialog(
        numbering_service,
        record_ids_provider=(
            ids_provider
            if ids_provider is not None
            else _all_ids_closure(numbering_service)
        ),
        education_level_provider=numbering_service.list_education_levels,
        on_numbered=on_numbered,
    )


def _levels(numbering_service: NumberingService, levels: list[str]) -> None:
    numbering_service.update_rule("graduation", {"education_levels": levels})


# ---------------------------------------------------------------- 规则对话框


def test_rule_dialog_removes_name_enabled_and_save_button(
    numbering_service: NumberingService, qapp: QApplication
) -> None:
    dialog = NumberingRuleDialog(numbering_service)

    assert dialog.windowTitle() == "证书编码"
    assert dialog.current_type() == "graduation"
    assert dialog.width_input.text() == "4"
    # 规则名称、启用开关、保存按钮与「下一流水号」均已移除。
    assert not hasattr(dialog, "rule_name_input")
    assert not hasattr(dialog, "enabled_check")
    assert not hasattr(dialog, "save_button")
    assert not hasattr(dialog, "next_value_label")
    assert dialog.sequence_input.text() == ""
    assert dialog.sequence_input.isReadOnly() is False
    assert dialog.number_button.isEnabled() is False
    assert dialog.selected_education_levels() == []
    assert dialog.level_hint_label.text() == LEVEL_HINT_EMPTY

    dialog.close()


@pytest.mark.parametrize("text", ["", "0", "abc"])
def test_sequence_input_requires_positive_integer(
    numbering_service: NumberingService, qapp: QApplication, text: str
) -> None:
    dialog = NumberingRuleDialog(numbering_service)
    dialog.sequence_input.setText(text)

    assert dialog.start_sequence() is None

    dialog.sequence_input.setText("100")
    assert dialog.start_sequence() == 100

    dialog.close()


def test_switching_type_autosaves_and_clears_sequence(
    numbering_service: NumberingService, qapp: QApplication
) -> None:
    dialog = _dialog(numbering_service)
    dialog.fixed_text_inputs[0].setText("GX")
    dialog.width_input.setText("6")
    dialog.sequence_input.setText("100")

    dialog.type_combo.setCurrentIndex(dialog.type_combo.findData("degree"))

    # 切换前已静默保存到旧类型。
    view = numbering_service.get_rule("graduation")
    assert view.fixed_texts[0] == "GX"
    assert view.sequence_width == 6
    assert "规则已自动保存" in dialog.status_label.text()
    # 切换成功后清空流水号输入与预览。
    assert dialog.current_type() == "degree"
    assert dialog.sequence_input.text() == ""
    assert dialog.preview_label.text() == "预览编号：-"

    dialog.close()


def test_switching_type_aborts_when_autosave_fails(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: type[_FakeMessageBox],
) -> None:
    dialog = _dialog(numbering_service)
    dialog.width_input.setText("21")

    dialog.type_combo.setCurrentIndex(dialog.type_combo.findData("degree"))

    # 保存失败：提示原因、类型回退、数据库原值不变。
    assert silent_message_box.warning_calls
    assert "1 到 20" in silent_message_box.warning_calls[0][1]
    assert dialog.current_type() == "graduation"
    assert numbering_service.get_rule("graduation").sequence_width == 4

    dialog.width_input.setText("4")
    dialog.close()


def test_closing_autosaves_current_rule(
    numbering_service: NumberingService, qapp: QApplication
) -> None:
    dialog = _dialog(numbering_service)
    dialog.fixed_text_inputs[1].setText("2026")

    dialog.reject()

    assert numbering_service.get_rule("graduation").fixed_texts[1] == "2026"
    assert numbering_service.get_last_type() == "graduation"


def test_closing_aborts_when_autosave_fails(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: type[_FakeMessageBox],
) -> None:
    dialog = _dialog(numbering_service)
    dialog.show()
    qapp.processEvents()
    dialog.width_input.setText("21")

    dialog.reject()

    assert silent_message_box.warning_calls
    assert dialog.isVisible() is True
    assert numbering_service.get_rule("graduation").sequence_width == 4

    dialog.width_input.setText("4")
    dialog.close()


def test_preview_does_not_save(
    numbering_service: NumberingService, qapp: QApplication
) -> None:
    dialog = _dialog(numbering_service)
    dialog.fixed_text_inputs[0].setText("GX")
    dialog.width_input.setText("6")
    dialog.sequence_input.setText("100")

    assert dialog.preview() is True
    assert "GX000100" in dialog.preview_label.text()

    view = numbering_service.get_rule("graduation")
    assert view.fixed_texts[0] == ""
    assert view.sequence_width == 4
    assert "自动保存" not in dialog.status_label.text()

    dialog.width_input.setText("4")
    dialog.fixed_text_inputs[0].clear()
    dialog.close()


def test_preview_without_sequence_reports_hint(
    numbering_service: NumberingService, qapp: QApplication
) -> None:
    dialog = NumberingRuleDialog(numbering_service)
    dialog.sequence_input.setText("0")

    assert dialog.preview() is False
    assert "无法计算" in dialog.preview_label.text()
    assert dialog.status_label.text() == START_SEQUENCE_HINT

    dialog.close()


def test_default_type_uses_last_remembered_type(
    numbering_service: NumberingService, qapp: QApplication
) -> None:
    numbering_service.remember_last_type("degree")
    dialog = NumberingRuleDialog(numbering_service)

    assert dialog.current_type() == "degree"

    dialog.close()


def test_default_type_falls_back_for_invalid_setting(
    numbering_service: NumberingService, qapp: QApplication, silent_message_box: type[_FakeMessageBox]
) -> None:
    numbering_service.settings.set_value("numbering.last_rule_key", "invalid")
    dialog = NumberingRuleDialog(numbering_service)

    assert dialog.current_type() == "graduation"

    dialog.close()


# ---------------------------------------------------------------- 层次选择


def test_level_candidates_and_saved_selection_are_loaded(
    numbering_service: NumberingService, qapp: QApplication
) -> None:
    _record(numbering_service, "S-1", education_level="本科")
    _record(numbering_service, "S-2", education_level="专科")
    _levels(numbering_service, ["本科"])

    dialog = _dialog(numbering_service)

    texts = [
        dialog.level_list.item(row).text()
        for row in range(dialog.level_list.count())
    ]
    assert texts == ["专科", "本科"]
    assert dialog.selected_education_levels() == ["本科"]
    assert dialog.number_button.isEnabled() is True

    dialog.select_all_levels_button.click()
    assert dialog.selected_education_levels() == ["专科", "本科"]

    dialog.clear_all_levels_button.click()
    assert dialog.selected_education_levels() == []
    assert dialog.number_button.isEnabled() is False
    assert dialog.level_hint_label.text() == LEVEL_HINT_EMPTY

    dialog.close()


def test_level_selection_is_autosaved_on_close(
    numbering_service: NumberingService, qapp: QApplication
) -> None:
    _record(numbering_service, "S-1", education_level="本科")
    _record(numbering_service, "S-2", education_level="专科")

    dialog = _dialog(numbering_service)
    dialog.select_all_levels_button.click()
    dialog.reject()

    assert numbering_service.get_rule("graduation").education_levels == (
        "专科",
        "本科",
    )


def test_number_button_disabled_without_selected_levels(
    numbering_service: NumberingService, qapp: QApplication
) -> None:
    _record(numbering_service, "S-1")

    dialog = _dialog(numbering_service)

    assert dialog.number_button.isEnabled() is False
    assert dialog.number_button.toolTip() == LEVEL_HINT_EMPTY
    assert dialog.level_hint_label.text() == LEVEL_HINT_EMPTY

    dialog.close()


# ---------------------------------------------------------------- 批量编号


def test_number_all_requires_manual_sequence(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: type[_FakeMessageBox],
) -> None:
    _record(numbering_service, "S-1")
    _levels(numbering_service, ["本科"])
    dialog = _dialog(numbering_service)

    assert dialog.number_all() is False
    _title, text = silent_message_box.warning_calls[0]
    assert text == START_SEQUENCE_HINT
    assert silent_message_box.question_calls == []
    assert numbering_service.records.get_by_student_no("S-1")["certificate_no"] is None

    dialog.close()


def test_number_all_autosaves_before_numbering(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: type[_FakeMessageBox],
) -> None:
    _record(numbering_service, "S-1")
    _levels(numbering_service, ["本科"])
    dialog = _dialog(numbering_service)
    dialog.fixed_text_inputs[0].setText("GX")
    dialog.sequence_input.setText("100")

    assert dialog.number_all() is True

    # 编号前自动保存，且不再出现「规则未保存」提示。
    assert numbering_service.get_rule("graduation").fixed_texts[0] == "GX"
    assert silent_message_box.question_calls[0][0] == "确认批量编号"
    assert (
        numbering_service.records.get_by_student_no("S-1")["certificate_no"]
        == "GX0100"
    )

    dialog.close()


def test_number_all_cancel_confirmation_does_not_write(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: type[_FakeMessageBox],
) -> None:
    _record(numbering_service, "S-1")
    _record(numbering_service, "S-2", education_level="专科")
    _levels(numbering_service, ["本科"])
    dialog = _dialog(numbering_service)
    dialog.sequence_input.setText("100")
    silent_message_box.question_reply = QMessageBox.StandardButton.No

    assert dialog.number_all() is False

    title, text = silent_message_box.question_calls[0]
    assert title == "确认批量编号"
    assert "应用层次：本科" in text
    assert "当前筛选结果：2 条" in text
    assert "命中层次条件：1 条" in text
    assert "起始流水号（手动输入）：100" in text
    assert "0100" in text
    assert "不可撤销" in text
    assert "已取消" in dialog.status_label.text()
    assert dialog.sequence_input.text() == "100"

    assert numbering_service.records.get_by_student_no("S-1")["certificate_no"] is None

    dialog.close()


def test_number_all_numbers_from_manual_start_and_clears_input(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: type[_FakeMessageBox],
) -> None:
    for student_no in ("S-1", "S-2", "S-3"):
        _record(numbering_service, student_no)
    _levels(numbering_service, ["本科"])
    refreshed: list[bool] = []
    dialog = _dialog(
        numbering_service, on_numbered=lambda: refreshed.append(True)
    )
    dialog.sequence_input.setText("100")

    assert dialog.number_all() is True

    assert refreshed == [True]
    numbers = [
        numbering_service.records.get_by_student_no(student_no)["certificate_no"]
        for student_no in ("S-1", "S-2", "S-3")
    ]
    assert numbers == ["0100", "0101", "0102"]
    assert dialog.sequence_input.text() == ""
    assert dialog.preview_label.text() == "预览编号：-"
    assert "流水号输入已清空" in dialog.status_label.text()

    title, text = silent_message_box.information_calls[0]
    assert title == "编号完成"
    assert "成功编号：3 条" in text
    assert "编号起始值：0100" in text
    assert "编号结束值：0102" in text
    assert "下一流水号" not in text

    dialog.close()


def test_number_all_skips_records_that_already_have_numbers(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: type[_FakeMessageBox],
) -> None:
    _record(numbering_service, "S-1")
    _record(numbering_service, "S-2", certificate_no="OLD-002")
    _record(numbering_service, "S-3")
    _levels(numbering_service, ["本科"])
    dialog = _dialog(numbering_service)
    dialog.sequence_input.setText("100")

    assert dialog.number_all() is True

    rows = {
        student_no: numbering_service.records.get_by_student_no(student_no)
        for student_no in ("S-1", "S-2", "S-3")
    }
    assert rows["S-1"]["certificate_no"] == "0100"
    assert rows["S-2"]["certificate_no"] == "OLD-002"
    assert rows["S-3"]["certificate_no"] == "0101"

    dialog.close()


def test_number_all_skips_records_outside_selected_levels(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: type[_FakeMessageBox],
) -> None:
    _record(numbering_service, "S-1", education_level="本科")
    _record(numbering_service, "S-2", education_level="专科")
    _record(numbering_service, "S-3", education_level=None)
    _levels(numbering_service, ["本科"])
    dialog = _dialog(numbering_service)
    dialog.sequence_input.setText("100")

    assert dialog.number_all() is True

    rows = {
        student_no: numbering_service.records.get_by_student_no(student_no)
        for student_no in ("S-1", "S-2", "S-3")
    }
    assert rows["S-1"]["certificate_no"] == "0100"
    assert rows["S-2"]["certificate_no"] is None
    assert rows["S-3"]["certificate_no"] is None
    assert "跳过（层次不符）：2 条" in silent_message_box.information_calls[0][1]

    dialog.close()


def test_number_all_reports_empty_filter_result(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: type[_FakeMessageBox],
) -> None:
    _levels(numbering_service, ["本科"])
    dialog = _dialog(numbering_service, ids_provider=lambda: [])
    dialog.sequence_input.setText("100")

    assert dialog.number_all() is False

    title, text = silent_message_box.information_calls[0]
    assert title == "无需编号"
    assert "当前筛选结果为空，无可编号记录" in text
    assert silent_message_box.question_calls == []
    assert dialog.sequence_input.text() == "100"

    dialog.close()


def test_number_all_reports_no_matching_level(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: type[_FakeMessageBox],
) -> None:
    _record(numbering_service, "S-1", education_level="专科")
    _levels(numbering_service, ["本科"])
    dialog = _dialog(numbering_service)
    dialog.sequence_input.setText("100")

    assert dialog.number_all() is False

    title, text = silent_message_box.information_calls[0]
    assert title == "无需编号"
    assert "没有符合层次条件" in text
    assert silent_message_box.question_calls == []
    assert _success_logs(numbering_service) == []

    dialog.close()


def test_number_all_reports_all_records_already_numbered(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: type[_FakeMessageBox],
) -> None:
    _record(numbering_service, "S-1", certificate_no="0001")
    _record(numbering_service, "S-2", certificate_no="0002")
    _levels(numbering_service, ["本科"])
    dialog = _dialog(numbering_service)
    dialog.sequence_input.setText("100")

    assert dialog.number_all() is False

    title, text = silent_message_box.information_calls[0]
    assert title == "无需编号"
    assert "无需编号" in text
    assert silent_message_box.question_calls == []
    assert _success_logs(numbering_service) == []

    dialog.close()


def test_number_all_reports_conflict_and_keeps_sequence(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: type[_FakeMessageBox],
) -> None:
    numbering_service.update_rule(
        "graduation", {"fixed_text_1": "GX", "education_levels": ["本科"]}
    )
    _record(numbering_service, "S-0", certificate_no="GX0101")
    _record(numbering_service, "S-1")
    _record(numbering_service, "S-2")
    dialog = _dialog(numbering_service)
    dialog.sequence_input.setText("100")

    assert dialog.number_all() is False

    _title, text = silent_message_box.warning_calls[0]
    assert "已存在" in text
    assert numbering_service.records.get_by_student_no("S-1")["certificate_no"] is None
    assert numbering_service.records.get_by_student_no("S-2")["certificate_no"] is None
    assert silent_message_box.information_calls == []
    assert dialog.sequence_input.text() == "100"

    dialog.close()


# ---------------------------------------------------------------- 清空编号


def test_clear_numbers_autosaves_and_cancel_does_not_clear(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: type[_FakeMessageBox],
) -> None:
    _record(numbering_service, "S-1", certificate_no="BJ-001")
    dialog = _dialog(numbering_service)
    dialog.fixed_text_inputs[0].setText("GX")
    silent_message_box.question_reply = QMessageBox.StandardButton.No

    assert dialog.clear_numbers() is False

    # 清空前已自动保存表单。
    assert numbering_service.get_rule("graduation").fixed_texts[0] == "GX"
    title, text = silent_message_box.question_calls[0]
    assert title == "确认清空编号"
    assert "全部 1 条记录" in text
    assert "手动输入起始值" in text
    assert "重置为 1" not in text
    assert "已取消" in dialog.status_label.text()
    assert numbering_service.records.get_by_student_no("S-1")["certificate_no"] == "BJ-001"

    dialog.close()


def test_clear_numbers_clears_all_and_keeps_manual_sequence(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: type[_FakeMessageBox],
) -> None:
    _record(numbering_service, "S-1", certificate_no="BJ-001")
    _record(numbering_service, "S-2", certificate_no="BJ-002", education_level="专科")
    refreshed: list[bool] = []
    dialog = _dialog(
        numbering_service, on_numbered=lambda: refreshed.append(True)
    )
    dialog.sequence_input.setText("100")

    assert dialog.clear_numbers() is True

    assert refreshed == [True]
    assert numbering_service.records.get_by_student_no("S-1")["certificate_no"] is None
    assert numbering_service.records.get_by_student_no("S-2")["certificate_no"] is None
    # 清空编号不清空用户已输入的流水号。
    assert dialog.sequence_input.text() == "100"
    assert "0100" in dialog.preview_label.text()

    title, text = silent_message_box.information_calls[0]
    assert title == "清空完成"
    assert "清空条数：2 条" in text
    assert "手动输入起始值" in text
    assert "下一流水号" not in text

    dialog.close()


def test_clear_numbers_reports_when_nothing_to_clear(
    numbering_service: NumberingService,
    qapp: QApplication,
    silent_message_box: type[_FakeMessageBox],
) -> None:
    _record(numbering_service, "S-1")
    dialog = _dialog(numbering_service)

    assert dialog.clear_numbers() is False

    title, text = silent_message_box.information_calls[0]
    assert title == "无需清空"
    assert "没有可清空的编号" in text
    assert silent_message_box.question_calls == []

    dialog.close()


# ---------------------------------------------------------------- 主窗口入口


def test_main_window_wires_certificate_code_menu(
    qapp: QApplication, tmp_path: Path
) -> None:
    context = bootstrap(AppPaths.for_program(tmp_path))
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings, context=context)

    assert window.numbering_service is not None
    assert window.student_page is not None
    menu_titles = [action.text() for action in window.menuBar().actions()]
    assert "证书编码(&C)" in menu_titles
    assert "编号管理(&N)" not in menu_titles

    window.close()
    settings.sync()
