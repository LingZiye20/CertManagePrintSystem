"""NumberSequenceRepository 测试：三类流水独立与约束。"""

from __future__ import annotations

import sqlite3

import pytest

from app.data.db import Database
from app.repositories import NumberSequenceRepository


@pytest.fixture
def repo(repository_database: Database) -> NumberSequenceRepository:
    return NumberSequenceRepository(repository_database)


def test_default_sequences_are_independent(repo: NumberSequenceRepository) -> None:
    rows = repo.list_all()

    assert [row["sequence_key"] for row in rows] == ["degree", "graduation", "other"]
    assert all(row["next_value"] == 1 for row in rows)
    assert repo.exists("degree") is True
    assert repo.get("unknown") is None


def test_update_one_sequence_does_not_affect_others(repo: NumberSequenceRepository) -> None:
    assert repo.update_next_value("degree", 5) is True

    assert repo.get("degree")["next_value"] == 5
    assert repo.get("graduation")["next_value"] == 1
    assert repo.get("other")["next_value"] == 1


def test_update_missing_sequence_returns_false(repo: NumberSequenceRepository) -> None:
    assert repo.update_next_value("unknown", 5) is False


def test_invalid_next_value_propagates(repo: NumberSequenceRepository) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        repo.update_next_value("degree", 0)

    assert repo.get("degree")["next_value"] == 1
