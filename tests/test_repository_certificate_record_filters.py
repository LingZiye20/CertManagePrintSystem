"""CertificateRecordRepository 筛选专项测试。"""

from __future__ import annotations

import pytest

from app.data.db import Database
from app.repositories import CertificateRecordRepository


@pytest.fixture
def repo(repository_database: Database) -> CertificateRecordRepository:
    return CertificateRecordRepository(repository_database)


def _seed(repo: CertificateRecordRepository) -> None:
    repo.create(
        {
            "student_no": "001",
            "name": "张三",
            "class_name": "软件一班",
            "major_name": "软件工程",
            "faculty_name": "计算机学院",
        }
    )
    repo.create(
        {
            "student_no": "002",
            "name": "李四",
            "class_name": "软件二班",
            "major_name": "网络工程",
            "faculty_name": "计算机学院",
        }
    )
    repo.create(
        {
            "student_no": "100",
            "name": "王五",
            "class_name": "会计一班",
            "major_name": "会计学",
            "faculty_name": "商学院",
        }
    )


def test_student_no_filter_is_exact(repo: CertificateRecordRepository) -> None:
    _seed(repo)

    rows = repo.list_records(filters={"student_no": "001"})
    assert [row["student_no"] for row in rows] == ["001"]
    assert repo.list_records(filters={"student_no": "00"}) == []
    assert repo.list_records(filters={"student_no": " 002 "})[0]["student_no"] == "002"


def test_text_filters_use_contains_match_and_combine(repo: CertificateRecordRepository) -> None:
    _seed(repo)

    rows = repo.list_records(filters={"major_name": "工程", "faculty_name": "计算机"})
    assert {row["student_no"] for row in rows} == {"001", "002"}
    assert repo.count_records(filters={"major_name": "工程", "faculty_name": "计算机"}) == 2

    assert [row["student_no"] for row in repo.list_records(filters={"class_name": "会计"})] == [
        "100"
    ]


def test_like_wildcards_are_escaped(repo: CertificateRecordRepository) -> None:
    repo.create({"student_no": "S-1", "name": "100%通过"})
    repo.create({"student_no": "S-2", "name": "100X通过"})
    repo.create({"student_no": "S-3", "name": "A_B"})
    repo.create({"student_no": "S-4", "name": "AXB"})
    repo.create({"student_no": "S-5", "name": "反斜杠\\值"})

    percent = repo.list_records(filters={"name": "%"})
    assert [row["student_no"] for row in percent] == ["S-1"]

    underscore = repo.list_records(filters={"name": "_"})
    assert [row["student_no"] for row in underscore] == ["S-3"]

    backslash = repo.list_records(filters={"name": "\\"})
    assert [row["student_no"] for row in backslash] == ["S-5"]


def test_blank_filters_are_ignored(repo: CertificateRecordRepository) -> None:
    _seed(repo)

    rows = repo.list_records(filters={"name": "  ", "class_name": "", "student_no": " "})
    assert len(rows) == 3
    assert repo.count_records(filters={"name": "  "}) == 3


def test_unknown_filter_field_rejected(repo: CertificateRecordRepository) -> None:
    with pytest.raises(ValueError):
        repo.list_records(filters={"name; DROP TABLE certificate_records": "x"})
    with pytest.raises(ValueError):
        repo.count_records(filters={"updated_at": "2026"})


def test_filters_apply_to_pagination_and_count_consistently(
    repo: CertificateRecordRepository,
) -> None:
    _seed(repo)

    filters = {"faculty_name": "计算机"}
    total = repo.count_records(filters=filters)
    first = repo.list_records(filters=filters, limit=1, order_by="student_no")
    second = repo.list_records(filters=filters, limit=1, offset=1, order_by="student_no")

    assert total == 2
    assert [first[0]["student_no"], second[0]["student_no"]] == ["001", "002"]
