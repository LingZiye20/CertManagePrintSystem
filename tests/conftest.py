"""Pytest 全局配置：离屏渲染与 Repository 测试用的临时数据库。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from app.config.paths import AppPaths  # noqa: E402
from app.data.db import Database, initialize_database  # noqa: E402
from app.services import NumberingService, PhotoService, StudentService  # noqa: E402


@pytest.fixture
def repository_database(tmp_path: Path) -> Database:
    """在 ``tmp_path`` 下初始化独立数据库，供 Repository 测试使用。"""
    db_path = tmp_path / "data" / "certificates.db"
    initialize_database(db_path)
    return Database(db_path)


@pytest.fixture
def student_service(repository_database: Database) -> StudentService:
    """基于临时数据库构造 StudentService，不触碰真实业务数据。"""
    return StudentService(repository_database)


@pytest.fixture
def numbering_service(repository_database: Database) -> NumberingService:
    """基于临时数据库构造 NumberingService，不触碰真实业务数据。"""
    return NumberingService(repository_database)


@pytest.fixture
def app_paths(tmp_path: Path) -> AppPaths:
    """在 ``tmp_path`` 下创建完整的运行期目录，供照片等文件测试使用。"""
    paths = AppPaths.for_program(tmp_path)
    paths.ensure_user_dirs()
    return paths


@pytest.fixture
def photo_service(
    repository_database: Database, app_paths: AppPaths
) -> PhotoService:
    """基于临时数据库与临时照片目录构造 PhotoService。"""
    return PhotoService(repository_database, app_paths)
