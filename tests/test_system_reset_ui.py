"""系统重置界面测试：两步确认、取消无副作用、真实结果展示。"""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.config.paths import AppPaths
from app.data.db import Database
from app.services import ResetService
from app.ui.settings_dialog import SettingsDialog
from app.ui.system_reset_dialog import SystemResetDialog


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def service(repository_database: Database, app_paths: AppPaths) -> ResetService:
    return ResetService(repository_database, app_paths)


def _answer(monkeypatch: pytest.MonkeyPatch, button: QMessageBox.StandardButton) -> None:
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: button)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: button)


def test_scope_and_backup_notice_are_visible(
    qapp: QApplication, service: ResetService
) -> None:
    dialog = SystemResetDialog(service)

    text = dialog.scope_label.text()
    assert "学生" in text
    assert "照片" in text
    assert "QSettings" in text or "界面" in text
    assert "备份" in dialog.notice_label.text()
    assert dialog.run_button.isEnabled()


def test_cancel_before_first_step_does_nothing(
    qapp: QApplication, service: ResetService, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[bool] = []
    monkeypatch.setattr(service, "reset_system", lambda: called.append(True))

    dialog = SystemResetDialog(service)
    dialog.reject()

    assert called == []
    assert dialog.result is None


def test_second_confirmation_cancel_does_nothing(
    qapp: QApplication, service: ResetService, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[bool] = []
    monkeypatch.setattr(service, "reset_system", lambda: called.append(True))
    _answer(monkeypatch, QMessageBox.StandardButton.No)

    dialog = SystemResetDialog(service)
    assert dialog.request_reset() is False

    assert called == []
    assert "取消" in dialog.status_label.text()
    assert "未" in dialog.status_label.text()


def test_confirmed_reset_runs_once_and_reports_result(
    qapp: QApplication, service: ResetService, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[bool] = []

    def fake_reset() -> Any:
        calls.append(True)
        return type(
            "Result",
            (),
            {
                "success": True,
                "message": "系统重置完成：测试",
                "warnings": ("照片暂存目录未能删除",),
                "photos_removed": 0,
                "counts": {},
                "staging_path": None,
                "audit_log_id": 1,
                "audit_saved": True,
            },
        )()

    monkeypatch.setattr(service, "reset_system", fake_reset)
    _answer(monkeypatch, QMessageBox.StandardButton.Yes)

    dialog = SystemResetDialog(service)
    completed: list[object] = []
    dialog.reset_completed.connect(lambda result: completed.append(result))

    assert dialog.request_reset() is True
    assert calls == [True]
    assert len(completed) == 1
    assert "系统重置完成" in dialog.status_label.text()
    assert "照片暂存目录未能删除" in dialog.status_label.text()


def test_failure_result_is_not_reported_as_success(
    qapp: QApplication, service: ResetService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_reset() -> Any:
        return type(
            "Result",
            (),
            {
                "success": False,
                "message": "系统重置失败：数据库已回滚，业务数据未改变",
                "warnings": (),
                "photos_removed": 0,
                "counts": {},
                "staging_path": None,
                "audit_log_id": None,
                "audit_saved": False,
            },
        )()

    monkeypatch.setattr(service, "reset_system", fake_reset)
    _answer(monkeypatch, QMessageBox.StandardButton.Yes)

    dialog = SystemResetDialog(service)
    assert dialog.request_reset() is False

    assert "失败" in dialog.status_label.text()
    assert "完成" not in dialog.status_label.text()


def test_settings_dialog_has_no_system_reset_entry(
    qapp: QApplication, repository_database: Database
) -> None:
    from app.services import SettingsService

    dialog = SettingsDialog(SettingsService(repository_database))

    texts = [button.text() for button in dialog.findChildren(type(dialog.save_button))]
    assert not any("重置系统" in text for text in texts)
