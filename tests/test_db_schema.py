"""Schema 约束测试：37 字段映射、学号、三类编号、外键与检查约束。

全部使用 ``tmp_path`` 下的临时数据库，不触碰真实业务数据。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.data.db import EXCEL_FIELD_NAMES, Database, initialize_database

# 独立于实现代码的 C.2 字段清单，用于校验 37 个 Excel 字段没有遗漏或改名。
EXPECTED_EXCEL_FIELDS = (
    "graduation_batch",
    "graduation_review",
    "degree_review",
    "student_type",
    "college_number",
    "degree_category_code",
    "degree_category_name",
    "professional_category",
    "print_marker",
    "certificate_no",
    "degree_certificate_no",
    "candidate_no",
    "province_code",
    "student_no",
    "name",
    "gender",
    "birth_date",
    "id_type",
    "id_number",
    "political_status",
    "ethnicity",
    "institution_code",
    "institution_name",
    "major_code",
    "major_name",
    "branch",
    "faculty_name",
    "class_name",
    "education_level",
    "study_years",
    "study_mode",
    "current_grade",
    "enrollment_date",
    "graduation_date",
    "registration_status",
    "remarks",
    "other_certificate_no",
)

CERTIFICATE_NUMBER_FIELDS = (
    "certificate_no",
    "degree_certificate_no",
    "other_certificate_no",
)

# 覆盖全部 37 个字段的样例记录，值均为虚构测试数据。
FULL_RECORD = {
    "graduation_batch": "2026-01",
    "graduation_review": "通过",
    "degree_review": "通过",
    "student_type": "普通",
    "college_number": "0101",
    "degree_category_code": "0809",
    "degree_category_name": "工学",
    "professional_category": "计算机类",
    "print_marker": "Y",
    "certificate_no": "BJ20260001",
    "degree_certificate_no": "XW20260001",
    "candidate_no": "261101010001",
    "province_code": "45",
    "student_no": "00123",
    "name": "测试学生甲",
    "gender": "女",
    "birth_date": "2000-01-02",
    "id_type": "居民身份证",
    "id_number": "110101200001020011",
    "political_status": "共青团员",
    "ethnicity": "汉族",
    "institution_code": "0101",
    "institution_name": "测试学院",
    "major_code": "080901",
    "major_name": "计算机科学与技术",
    "branch": "第一分院",
    "faculty_name": "信息学院",
    "class_name": "计科2026-1班",
    "education_level": "本科",
    "study_years": "4",
    "study_mode": "普通全日制",
    "current_grade": "2026",
    "enrollment_date": "2022-09-01",
    "graduation_date": "2026-06-30",
    "registration_status": "已注册",
    "remarks": "无",
    "other_certificate_no": "QT20260001",
    "photo_path": "00123.jpg",
}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "certificates.db"
    initialize_database(path)
    return path


def _insert_record(connection: sqlite3.Connection, **values: object) -> int:
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    cursor = connection.execute(
        f"INSERT INTO certificate_records ({columns}) VALUES ({placeholders})",
        tuple(values.values()),
    )
    return int(cursor.lastrowid)


def _insert_print_record(connection: sqlite3.Connection, **overrides: object) -> int:
    values: dict[str, object] = {
        "certificate_record_id": None,
        "certificate_type": "graduation",
        "certificate_no_snapshot": "BJ20260001",
        "template_id": None,
        "printer_name": "测试打印机",
        "result": "success",
    }
    values.update(overrides)
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    cursor = connection.execute(
        f"INSERT INTO print_records ({columns}) VALUES ({placeholders})",
        tuple(values.values()),
    )
    return int(cursor.lastrowid)


def _insert_template(connection: sqlite3.Connection, **overrides: object) -> int:
    values: dict[str, object] = {
        "name": "默认毕结业模板",
        "certificate_type": "graduation",
        "canvas_width_mm": 210.0,
        "canvas_height_mm": 297.0,
        "template_json": "{}",
        "is_builtin": 1,
    }
    values.update(overrides)
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    cursor = connection.execute(
        f"INSERT INTO certificate_templates ({columns}) VALUES ({placeholders})",
        tuple(values.values()),
    )
    return int(cursor.lastrowid)


# ---------------------------------------------------------------------------
# 37 个 Excel 字段映射
# ---------------------------------------------------------------------------


def test_excel_fields_match_c2_mapping() -> None:
    assert EXCEL_FIELD_NAMES == EXPECTED_EXCEL_FIELDS
    assert len(EXCEL_FIELD_NAMES) == 37
    assert len(set(EXCEL_FIELD_NAMES)) == 37


def test_all_excel_fields_and_internal_fields_are_text(db_path: Path) -> None:
    with Database(db_path).connect() as connection:
        columns = {
            row["name"]: row["type"]
            for row in connection.execute("PRAGMA table_info(certificate_records)")
        }

    for field in EXPECTED_EXCEL_FIELDS:
        assert columns[field] == "TEXT", f"{field} 必须是 TEXT"
    assert columns["photo_path"] == "TEXT"
    assert columns["created_at"] == "TEXT"
    assert columns["updated_at"] == "TEXT"


def test_all_37_fields_round_trip_and_keep_leading_zeros(db_path: Path) -> None:
    with Database(db_path).transaction() as connection:
        record_id = _insert_record(connection, **FULL_RECORD)

    with Database(db_path).connect() as connection:
        row = connection.execute(
            "SELECT * FROM certificate_records WHERE id = ?", (record_id,)
        ).fetchone()

    for field, value in FULL_RECORD.items():
        assert row[field] == value, f"{field} 读写不一致"
    # 学号、院校代码、专业代码、考生号、院号必须是字符串并保留前导零。
    assert isinstance(row["student_no"], str)
    assert row["student_no"] == "00123"
    assert row["college_number"] == "0101"
    assert row["institution_code"] == "0101"
    assert row["major_code"] == "080901"
    assert row["candidate_no"] == "261101010001"
    # 打印标记按 TEXT 原样存取，不解释为布尔值。
    assert row["print_marker"] == "Y"
    # 照片只保存相对文件名。
    assert row["photo_path"] == "00123.jpg"


def test_only_student_no_is_required(db_path: Path) -> None:
    with Database(db_path).transaction() as connection:
        record_id = _insert_record(connection, student_no="00124")

    with Database(db_path).connect() as connection:
        row = connection.execute(
            "SELECT * FROM certificate_records WHERE id = ?", (record_id,)
        ).fetchone()

    for field in EXPECTED_EXCEL_FIELDS:
        if field != "student_no":
            assert row[field] is None, f"{field} 应可为空"
    assert row["photo_path"] is None


# ---------------------------------------------------------------------------
# 学号规则
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   "])
def test_student_no_is_required_and_not_blank(db_path: Path, value: object) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with Database(db_path).transaction() as connection:
            _insert_record(connection, student_no=value)


def test_student_no_is_unique(db_path: Path) -> None:
    with Database(db_path).transaction() as connection:
        _insert_record(connection, student_no="00123")

    with pytest.raises(sqlite3.IntegrityError):
        with Database(db_path).transaction() as connection:
            _insert_record(connection, student_no="00123")


def test_student_no_with_and_without_leading_zero_are_distinct(db_path: Path) -> None:
    with Database(db_path).transaction() as connection:
        _insert_record(connection, student_no="00123")
        _insert_record(connection, student_no="123")

    with Database(db_path).connect() as connection:
        values = [
            row["student_no"]
            for row in connection.execute(
                "SELECT student_no FROM certificate_records ORDER BY id"
            )
        ]
    assert values == ["00123", "123"]


# ---------------------------------------------------------------------------
# 三类证书编号：独立可空、分别唯一、互不关联
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", CERTIFICATE_NUMBER_FIELDS)
def test_each_certificate_number_is_optional(db_path: Path, field: str) -> None:
    with Database(db_path).transaction() as connection:
        record_id = _insert_record(
            connection, student_no="S-001", **{field: "NO-001"}
        )

    with Database(db_path).connect() as connection:
        row = connection.execute(
            "SELECT * FROM certificate_records WHERE id = ?", (record_id,)
        ).fetchone()

    assert row[field] == "NO-001"
    for other in CERTIFICATE_NUMBER_FIELDS:
        if other != field:
            assert row[other] is None


def test_record_may_hold_all_three_certificate_numbers(db_path: Path) -> None:
    with Database(db_path).transaction() as connection:
        record_id = _insert_record(
            connection,
            student_no="S-002",
            certificate_no="BJ-001",
            degree_certificate_no="XW-001",
            other_certificate_no="QT-001",
        )

    with Database(db_path).connect() as connection:
        row = connection.execute(
            "SELECT * FROM certificate_records WHERE id = ?", (record_id,)
        ).fetchone()

    assert row["certificate_no"] == "BJ-001"
    assert row["degree_certificate_no"] == "XW-001"
    assert row["other_certificate_no"] == "QT-001"


@pytest.mark.parametrize("field", CERTIFICATE_NUMBER_FIELDS)
def test_each_certificate_number_is_unique(db_path: Path, field: str) -> None:
    with Database(db_path).transaction() as connection:
        _insert_record(connection, student_no="S-001", **{field: "NO-DUP"})

    with pytest.raises(sqlite3.IntegrityError):
        with Database(db_path).transaction() as connection:
            _insert_record(connection, student_no="S-002", **{field: "NO-DUP"})


def test_same_value_allowed_across_different_number_columns(db_path: Path) -> None:
    # 三个字段之间没有强制业务关联，各自唯一即可，不得互相排斥。
    with Database(db_path).transaction() as connection:
        _insert_record(
            connection,
            student_no="S-003",
            certificate_no="SHARED",
            degree_certificate_no="SHARED",
            other_certificate_no="SHARED",
        )

    with Database(db_path).connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS total FROM certificate_records"
        ).fetchone()["total"]
    assert count == 1


@pytest.mark.parametrize("field", CERTIFICATE_NUMBER_FIELDS)
def test_blank_certificate_number_is_rejected(db_path: Path, field: str) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with Database(db_path).transaction() as connection:
            _insert_record(connection, student_no="S-004", **{field: "  "})


# ---------------------------------------------------------------------------
# 外键与删除行为
# ---------------------------------------------------------------------------


def test_print_record_requires_existing_certificate_record(db_path: Path) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with Database(db_path).transaction() as connection:
            _insert_print_record(connection, certificate_record_id=999999)


def test_print_record_requires_existing_template(db_path: Path) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with Database(db_path).transaction() as connection:
            _insert_print_record(connection, template_id=999999)


def test_deleting_certificate_record_sets_print_reference_to_null(db_path: Path) -> None:
    with Database(db_path).transaction() as connection:
        record_id = _insert_record(connection, student_no="S-005")
        print_id = _insert_print_record(connection, certificate_record_id=record_id)

    with Database(db_path).transaction() as connection:
        connection.execute("DELETE FROM certificate_records WHERE id = ?", (record_id,))

    with Database(db_path).connect() as connection:
        row = connection.execute(
            "SELECT * FROM print_records WHERE id = ?", (print_id,)
        ).fetchone()
    assert row["certificate_record_id"] is None
    assert row["certificate_no_snapshot"] == "BJ20260001"


# ---------------------------------------------------------------------------
# 检查约束
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule_key", ["invalid", "GRADUATION", ""])
def test_numbering_rule_key_is_limited(db_path: Path, rule_key: str) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with Database(db_path).transaction() as connection:
            connection.execute(
                "INSERT INTO numbering_rules (rule_key, rule_name) VALUES (?, ?)",
                (rule_key, "非法规则"),
            )


@pytest.mark.parametrize("width", [0, -1])
def test_sequence_width_must_be_positive(db_path: Path, width: int) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with Database(db_path).transaction() as connection:
            connection.execute(
                "UPDATE numbering_rules SET sequence_width = ? WHERE rule_key = 'graduation'",
                (width,),
            )


def test_enabled_accepts_only_zero_or_one(db_path: Path) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with Database(db_path).transaction() as connection:
            connection.execute(
                "UPDATE numbering_rules SET enabled = 2 WHERE rule_key = 'graduation'"
            )


def test_sequence_next_value_must_be_at_least_one(db_path: Path) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with Database(db_path).transaction() as connection:
            connection.execute(
                "UPDATE number_sequences SET next_value = 0 WHERE sequence_key = 'graduation'"
            )


def test_print_snapshot_must_not_be_blank(db_path: Path) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with Database(db_path).transaction() as connection:
            _insert_print_record(connection, certificate_no_snapshot="  ")


def test_print_record_type_is_limited(db_path: Path) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with Database(db_path).transaction() as connection:
            _insert_print_record(connection, certificate_type="unknown")


def test_template_constraints_and_builtin_uniqueness(db_path: Path) -> None:
    with Database(db_path).transaction() as connection:
        _insert_template(connection, certificate_type="graduation", is_builtin=1)
        # 同一类型允许一个非内置模板。
        _insert_template(
            connection, name="自定义毕结业模板", certificate_type="graduation", is_builtin=0
        )
        # 不同类型可以各有内置模板。
        _insert_template(connection, name="默认学位模板", certificate_type="degree", is_builtin=1)

    with pytest.raises(sqlite3.IntegrityError):
        with Database(db_path).transaction() as connection:
            _insert_template(connection, name="第二个内置模板", certificate_type="graduation", is_builtin=1)

    with pytest.raises(sqlite3.IntegrityError):
        with Database(db_path).transaction() as connection:
            _insert_template(connection, name="非法类型", certificate_type="unknown")

    with pytest.raises(sqlite3.IntegrityError):
        with Database(db_path).transaction() as connection:
            _insert_template(connection, name="非法尺寸", canvas_width_mm=0)


def test_builtin_templates_are_not_seeded_in_phase_two(db_path: Path) -> None:
    with Database(db_path).connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS total FROM certificate_templates"
        ).fetchone()["total"]
    assert count == 0


def test_operation_log_has_no_unconfirmed_enum_check(db_path: Path) -> None:
    with Database(db_path).transaction() as connection:
        connection.execute(
            """
            INSERT INTO operation_logs (
                operation_type, target_type, target_id, summary, result
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("test_action", "certificate_record", None, "测试操作", "待业务确认的状态"),
        )


def test_app_settings_key_must_not_be_blank(db_path: Path) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with Database(db_path).transaction() as connection:
            connection.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?)", ("  ", "x")
            )
