"""NumberingRuleRepository 测试：默认规则、更新与约束传播。"""

from __future__ import annotations

import sqlite3

import pytest

from app.data.db import Database, initialize_database
from app.repositories import NumberingRuleRepository
from app.repositories.numbering_rules import WRITABLE_FIELDS


@pytest.fixture
def repo(repository_database: Database) -> NumberingRuleRepository:
    return NumberingRuleRepository(repository_database)


def test_writable_fields_exclude_removed_columns() -> None:
    assert WRITABLE_FIELDS == (
        "fixed_text_1",
        "fixed_text_2",
        "fixed_text_3",
        "fixed_text_4",
        "sequence_width",
        "education_levels",
    )
    assert "rule_name" not in WRITABLE_FIELDS
    assert "enabled" not in WRITABLE_FIELDS


def test_default_rules_are_readable(repo: NumberingRuleRepository) -> None:
    rows = repo.list_all()

    assert [row["rule_key"] for row in rows] == ["degree", "graduation", "other"]
    graduation = repo.get("graduation")
    assert graduation["sequence_width"] == 4
    assert graduation["fixed_text_1"] == ""
    assert graduation["education_levels"] == "[]"
    # v4 起行内不再有规则名称与启用开关。
    assert "rule_name" not in graduation
    assert "enabled" not in graduation
    assert repo.exists("other") is True
    assert repo.get("unknown") is None
    assert repo.exists("unknown") is False


def test_update_rule_persists(repo: NumberingRuleRepository) -> None:
    assert (
        repo.update(
            "graduation",
            {
                "fixed_text_1": "GX",
                "fixed_text_4": "2026",
                "sequence_width": 6,
                "education_levels": '["本科"]',
            },
        )
        is True
    )

    row = repo.get("graduation")
    assert row["fixed_text_1"] == "GX"
    assert row["fixed_text_4"] == "2026"
    assert row["sequence_width"] == 6
    assert row["education_levels"] == '["本科"]'
    # 其他规则不受影响。
    assert repo.get("degree")["sequence_width"] == 4


@pytest.mark.parametrize(
    "changes",
    [
        {"rule_key": "degree"},
        {"rule_name": "新名称"},
        {"enabled": 0},
        {"unknown": "x"},
    ],
)
def test_update_rejects_primary_key_and_removed_fields(
    repo: NumberingRuleRepository, changes: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        repo.update("graduation", changes)


def test_update_missing_or_empty_changes(repo: NumberingRuleRepository) -> None:
    assert repo.update("unknown", {"sequence_width": 5}) is False
    assert repo.update("graduation", {}) is False


def test_invalid_values_propagate(repo: NumberingRuleRepository) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        repo.update("graduation", {"sequence_width": 0})

    # 失败后原值保持不变。
    assert repo.get("graduation")["sequence_width"] == 4


def test_reinitialization_does_not_overwrite_user_changes(
    repo: NumberingRuleRepository, repository_database: Database
) -> None:
    repo.update(
        "graduation",
        {"sequence_width": 6, "fixed_text_1": "GX", "education_levels": '["本科"]'},
    )

    initialize_database(repository_database.db_path)

    row = repo.get("graduation")
    assert row["sequence_width"] == 6
    assert row["fixed_text_1"] == "GX"
    assert row["education_levels"] == '["本科"]'
