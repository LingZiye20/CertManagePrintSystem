"""AppSettingsRepository 测试：增删改查、upsert 与参数安全。"""

from __future__ import annotations

import sqlite3

import pytest

from app.data.db import Database
from app.repositories import AppSettingsRepository


@pytest.fixture
def repo(repository_database: Database) -> AppSettingsRepository:
    return AppSettingsRepository(repository_database)


def test_create_get_update_delete(repo: AppSettingsRepository) -> None:
    assert repo.create("printer.default", "打印机甲") is None
    assert repo.get("printer.default")["value"] == "打印机甲"
    assert repo.exists("printer.default") is True

    assert repo.update("printer.default", "打印机乙") is True
    assert repo.get("printer.default")["value"] == "打印机乙"

    assert repo.delete("printer.default") is True
    assert repo.get("printer.default") is None
    assert repo.delete("printer.default") is False
    assert repo.exists("printer.default") is False


def test_create_duplicate_propagates(repo: AppSettingsRepository) -> None:
    repo.create("same.key", "v1")

    with pytest.raises(sqlite3.IntegrityError):
        repo.create("same.key", "v2")

    assert repo.get("same.key")["value"] == "v1"


def test_update_missing_returns_false(repo: AppSettingsRepository) -> None:
    assert repo.update("missing.key", "v") is False


def test_set_value_upserts_without_duplicate_rows(repo: AppSettingsRepository) -> None:
    assert repo.set_value("same.key", "v1") is None
    assert repo.get("same.key")["value"] == "v1"

    assert repo.set_value("same.key", "v2") is None
    assert repo.get("same.key")["value"] == "v2"
    assert len(repo.list_all()) == 1


def test_list_all_is_sorted_by_key(repo: AppSettingsRepository) -> None:
    repo.set_value("b.key", "2")
    repo.set_value("a.key", "1")
    repo.set_value("c.key", "3")

    assert [row["key"] for row in repo.list_all()] == ["a.key", "b.key", "c.key"]


def test_values_keep_text_semantics(repo: AppSettingsRepository) -> None:
    repo.set_value("number.like", "00123")
    repo.set_value("boolean.like", "1")
    repo.set_value("empty", "")

    assert repo.get("number.like")["value"] == "00123"
    assert repo.get("boolean.like")["value"] == "1"
    assert repo.get("empty")["value"] == ""


def test_special_characters_are_bound_parameters(repo: AppSettingsRepository) -> None:
    key = "special'); DELETE FROM app_settings;--"
    value = "x'); DROP TABLE app_settings;--"

    repo.set_value(key, value)

    assert repo.get(key)["value"] == value
    assert len(repo.list_all()) == 1


def test_blank_key_propagates(repo: AppSettingsRepository) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        repo.create("  ", "v")
