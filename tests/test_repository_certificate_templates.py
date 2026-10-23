"""CertificateTemplateRepository 测试：模板读写与内置模板唯一约束。"""

from __future__ import annotations

import sqlite3

import pytest

from app.data.db import Database
from app.repositories import CertificateTemplateRepository


@pytest.fixture
def repo(repository_database: Database) -> CertificateTemplateRepository:
    return CertificateTemplateRepository(repository_database)


def _create(repo: CertificateTemplateRepository, **overrides: object) -> int:
    values: dict[str, object] = {
        "name": "自定义模板",
        "certificate_type": "graduation",
        "canvas_width_mm": 210.0,
        "canvas_height_mm": 297.0,
        "template_json": '{"elements": []}',
    }
    values.update(overrides)
    return repo.create(**values)


def test_no_default_templates_are_seeded(repo: CertificateTemplateRepository) -> None:
    assert repo.list_all() == []
    for certificate_type in ("graduation", "degree", "other"):
        assert repo.get_builtin(certificate_type) is None


def test_create_and_query(repo: CertificateTemplateRepository) -> None:
    template_id = _create(repo)

    row = repo.get(template_id)
    assert row["id"] == template_id
    assert row["name"] == "自定义模板"
    assert row["canvas_width_mm"] == 210.0
    assert row["is_builtin"] == 0

    assert [item["id"] for item in repo.list_by_type("graduation")] == [template_id]
    assert repo.list_by_type("degree") == []
    assert [item["id"] for item in repo.list_all()] == [template_id]
    assert repo.get(999999) is None


def test_builtin_template_lookup_and_uniqueness(repo: CertificateTemplateRepository) -> None:
    graduation_builtin = _create(repo, name="内置毕结业模板", is_builtin=1)
    degree_builtin = _create(repo, name="内置学位模板", certificate_type="degree", is_builtin=1)

    assert repo.get_builtin("graduation")["id"] == graduation_builtin
    assert repo.get_builtin("degree")["id"] == degree_builtin

    with pytest.raises(sqlite3.IntegrityError):
        _create(repo, name="第二个内置毕结业模板", is_builtin=1)

    # 同类型的非内置模板可以有多个。
    first_custom = _create(repo, name="自定义一")
    second_custom = _create(repo, name="自定义二")
    assert {item["id"] for item in repo.list_by_type("graduation")} == {
        graduation_builtin,
        first_custom,
        second_custom,
    }


def test_update_and_delete(repo: CertificateTemplateRepository) -> None:
    template_id = _create(repo)

    assert repo.update(template_id, {"name": "改名", "canvas_width_mm": 100.5}) is True
    row = repo.get(template_id)
    assert row["name"] == "改名"
    assert row["canvas_width_mm"] == 100.5

    assert repo.update(template_id, {}) is False
    assert repo.update(999999, {"name": "不存在"}) is False

    assert repo.delete(template_id) is True
    assert repo.get(template_id) is None
    assert repo.delete(template_id) is False


def test_update_rejects_non_writable_fields(repo: CertificateTemplateRepository) -> None:
    template_id = _create(repo)

    for changes in ({"id": 2}, {"created_at": "2026-10-22T00:00:00.000Z"}, {"unknown": "x"}):
        with pytest.raises(ValueError):
            repo.update(template_id, changes)


def test_invalid_values_propagate(repo: CertificateTemplateRepository) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _create(repo, canvas_width_mm=0)
    with pytest.raises(sqlite3.IntegrityError):
        _create(repo, certificate_type="unknown")
    with pytest.raises(sqlite3.IntegrityError):
        _create(repo, name="  ")

    assert repo.list_all() == []


def test_pagination_validation(repo: CertificateTemplateRepository) -> None:
    _create(repo)

    with pytest.raises(ValueError):
        repo.list_all(limit=0)
    with pytest.raises(ValueError):
        repo.list_by_type("graduation", offset=-1)
