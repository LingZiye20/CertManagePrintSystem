"""系统设置 Service 测试：默认值、校验、持久化、恢复默认与审计。"""

from __future__ import annotations

import sqlite3

import pytest

from app.data.db import Database
from app.repositories import OperationLogRepository
from app.services import (
    AuditWriteError,
    DEFAULT_CERTIFICATE_TYPE_KEY,
    PAGE_SIZE_KEY,
    SettingsService,
    SettingsValidationError,
)


@pytest.fixture
def service(repository_database: Database) -> SettingsService:
    return SettingsService(repository_database)


@pytest.fixture
def logs(repository_database: Database) -> OperationLogRepository:
    return OperationLogRepository(repository_database)


def test_defaults_when_table_empty(service: SettingsService) -> None:
    snapshot = service.load()

    assert snapshot.values[PAGE_SIZE_KEY] == 20
    assert snapshot.values[DEFAULT_CERTIFICATE_TYPE_KEY] == "graduation"
    assert snapshot.warnings == ()


def test_update_persists_and_reloads(repository_database: Database) -> None:
    SettingsService(repository_database).update(
        {PAGE_SIZE_KEY: 50, DEFAULT_CERTIFICATE_TYPE_KEY: "degree"}
    )

    reloaded = SettingsService(repository_database).load()
    assert reloaded.values[PAGE_SIZE_KEY] == 50
    assert reloaded.values[DEFAULT_CERTIFICATE_TYPE_KEY] == "degree"


@pytest.mark.parametrize("value", [0, 30, "50", True, 50.0])
def test_update_rejects_invalid_page_size(
    service: SettingsService, value: object
) -> None:
    with pytest.raises(SettingsValidationError):
        service.update({PAGE_SIZE_KEY: value})

    assert service.load().values[PAGE_SIZE_KEY] == 20


def test_update_rejects_unknown_certificate_type(service: SettingsService) -> None:
    with pytest.raises(SettingsValidationError):
        service.update({DEFAULT_CERTIFICATE_TYPE_KEY: "master"})

    assert service.load().values[DEFAULT_CERTIFICATE_TYPE_KEY] == "graduation"


def test_update_rejects_unknown_key_and_empty_changes(service: SettingsService) -> None:
    with pytest.raises(SettingsValidationError):
        service.update({"unknown.key": "x"})
    with pytest.raises(SettingsValidationError):
        service.update({})


def test_invalid_stored_value_falls_back_with_warning(
    service: SettingsService, repository_database: Database
) -> None:
    with repository_database.transaction() as connection:
        connection.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            (PAGE_SIZE_KEY, "999"),
        )

    snapshot = service.load()

    assert snapshot.values[PAGE_SIZE_KEY] == 20
    assert any("999" in warning for warning in snapshot.warnings)
    # 非法原值保留，等待用户确认后覆盖，不被静默改写。
    with repository_database.connect() as connection:
        row = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?", (PAGE_SIZE_KEY,)
        ).fetchone()
    assert row["value"] == "999"


def test_unknown_stored_keys_are_ignored(service: SettingsService, repository_database: Database) -> None:
    with repository_database.transaction() as connection:
        connection.execute(
            "INSERT INTO app_settings (key, value) VALUES ('legacy.key', 'x')"
        )

    snapshot = service.load()
    assert "legacy.key" not in snapshot.values
    assert snapshot.warnings == ()


def test_update_writes_success_audit_per_changed_key(
    service: SettingsService, logs: OperationLogRepository
) -> None:
    service.update({PAGE_SIZE_KEY: 50, DEFAULT_CERTIFICATE_TYPE_KEY: "other"})

    records = logs.list_logs(operation_type="settings_update", limit=10)
    assert len(records) == 2
    assert all(record["result"] == "success" for record in records)
    summaries = " ".join(record["summary"] for record in records)
    assert "每页条数" in summaries
    assert "默认证书类型" in summaries


def test_update_reports_unchanged_value_without_noise(
    service: SettingsService, logs: OperationLogRepository
) -> None:
    service.update({PAGE_SIZE_KEY: 20})

    records = logs.list_logs(operation_type="settings_update", limit=10)
    assert len(records) == 1
    assert "20" in records[0]["summary"]


def test_audit_failure_rolls_back_settings(
    service: SettingsService,
    repository_database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*args: object, **kwargs: object) -> int:
        raise sqlite3.Error("boom")

    monkeypatch.setattr(service.logs, "create", broken)

    with pytest.raises(AuditWriteError):
        service.update({PAGE_SIZE_KEY: 50})

    assert SettingsService(repository_database).load().values[PAGE_SIZE_KEY] == 20


def test_validation_failure_writes_independent_failure_audit(
    service: SettingsService, logs: OperationLogRepository
) -> None:
    with pytest.raises(SettingsValidationError):
        service.update({PAGE_SIZE_KEY: 0})

    records = logs.list_logs(operation_type="settings_update", limit=10)
    assert len(records) == 1
    assert records[0]["result"] == "failure"
    assert records[0]["error_message"]


def test_failure_audit_error_does_not_mask_original(
    service: SettingsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> int:
        raise RuntimeError("audit down")

    monkeypatch.setattr(service.logs, "create", broken)

    with pytest.raises(SettingsValidationError):
        service.update({PAGE_SIZE_KEY: 0})


def test_reset_to_defaults_clears_rows_and_audits(
    service: SettingsService,
    repository_database: Database,
    logs: OperationLogRepository,
) -> None:
    service.update({PAGE_SIZE_KEY: 100, DEFAULT_CERTIFICATE_TYPE_KEY: "degree"})

    service.reset_to_defaults()

    snapshot = SettingsService(repository_database).load()
    assert snapshot.values[PAGE_SIZE_KEY] == 20
    assert snapshot.values[DEFAULT_CERTIFICATE_TYPE_KEY] == "graduation"
    with repository_database.connect() as connection:
        total = connection.execute("SELECT COUNT(*) AS total FROM app_settings").fetchone()
    assert total["total"] == 0

    records = logs.list_logs(operation_type="settings_reset", limit=10)
    assert len(records) == 1
    assert records[0]["result"] == "success"


def test_reset_when_already_default_writes_no_audit(
    service: SettingsService, logs: OperationLogRepository
) -> None:
    service.reset_to_defaults()

    assert logs.count_logs(operation_type="settings_reset") == 0


def test_reset_does_not_touch_business_data(
    service: SettingsService, repository_database: Database
) -> None:
    with repository_database.transaction() as connection:
        connection.execute(
            "INSERT INTO certificate_records (student_no, name) VALUES ('S-1', '张三')"
        )

    service.update({PAGE_SIZE_KEY: 50})
    service.reset_to_defaults()

    with repository_database.connect() as connection:
        total = connection.execute(
            "SELECT COUNT(*) AS total FROM certificate_records"
        ).fetchone()
    assert total["total"] == 1


def test_get_rejects_unknown_key(service: SettingsService) -> None:
    with pytest.raises(SettingsValidationError):
        service.get("unknown.key")
