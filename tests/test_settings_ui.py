"""系统设置界面测试。"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QMessageBox

from app.data.db import Database
from app.services import (
    DEFAULT_CERTIFICATE_TYPE_KEY,
    PAGE_SIZE_KEY,
    ServiceError,
    SettingsService,
    SettingsValidationError,
)
from app.ui.settings_dialog import SettingsDialog


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def service(repository_database: Database) -> SettingsService:
    return SettingsService(repository_database)


def test_dialog_loads_current_values(qapp: QApplication, service: SettingsService) -> None:
    service.update({PAGE_SIZE_KEY: 50, DEFAULT_CERTIFICATE_TYPE_KEY: "degree"})

    dialog = SettingsDialog(service)

    assert dialog.page_size_combo.currentData() == 50
    assert dialog.certificate_type_combo.currentData() == "degree"
    assert dialog.detail_label.text() == ""


def test_dialog_shows_invalid_stored_value_warning(
    qapp: QApplication, service: SettingsService, repository_database: Database
) -> None:
    with repository_database.transaction() as connection:
        connection.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, '999')", (PAGE_SIZE_KEY,)
        )

    dialog = SettingsDialog(service)

    assert "999" in dialog.warning_label.text()
    assert dialog.page_size_combo.currentData() == 20


def test_save_persists_and_reports_success(
    qapp: QApplication, service: SettingsService, repository_database: Database
) -> None:
    dialog = SettingsDialog(service)
    dialog.page_size_combo.setCurrentIndex(
        dialog.page_size_combo.findData(100)
    )
    dialog.certificate_type_combo.setCurrentIndex(
        dialog.certificate_type_combo.findData("other")
    )

    assert dialog.save_settings() is True
    snapshot = SettingsService(repository_database).load()
    assert snapshot.values[PAGE_SIZE_KEY] == 100
    assert snapshot.values[DEFAULT_CERTIFICATE_TYPE_KEY] == "other"
    assert "已保存" in dialog.detail_label.text()


def test_save_failure_shows_message_and_keeps_dialog(
    qapp: QApplication, service: SettingsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = SettingsDialog(service)

    def broken(changes: object) -> None:
        raise SettingsValidationError("保存被拒绝")

    monkeypatch.setattr(service, "update", broken)

    assert dialog.save_settings() is False
    assert "保存被拒绝" in dialog.detail_label.text()


def test_service_error_is_displayed(
    qapp: QApplication, service: SettingsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = SettingsDialog(service)

    def broken(changes: object) -> None:
        raise ServiceError("数据库不可用")

    monkeypatch.setattr(service, "update", broken)

    assert dialog.save_settings() is False
    assert "数据库不可用" in dialog.detail_label.text()


def test_restore_defaults_requires_confirmation_and_keeps_business_data(
    qapp: QApplication,
    service: SettingsService,
    repository_database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service.update({PAGE_SIZE_KEY: 100})
    with repository_database.transaction() as connection:
        connection.execute(
            "INSERT INTO certificate_records (student_no) VALUES ('S-1')"
        )
    dialog = SettingsDialog(service)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    assert dialog.restore_defaults() is True

    assert SettingsService(repository_database).load().values[PAGE_SIZE_KEY] == 20
    with repository_database.connect() as connection:
        total = connection.execute(
            "SELECT COUNT(*) AS total FROM certificate_records"
        ).fetchone()
    assert total["total"] == 1
    assert "默认" in dialog.detail_label.text()


def test_restore_defaults_cancel_changes_nothing(
    qapp: QApplication,
    service: SettingsService,
    repository_database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service.update({PAGE_SIZE_KEY: 100})
    dialog = SettingsDialog(service)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )

    assert dialog.restore_defaults() is False
    assert SettingsService(repository_database).load().values[PAGE_SIZE_KEY] == 100


def test_cancel_button_writes_nothing(
    qapp: QApplication, service: SettingsService, repository_database: Database
) -> None:
    dialog = SettingsDialog(service)
    dialog.page_size_combo.setCurrentIndex(dialog.page_size_combo.findData(100))

    dialog.reject()

    assert SettingsService(repository_database).load().values[PAGE_SIZE_KEY] == 20


def test_restart_hint_is_visible(qapp: QApplication, service: SettingsService) -> None:
    dialog = SettingsDialog(service)

    texts = " ".join(
        label.text() for label in dialog.findChildren(QLabel)
    )
    assert "重启" in texts
    assert "系统重置" in texts


def test_widgets_only_offer_allowed_values(qapp: QApplication, service: SettingsService) -> None:
    dialog = SettingsDialog(service)

    page_sizes = {
        dialog.page_size_combo.itemData(index)
        for index in range(dialog.page_size_combo.count())
    }
    types = {
        dialog.certificate_type_combo.itemData(index)
        for index in range(dialog.certificate_type_combo.count())
    }
    assert page_sizes == {20, 50, 100}
    assert types == {"graduation", "degree", "other"}
    assert dialog.findChildren(QComboBox)
