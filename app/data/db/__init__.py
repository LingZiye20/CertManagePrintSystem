"""数据库基础设施：连接、Schema、迁移与默认数据。"""

from app.data.db.connection import Database
from app.data.db.migrations import SchemaVersionError, get_schema_version, initialize_database
from app.data.db.schema import EXCEL_FIELD_NAMES, SCHEMA_VERSION

__all__ = [
    "Database",
    "EXCEL_FIELD_NAMES",
    "SCHEMA_VERSION",
    "SchemaVersionError",
    "get_schema_version",
    "initialize_database",
]
