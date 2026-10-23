"""数据库表结构与索引定义。

字段顺序与提示词包 C.2 的 37 个 Excel 字段映射完全一致。
日期统一保存为 ``YYYY-MM-DD`` 文本；时间戳统一保存为 UTC ISO-8601 文本，
例如 ``2026-10-22T10:00:00.000Z``。
"""

from __future__ import annotations

SCHEMA_VERSION = 1

# C.2 规定的 37 个 Excel 字段（顺序即 Excel 列顺序）。
EXCEL_FIELD_NAMES: tuple[str, ...] = (
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

# 基础 Schema（v1）。所有语句使用 IF NOT EXISTS，便于重复初始化时补建缺失对象。
BASE_SCHEMA_SQL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS certificate_records (
        id INTEGER PRIMARY KEY,
        graduation_batch TEXT,
        graduation_review TEXT,
        degree_review TEXT,
        student_type TEXT,
        college_number TEXT,
        degree_category_code TEXT,
        degree_category_name TEXT,
        professional_category TEXT,
        print_marker TEXT,
        certificate_no TEXT UNIQUE
            CHECK (certificate_no IS NULL OR length(trim(certificate_no)) > 0),
        degree_certificate_no TEXT UNIQUE
            CHECK (degree_certificate_no IS NULL OR length(trim(degree_certificate_no)) > 0),
        candidate_no TEXT,
        province_code TEXT,
        student_no TEXT NOT NULL UNIQUE
            CHECK (length(trim(student_no)) > 0),
        name TEXT,
        gender TEXT,
        birth_date TEXT,
        id_type TEXT,
        id_number TEXT,
        political_status TEXT,
        ethnicity TEXT,
        institution_code TEXT,
        institution_name TEXT,
        major_code TEXT,
        major_name TEXT,
        branch TEXT,
        faculty_name TEXT,
        class_name TEXT,
        education_level TEXT,
        study_years TEXT,
        study_mode TEXT,
        current_grade TEXT,
        enrollment_date TEXT,
        graduation_date TEXT,
        registration_status TEXT,
        remarks TEXT,
        other_certificate_no TEXT UNIQUE
            CHECK (other_certificate_no IS NULL OR length(trim(other_certificate_no)) > 0),
        photo_path TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS numbering_rules (
        rule_key TEXT PRIMARY KEY
            CHECK (rule_key IN ('graduation', 'degree', 'other')),
        rule_name TEXT NOT NULL CHECK (length(trim(rule_name)) > 0),
        fixed_text_1 TEXT NOT NULL DEFAULT '',
        fixed_text_2 TEXT NOT NULL DEFAULT '',
        fixed_text_3 TEXT NOT NULL DEFAULT '',
        fixed_text_4 TEXT NOT NULL DEFAULT '',
        sequence_width INTEGER NOT NULL DEFAULT 4 CHECK (sequence_width >= 1),
        enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS number_sequences (
        sequence_key TEXT PRIMARY KEY
            CHECK (sequence_key IN ('graduation', 'degree', 'other')),
        next_value INTEGER NOT NULL DEFAULT 1 CHECK (next_value >= 1),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS certificate_templates (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL CHECK (length(trim(name)) > 0),
        certificate_type TEXT NOT NULL
            CHECK (certificate_type IN ('graduation', 'degree', 'other')),
        canvas_width_mm REAL NOT NULL CHECK (canvas_width_mm > 0),
        canvas_height_mm REAL NOT NULL CHECK (canvas_height_mm > 0),
        template_json TEXT NOT NULL,
        is_builtin INTEGER NOT NULL DEFAULT 0 CHECK (is_builtin IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_certificate_templates_builtin
        ON certificate_templates (certificate_type)
        WHERE is_builtin = 1
    """,
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY CHECK (length(trim(key)) > 0),
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS operation_logs (
        id INTEGER PRIMARY KEY,
        operation_type TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT,
        summary TEXT NOT NULL,
        result TEXT NOT NULL,
        error_message TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS print_records (
        id INTEGER PRIMARY KEY,
        certificate_record_id INTEGER
            REFERENCES certificate_records (id) ON DELETE SET NULL,
        certificate_type TEXT NOT NULL
            CHECK (certificate_type IN ('graduation', 'degree', 'other')),
        certificate_no_snapshot TEXT NOT NULL
            CHECK (length(trim(certificate_no_snapshot)) > 0),
        template_id INTEGER
            REFERENCES certificate_templates (id) ON DELETE SET NULL,
        printer_name TEXT,
        submitted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        result TEXT NOT NULL,
        error_message TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS import_batches (
        id INTEGER PRIMARY KEY,
        file_name TEXT NOT NULL,
        started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        finished_at TEXT,
        status TEXT NOT NULL,
        total_rows INTEGER NOT NULL DEFAULT 0 CHECK (total_rows >= 0),
        error_summary TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_certificate_records_name
        ON certificate_records (name)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_certificate_records_class_name
        ON certificate_records (class_name)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_operation_logs_created_at
        ON operation_logs (created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_print_records_certificate_record_id
        ON print_records (certificate_record_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_print_records_submitted_at
        ON print_records (submitted_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_import_batches_started_at
        ON import_batches (started_at)
    """,
)
