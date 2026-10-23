"""CertificateRecordRepository 测试：基本读写、学号与三类编号语义。"""

from __future__ import annotations

import sqlite3

import pytest

from app.data.db import EXCEL_FIELD_NAMES, Database
from app.repositories import CertificateRecordRepository


@pytest.fixture
def repo(repository_database: Database) -> CertificateRecordRepository:
    return CertificateRecordRepository(repository_database)


def test_create_and_query_by_id_and_student_no(repo: CertificateRecordRepository) -> None:
    record_id = repo.create({"student_no": "00123", "name": "测试甲"})

    row = repo.get(record_id)
    assert row is not None
    assert row["id"] == record_id
    assert row["student_no"] == "00123"
    assert row["name"] == "测试甲"
    assert row["photo_path"] is None
    assert row["created_at"].endswith("Z")

    assert repo.get_by_student_no("00123")["id"] == record_id
    # 前导零是学号的一部分，不能与 "123" 混同。
    assert repo.get_by_student_no("123") is None
    assert repo.get(999999) is None


def test_create_accepts_all_37_fields_and_photo_path(repo: CertificateRecordRepository) -> None:
    values = {name: f"值-{index}" for index, name in enumerate(EXCEL_FIELD_NAMES)}
    values["student_no"] = "00123"
    values["photo_path"] = "00123.jpg"

    record_id = repo.create(values)
    row = repo.get(record_id)
    assert row is not None

    for name in EXCEL_FIELD_NAMES:
        assert row[name] == values[name], f"{name} 未正确持久化"
    assert row["photo_path"] == "00123.jpg"


def test_create_rejects_unknown_or_empty_fields(repo: CertificateRecordRepository) -> None:
    with pytest.raises(ValueError):
        repo.create({"student_no": "00123", "unknown_field": "x"})
    with pytest.raises(ValueError):
        repo.create({})


def test_student_no_required_and_unique(repo: CertificateRecordRepository) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        repo.create({"student_no": "  "})
    with pytest.raises(sqlite3.IntegrityError):
        repo.create({"name": "缺少学号"})

    repo.create({"student_no": "00123"})
    with pytest.raises(sqlite3.IntegrityError):
        repo.create({"student_no": "00123"})
    assert repo.count_records() == 1


def test_leading_zero_student_numbers_are_distinct(repo: CertificateRecordRepository) -> None:
    first = repo.create({"student_no": "00123"})
    second = repo.create({"student_no": "123"})

    assert repo.get(first)["student_no"] == "00123"
    assert repo.get(second)["student_no"] == "123"
    assert repo.count_records() == 2


def test_existing_student_nos_returns_exact_matches_only(
    repo: CertificateRecordRepository,
) -> None:
    repo.create({"student_no": "00123"})
    repo.create({"student_no": "S-2"})

    assert repo.existing_student_nos(["00123", "123", "S-2", "missing"]) == {
        "00123",
        "S-2",
    }
    assert repo.existing_student_nos([]) == set()
    assert repo.existing_student_nos(["00123", "00123"]) == {"00123"}


def test_existing_student_nos_handles_more_than_sqlite_parameter_limit(
    repo: CertificateRecordRepository,
) -> None:
    student_numbers = [f"S-{index:04d}" for index in range(501)]
    for student_no in student_numbers:
        repo.create({"student_no": student_no})

    found = repo.existing_student_nos(student_numbers)

    assert found == set(student_numbers)


def test_list_by_ids_returns_existing_records_in_id_order(
    repo: CertificateRecordRepository,
) -> None:
    first = repo.create({"student_no": "S-1"})
    second = repo.create({"student_no": "S-2"})

    rows = repo.list_by_ids([second, first, 999999, second])

    assert [row["id"] for row in rows] == [first, second]
    assert repo.list_by_ids([]) == []


def test_list_by_ids_handles_more_than_sqlite_parameter_limit(
    repo: CertificateRecordRepository,
) -> None:
    ids = [repo.create({"student_no": f"S-{index:04d}"}) for index in range(501)]

    rows = repo.list_by_ids(ids)

    assert [row["id"] for row in rows] == ids


def test_certificate_numbers_are_optional_and_independent(repo: CertificateRecordRepository) -> None:
    only_graduation = repo.create({"student_no": "S-1", "certificate_no": "NO-A"})
    only_degree = repo.create({"student_no": "S-2", "degree_certificate_no": "NO-A"})
    only_other = repo.create({"student_no": "S-3", "other_certificate_no": "NO-A"})

    assert repo.get(only_graduation)["certificate_no"] == "NO-A"
    assert repo.get(only_graduation)["degree_certificate_no"] is None
    assert repo.get(only_degree)["degree_certificate_no"] == "NO-A"
    assert repo.get(only_other)["other_certificate_no"] == "NO-A"

    combined = repo.create(
        {
            "student_no": "S-4",
            "certificate_no": "NO-B1",
            "degree_certificate_no": "NO-B2",
            "other_certificate_no": "NO-B3",
        }
    )
    row = repo.get(combined)
    assert row["certificate_no"] == "NO-B1"
    assert row["degree_certificate_no"] == "NO-B2"
    assert row["other_certificate_no"] == "NO-B3"

    # 每个字段分别唯一：重复使用同一值会被拒绝。
    with pytest.raises(sqlite3.IntegrityError):
        repo.create({"student_no": "S-5", "certificate_no": "NO-A"})
    with pytest.raises(sqlite3.IntegrityError):
        repo.create({"student_no": "S-6", "degree_certificate_no": "NO-A"})
    with pytest.raises(sqlite3.IntegrityError):
        repo.create({"student_no": "S-7", "other_certificate_no": "NO-A"})


def test_update_changes_only_given_fields(repo: CertificateRecordRepository) -> None:
    record_id = repo.create(
        {"student_no": "00123", "name": "甲", "certificate_no": "NO-A"}
    )

    assert repo.update(record_id, {"name": "乙"}) is True
    row = repo.get(record_id)
    assert row["name"] == "乙"
    assert row["student_no"] == "00123"
    assert row["certificate_no"] == "NO-A"
    assert row["id"] == record_id


def test_update_rejects_non_writable_fields(repo: CertificateRecordRepository) -> None:
    record_id = repo.create({"student_no": "00123"})

    for changes in ({"id": 2}, {"created_at": "2026-10-22T00:00:00.000Z"}, {"unknown": "x"}):
        with pytest.raises(ValueError):
            repo.update(record_id, changes)


def test_update_missing_or_empty_changes(repo: CertificateRecordRepository) -> None:
    assert repo.update(999999, {"name": "不存在"}) is False
    record_id = repo.create({"student_no": "00123"})
    assert repo.update(record_id, {}) is False


def test_update_constraint_failure_keeps_original_value(repo: CertificateRecordRepository) -> None:
    record_id = repo.create({"student_no": "00123"})

    with pytest.raises(sqlite3.IntegrityError):
        repo.update(record_id, {"student_no": "  "})

    assert repo.get(record_id)["student_no"] == "00123"


def test_delete_existing_and_missing(repo: CertificateRecordRepository) -> None:
    record_id = repo.create({"student_no": "00123"})

    assert repo.delete(record_id) is True
    assert repo.get(record_id) is None
    assert repo.delete(record_id) is False


def test_list_and_count_have_stable_order(repo: CertificateRecordRepository) -> None:
    ids = [repo.create({"student_no": f"S-{index}", "name": "同名"}) for index in range(3)]

    assert [row["id"] for row in repo.list_records()] == ids
    assert [row["id"] for row in repo.list_records(descending=True)] == ids[::-1]
    assert [row["id"] for row in repo.list_records(limit=1, offset=1)] == [ids[1]]
    assert [row["id"] for row in repo.list_records(limit=2)] == ids[:2]
    assert repo.count_records() == 3


def test_list_order_by_whitelist_and_pagination_validation(
    repo: CertificateRecordRepository,
) -> None:
    repo.create({"student_no": "S-2", "name": "乙"})
    repo.create({"student_no": "S-1", "name": "甲"})

    rows = repo.list_records(order_by="student_no")
    assert [row["student_no"] for row in rows] == ["S-1", "S-2"]

    with pytest.raises(ValueError):
        repo.list_records(order_by="name; DROP TABLE certificate_records")
    with pytest.raises(ValueError):
        repo.list_records(limit=0)
    with pytest.raises(ValueError):
        repo.list_records(offset=-1)


def test_exists_by_number_is_scoped_per_field(
    repo: CertificateRecordRepository,
) -> None:
    record_id = repo.create({"student_no": "S-1", "certificate_no": "NO-A"})

    assert repo.exists_by_number("certificate_no", "NO-A") is True
    # 不同编号字段互不影响。
    assert repo.exists_by_number("degree_certificate_no", "NO-A") is False
    assert repo.exists_by_number("other_certificate_no", "NO-A") is False
    assert repo.exists_by_number("certificate_no", "NO-B") is False
    # 排除自身后可用于编辑场景。
    assert repo.exists_by_number("certificate_no", "NO-A", exclude_id=record_id) is False


def test_exists_by_number_rejects_non_number_fields(
    repo: CertificateRecordRepository,
) -> None:
    with pytest.raises(ValueError):
        repo.exists_by_number("student_no", "S-1")
    with pytest.raises(ValueError):
        repo.exists_by_number("certificate_no", "  ")
    with pytest.raises(ValueError):
        repo.exists_by_number("certificate_no", None)  # type: ignore[arg-type]


def test_count_records_by_photo_path_and_exclude_self(
    repo: CertificateRecordRepository,
) -> None:
    first = repo.create({"student_no": "S-1", "photo_path": "shared.jpg"})
    second = repo.create({"student_no": "S-2", "photo_path": "shared.jpg"})
    repo.create({"student_no": "S-3", "photo_path": "other.jpg"})

    assert repo.count_records_by_photo_path("shared.jpg") == 2
    assert repo.count_records_by_photo_path("shared.jpg", exclude_id=first) == 1
    assert repo.count_records_by_photo_path("shared.jpg", exclude_id=second) == 1
    assert repo.count_records_by_photo_path("missing.jpg") == 0


@pytest.mark.parametrize("photo_path", ["", "   ", None, 123])
def test_count_records_by_photo_path_rejects_empty_value(
    repo: CertificateRecordRepository, photo_path: object
) -> None:
    with pytest.raises(ValueError):
        repo.count_records_by_photo_path(photo_path)  # type: ignore[arg-type]
