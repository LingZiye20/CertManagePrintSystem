"""打印对话框测试：模板选择、打印设置、预检阻塞与结果文案。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from app.config.paths import AppPaths
from app.data.db import Database
from app.services import PrintService, TemplateService
from app.ui.print_dialog import PrintDialog
from print_fakes import FakePrintTarget, simple_template

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def service(repository_database: Database, app_paths: AppPaths) -> PrintService:
    templates = TemplateService(repository_database, app_paths)
    return PrintService(repository_database, templates, app_paths)


@pytest.fixture
def records(service: PrintService) -> list[dict]:
    rows: list[dict] = []
    for index in range(1, 4):
        record_id = service.records.create(
            {
                "student_no": f"S-{index:03d}",
                "name": "张三",
                "certificate_no": f"BJ{index:04d}",
                "degree_certificate_no": f"XW{index:04d}",
            }
        )
        row = service.records.get(record_id)
        assert row is not None
        rows.append(row)
    return rows


@pytest.fixture
def template_id(service: PrintService) -> int:
    return service.templates.create_template(simple_template())


@pytest.fixture
def fake_target() -> FakePrintTarget:
    return FakePrintTarget()


def _dialog(
    service: PrintService,
    records: list[dict],
    target: FakePrintTarget | None,
    *,
    initial_type: str = "graduation",
) -> PrintDialog:
    return PrintDialog(
        service,
        records,
        target_factory=lambda template: target,
        initial_type=initial_type,
    )


def test_shows_record_count_and_templates(
    service: PrintService,
    records: list[dict],
    template_id: int,
    fake_target: FakePrintTarget,
    qapp: QApplication,
) -> None:
    dialog = _dialog(service, records, fake_target)

    assert "3" in dialog.record_count_label.text()
    assert dialog.template_combo.count() == 1
    assert "打印模板" in dialog.template_combo.itemText(0)
    assert dialog.print_button.isEnabled() is False
    dialog.close()


def test_configure_printer_enables_printing(
    service: PrintService,
    records: list[dict],
    template_id: int,
    fake_target: FakePrintTarget,
    qapp: QApplication,
) -> None:
    dialog = _dialog(service, records, fake_target)

    assert dialog.configure_printer() is True

    assert "测试打印机" in dialog.printer_label.text()
    assert dialog.print_button.isEnabled() is True
    # 永远提示无法获取硬件边距。
    assert "硬边距" in dialog.warnings_label.text()
    dialog.close()


def test_cancelled_print_settings_writes_nothing(
    service: PrintService,
    records: list[dict],
    template_id: int,
    qapp: QApplication,
) -> None:
    dialog = _dialog(service, records, None)

    assert dialog.configure_printer() is False

    assert "取消" in dialog.status_label.text()
    assert dialog.print_button.isEnabled() is False
    assert dialog.start_printing() is None
    assert service.print_records.count_records() == 0
    dialog.close()


def test_start_printing_uses_services_and_reports_unknown(
    service: PrintService,
    records: list[dict],
    template_id: int,
    fake_target: FakePrintTarget,
    qapp: QApplication,
) -> None:
    dialog = _dialog(service, records, fake_target)
    dialog.configure_printer()

    result = dialog.start_printing()

    assert result is not None
    assert [attempt.record_id for attempt in result.attempts] == [
        record["id"] for record in records
    ]
    assert result.unknown_count == 3
    assert "渲染完成 3" in result.message
    assert "无法确认" in result.message
    assert "无法确认" in dialog.summary_label.text()
    assert service.print_records.count_records() == 3
    dialog.close()


def test_missing_certificate_number_blocks(
    service: PrintService,
    records: list[dict],
    template_id: int,
    fake_target: FakePrintTarget,
    qapp: QApplication,
) -> None:
    records[0]["certificate_no"] = None
    dialog = _dialog(service, records, fake_target)

    dialog.configure_printer()

    assert dialog.print_button.isEnabled() is False
    assert "证书编号" in dialog.warnings_label.text()
    assert dialog.start_printing() is None
    assert service.print_records.count_records() == 0
    dialog.close()


def test_type_switch_without_templates_disables(
    service: PrintService,
    records: list[dict],
    template_id: int,
    fake_target: FakePrintTarget,
    qapp: QApplication,
) -> None:
    dialog = _dialog(service, records, fake_target)
    dialog.configure_printer()

    index = dialog.type_combo.findData("degree")
    dialog.type_combo.setCurrentIndex(index)

    assert dialog.template_combo.count() == 0
    assert dialog.print_button.isEnabled() is False
    assert "没有可用模板" in dialog.status_label.text()
    dialog.close()


def test_builtin_fallback_when_database_has_no_template(
    service: PrintService,
    records: list[dict],
    app_paths: AppPaths,
    qapp: QApplication,
) -> None:
    source = PROJECT_ROOT / "resources" / "templates"
    target_dir = app_paths.resource_dir / "templates"
    target_dir.mkdir(parents=True, exist_ok=True)
    for item in source.glob("*.json"):
        shutil.copy(item, target_dir / item.name)

    fake = FakePrintTarget(paper_mm=(297.0, 210.0))
    dialog = _dialog(service, records, fake, initial_type="degree")

    assert dialog.template_combo.count() == 1
    assert "内置资源" in dialog.template_combo.itemText(0)
    assert dialog.configure_printer() is True
    result = dialog.start_printing()
    assert result is not None
    assert result.template_id is None
    batch_log = service.logs.get(result.batch_log_id)
    assert batch_log is not None
    assert batch_log["target_id"] is None
    dialog.close()


def test_start_without_settings_is_rejected(
    service: PrintService,
    records: list[dict],
    template_id: int,
    fake_target: FakePrintTarget,
    qapp: QApplication,
) -> None:
    dialog = _dialog(service, records, fake_target)

    assert dialog.start_printing() is None
    assert "打印设置" in dialog.status_label.text()
    assert service.print_records.count_records() == 0
    dialog.close()


def test_audit_failure_is_reported_in_summary(
    service: PrintService,
    records: list[dict],
    template_id: int,
    fake_target: FakePrintTarget,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*args: object, **kwargs: object) -> int:
        raise RuntimeError("审计库不可写")

    monkeypatch.setattr(service.print_records, "create", _boom)
    dialog = _dialog(service, records, fake_target)
    dialog.configure_printer()

    result = dialog.start_printing()

    assert result is not None
    assert "审计不完整" in result.message
    assert "审计" in dialog.summary_label.text()
    dialog.close()


def test_empty_records_block_printing(
    service: PrintService,
    template_id: int,
    fake_target: FakePrintTarget,
    qapp: QApplication,
) -> None:
    dialog = _dialog(service, [], fake_target)

    assert dialog.print_button.isEnabled() is False
    assert dialog.configure_printer() is False
    assert dialog.record_count_label.text().startswith("待打印 0")
    dialog.close()


def test_close_without_printing_writes_nothing(
    service: PrintService,
    records: list[dict],
    template_id: int,
    fake_target: FakePrintTarget,
    qapp: QApplication,
) -> None:
    dialog = _dialog(service, records, fake_target)
    dialog.configure_printer()

    dialog.close()

    assert service.print_records.count_records() == 0
